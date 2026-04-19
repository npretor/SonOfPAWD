#!/usr/bin/env bash
# One-time host prep for the Pi. Idempotent; safe to re-run.
set -euo pipefail

echo "== apt packages =="
sudo apt update
sudo apt install -y \
    rpicam-apps \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-rtsp \
    gir1.2-gst-rtsp-server-1.0 \
    python3-gst-1.0 \
    python3-gi \
    python3-paho-mqtt \
    python3-gpiozero \
    chrony \
    avahi-daemon

echo "== enable lingering so user units run without login =="
sudo loginctl enable-linger "$USER"

echo "== verify rpicam-vid (our encoder path) =="
if command -v rpicam-vid >/dev/null 2>&1; then
    echo "  rpicam-vid: $(command -v rpicam-vid)"
else
    echo "  rpicam-vid NOT FOUND -- install rpicam-apps"
    exit 1
fi

echo "== quick smoke test (2s capture to /dev/null) =="
rpicam-vid -t 2000 -n --inline --codec h264 -o /dev/null >/dev/null 2>&1 \
    && echo "  rpicam-vid smoke test: OK" \
    || { echo "  rpicam-vid smoke test FAILED"; exit 1; }

echo "== verify gst-rtsp-server python bindings =="
python3 -c "import gi; gi.require_version('GstRtspServer','1.0'); from gi.repository import GstRtspServer" \
    && echo "  GstRtspServer python bindings: OK" \
    || { echo "  GstRtspServer bindings MISSING -- install gir1.2-gst-rtsp-server-1.0"; exit 1; }

echo "== done =="
echo "Next: deploy from the Nano with 'make -C deploy deploy-pi install-pi-units enable-pi-units'."
