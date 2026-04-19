#!/usr/bin/env python3
"""camera-pipeline: rpicam-vid (HW encoder) -> RTSP on :8554/live.

The libcamerasrc + v4l2h264enc GStreamer path is broken on this Pi/firmware
combo (encoder rejects every frame). rpicam-vid reaches the same hardware
encoder via libcamera's private path, so we use it as a subprocess and let
gst-rtsp-server consume its raw H.264 stdout via fdsrc.
"""
import logging
import os
import signal
import subprocess
import sys
import threading

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstRtspServer", "1.0")
from gi.repository import Gst, GstRtspServer, GLib  # noqa: E402

from common.config.hosts import (
    RTSP_PORT,
    RTSP_PATH,
    CAMERA_WIDTH,
    CAMERA_HEIGHT,
    CAMERA_FPS,
    CAMERA_BITRATE,
)
from common.messaging.client import MqttClient
from common.messaging.topics import TOPIC_CAMERA_STATUS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("camera-pipeline")


def spawn_rpicam() -> subprocess.Popen:
    """Start rpicam-vid streaming raw H.264 to its stdout, forever."""
    cmd = [
        "rpicam-vid",
        "-t", "0",                          # run forever
        "-n",                               # no preview window
        "--inline",                         # inline SPS/PPS (every IDR carries headers)
        "--width", str(CAMERA_WIDTH),
        "--height", str(CAMERA_HEIGHT),
        "--framerate", str(CAMERA_FPS),
        "--codec", "h264",
        "--bitrate", str(CAMERA_BITRATE),
        "-o", "-",                          # stdout
    ]
    log.info("spawning: %s", " ".join(cmd))
    # stderr -> our journal so we can see rpicam-vid errors alongside ours
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def main() -> int:
    Gst.init(None)

    rpicam = spawn_rpicam()
    fd = rpicam.stdout.fileno()
    # fdsrc reads via the inherited fd; make sure it stays open across exec/clone
    os.set_inheritable(fd, True)

    pipeline_str = (
        f"fdsrc fd={fd} do-timestamp=true "
        "! h264parse config-interval=1 "
        "! rtph264pay name=pay0 pt=96 config-interval=1"
    )
    log.info("pipeline: %s", pipeline_str)

    server = GstRtspServer.RTSPServer()
    server.set_service(str(RTSP_PORT))

    factory = GstRtspServer.RTSPMediaFactory()
    factory.set_launch(pipeline_str)
    factory.set_shared(True)

    server.get_mount_points().add_factory(RTSP_PATH, factory)

    if server.attach(None) == 0:
        log.error("RTSP server failed to attach on port %d", RTSP_PORT)
        rpicam.terminate()
        return 1

    log.info("RTSP ready on 0.0.0.0:%d%s", RTSP_PORT, RTSP_PATH)

    mqtt = MqttClient(service_name="camera-pipeline")
    mqtt.connect()
    mqtt.publish(TOPIC_CAMERA_STATUS, {
        "rtsp_port": RTSP_PORT,
        "rtsp_path": RTSP_PATH,
        "encoding": "h264",
        "resolution": f"{CAMERA_WIDTH}x{CAMERA_HEIGHT}",
        "framerate": CAMERA_FPS,
        "bitrate": CAMERA_BITRATE,
        "encoder": "rpicam-vid",
    })

    loop = GLib.MainLoop()

    def watch_rpicam():
        rc = rpicam.wait()
        log.error("rpicam-vid exited rc=%d, stopping service so systemd restarts us", rc)
        GLib.idle_add(loop.quit)

    threading.Thread(target=watch_rpicam, daemon=True).start()

    def shutdown(signum, _frame):
        log.info("signal %d received, shutting down", signum)
        try:
            mqtt.disconnect()
        except Exception:
            log.exception("mqtt disconnect failed")
        if rpicam.poll() is None:
            rpicam.terminate()
            try:
                rpicam.wait(timeout=3)
            except subprocess.TimeoutExpired:
                rpicam.kill()
        loop.quit()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
