#!/usr/bin/env bash
# One-time host prep for the Pi. Idempotent; safe to re-run.
set -euo pipefail

echo "== apt packages =="
sudo apt update
sudo apt install -y \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    gstreamer1.0-libcamera \
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

echo "== verify libcamerasrc is available =="
if gst-inspect-1.0 libcamerasrc >/dev/null 2>&1; then
    echo "  libcamerasrc: OK"
else
    echo "  libcamerasrc NOT FOUND — check gstreamer1.0-libcamera install"
    exit 1
fi

echo "== verify v4l2h264enc is available =="
if gst-inspect-1.0 v4l2h264enc >/dev/null 2>&1; then
    echo "  v4l2h264enc: OK"
else
    echo "  v4l2h264enc NOT FOUND — check v4l2 m2m drivers"
    exit 1
fi

echo "== done =="
echo "Next: scp pi/systemd/*.service to ~/.config/systemd/user/ and enable them."
