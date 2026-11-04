#!/usr/bin/env python3
"""camera-pipeline (Nano): IMX219 -> Argus -> NVENC H.264 -> MediaMTX :8554/live.

We publish INTO the local MediaMTX with rtspclientsink rather than running
our own RTSP server; MediaMTX fans the stream out to the UI (WebRTC),
inference and recorder.

Runs as a *system* unit (User=dlinano) so that a root-owned ExecStopPost
helper (nano/scripts/camera_recover.sh) can restart nvargus-daemon, and
reboot as a last resort, when this process dies in a way that may have left
Argus wedged. The exit code tells the helper what went wrong:

    0  clean stop (SIGTERM/SIGINT)
    1  unexpected / other pipeline error   -> plain restart
    3  camera/Argus failure or stall       -> restart nvargus-daemon, escalate
    4  publish failure (MediaMTX)          -> plain restart

Death by signal or core dump (OOM kill, segfault, stop timeout) is treated
by the helper like exit 3.
"""
import argparse
import logging
import os
import signal
import socket
import sys
import threading
import time

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib  # noqa: E402

from common.config.hosts import (  # noqa: E402
    RTSP_PORT,
    RTSP_PATH,
    CAMERA_WIDTH,
    CAMERA_HEIGHT,
    CAMERA_FPS,
    CAMERA_BITRATE,
    CAMERA_SENSOR_ID,
    CAMERA_SENSOR_MODE,
    CAMERA_FLIP,
    CAMERA_STATE_DIR,
)
from common.messaging.client import MqttClient  # noqa: E402
from common.messaging.topics import TOPIC_CAMERA_STATUS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("camera-pipeline")

EXIT_CLEAN = 0
EXIT_OTHER = 1
EXIT_CAMERA = 3
EXIT_PUBLISH = 4

# Argus session setup takes ~2-3 s on a cold daemon.
FIRST_FRAME_TIMEOUT_S = 10.0
# Camera delivering no frames for this long => Argus/sensor stall.
SRC_STALL_S = 3.0
# Camera fine but nothing leaving the encoder/parser => publish side stuck.
OUT_STALL_S = 5.0
# Streaming this long without trouble => tell the recovery helper we're healthy
# (resets its consecutive-failure counter).
HEALTHY_AFTER_S = 30.0
# Upper bound on pipeline teardown; a wedged Argus can hang set_state(NULL).
TEARDOWN_TIMEOUT_S = 5.0
EOS_DRAIN_TIMEOUT_S = 2.0


def build_pipeline_str(rtsp_url: str) -> str:
    caps = (
        f"video/x-raw(memory:NVMM),width={CAMERA_WIDTH},height={CAMERA_HEIGHT},"
        f"framerate={CAMERA_FPS}/1"
    )
    flip = ""
    if CAMERA_FLIP:
        flip = f"! nvvidconv flip-method={CAMERA_FLIP} ! {caps} "
    # The leaky queue right after the source decouples Argus from downstream
    # back-pressure: if the publish side blocks, the camera keeps flowing (we
    # drop raw frames, which is harmless) so the two stall detectors below can
    # tell "camera dead" apart from "MediaMTX stuck".
    return (
        f"nvarguscamerasrc name=src sensor-id={CAMERA_SENSOR_ID} "
        f"sensor-mode={CAMERA_SENSOR_MODE} "
        f"! {caps} "
        "! queue name=srcq max-size-buffers=2 max-size-bytes=0 max-size-time=0 "
        "leaky=downstream "
        f"{flip}"
        f"! nvv4l2h264enc name=enc bitrate={CAMERA_BITRATE} control-rate=1 "
        f"iframeinterval={CAMERA_FPS} insert-sps-pps=true preset-level=1 "
        "! h264parse name=parse config-interval=1 "
        f"! rtspclientsink name=sink location={rtsp_url} protocols=tcp"
    )


def wait_for_port(host: str, port: int, stop: threading.Event) -> bool:
    """Block until host:port accepts TCP. Returns False if asked to stop.

    Waiting here (instead of exiting) keeps a MediaMTX outage from turning
    into a rapid restart loop.
    """
    last_log = 0.0
    while not stop.is_set():
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            now = time.monotonic()
            if now - last_log > 30:
                log.warning("waiting for MediaMTX on %s:%d ...", host, port)
                last_log = now
            stop.wait(2.0)
    return False


class CameraPipeline:
    def __init__(self, rtsp_url: str, state_dir: str):
        self.rtsp_url = rtsp_url
        self.state_dir = state_dir
        self.loop = GLib.MainLoop()
        self.pipeline = None
        self.src = None
        self.sink = None
        self.exit_code = EXIT_CLEAN
        self._stopping = False
        self._eos_seen = threading.Event()

        self._start_t = 0.0
        self._last_src_t = 0.0
        self._last_out_t = 0.0
        self._src_frames = 0
        self._status_sent = False
        self._healthy_marked = False

        self.mqtt = MqttClient(service_name="camera-pipeline")

    # ----- setup ----------------------------------------------------------

    def start(self) -> None:
        pipeline_str = build_pipeline_str(self.rtsp_url)
        log.info("pipeline: %s", pipeline_str)
        self.pipeline = Gst.parse_launch(pipeline_str)
        self.src = self.pipeline.get_by_name("src")
        self.sink = self.pipeline.get_by_name("sink")

        # Frame-flow probes. Called on streaming threads; plain float writes
        # are atomic enough for a watchdog.
        srcq_sink = self.pipeline.get_by_name("srcq").get_static_pad("sink")
        srcq_sink.add_probe(Gst.PadProbeType.BUFFER, self._on_src_buffer)
        parse_src = self.pipeline.get_by_name("parse").get_static_pad("src")
        parse_src.add_probe(Gst.PadProbeType.BUFFER, self._on_out_buffer)

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        bus.connect("message::eos", self._on_eos)

        self._start_t = time.monotonic()
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            # Usually Argus refusing the session (sensor busy / daemon wedged).
            self.fail(EXIT_CAMERA, "pipeline failed to enter PLAYING")
            return
        GLib.timeout_add(500, self._watchdog)

    # ----- probes & watchdog ---------------------------------------------

    def _on_src_buffer(self, _pad, _info):
        self._last_src_t = time.monotonic()
        self._src_frames += 1
        return Gst.PadProbeReturn.OK

    def _on_out_buffer(self, _pad, _info):
        self._last_out_t = time.monotonic()
        return Gst.PadProbeReturn.OK

    def _watchdog(self) -> bool:
        if self._stopping:
            return False
        now = time.monotonic()

        if self._last_src_t == 0.0:
            if now - self._start_t > FIRST_FRAME_TIMEOUT_S:
                self.fail(EXIT_CAMERA, "no frames from camera within %.0fs" % FIRST_FRAME_TIMEOUT_S)
                return False
            return True

        if now - self._last_src_t > SRC_STALL_S:
            self.fail(EXIT_CAMERA, "camera stalled: no frames for %.1fs" % (now - self._last_src_t))
            return False

        # Only judge the output side once it has had a chance to start.
        out_ref = self._last_out_t or self._start_t
        if now - out_ref > OUT_STALL_S + (FIRST_FRAME_TIMEOUT_S if not self._last_out_t else 0):
            self.fail(EXIT_PUBLISH, "publish stalled: camera OK but no encoded output for %.1fs"
                      % (now - out_ref))
            return False

        if not self._status_sent and self._last_out_t:
            self._publish_status()
            self._status_sent = True

        if not self._healthy_marked and now - self._start_t > HEALTHY_AFTER_S:
            self._mark_healthy()
            self._healthy_marked = True
        return True

    def _publish_status(self) -> None:
        log.info("streaming to %s", self.rtsp_url)
        try:
            self.mqtt.publish(TOPIC_CAMERA_STATUS, {
                "rtsp_port": RTSP_PORT,
                "rtsp_path": RTSP_PATH,
                "encoding": "h264",
                "resolution": f"{CAMERA_WIDTH}x{CAMERA_HEIGHT}",
                "framerate": CAMERA_FPS,
                "bitrate": CAMERA_BITRATE,
                "encoder": "nvv4l2h264enc",
                "sensor": "imx219",
                "sensor_mode": CAMERA_SENSOR_MODE,
            })
        except Exception:
            log.exception("camera status publish failed")

    def _mark_healthy(self) -> None:
        # Read (and deleted) by the root recovery helper to reset its
        # consecutive-failure counter. Best-effort: missing dir is fine when
        # running by hand.
        path = os.path.join(self.state_dir, "healthy")
        try:
            with open(path, "w") as f:
                f.write(str(int(time.time())))
            log.info("stream healthy for %.0fs; marked %s", HEALTHY_AFTER_S, path)
        except OSError as e:
            log.debug("could not write healthy marker %s: %s", path, e)

    # ----- bus handlers ---------------------------------------------------

    def _classify(self, obj) -> int:
        while obj is not None:
            if obj is self.src:
                return EXIT_CAMERA
            if obj is self.sink:
                return EXIT_PUBLISH
            obj = obj.get_parent()
        return EXIT_OTHER

    def _on_error(self, _bus, msg):
        err, dbg = msg.parse_error()
        code = self._classify(msg.src)
        self.fail(code, "pipeline ERROR from %s: %s [%s]" % (msg.src.get_name(), err, dbg))

    def _on_eos(self, _bus, _msg):
        if self._stopping:
            self._eos_seen.set()
            return
        # The camera never ends on its own; an unrequested EOS means Argus quit.
        self.fail(EXIT_CAMERA, "unexpected EOS from pipeline")

    # ----- shutdown -------------------------------------------------------

    def fail(self, code: int, reason: str) -> None:
        if self._stopping:
            return
        log.error("%s -> exiting with code %d", reason, code)
        self.exit_code = code
        self._stopping = True
        # No EOS drain on failure: the pipeline is already broken.
        GLib.idle_add(self._finish)

    def request_stop(self) -> bool:
        """SIGTERM/SIGINT handler (GLib main loop)."""
        if self._stopping:
            return GLib.SOURCE_CONTINUE
        log.info("stop requested; draining")
        self._stopping = True
        self.exit_code = EXIT_CLEAN
        if self.pipeline is not None:
            # EOS lets nvarguscamerasrc close its Argus session cleanly —
            # an abrupt teardown is what tends to wedge nvargus-daemon.
            self.pipeline.send_event(Gst.Event.new_eos())

            def _wait_eos():
                self._eos_seen.wait(EOS_DRAIN_TIMEOUT_S)
                GLib.idle_add(self._finish)
            threading.Thread(target=_wait_eos, daemon=True).start()
        else:
            GLib.idle_add(self._finish)
        return GLib.SOURCE_CONTINUE

    def _finish(self) -> bool:
        self.teardown()
        self.loop.quit()
        return False

    def teardown(self) -> None:
        if self.pipeline is not None:
            pipeline, self.pipeline = self.pipeline, None
            t = threading.Thread(target=pipeline.set_state, args=(Gst.State.NULL,), daemon=True)
            t.start()
            t.join(TEARDOWN_TIMEOUT_S)
            if t.is_alive():
                # Argus is hung. Bail out hard; the helper will restart the daemon.
                log.error("pipeline teardown hung >%.0fs; forcing exit", TEARDOWN_TIMEOUT_S)
                self._disconnect_mqtt()
                os._exit(EXIT_CAMERA)
        self._disconnect_mqtt()

    def _disconnect_mqtt(self) -> None:
        try:
            self.mqtt.disconnect()
        except Exception:
            log.exception("mqtt disconnect failed")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rtsp-url", default=f"rtsp://127.0.0.1:{RTSP_PORT}{RTSP_PATH}",
                    help="RTSP publish target (default: local MediaMTX)")
    ap.add_argument("--state-dir", default=CAMERA_STATE_DIR,
                    help="dir for the 'healthy' marker read by camera_recover.sh")
    args = ap.parse_args()

    Gst.init(None)
    app = CameraPipeline(args.rtsp_url, args.state_dir)

    stop_wait = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # Before the GLib loop runs, a signal just aborts the port wait.
        signal.signal(sig, lambda *_: stop_wait.set())

    app.mqtt.connect()

    host = args.rtsp_url.split("//", 1)[1].split("/", 1)[0]
    rhost, _, rport = host.rpartition(":")
    if not wait_for_port(rhost or host, int(rport or 554), stop_wait):
        app._disconnect_mqtt()
        return EXIT_CLEAN

    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, app.request_stop)

    app.start()
    app.loop.run()
    log.info("exit code %d", app.exit_code)
    return app.exit_code


if __name__ == "__main__":
    sys.exit(main())
