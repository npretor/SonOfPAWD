#!/usr/bin/env python3
"""recorder: pulls Pi RTSP -> writes MP4 clips to disk on arm.

No decode, no re-encode -- bytes off the wire are bytes on disk.

Pipeline:
    rtspsrc -> rtph264depay -> h264parse -> valve(gate) -> splitmuxsink(mux)

Arming model (recorder owns the trailing window):
    armed=true  -> open gate, split-now (clean clip boundary)
    armed=false -> start trailing-window timer; on expiry, split-now and
                   close gate. If armed=true arrives before expiry, cancel
                   and stay recording. One armed cycle = one clip file.
"""
import datetime as dt
import logging
import os
import signal
import sys

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib  # noqa: E402

from common.config.hosts import RTSP_URL, CLIPS_DIR, TRAILING_SECONDS
from common.messaging.client import MqttClient
from common.messaging.topics import TOPIC_RECORDER_ARM, TOPIC_RECORDER_EVENT

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("recorder")

RECONNECT_BACKOFF_S = 2

PIPELINE = (
    f"rtspsrc location={RTSP_URL} protocols=tcp latency=100 name=src "
    "! rtph264depay ! h264parse "
    "! valve name=gate drop=true "
    "! splitmuxsink name=mux muxer=matroskamux "
    "  max-size-time=0 max-size-bytes=0"
)


class Recorder:
    def __init__(self):
        self.pipeline = None
        self.valve = None
        self.mux = None
        self.mqtt = MqttClient(service_name="recorder")
        self.loop = GLib.MainLoop()
        self._close_timer_id = None
        self._restart_pending = False
        # Cached arm state so we re-apply it after a reconnect
        self._desired_armed = False
        self._desired_trailing_s = TRAILING_SECONDS

    # ----- pipeline construction & lifecycle ------------------------------

    def _build_pipeline(self):
        log.info("building pipeline")
        self.pipeline = Gst.parse_launch(PIPELINE)
        self.valve = self.pipeline.get_by_name("gate")
        self.mux = self.pipeline.get_by_name("mux")
        self.mux.connect("format-location", self._on_format_location)

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", self._on_eos)
        bus.connect("message::error", self._on_error)
        bus.connect("message::element", self._on_element_msg)

    def _start_pipeline(self):
        self._build_pipeline()
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            log.error("pipeline failed to start, retrying in %ds", RECONNECT_BACKOFF_S)
            self._restart_pipeline()
            return False
        log.info("pipeline PLAYING (rtsp=%s)", RTSP_URL)
        # Re-apply arm state after a reconnect
        if self._desired_armed:
            log.info("re-applying armed state after reconnect")
            self._open_gate()
        return False  # one-shot for GLib.timeout

    def _restart_pipeline(self):
        if self._restart_pending:
            return
        self._restart_pending = True
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
            self.valve = None
            self.mux = None
        if self._close_timer_id is not None:
            GLib.source_remove(self._close_timer_id)
            self._close_timer_id = None
        GLib.timeout_add_seconds(RECONNECT_BACKOFF_S, self._do_restart)

    def _do_restart(self):
        self._restart_pending = False
        self._start_pipeline()
        return False

    # ----- bus message handlers -------------------------------------------

    def _on_eos(self, _bus, _msg):
        log.warning("EOS on pipeline; reconnecting in %ds", RECONNECT_BACKOFF_S)
        self._restart_pipeline()

    def _on_error(self, _bus, msg):
        err, dbg = msg.parse_error()
        log.error("pipeline ERROR: %s [%s]; reconnecting in %ds",
                  err, dbg, RECONNECT_BACKOFF_S)
        self._restart_pipeline()

    def _on_element_msg(self, _bus, msg):
        s = msg.get_structure()
        if s is None:
            return
        if s.get_name() == "splitmuxsink-fragment-closed":
            location = s.get_string("location") or "?"
            log.info("clip closed: %s", location)
            self.mqtt.publish(TOPIC_RECORDER_EVENT, {
                "event": "clip_closed",
                "path": location,
            })

    def _on_format_location(self, _mux, _fragment_id):
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(CLIPS_DIR, f"clip_{ts}.mkv")
        log.info("clip starting: %s", path)
        self.mqtt.publish(TOPIC_RECORDER_EVENT, {
            "event": "clip_started",
            "path": path,
        })
        return path

    # ----- arming logic (always invoked on the GLib main loop thread) -----

    def _on_arm_glib(self, payload):
        armed = bool(payload.get("armed", False))
        trailing_s = int(payload.get("trailing_s", TRAILING_SECONDS))
        self._desired_armed = armed
        self._desired_trailing_s = trailing_s
        log.info("arm message: armed=%s trailing_s=%d", armed, trailing_s)
        if armed:
            self._open_gate()
        else:
            self._schedule_close(trailing_s)
        return False  # idle_add one-shot

    def _open_gate(self):
        if self._close_timer_id is not None:
            GLib.source_remove(self._close_timer_id)
            self._close_timer_id = None
            log.info("rearm: cancelled pending close")
        if self.valve is None:
            log.warning("gate open requested but pipeline not ready; will apply on reconnect")
            return
        if self.valve.get_property("drop"):
            log.info("opening gate")
            self.valve.set_property("drop", False)
            if self.mux is not None:
                self.mux.emit("split-now")

    def _schedule_close(self, trailing_s):
        if self._close_timer_id is not None:
            GLib.source_remove(self._close_timer_id)
        log.info("close scheduled in %ds", trailing_s)
        self._close_timer_id = GLib.timeout_add_seconds(trailing_s, self._close_clip)

    def _close_clip(self):
        log.info("closing clip")
        self._close_timer_id = None
        if self.valve is not None:
            self.valve.set_property("drop", True)
        if self.mux is not None:
            self.mux.emit("split-now")
        return False  # one-shot

    # ----- MQTT entry point (paho thread) ---------------------------------

    def _on_arm_mqtt(self, _topic, payload):
        # Marshal onto the GLib main loop so all state mutations happen there.
        GLib.idle_add(self._on_arm_glib, payload)

    # ----- main -----------------------------------------------------------

    def run(self):
        Gst.init(None)
        os.makedirs(CLIPS_DIR, exist_ok=True)

        self.mqtt.connect()
        self.mqtt.subscribe(TOPIC_RECORDER_ARM, self._on_arm_mqtt)

        self._start_pipeline()

        def shutdown(signum, _frame):
            log.info("signal %d received, shutting down", signum)
            # If currently recording, flush the in-progress clip first.
            if self.valve is not None and not self.valve.get_property("drop"):
                self._close_clip()
            if self.pipeline is not None:
                self.pipeline.set_state(Gst.State.NULL)
            try:
                self.mqtt.disconnect()
            except Exception:
                log.exception("mqtt disconnect failed")
            self.loop.quit()

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        self.loop.run()
        return 0


def main():
    return Recorder().run()


if __name__ == "__main__":
    sys.exit(main())
