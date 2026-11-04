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
    python3-numpy \
    python3-opencv \
    python3-libnvinfer \
    python3-libnvinfer-dev \
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

echo "== mosquitto auth =="
sudo mkdir -p /etc/turret
if [ ! -s /etc/turret/mqtt.password ]; then
    echo "  generating new MQTT password at /etc/turret/mqtt.password"
    PW=$(openssl rand -hex 24)
    echo -n "$PW" | sudo tee /etc/turret/mqtt.password >/dev/null
    sudo chmod 644 /etc/turret/mqtt.password
else
    echo "  /etc/turret/mqtt.password exists, reusing"
    PW=$(sudo cat /etc/turret/mqtt.password)
fi
# Mosquitto 1.4 (default on L4T 32 / Ubuntu 18.04) does not accept -c and -b together.
# Reset the password file first, then add the user in batch mode.
echo -n | sudo tee /etc/mosquitto/passwd >/dev/null
sudo chown mosquitto:mosquitto /etc/mosquitto/passwd
sudo chmod 600 /etc/mosquitto/passwd
sudo mosquitto_passwd -b /etc/mosquitto/passwd turret "$PW"
sudo tee /etc/mosquitto/conf.d/turret.conf >/dev/null <<EOF
allow_anonymous false
password_file /etc/mosquitto/passwd
EOF
# mosquitto on 18.04 is an LSB init script: its 'stop' returns before the old
# broker has exited, and 'start' backgrounds the daemon and reports success
# even if it dies at once (e.g. port 1883 still held by the exiting broker).
# systemd then shows "active (exited)" with no broker running. So: stop, wait
# for the process to really be gone, start, and wait for the port to listen.
sudo systemctl stop mosquitto
for _ in $(seq 1 50); do pgrep -x mosquitto >/dev/null || break; sleep 0.2; done
if pgrep -x mosquitto >/dev/null; then
    echo "  old mosquitto still running after 10s; killing it"
    sudo pkill -x mosquitto || true
    sleep 1
fi
sudo systemctl start mosquitto
LISTENING=0
for _ in $(seq 1 50); do
    if ss -ltn 2>/dev/null | grep -q ':1883 '; then LISTENING=1; break; fi
    sleep 0.2
done
if [ "$LISTENING" != 1 ]; then
    echo "  mosquitto is not listening on :1883 after applying auth config."
    echo "  Broker log:"
    sudo tail -n 30 /var/log/mosquitto/mosquitto.log
    exit 1
fi
echo "  mosquitto listening on :1883"
PW_SHA=$(echo -n "$PW" | sha256sum | awk '{print $1}')
echo "  password sha256: $PW_SHA"

echo "== smoke test mosquitto =="
mosquitto_pub -h localhost -u turret -P "$PW" -t turret/test/prep -m '{"ts":0,"ok":true}' \
    && echo "  mosquitto pub (with auth): OK" \
    || { echo "  mosquitto pub FAILED"; exit 1; }
if mosquitto_pub -h localhost -t turret/test/prep -m '{"ts":0,"ok":true}' >/dev/null 2>&1; then
    echo "  anonymous pub UNEXPECTEDLY SUCCEEDED -- auth is not enforced"; exit 1
else
    echo "  anonymous pub correctly rejected"
fi

echo "== inference model dir =="
sudo mkdir -p /var/lib/turret/models
sudo chown "$USER":"$USER" /var/lib/turret/models

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "== inference container image =="
INFERENCE_TAG="${INFERENCE_IMAGE:-turret-inference:r32.7.1}"
if docker image inspect "$INFERENCE_TAG" >/dev/null 2>&1; then
    echo "  $INFERENCE_TAG present, skipping"
else
    echo "  building $INFERENCE_TAG (first run pulls the L4T base, ~5-10 GB)"
    "$REPO_ROOT/nano/scripts/build_inference_image.sh" || \
        echo "  image build failed; inference service will not start until resolved."
fi

echo "== TensorRT engine =="
ENGINE_PATH=/var/lib/turret/models/yolov8n.engine
ONNX_PATH="$REPO_ROOT/nano/src/inference/models/yolov8n.onnx"
if [ ! -f "$ENGINE_PATH" ]; then
    if [ -f "$ONNX_PATH" ]; then
        echo "  building $ENGINE_PATH (this can take 2-4 minutes)"
        "$REPO_ROOT/nano/scripts/build_engine.sh" || \
            echo "  engine build failed; inference service will not start until resolved."
    else
        echo "  ONNX not committed yet at $ONNX_PATH; skip. Run build_engine.sh later."
    fi
else
    echo "  $ENGINE_PATH exists, skipping"
fi

echo "== GPIO + serial access (sensor-wakeup, ir-led, motion-ctrl, fire-control) =="
NEED_RELOGIN=0
for grp in gpio dialout; do
    if id -nG "$USER" | tr ' ' '\n' | grep -qx "$grp"; then
        echo "  $USER already in $grp"
    else
        sudo usermod -aG "$grp" "$USER"
        echo "  added $USER to $grp"
        NEED_RELOGIN=1
    fi
done
sudo install -m 644 "$REPO_ROOT/nano/scripts/udev/99-turret-arduino.rules" /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=tty
echo "  udev rule installed (/dev/turret-arduino appears when the Arduino is plugged in)"
sudo install -d -o "$USER" -g "$USER" /var/lib/turret/motion_ctrl
echo "  $(ls -ld /var/lib/turret/motion_ctrl)"
python3 -c "import Jetson.GPIO, serial, dataclasses" \
    && echo "  Jetson.GPIO / pyserial / dataclasses: OK" \
    || { echo "  missing python deps -- run: pip3 install --user -r nano/requirements.txt"; exit 1; }

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
if [ "${NEED_RELOGIN:-0}" = 1 ]; then
    echo "NOTE: group membership changed -- reboot (or log out/in) before starting the GPIO services."
fi
echo "Next: 'make -C deploy install-nano-units enable-nano-units start-nano install-camera-unit'."
