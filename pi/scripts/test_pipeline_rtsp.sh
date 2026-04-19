#!/usr/bin/env bash
# Shell-only reproduction of the camera_pipeline service.
# Serves an RTSP stream on :8554/live using libcamerasrc + v4l2h264enc.
# Run this BEFORE debugging the Python service — if this fails, Python won't fix it.
set -euo pipefail

WIDTH=${WIDTH:-1920}
HEIGHT=${HEIGHT:-1080}
FPS=${FPS:-30}
BITRATE=${BITRATE:-6000000}

# Requires `gst-rtsp-server` test-launch binary. On Pi OS it lives in
# /usr/lib/aarch64-linux-gnu/gstreamer-1.0/examples/ or similar; adjust path if needed.
TEST_LAUNCH="${TEST_LAUNCH:-$(command -v test-launch || true)}"
if [[ -z "$TEST_LAUNCH" ]]; then
    echo "test-launch not found on PATH. Install gstreamer1.0-rtsp or build from gst-rtsp-server examples."
    exit 1
fi

exec "$TEST_LAUNCH" -p 8554 \
    "libcamerasrc ! video/x-raw,width=${WIDTH},height=${HEIGHT},framerate=${FPS}/1 ! \
     v4l2convert ! \
     v4l2h264enc extra-controls=\"controls,h264_profile=4,video_bitrate=${BITRATE}\" ! \
     h264parse config-interval=1 ! \
     rtph264pay name=pay0 pt=96 config-interval=1"
