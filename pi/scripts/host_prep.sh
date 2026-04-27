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

echo "== check MQTT password file =="
if [ ! -s /etc/turret/mqtt.password ]; then
    cat <<EOF
/etc/turret/mqtt.password is MISSING on this host. Services cannot authenticate.

After running host_prep.sh on the Nano, copy the file here. From the Nano run:

    scp /etc/turret/mqtt.password ${USER}@$(hostname):/tmp/mqtt.password

Then on this Pi:

    sudo mkdir -p /etc/turret
    sudo mv /tmp/mqtt.password /etc/turret/mqtt.password
    sudo chmod 644 /etc/turret/mqtt.password

Then re-run this script.
EOF
    exit 1
fi
PW_SHA=$(sudo sha256sum /etc/turret/mqtt.password | awk '{print $1}')
echo "  /etc/turret/mqtt.password OK"
echo "  password sha256: $PW_SHA"
echo "  (must match the sha256 printed by the Nano's host_prep.sh)"

echo "== enable lingering so user units run without login =="
sudo loginctl enable-linger "$USER"

echo "== /var/lib/turret writable by $USER (motion-ctrl calibration) =="
sudo mkdir -p /var/lib/turret
sudo chown "$USER":"$USER" /var/lib/turret
echo "  $(ls -ld /var/lib/turret)"

echo "== verify rpicam-vid (our encoder path) =="
if command -v rpicam-vid >/dev/null 2>&1; then
    echo "  rpicam-vid: $(command -v rpicam-vid)"
else
    echo "  rpicam-vid NOT FOUND -- install rpicam-apps"
    exit 1
fi

echo "== quick smoke test (2s capture to /dev/null) =="
if rpicam-vid -t 2000 -n --inline --codec h264 -o /dev/null >/dev/null 2>/tmp/rpicam-smoke.err; then
    echo "  rpicam-vid smoke test: OK"
else
    echo "  rpicam-vid smoke test FAILED. Error output:"
    sed 's/^/    /' /tmp/rpicam-smoke.err
    echo "  If the camera is in use by the service, stop it first and re-run:"
    echo "    systemctl --user stop camera-pipeline"
    exit 1
fi

echo "== verify gst-rtsp-server python bindings =="
python3 -c "import gi; gi.require_version('GstRtspServer','1.0'); from gi.repository import GstRtspServer" \
    && echo "  GstRtspServer python bindings: OK" \
    || { echo "  GstRtspServer bindings MISSING -- install gir1.2-gst-rtsp-server-1.0"; exit 1; }

echo "== done =="
echo "Next: deploy from the Nano with 'make -C deploy deploy-pi install-pi-units enable-pi-units'."
