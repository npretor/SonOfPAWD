#!/usr/bin/env bash
# Verify that nvarguscamerasrc can open the IMX219 (NoIR, CAM1) and produce frames.
# Displays a local preview window for DURATION seconds then exits.
# Run on the Nano directly (requires a connected display or X forwarding).
#
# Usage: ./test_camera.sh [duration_seconds]
#   duration_seconds  how long to run the preview (default: 10)
#
# Fallback: if nvarguscamerasrc fails, re-runs with v4l2src so you can tell
# whether the problem is Argus/IMX219 or GStreamer itself.
# Stop camera-pipeline first (make -C deploy stop-camera): Argus allows one
# client per sensor.

set -euo pipefail

DURATION="${1:-10}"

echo "=== SonOfPAWD camera smoke-test ==="
echo "Duration: ${DURATION}s"
echo ""

# Check that GStreamer is available
if ! command -v gst-launch-1.0 &>/dev/null; then
    echo "ERROR: gst-launch-1.0 not found. Install gstreamer1.0-tools." >&2
    exit 1
fi

run_argus() {
    echo "--- Attempt 1: nvarguscamerasrc (IMX219 via Argus) ---"
    # Always pin sensor-mode: letting Argus pick a full-res mode exhausted NVMM
    # and panicked the Nano (seen with the previous IMX477). IMX219 mode 2 =
    # 1920x1080 @ 30fps, the mode camera-pipeline uses. Width/height pinned
    # right after the source so Argus can't negotiate a larger allocation.
    gst-launch-1.0 -e \
        nvarguscamerasrc sensor-id=0 sensor-mode=2 num-buffers=$(( DURATION * 30 )) \
        ! 'video/x-raw(memory:NVMM),width=1920,height=1080,framerate=30/1' \
        ! nvvidconv \
        ! 'video/x-raw(memory:NVMM),width=640,height=480,format=I420' \
        ! nvoverlaysink sync=false \
    || gst-launch-1.0 -e \
        nvarguscamerasrc sensor-id=0 sensor-mode=2 num-buffers=$(( DURATION * 30 )) \
        ! 'video/x-raw(memory:NVMM),width=1920,height=1080,framerate=30/1' \
        ! nvvidconv \
        ! 'video/x-raw,width=640,height=480,format=BGRx' \
        ! videoconvert \
        ! autovideosink sync=false
}

run_v4l2_fallback() {
    echo ""
    echo "--- Fallback: v4l2src (checks whether GStreamer itself works) ---"
    echo "    Available V4L2 devices:"
    v4l2-ctl --list-devices 2>/dev/null || echo "    (v4l2-ctl not found)"
    echo ""
    gst-launch-1.0 -e \
        v4l2src num-buffers=$(( DURATION * 30 )) \
        ! videoconvert \
        ! autovideosink sync=false
}

if run_argus; then
    echo ""
    echo "SUCCESS: nvarguscamerasrc produced frames for ${DURATION}s."
else
    ARGUS_EXIT=$?
    echo ""
    echo "FAILED: nvarguscamerasrc exited with code ${ARGUS_EXIT}."
    echo "Trying v4l2src fallback to isolate the problem..."
    if run_v4l2_fallback; then
        echo ""
        echo "v4l2src works — problem is specific to Argus / IMX219 (cable, sensor_mode, driver)."
    else
        echo ""
        echo "v4l2src also failed — GStreamer display stack may be broken or no camera attached."
    fi
    exit "${ARGUS_EXIT}"
fi
