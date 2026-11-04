# Core Service Implementation

Source-of-truth plan for shipping the hub. Incorporates the answers in `core_service_plan_answers.md`. Supersedes the "assumed defaults" in `core_service_plan.md` §0 — below is what we're actually building.

---

## 1. Locked decisions (from the answers)

| # | Decision |
|---|---|
| 1 | Flask UI is a **separate service** on the Nano. Talks to hub only over MQTT. |
| 2 | Hub boots into `idle`. No mode persistence. An `alert_user(msg)` shim exists as a stub for future notification logic. |
| 3 | Trailcam stills = keyframe pulled from RTSP feed on the Nano (GStreamer `appsink`). No new Pi code. |
| 4 | Target classes are a fixed enum: `cat`, `raccoon`, `rat`, `deer`. This is an anti-pest garden turret, not a general object detector. |
| 5 | **Firing** uses two Pi GPIO pins: `FIRE_ENABLE` (cuts power to the solenoid circuit via MOSFET) and `FIRE_TRIGGER` (opens the water-cannon solenoid). A hardware switch in series also cuts solenoid power and is not software-visible. |
| 6 | Pan/tilt = two servos behind a microcontroller. Wire protocol TBD. Hub publishes commands over MQTT; a `motion-ctrl` worker on whichever host owns the MCU link translates. Must also support arm/disarm. |
| 7 | Inference runtime = TensorRT. Model choice is a **research task**, not blocking hub v1; raccoon/rat are unusual classes and likely need a custom or fine-tuned model. |
| 8 | Telemetry: hub publishes every 5 s; Flask subscribes. |
| 9 | **No retention logic.** Clips accumulate; external storage will be mounted at `CLIPS_DIR`. Telemetry still publishes disk free so the UI can warn. |
| 10 | Mosquitto runs with username/password auth. All services carry credentials. |
| 11 | `idle` = motion events **logged only**, zero side effects. Placeholder for low-power mode later. |
| 12 | IR LED is ON if **any** of: (a) a clip is currently being recorded, (b) operator flipped a manual toggle in the UI, (c) the mode's motion/trigger system is currently active (i.e., motion within trailing window and mode is not idle/trailcam-snapshot-done). Logical OR. Hub owns the merge and is the sole publisher to `turret/ir/set`. |

Blocker status: **none**. Firing and pan/tilt hardware answers unblock auto-sentry and auto-aim respectively; remaining ambiguity (MCU wire protocol, specific TensorRT model) is scoped into deferred work, not this plan.

---

## 2. Changes to existing config and infrastructure

### 2.1 `common/config/hosts.py` additions

```python
# --- MQTT auth (broker enforces; all clients send these) ---
MQTT_USERNAME = "turret"
MQTT_PASSWORD = ""  # loaded from env or /etc/turret/mqtt.password; see §2.3

# --- Pi GPIO additions ---
FIRE_ENABLE_PIN = 22   # MOSFET gate; cuts solenoid circuit power when low
FIRE_TRIGGER_PIN = 23  # solenoid valve open when high

# --- Fire safety ---
FIRE_MIN_INTERVAL_S = 5.0     # minimum gap between consecutive shots
FIRE_MAX_PULSE_MS = 500       # hard cap on trigger pulse width
FIRE_DEFAULT_PULSE_MS = 200

# --- Telemetry cadence ---
TELEMETRY_PERIOD_S = 5

# --- Snapshot output ---
SNAPSHOTS_DIR = "/var/lib/turret/snapshots"
```

Pin numbers above are placeholders; wire once and update.

### 2.2 MQTT auth

- `nano/scripts/host_prep.sh` appends a `password_file` + `allow_anonymous false` stanza to `/etc/mosquitto/conf.d/turret.conf` and provisions a password via `mosquitto_passwd -b`.
- The password lives in `/etc/turret/mqtt.password` (mode 0640, owner `turret`). `host_prep.sh` generates it if missing.
- `pi/scripts/host_prep.sh` reads the same path (synced from the Nano out-of-band; not committed to git).
- `MqttClient.connect()` calls `self._client.username_pw_set(MQTT_USERNAME, _read_password())` before `connect_async`.
- **Do not commit the password file.** Add `/etc/turret/**` is irrelevant (not in repo); the relevant thing is: no hardcoded creds in `hosts.py`. The env/file read path is the single source.

### 2.3 `common/messaging/topics.py` additions

```python
TOPIC_MODE_SET      = "turret/mode/set"
TOPIC_MODE_CURRENT  = "turret/mode/current"   # retained
TOPIC_HUB_STATE     = "turret/hub/state"      # retained
TOPIC_TELEMETRY     = "turret/nano/telemetry"

TOPIC_IR_MANUAL     = "turret/ir/manual"      # UI operator override

TOPIC_SNAPSHOT_REQ  = "turret/snapshot/request"
TOPIC_SNAPSHOT_EVT  = "turret/snapshot/event"

TOPIC_INFER_ENABLE  = "turret/inference/enable"     # retained
TOPIC_INFER_DETECT  = "turret/inference/detection"

TOPIC_MOTION_CTRL_ENABLE = "turret/motion_ctrl/enable"  # retained
TOPIC_MOTION_CTRL_AIM    = "turret/motion_ctrl/aim"

TOPIC_FIRE_ENABLE   = "turret/fire/enable"    # arms the MOSFET power gate
TOPIC_FIRE_REQUEST  = "turret/fire/request"   # hub -> fire service
TOPIC_FIRE_EVENT    = "turret/fire/event"     # fire service -> all

TOPIC_ALERT_USER    = "turret/alert"          # shim channel for future alerting
```

Retained topics are the ones a late-joining UI should be able to render from without a round-trip.

---

## 3. New services

### 3.1 Hub (`nano/src/hub/`)

Files to create:

```
main.py         # GLib loop wiring, MQTT subscriptions, telemetry timer
state.py        # HubState dataclass + apply(event) reducer
modes.py        # Mode enum, mode → action table
telemetry.py    # cpu/ram/temp/disk readers
snapshot.py     # GStreamer appsink that pulls one keyframe on demand
ir_merge.py     # merges three IR triggers into a single published state
alerts.py       # alert_user(level, message) shim — logs + publishes TOPIC_ALERT_USER
```

Hub responsibilities:

1. Subscribes: `turret/mode/set`, `turret/motion`, `turret/recorder/event`, `turret/+/health`, `turret/ir/manual`, `turret/inference/detection`, `turret/fire/event`.
2. Publishes: `turret/mode/current` (retained), `turret/hub/state` (retained), `turret/nano/telemetry`, `turret/ir/set`, `turret/recorder/arm`, `turret/snapshot/request`, `turret/inference/enable` (retained), `turret/motion_ctrl/enable` (retained), `turret/motion_ctrl/aim`, `turret/fire/enable`, `turret/fire/request`, `turret/alert`.
3. On startup: publish `mode=idle` retained, `inference/enable=false` retained, `motion_ctrl/enable=false` retained, `fire/enable=false` retained. Every "dangerous" surface starts disabled.

All callbacks marshal onto the GLib main loop via `GLib.idle_add` (same pattern as the recorder).

### 3.2 Pi fire service (`pi/src/fire_control/`)

New service on the Pi. Separate from `ir_led` even though both are GPIO — keeps fail domains independent.

```
pi/src/fire_control/main.py
pi/systemd/fire-control.service
```

Behavior:

- `FIRE_ENABLE_PIN` is driven by `turret/fire/enable {on: bool}`. Boot state: low (disabled). The hub-published retained value takes over on first connect.
- `FIRE_TRIGGER_PIN` is driven by `turret/fire/request {pulse_ms: int}`. Service:
  1. Rejects if `FIRE_ENABLE_PIN` is low.
  2. Clamps `pulse_ms` to `FIRE_MAX_PULSE_MS`.
  3. Enforces `FIRE_MIN_INTERVAL_S` between pulses; logs and publishes `{event: "rejected", reason}` if violated.
  4. Drives trigger high for `pulse_ms` via `DigitalOutputDevice.blink(on_time=..., off_time=0, n=1, background=False)` or equivalent, then returns low.
  5. Publishes `turret/fire/event {event: "fired", pulse_ms, ts}`.
- On **any** shutdown path (SIGTERM, SIGINT, uncaught exception via `finally`), drive both pins low. Trigger before enable, always.
- On the hub's last-will (`turret/nano/health down`), the Pi fire service resets `FIRE_ENABLE_PIN` low. This is the software half of the hardware kill-switch.

The physical switch in series with the solenoid power is not software-visible; document it in `pi/docs/wiring.md` so no one "helpfully" removes it.

### 3.3 Flask UI (`nano/src/ui/` — separate effort, specified here so hub doesn't paint itself into a corner)

Not built in this phase, but the hub must be usable from it immediately. Flask will:

- Publish `turret/mode/set` when the operator picks a mode.
- Publish `turret/ir/manual {on: bool}` for manual IR override.
- Publish `turret/motion_ctrl/enable` and `turret/fire/enable` for the arm/disarm toggles.
- Subscribe to `turret/mode/current`, `turret/hub/state`, `turret/nano/telemetry`, `turret/recorder/event`, `turret/snapshot/event`, `turret/fire/event`, `turret/alert`.
- Share the same MQTT credentials as other services.

### 3.4 Stubs — not implemented this phase, but skeleton `__init__.py` with a docstring so the layout is visible

- `nano/src/inference/` — will publish `turret/inference/detection` when it exists. Hub is already wired to consume it. Blocker: choosing a model that can distinguish cat / raccoon / rat / deer.
- `nano/src/motion_ctrl/` (or `pi/src/motion_ctrl/`, TBD once MCU wiring is decided) — translates `turret/motion_ctrl/aim` into servo commands.

---

## 4. Mode → action matrix (final)

| Mode | Recorder arm on motion | IR (auto source) | Snapshot | Inference enabled | motion_ctrl enabled | Fire enable |
|---|---|---|---|---|---|---|
| `idle` | — | — | — | false | false | false |
| `monitor` | yes (trailing 10 s) | while armed | — | false | false | false |
| `trailcam` | — | brief pulse around snapshot | yes (1 JPEG) | false | false | false |
| `auto-aim` | yes | while armed | — | true | true | false |
| `auto-sentry` | yes | while armed | — | true | true | **true** |

On mode transition, hub **explicitly republishes** every retained "enable" topic — even if the value hasn't changed — to make mode changes easier to audit in logs.

IR "auto source" above is just one of the three IR inputs; see §5.

---

## 5. IR merge logic (`ir_merge.py`)

Three independent input booleans maintained inside `HubState`:

- `ir_recording` — true while `recorder_armed` is true (tracked via `turret/recorder/event`).
- `ir_manual` — last value received on `turret/ir/manual`. Defaults false.
- `ir_trigger` — true while mode is not idle/trailcam AND motion is active-or-within-trailing-window.

Merge: `ir_on = ir_recording or ir_manual or ir_trigger`. Hub publishes `turret/ir/set {on: ir_on}` **only on change**, not on every tick, to keep the log readable.

On mode change to `idle`, `ir_trigger` drops to false immediately regardless of motion state. `ir_manual` is preserved — that's an operator decision and the mode change shouldn't clobber it.

---

## 6. Telemetry (`telemetry.py`)

No new dependencies. Every `TELEMETRY_PERIOD_S`:

- **Temperature:** `/sys/class/thermal/thermal_zone0/temp` / 1000.0 (°C).
- **CPU %:** delta of `/proc/stat` line 1 (`user+nice+system+irq+softirq+steal`) / delta of total since last tick. First tick returns 0.0.
- **RAM %:** `1 - MemAvailable/MemTotal` from `/proc/meminfo`.
- **Disk:** `shutil.disk_usage(CLIPS_DIR)` → `(free_bytes, total_bytes)`.

Publish shape:
```json
{"ts": ..., "temp_c": 46.2, "cpu_pct": 18.7, "ram_pct": 43.1,
 "disk_free_bytes": 104857600000, "disk_total_bytes": 500000000000}
```

Read paths are pinned in constants at the top of `telemetry.py` so swapping for a different board (e.g., moving to Jetson Orin) is a single-file change.

---

## 7. Snapshot worker (`snapshot.py`)

Runs inside the hub process. One GStreamer pipeline built lazily on first request and reused:

```
rtspsrc location={RTSP_URL} protocols=tcp latency=100
  ! rtph264depay ! h264parse ! avdec_h264
  ! videoconvert ! jpegenc quality=85
  ! appsink name=sink max-buffers=1 drop=true emit-signals=false sync=false
```

On `turret/snapshot/request`:
1. If pipeline is NULL, set PLAYING and wait ≤ 2 s for the first buffer.
2. Pull one sample from `appsink` with `try_pull_sample(timeout=1s)`.
3. Write buffer bytes to `SNAPSHOTS_DIR/snap_<ts>.jpg`.
4. Publish `turret/snapshot/event {path}`.
5. Keep pipeline alive for `SNAPSHOT_IDLE_HOLD_S` (say 60 s) then tear down to release camera bandwidth and CPU.

Fallback on decode failure: log, publish `snapshot/event {error: "..."}`, leave pipeline alive for one more try.

---

## 8. `alert_user` shim (`alerts.py`)

```python
def alert_user(level: str, message: str, *, mqtt: MqttClient) -> None:
    log.warning("ALERT [%s] %s", level, message)
    mqtt.publish(TOPIC_ALERT_USER, {"level": level, "message": message})
```

That's the whole thing for now. Future: email, Pushover, whatever. Hub uses it for:
- boot state ("starting in idle mode")
- entering auto-sentry ("fire enabled")
- fire rejections (rate limit, disabled, etc.) — `level="warn"`
- repeated recorder reconnects (eventually; not v1)

---

## 9. Build order and milestones

Each step is independently verifiable. Do not skip verification.

### Step A — MQTT auth rollout

1. Edit `nano/scripts/host_prep.sh`: add mosquitto auth config + password file generation.
2. Edit `MqttClient.connect` to call `username_pw_set`.
3. Add `_read_password()` helper that reads `/etc/turret/mqtt.password` with a clear error if missing.
4. Re-run `host_prep.sh` on the Nano; sync `/etc/turret/mqtt.password` to the Pi (out-of-band, `scp` once).
5. Restart existing services.

**Done when:** `mosquitto_sub -h <nano> -t 'turret/#' -v` without creds fails with "not authorised"; all existing services show `up` on their health topics after the restart.

### Step B — Hub skeleton in `idle`

1. Create the six files in `nano/src/hub/`.
2. `state.py` + `modes.py`: pure-Python reducer + table, no I/O. Unit tests (pytest, no hardware) that push motion/mode events through `apply()` and snapshot the outgoing MQTT calls.
3. `main.py`: wires MQTT, the GLib loop, `telemetry.py` timer. Publishes retained `mode/current=idle`, `inference/enable=false`, `motion_ctrl/enable=false`, `fire/enable=false` on start.
4. `nano/systemd/hub.service`. Add `hub` to `NANO_SERVICES` in `deploy/Makefile`.

**Done when:** `make -C deploy install-nano-units restart-nano` leaves hub `active`; `mosquitto_sub` with creds shows retained `mode/current=idle` and telemetry every 5 s.

### Step C — Monitor mode end-to-end

1. Implement the `motion` → `recorder/arm=true` reaction for `monitor` mode.
2. Implement `ir_merge.py`; wire `recorder/event` and `ir/manual` into state.
3. Publish `turret/mode/set {"mode":"monitor"}` from the Nano command line (Flask isn't up yet).

**Done when:** waving at the PIR produces: `motion=true` → `arm=true` → clip starts → `ir/set on=true` → 10 s trailing → clip closes → `ir/set on=false`. All existing services. No hub-side code for inference/fire/motion_ctrl yet.

### Step D — Trailcam snapshot

1. Implement `snapshot.py`.
2. Add `trailcam` branch in the mode → action table: on motion edge, publish `turret/snapshot/request`. Hub subscribes to its own request to drive the worker (same process — it's one pipeline, no extra broker hop).
3. Wire `ir_trigger=true` for a short pulse (`SNAPSHOT_IR_PULSE_S = 3`) around each snapshot.

**Done when:** `turret/mode/set {"mode":"trailcam"}` + PIR wave leaves a `snap_*.jpg` in `SNAPSHOTS_DIR` that opens in any image viewer; `turret/snapshot/event` fires with the path; IR pulses briefly.

### Step E — Pi fire service + auto-sentry arming (no detection yet)

1. Build `pi/src/fire_control/main.py` and systemd unit.
2. Add to `PI_SERVICES` in `deploy/Makefile`.
3. Hub's auto-sentry mode publishes `fire/enable=true` on entry, `false` on exit.
4. Smoke test: with mode `auto-sentry`, manually publish `turret/fire/request {pulse_ms: 100}` from the Nano. Solenoid fires once. Try again within 1 s: service publishes `fire/event rejected: rate_limited`.
5. With mode anything-else, publish `fire/request`: service rejects because `fire/enable` is false.

**Done when:** the solenoid fires only when the mode is `auto-sentry` AND rate limit is satisfied AND `pulse_ms` clamped. `kill -9` of either hub or fire service leaves both fire pins low.

### Step F — Inference contract (stub only)

1. Add `nano/src/inference/README.md` documenting the contract: subscribes `turret/inference/enable`, publishes `turret/inference/detection {class, score, bbox}`.
2. Hub's `auto-aim`/`auto-sentry` handlers log detections and, for `auto-sentry`, run the fire-decision block (centered bbox + confidence + class in target set + interlocks). Until inference exists, these handlers fire on **synthetic** detections published by hand from `mosquitto_pub` — proves the interlock math works.

**Done when:** with `auto-sentry` active and a synthetic detection fed in that meets all criteria, the hub publishes `turret/fire/request`. With any criterion missing (wrong class, low confidence, off-center), it does not. Log lines explicitly state which criterion failed.

### Step G — motion_ctrl contract (stub only)

1. Add `nano/src/motion_ctrl/README.md` with the wire protocol TBD but the MQTT contract frozen: subscribes `turret/motion_ctrl/enable`, `turret/motion_ctrl/aim {pan_deg, tilt_deg}`.
2. Hub publishes `aim` from the centroid of the current tracked detection when `auto-aim`/`auto-sentry` is active.

**Done when:** a synthetic detection causes a correctly-shaped `aim` message to appear on MQTT. No servos move — there's no worker yet.

### Step H — Flask UI

Separate document. Hub stops here and is considered shipped.

### Research thread (parallel, not blocking)

- Survey TensorRT-runnable models that classify the four target species. Candidates to evaluate: MegaDetector v5 (localizer) + a species classifier; a custom-trained YOLOv8n fine-tuned on iNaturalist data; commercial wildlife cams' open datasets. Needs a writeup in `inference_model_plan.md` before any code lands in `nano/src/inference/`.

---

## 10. Deferred / out of scope for this phase

- Inference implementation (Step F is contract only).
- motion_ctrl worker (Step G is contract only).
- Flask UI (Step H).
- Clip retention — not happening; external storage is the plan.
- Low-power idle (answer 11 mentions "minimal energy"; revisit once Nano-side power measurement exists).
- Actual email/push notification path for `alert_user`.

---

## 11. Risks to flag

- **Credentials syncing.** Every host prep step now depends on the password file being present. If the Pi's copy drifts, services silently fail to connect. Mitigation: have `host_prep.sh` print the password's sha256 and compare across hosts. Rotation story: "stop all services, regenerate, restart" — brief outage acceptable.
- **Water and electronics.** Solenoid firing near a Pi running camera+GPIO. Make sure the valve is plumbed so a leak drains away from the electronics. Documenting, not coding, but it's a real failure mode.
- **Trailcam snapshot cost on Nano.** `avdec_h264` is CPU. If this bites during heavy PIR activity, swap to `nvv4l2decoder` — single-line pipeline change behind the same `appsink`.
- **Model availability for raccoon/rat.** Most public wildlife models are built for North American large mammals. Rats are *rarely* an output class. If no off-the-shelf model works, we're into dataset/labelling territory, which is weeks, not days. Be ready for that.
- **Fire-rate + pan/tilt coupling.** Auto-sentry in a real garden scenario involves moving the cannon to center the target before firing. The current plan has fire and aim decoupled on MQTT. That's fine for v1 (single target, slow aim), but the hub will eventually need a "locked on for N frames" gate before firing. Noted; not coded.

---

## 12. What I need from you to start

Nothing blocking on Step A. I will begin with the MQTT auth rollout and stop for review after Step B (hub in idle, telemetry flowing) before moving to monitor mode.

mosquitto_sub -h localhost -u turret -P "$(cat /etc/turret/mqtt.password)" -t 'turret/+/health' -v 
mosquitto_sub -h localhost -u turret -P "$(cat /etc/turret/mqtt.password)"  -t 'turret/+/health' -v 


mosquitto_pub -h localhost -u turret -P "$(cat /etc/turret/mqtt.password)"  -t turret/mode/set -m '{"mode":"monitor"}'