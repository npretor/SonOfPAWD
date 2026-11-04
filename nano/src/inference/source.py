"""GStreamer appsink reader for the local MediaMTX RTSP egress.

`max-buffers=1 drop=true` is the freshest-frame strategy: detection cadence
decouples from stream cadence and there is no buffering backlog. Reads
return None on EOS or pipeline error; main.py is expected to reconnect.
"""
import logging
import threading
import time
from typing import Optional

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib  # noqa: E402

import numpy as np  # noqa: E402

log = logging.getLogger("inference.source")

# 640x384 keeps the 16:10 letterbox close to the camera's 16:9, with both
# dims divisible by 32 — friendly to the downstream letterbox math.
_PIPELINE = (
    "rtspsrc location={url} latency=100 protocols=tcp "
    "! rtph264depay ! h264parse ! nvv4l2decoder "
    "! nvvidconv ! video/x-raw,format=BGRx,width=640,height=384 "
    "! videoconvert ! video/x-raw,format=BGR "
    "! appsink name=sink emit-signals=true sync=false max-buffers=1 drop=true"
)

_FRAME_W = 640
_FRAME_H = 384


class RtspSource:
    def __init__(self, url: str):
        self.url = url
        self._pipeline: Optional[Gst.Element] = None
        self._appsink: Optional[Gst.Element] = None
        self._loop: Optional[GLib.MainLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._latest: Optional[np.ndarray] = None
        self._last_frame_t = 0.0
        self._eos_or_err = False

    # ----- lifecycle ------------------------------------------------------

    def start(self) -> None:
        Gst.init(None)
        self._pipeline = Gst.parse_launch(_PIPELINE.format(url=self.url))
        self._appsink = self._pipeline.get_by_name("sink")
        self._appsink.connect("new-sample", self._on_sample)

        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", self._on_eos)
        bus.connect("message::error", self._on_error)

        self._loop = GLib.MainLoop()
        self._loop_thread = threading.Thread(
            target=self._loop.run, name="rtsp-glib-loop", daemon=True
        )
        self._loop_thread.start()

        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self.stop()
            raise RuntimeError("RTSP pipeline failed to enter PLAYING")
        # Wait up to 5s for first frame so callers see fast failure on a
        # missing source.
        deadline = time.monotonic() + 5.0
        with self._cond:
            while self._latest is None and not self._eos_or_err:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(timeout=remaining)
        log.info("rtsp source PLAYING url=%s healthy=%s", self.url, self.healthy())

    def stop(self) -> None:
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        if self._loop is not None:
            self._loop.quit()
            self._loop = None
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=1.0)
            self._loop_thread = None

    def healthy(self) -> bool:
        return (
            not self._eos_or_err
            and self._last_frame_t > 0
            and (time.monotonic() - self._last_frame_t) < 2.0
        )

    # ----- read -----------------------------------------------------------

    def read(self, timeout_s: float = 0.5) -> Optional[np.ndarray]:
        deadline = time.monotonic() + timeout_s
        with self._cond:
            while self._latest is None and not self._eos_or_err:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(timeout=remaining)
            if self._eos_or_err:
                return None
            frame = self._latest
            self._latest = None  # consume; next read waits for fresher
            return frame

    # ----- gstreamer callbacks (called on GLib loop thread) ---------------

    def _on_sample(self, appsink):
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            arr = np.frombuffer(mapinfo.data, dtype=np.uint8)
            if arr.size != _FRAME_H * _FRAME_W * 3:
                # Caps mismatch — drop silently, the pipeline pre-rolls.
                return Gst.FlowReturn.OK
            frame = arr.reshape((_FRAME_H, _FRAME_W, 3)).copy()
        finally:
            buf.unmap(mapinfo)
        with self._cond:
            self._latest = frame
            self._last_frame_t = time.monotonic()
            self._cond.notify_all()
        return Gst.FlowReturn.OK

    def _on_eos(self, _bus, _msg):
        log.warning("rtsp source EOS")
        with self._cond:
            self._eos_or_err = True
            self._cond.notify_all()

    def _on_error(self, _bus, msg):
        err, dbg = msg.parse_error()
        log.error("rtsp source ERROR: %s [%s]", err, dbg)
        with self._cond:
            self._eos_or_err = True
            self._cond.notify_all()
