#!/usr/bin/env bash
# Spin up a loopback RTSP server using gst-rtsp-server's test-launch binary.
# Stream is available at rtsp://<nano-ip>:8556/live
# Open from a laptop with: vlc rtsp://<nano-ip>:8556/live
# Stop camera-pipeline first (make -C deploy stop-camera): one Argus client per sensor.
#
# Usage: ./test_rtsp.sh [port]
#   port  RTSP port to listen on (default: 8556)
#
# Requires: gst-rtsp-server (apt: gstreamer1.0-rtsp)
# The test-launch binary is usually at /usr/lib/gstreamer-1.0/gst-rtsp-server
# or installed via: apt install gstreamer1.0-rtsp

set -euo pipefail

PORT="${1:-8556}"  # 8554 belongs to MediaMTX

echo "=== SonOfPAWD RTSP smoke-test ==="
echo "Port: ${PORT}"
echo ""

# Locate test-launch binary
TEST_LAUNCH=""
for candidate in \
    "$(command -v gst-rtsp-server-1.0 2>/dev/null)" \
    "/usr/lib/gstreamer-1.0/gst-rtsp-server" \
    "/usr/local/lib/gstreamer-1.0/gst-rtsp-server" \
    "$(dpkg -L gstreamer1.0-rtsp 2>/dev/null | grep test-launch | head -1)"; do
    if [[ -x "${candidate}" ]]; then
        TEST_LAUNCH="${candidate}"
        break
    fi
done

if [[ -z "${TEST_LAUNCH}" ]]; then
    echo "ERROR: gst-rtsp-server test-launch binary not found." >&2
    echo "Install with: sudo apt install gstreamer1.0-rtsp" >&2
    exit 1
fi

# Print LAN IPs so you know what address to give VLC
echo "Nano IP addresses:"
ip -4 addr show scope global | awk '/inet /{print "  " $2}' | cut -d/ -f1
echo ""
echo "Connect from your laptop with:"
echo "  vlc rtsp://$(hostname -I | awk '{print $1}'):${PORT}/live"
echo ""
echo "Press Ctrl-C to stop."
echo ""

# Pipeline: IMX219 → NVMM → H.264 (NVENC) → RTP payload
# test-launch wraps this in an RTSP server automatically.
"${TEST_LAUNCH}" --port "${PORT}" \
    "( nvarguscamerasrc sensor-id=0 sensor-mode=2 \
       ! 'video/x-raw(memory:NVMM),width=1920,height=1080,framerate=30/1' \
       ! nvv4l2h264enc iframeinterval=30 bitrate=4000000 \
       ! h264parse \
       ! rtph264pay name=pay0 pt=96 )"
