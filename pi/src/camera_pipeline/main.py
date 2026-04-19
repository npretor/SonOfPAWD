#!/usr/bin/env python3
"""camera-pipeline: IMX477 (libcamera) -> H.264 -> RTSP on :8554/live.

Runs on the Pi. Single owner of the camera sensor.
"""
import logging
import signal
import sys

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


PIPELINE = (
    "libcamerasrc ! "
    f"video/x-raw,width={CAMERA_WIDTH},height={CAMERA_HEIGHT},framerate={CAMERA_FPS}/1 ! "
    "v4l2convert ! "
    f'v4l2h264enc extra-controls="controls,h264_profile=4,video_bitrate={CAMERA_BITRATE}" ! '
    "h264parse config-interval=1 ! "
    "rtph264pay name=pay0 pt=96 config-interval=1"
)


def main() -> int:
    Gst.init(None)

    server = GstRtspServer.RTSPServer()
    server.set_service(str(RTSP_PORT))

    factory = GstRtspServer.RTSPMediaFactory()
    factory.set_launch(PIPELINE)
    factory.set_shared(True)

    mounts = server.get_mount_points()
    mounts.add_factory(RTSP_PATH, factory)

    if server.attach(None) == 0:
        log.error("RTSP server failed to attach on port %d", RTSP_PORT)
        return 1

    log.info("RTSP ready on 0.0.0.0:%d%s", RTSP_PORT, RTSP_PATH)
    log.info("Pipeline: %s", PIPELINE)

    mqtt = MqttClient(service_name="camera-pipeline")
    mqtt.connect()
    mqtt.publish(TOPIC_CAMERA_STATUS, {
        "rtsp_port": RTSP_PORT,
        "rtsp_path": RTSP_PATH,
        "encoding": "h264",
        "resolution": f"{CAMERA_WIDTH}x{CAMERA_HEIGHT}",
        "framerate": CAMERA_FPS,
        "bitrate": CAMERA_BITRATE,
    })

    loop = GLib.MainLoop()

    def shutdown(signum, _frame):
        log.info("signal %d received, shutting down", signum)
        mqtt.disconnect()
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
