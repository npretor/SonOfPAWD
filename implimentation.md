# Implementation Plan — SonOfPAWD

Locked decisions from `plan2.md`:

- **Pi 4** is the camera node.
- **Ethernet switch** between Pi and Nano.
- **MQTT** (mosquitto on the Nano) for all control-plane messaging.
- **PIR sensor and IR-LED wired to the Pi** (turret head stays self-contained).
- Power: decided, out of scope.
- **Nano IMX477 path is abandoned.** No Argus / `nvarguscamerasrc` code in this repo.

This document is the build order, repo layout, and per-service responsibilities. It replaces `development_steps1.md` for actual execution.

---

## 1. Repo layout

Physically separate Pi and Nano code so deploys are obvious:

```
SonOfPAWD/
├── pi/                         # deployed to Raspberry Pi
│   ├── src/
│   │   ├── camera_pipeline/    # libcamera → H.264 → gst-rtsp-server
│   │   ├── sensor_wakeup/      # PIR GPIO → MQTT
│   │   └── ir_led/             # MQTT → GPIO for IR LEDs
│   ├── scripts/                # one-liners, gst-launch tests, etc.
│   ├── systemd/                # *.service unit files
│   └── requirements.txt
│
├── nano/                       # deployed to Jetson Nano
│   ├── src/
│   │   ├── recorder/           # RTSP in → splitmuxsink
│   │   ├── streamer/           # LAN re-publish (RTSP → WebRTC later)
│   │   ├── hub/                # brain; MQTT orchestration
│   │   └── inference/          # (later) TensorRT / DeepStream
│   ├── scripts/
│   ├── systemd/
│   └── requirements.txt
│
├── common/                     # imported by both sides
│   ├── messaging/              # MQTT topic names, payload schemas, client helpers
│   ├── config/                 # hostnames, ports, bitrates — single source of truth
│   └── requirements.txt
│
├── deploy/
│   ├── Makefile                # `make deploy-pi`, `make deploy-nano`, `make logs-pi`
│   └── rsync-exclude.txt
│
├── plan1.md
├── plan2.md
└── implimentation.md           # this file
```

Rationale:

- **`pi/` and `nano/` are siblings, not nested.** A deploy script rsyncs exactly one of them plus `common/` to the target box.
- **`common/` is the only shared code.** It defines the MQTT contract — if both sides stay in sync on `common/`, they stay in sync on protocol.
- **No top-level `src/`.** The existing empty `src/` directory should be removed.
- **Tests live next to the code they test** (`pi/src/camera_pipeline/tests/`), not in a top-level `tests/`. Keeps a service self-contained.

---

## 2. Deployment model

One source of truth: this git repo on the Nano. The Pi receives code via `rsync` over SSH.

```makefile
# deploy/Makefile (sketch)
PI_HOST ?= raspberrypi.local
PI_USER ?= pi
PI_DEST ?= /home/pi/SonOfPAWD

deploy-pi:
	rsync -avz --delete \
		--exclude-from=rsync-exclude.txt \
		../pi/ ../common/ $(PI_USER)@$(PI_HOST):$(PI_DEST)/
	ssh $(PI_USER)@$(PI_HOST) 'sudo systemctl --user daemon-reload && systemctl --user restart camera-pipeline sensor-wakeup ir-led'

logs-pi:
	ssh $(PI_USER)@$(PI_HOST) 'journalctl --user -f -u camera-pipeline -u sensor-wakeup -u ir-led'
```

Nano-side services are restarted with a plain `systemctl --user restart` locally — no deploy step.

Why rsync, not `git pull` on the Pi:

- One place to commit from. The Pi is not a dev box.
- Fast iteration: edit on the Nano / laptop, `make deploy-pi`, done.
- If you eventually want two clones, you can switch — nothing else changes.

**Set up SSH key auth to the Pi on day one.** Password prompts in the deploy loop are the fastest way to start cutting corners.

---

## 3. Shared contract — `common/`

### 3.1 MQTT broker

- `mosquitto` on the Nano, listening on `0.0.0.0:1883`.
- No auth for v1 (LAN-only). Add username/password when a browser / external client shows up.
- Persistence off for v1 — nothing here needs to survive a broker restart.

### 3.2 Topic schema

All topics live under `turret/`. Payloads are JSON with a mandatory `ts` (unix ms) field.

| Topic | Dir | Publisher | Payload |
|---|---|---|---|
| `turret/pi/health` | Pi → Nano | `sensor-wakeup`, `camera-pipeline`, `ir-led` | `{ts, service, status: "up"\|"down"}`. Uses MQTT last-will for "down". |
| `turret/nano/health` | Nano → Pi | `hub`, `recorder`, `streamer` | same shape |
| `turret/motion` | Pi → Nano | `sensor-wakeup` | `{ts, sensor: "pir", active: true\|false}` |
| `turret/ir/set` | Nano → Pi | `hub` | `{ts, on: true\|false}` |
| `turret/recorder/arm` | Nano internal | `hub` | `{ts, armed: true\|false, trailing_s: 10}` |
| `turret/recorder/event` | Nano → all | `recorder` | `{ts, event: "clip_started"\|"clip_closed", path}` |
| `turret/camera/status` | Pi → Nano | `camera-pipeline` | `{ts, rtsp_url, encoding, resolution, framerate}` |
| `turret/hub/command/<svc>` | Nano → any | `hub` | free-form `{ts, cmd, args}` for RPC-ish needs later |

**`common/messaging/topics.py`** exports these as constants. Don't let magic strings leak into service code.

**`common/messaging/client.py`** wraps `paho-mqtt` with:

- `connect(service_name)` — sets the MQTT `client_id`, `will_set` on `turret/<host>/health`, auto-reconnect.
- `publish(topic, payload)` — serializes JSON, adds `ts` if missing.
- `subscribe(topic, handler)` — JSON-decodes before invoking the handler.

Keeps every service's MQTT code to ~5 lines.

### 3.3 Host / port config

`common/config/hosts.py`:

```python
NANO_HOST = "nano.local"      # mDNS; fall back to IP if mDNS flaky
PI_HOST   = "raspberrypi.local"
MQTT_PORT = 1883
RTSP_PORT = 8554
RTSP_PATH = "/live"
RTSP_URL  = f"rtsp://{PI_HOST}:{RTSP_PORT}{RTSP_PATH}"
```

Both sides import this. Change in one place, deploy to both.

---

## 4. One-time host prep

### Pi (run once)

- Fresh Pi OS (64-bit, Bookworm or later for mature libcamera).
- Enable camera (`raspi-config`), enable SSH, set hostname `raspberrypi`.
- `sudo apt install gstreamer1.0-tools gstreamer1.0-plugins-{base,good,bad,ugly} gstreamer1.0-libav gstreamer1.0-rtsp python3-gst-1.0 python3-gi python3-paho-mqtt python3-gpiozero chrony`
- Wire PIR to a GPIO pin (document which); wire IR-LED MOSFET gate to another pin. Record the pin numbers in `common/config/hosts.py` too.
- `systemctl enable --now chrony` and point it at the Nano or LAN router.

### Nano (run once)

- `sudo apt install mosquitto mosquitto-clients gstreamer1.0-tools python3-gst-1.0 python3-gi python3-paho-mqtt chrony`
- `systemctl enable --now mosquitto`
- Verify `mosquitto_sub -h localhost -t 'turret/#' -v` connects.
- NVDEC is already installed as part of L4T; no extra apt packages for `nvv4l2decoder`.

### Verify both (5-minute smoke test)

From the Nano:
```
mosquitto_sub -h localhost -t 'turret/#' -v
```
From the Pi:
```
mosquitto_pub -h nano.local -t 'turret/pi/health' -m '{"ts":0,"service":"test","status":"up"}'
```
Message should appear on the Nano. If not, stop and fix this before writing a line of Python.

---

## 5. Build order

Each step is independently testable. Each ends with a concrete "done when" check. Do not advance until the check passes.

### Step 0 — repo skeleton ✅ partial

- Create `pi/`, `nano/`, `common/`, `deploy/` directory trees.
- Remove the empty top-level `src/`.
- Write the `deploy/Makefile` and an empty `common/config/hosts.py` populated with the constants from §3.3.
- SSH key from Nano to Pi working.

**Done when:** `make deploy-pi` syncs an empty tree to the Pi with no prompts.

### Step 1 — `common/messaging/` + MQTT smoke test

- Write `topics.py` (constants), `client.py` (paho wrapper).
- Write a 10-line pub and sub script in `common/messaging/examples/` that exercise the wrapper from both hosts.

**Done when:** running the pub script on the Pi prints messages on the Nano via the sub script. Killing the pub triggers the last-will "down" message.

### Step 2 — Pi `camera-pipeline` service

Already proven end-to-end in the Option A test (§previous conversation). Now productionize it:

- Drop the `rpicam-vid` TCP approach. Replace with a Python script using `Gst.parse_launch` + `gst-rtsp-server` Python bindings that serves `rtsp://0.0.0.0:8554/live`.
- Pipeline string (starting point):
  ```
  libcamerasrc ! video/x-raw,width=1920,height=1080,framerate=30/1
    ! v4l2convert ! v4l2h264enc extra-controls="controls,h264_profile=4,video_bitrate=6000000"
    ! h264parse config-interval=1 ! rtph264pay name=pay0 pt=96
  ```
- On startup, publish `turret/camera/status` with the RTSP URL. On clean exit, last-will fires `turret/pi/health status=down`.
- Systemd unit: `pi/systemd/camera-pipeline.service`, `Restart=on-failure`, `RestartSec=2`.

**Done when:** after `make deploy-pi` and a Pi reboot, `vlc rtsp://raspberrypi.local:8554/live` on any LAN machine shows live video within 10 s, no manual commands.

### Step 3 — Pi `sensor-wakeup` service

- `gpiozero.MotionSensor` on the PIR pin. Debounce: publish `active=true` on rising edge, `active=false` after N seconds of no motion (configurable, default 2 s).
- Publishes `turret/motion` via the `common/messaging/client.py` wrapper.
- Health + last-will handled by the wrapper.
- Systemd unit: `pi/systemd/sensor-wakeup.service`.

**Done when:** `mosquitto_sub -h nano.local -t turret/motion -v` on the Nano shows events when you wave at the sensor. Service survives an unplugged-replugged sensor (no crash).

### Step 4 — Pi `ir-led` service

- Subscribes to `turret/ir/set`. Writes GPIO high/low accordingly.
- Safety: force LED off on service shutdown (signal handler) and on last-will of the hub (don't leave IR on after Nano crash).
- Systemd unit: `pi/systemd/ir-led.service`.

**Done when:** `mosquitto_pub -h nano.local -t turret/ir/set -m '{"on":true}'` turns the LED on; publishing `{"on":false}` turns it off; `kill -9` of the service leaves the LED off.

**Milestone: the Pi is feature-complete.** Everything from here on is Nano-side.

### Step 5 — Nano `recorder` service

- GStreamer pipeline:
  ```
  rtspsrc location={RTSP_URL} protocols=tcp latency=100
    ! rtph264depay ! h264parse
    ! splitmuxsink name=mux location=/var/lib/turret/clips/clip_%05d.mp4
                   muxer=mp4mux max-size-time=0
  ```
- Armed/disarmed by `turret/recorder/arm`. While disarmed, consume and drop frames (keep the RTSP session warm). While armed, forward to `splitmuxsink`. Use a `valve` element between `tee` and `splitmuxsink` and toggle its `drop` property.
- On each clip boundary (PIR event + trailing window), call `split-now` on `splitmuxsink`.
- Publishes `turret/recorder/event` on clip boundaries.
- Systemd unit. Storage: start with local SD; path is a config knob.

**Done when:** with the Pi streaming and the recorder service running, publishing `{"armed":true,"trailing_s":10}` on `turret/recorder/arm` and then triggering the PIR produces a clip that ends ~10 s after the last motion event and plays cleanly in VLC.

### Step 6 — Nano `hub` service

- Subscribes to `turret/motion`, `turret/+/health`, `turret/recorder/event`.
- Arms the recorder while motion is active (or has been within trailing window).
- Controls IR LED based on motion + time-of-day or a simple "always on when armed" policy for v1.
- Logs state transitions to `journalctl`.
- Systemd unit.

**Done when:** with all five services running (3 Pi + 2 Nano), waving at the PIR causes (a) IR LED on, (b) recorder arm, (c) clip captured ending 10 s after motion, (d) IR LED off. No manual intervention.

### Step 7 — Nano `streamer` service

- Re-publishes the Pi's RTSP feed on the Nano's LAN interface (different port, e.g. 8555) so external viewers hit the Nano rather than the Pi. Still no re-encode.
- Two benefits: the Pi's single RTSP server isn't hammered by multiple clients, and when WebRTC ships later, the gateway already lives on the Nano.

**Done when:** VLC on a laptop pulling `rtsp://nano.local:8555/live` shows video with a latency comparable to pulling from the Pi directly.

---

## 6. Systemd unit template

`pi/systemd/camera-pipeline.service` (other units are near-identical):

```ini
[Unit]
Description=SonOfPAWD camera-pipeline
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/pi/SonOfPAWD
ExecStart=/usr/bin/python3 -m pi.src.camera_pipeline.main
Restart=on-failure
RestartSec=2
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

Install as `systemd --user` units (`systemctl --user enable --now camera-pipeline`). No root needed; survives reboot if `loginctl enable-linger pi` is set.

---

## 7. Dev workflow

Typical edit-test cycle on the Pi side:

```
# on the Nano (or laptop that has the repo)
vim pi/src/camera_pipeline/main.py
make deploy-pi        # rsync + systemctl restart
make logs-pi          # watch journalctl
```

Typical edit-test cycle on the Nano side:

```
vim nano/src/recorder/main.py
systemctl --user restart recorder
journalctl --user -fu recorder
```

Testing the MQTT contract without services running:

```
mosquitto_sub -h nano.local -t 'turret/#' -v     # see everything
mosquitto_pub -h nano.local -t turret/motion -m '{"ts":0,"sensor":"pir","active":true}'
```

Both sides will believe a real motion event happened — useful for testing `recorder` and `hub` without wiggling at the PIR.

---

## 8. What's explicitly out of scope for v1

Parking these here so they don't accidentally creep in:

- **Inference.** Placeholder directory only. Build after the 7 steps above work end-to-end.
- **Motion control.** No motors, no pan/tilt logic.
- **Website / WebRTC gateway.** Revisit once the RTSP path is rock-solid.
- **Auth on MQTT / RTSP.** LAN-only for now.
- **Recording retention / rotation.** Clips accumulate; manual cleanup is fine for dev.
- **Multi-camera.** Architecture allows it (another Pi → another RTSP URL), but not a v1 goal.
- **Thermal sensor.** Same bus, same service shape; add after PIR is working.

---

## 9. Things that will bite later (know now)

- **libcamera version churn on Pi OS.** Pin the OS image; don't `apt full-upgrade` casually after it works.
- **`splitmuxsink` + RTSP reconnect.** When the Pi reboots, `rtspsrc` EOSes, `splitmuxsink` closes cleanly — but the recorder process needs to re-play the pipeline. Build the "restart pipeline on EOS" handler into the recorder from day one; don't rely on systemd restart alone, because a healthy process with a dead pipeline won't trigger it.
- **Clock skew across hosts.** `chrony` on both. Put a `ts` field in every MQTT message; if Pi vs Nano timestamps diverge more than ~1 s, you've got an ntp problem and the 10-second trailing-window math will look wrong.
- **`rtsp-server` Python bindings on Pi OS.** Package name has moved historically; if `gi.require_version('GstRtspServer', '1.0')` fails, install `gir1.2-gst-rtsp-server-1.0`.
- **mDNS flakiness.** If `raspberrypi.local` intermittently fails to resolve, either install `avahi-daemon` on both hosts or fall back to static IPs in `common/config/hosts.py`.

---

## 10. Immediate next action

1. Build the repo skeleton (§1) and remove the empty `src/`.
2. Install `mosquitto` on the Nano and confirm the smoke test in §4.
3. Write `common/messaging/` (§5 step 1).
4. Port the Option A Pi stream to a GStreamer + `gst-rtsp-server` Python service (§5 step 2).

Everything after that falls out in order.
