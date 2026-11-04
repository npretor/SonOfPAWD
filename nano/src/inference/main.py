#!/usr/bin/env python3
"""inference: TensorRT object detection on the Nano.

Reads frames from the local MediaMTX RTSP egress, runs YOLOv8n FP16 at a
fixed cadence, publishes detections on `turret/inference/detection`.

Disabled by default; the hub turns this on when `mode in {auto-aim, auto-sentry}`
via `turret/inference/enable`. The `target_class` from `turret/hub/config`
gates the allow-list.
"""
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional, Set

import numpy as np

from common.messaging.client import MqttClient
from common.messaging.topics import (
    TOPIC_HUB_CONFIG,
    TOPIC_INFER_DETECT,
    TOPIC_INFER_ENABLE,
)

from nano.src.inference.models.coco_classes import NAME_TO_ID
from nano.src.inference.postprocess import decode_yolov8, project_to_normalized
from nano.src.inference.preprocess import letterbox, to_chw_fp16
from nano.src.inference.source import RtspSource

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("inference")


@dataclass
class InferConfig:
    rtsp_url: str = "rtsp://127.0.0.1:8554/live"
    engine_path: str = "/var/lib/turret/models/yolov8n.engine"
    input_size: int = 320
    infer_hz: float = 10.0
    score_thresh: float = 0.25
    iou_thresh: float = 0.45
    default_target_class: str = "cat"


def _resolve_allow_classes(target_class: str) -> Set[int]:
    cid = NAME_TO_ID.get(target_class)
    if cid is None:
        log.warning("unknown target_class %r; falling back to 'cat'", target_class)
        cid = NAME_TO_ID["cat"]
    return {cid}


def _publish_health(mqtt: MqttClient, status: str, detail: str = "") -> None:
    mqtt.publish(mqtt.health_topic, {
        "service": mqtt.service_name,
        "status": status,
        "detail": detail,
    })


def main() -> int:
    cfg = InferConfig()

    if not os.path.exists(cfg.engine_path):
        log.error(
            "TRT engine not found at %s; build it with nano/scripts/build_engine.sh",
            cfg.engine_path,
        )
        return 2

    log.info("loading engine: %s", cfg.engine_path)
    # Import lazily so a missing CUDA stack still lets `python -m` produce a
    # readable error instead of crashing during module import.
    from nano.src.inference.engine import Engine, BACKEND
    log.info("CUDA backend: %s", BACKEND)
    engine = Engine(cfg.engine_path, input_size=cfg.input_size)

    log.info("opening rtsp: %s", cfg.rtsp_url)
    source = RtspSource(cfg.rtsp_url)
    source.start()

    mqtt = MqttClient(service_name="inference")
    mqtt_lock = threading.Lock()

    state = {
        "enabled": False,
        "target_class": cfg.default_target_class,
    }

    def on_enable(_topic, payload):
        with mqtt_lock:
            state["enabled"] = bool(payload.get("enabled", False))
        log.info("enable=%s", state["enabled"])

    def on_config(_topic, payload):
        tc = payload.get("target_class")
        if not tc:
            return
        with mqtt_lock:
            state["target_class"] = str(tc)
        log.info("target_class=%s", state["target_class"])

    mqtt.connect()
    mqtt.subscribe(TOPIC_INFER_ENABLE, on_enable)
    mqtt.subscribe(TOPIC_HUB_CONFIG, on_config)

    stop = threading.Event()

    def _on_signal(signum, _frame):
        log.info("signal %d, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    period = 1.0 / cfg.infer_hz
    next_run = time.monotonic()
    last_health = 0.0
    last_health_status = "up"
    fps_window: list = []  # (timestamp, ) for FPS calc
    rtsp_reconnect_at: Optional[float] = None

    rc = 0
    try:
        while not stop.is_set():
            with mqtt_lock:
                enabled = state["enabled"]
                target_class = state["target_class"]

            if not enabled:
                stop.wait(0.1)
                continue

            if rtsp_reconnect_at is not None:
                if time.monotonic() < rtsp_reconnect_at:
                    stop.wait(0.1)
                    continue
                log.info("rtsp reconnect attempt")
                try:
                    source.stop()
                    source = RtspSource(cfg.rtsp_url)
                    source.start()
                    rtsp_reconnect_at = None
                except Exception:
                    log.exception("rtsp reconnect failed; backoff")
                    rtsp_reconnect_at = time.monotonic() + 2.0
                    continue

            frame = source.read(timeout_s=0.5)
            if frame is None:
                _publish_health(mqtt, "degraded", "rtsp source")
                last_health_status = "degraded"
                rtsp_reconnect_at = time.monotonic() + 1.0
                continue

            now = time.monotonic()
            if now < next_run:
                stop.wait(next_run - now)
                continue
            next_run = now + period

            try:
                padded, scale, pad = letterbox(frame, size=cfg.input_size)
                chw = to_chw_fp16(padded)
                out = engine.infer(chw)
                allow = _resolve_allow_classes(target_class)
                allow = None
                raw_dets = decode_yolov8(
                    out,
                    conf_thresh=cfg.score_thresh,
                    iou_thresh=cfg.iou_thresh,
                    allow_classes=allow,
                )
                h, w = frame.shape[:2]
                dets = project_to_normalized(raw_dets, scale, pad, (w, h))
            except Exception:
                log.exception("inference step failed")
                _publish_health(mqtt, "degraded", "infer error")
                last_health_status = "degraded"
                stop.wait(0.5)
                continue

            if dets:
                mqtt.publish(TOPIC_INFER_DETECT, {"detections": dets})

            fps_window.append(now)
            fps_window[:] = [t for t in fps_window if now - t <= 5.0]

            if now - last_health > 5.0:
                fps = len(fps_window) / 5.0
                if fps < cfg.infer_hz * 0.5:
                    status = "degraded"
                    detail = "low fps %.1f" % fps
                else:
                    status = "up"
                    detail = "%.1ffps" % fps
                if status != last_health_status or status == "up":
                    _publish_health(mqtt, status, detail)
                    last_health_status = status
                last_health = now

    except Exception:
        log.exception("inference main crashed")
        rc = 1
    finally:
        try:
            source.stop()
        except Exception:
            log.exception("source stop failed")
        try:
            engine.close()
        except Exception:
            log.exception("engine close failed")
        try:
            mqtt.disconnect()
        except Exception:
            log.exception("mqtt disconnect failed")
    return rc


if __name__ == "__main__":
    sys.exit(main())
