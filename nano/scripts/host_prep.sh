#!/usr/bin/env bash
# One-time host prep for the Nano. Idempotent; safe to re-run.
# Cheap/essential steps come first so a flaky optional step (chrony, etc.)
# can't block the recorder from being able to start.
set -euo pipefail

echo "== apt packages =="
sudo apt update
sudo apt install -y \
    mosquitto \
    mosquitto-clients \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-libav \
    gstreamer1.0-rtsp \
    python3-gst-1.0 \
    python3-gi \
    python3-paho-mqtt \
    avahi-daemon

echo "== create clips directory (recorder needs this to start) =="
sudo mkdir -p /var/lib/turret/clips
sudo chown "$USER":"$USER" /var/lib/turret/clips
echo "  $(ls -ld /var/lib/turret/clips)"

echo "== verify GStreamer elements =="
for el in rtspsrc rtph264depay h264parse splitmuxsink mp4mux; do
    if gst-inspect-1.0 "$el" >/dev/null 2>&1; then
        echo "  $el: OK"
    else
        echo "  $el: MISSING"
        exit 1
    fi
done

echo "== enable mosquitto =="
sudo systemctl enable --now mosquitto

echo "== smoke test mosquitto =="
mosquitto_pub -h localhost -t turret/test/prep -m '{"ts":0,"ok":true}' \
    && echo "  mosquitto pub: OK" \
    || { echo "  mosquitto pub FAILED"; exit 1; }

echo "== enable lingering for user systemd units =="
sudo loginctl enable-linger "$USER"

# --- optional from here down; failures don't block the recorder ---
set +e

echo "== chrony (clock sync; nice-to-have for cross-host timestamps) =="
sudo apt install -y chrony
if sudo systemctl is-active --quiet systemd-timesyncd; then
    echo "  disabling systemd-timesyncd (conflicts with chrony on port 123)"
    sudo systemctl disable --now systemd-timesyncd
fi
if sudo systemctl enable --now chrony; then
    echo "  chrony: OK"
else
    echo "  chrony failed -- run 'journalctl -xeu chrony' to debug. Skipping; not fatal."
fi

echo "== done =="
echo "Next: 'make -C deploy install-nano-units enable-nano-units start-nano'."
