# Motion Control Plan

Concrete plan in response to `motor_control_brainstorm.md`. Locks the host
choice, the GPIO pin, the MQTT contract, and the worker shape so we can move
straight to implementation when it's time.

---

## 1. Locked decisions

| # | Decision |
|---|---|
| 1 | The motion-ctrl worker lives on the **Pi**. Same host as the Arduino USB link, the cannon GPIO, the IR LED, and the PIR. All actuators on one box. |
| 2 | The trigger solenoid pin is **not** new — it is the already-allocated `FIRE_TRIGGER_PIN = 23` from `common/config/hosts.py`. Active high. The brainstorm conflated it with motion; the cannon plumbing is owned by the existing fire-control service, not motion-ctrl. |
| 3 | Servo PSU enable: **BCM 24**, active high. Adjacent to the existing fire pins (22, 23) so the wiring stays in one corner of the header. Boot-default low, no special-function alt mapping, not part of any onboard bus. |
| 4 | MQTT-only handoff between hub and motion-ctrl. Aim coordinates are normalized `[-1.0, 1.0]` (already what the UI joystick publishes). Worker maps to servo microseconds via per-axis calibration. |
| 5 | Manual UI control and auto-aim publish to the **same topic** (`turret/motion_ctrl/aim`). The worker doesn't care which produced it; the hub owns "is this allowed right now" via `motion_ctrl/enable`. |
| 6 | Servo PSU follows `motion_ctrl/enable` exactly. Disabled = power cut, servos limp. No buzz, no holding current, no idle-mode draw. |

---

## 2. Why the Pi, not the Nano

| Factor | Pi | Nano |
|---|---|---|
| USB to Arduino | yes | yes |
| GPIO for PSU enable | already has fire-control GPIO module loaded | would add a new hardware surface |
| Cohabitation with other actuators | fire-control + IR LED already here | only camera/recorder/inference here |
| Latency from auto-aim detection → servo | ~1-3 ms MQTT hop over wired Ethernet | in-process, ~0 ms |
| Fault isolation | hub restart leaves the Arduino link up | hub restart would yank serial |
| Operator surface (UI) | already publishes via MQTT | already publishes via MQTT |

The latency win on the Nano is not real — auto-aim is gated on detection
inference (tens of milliseconds best case), so a single-digit ms MQTT hop is
noise. The "all actuators on the same lifecycle" win on the Pi is real:
fire-control and motion-ctrl can share GPIO setup, share the safety story,
and stay up during Nano restarts.

---

## 3. GPIO additions

### `common/config/hosts.py`

```python
# Servo PSU enable. Active high; gates the dedicated servo supply via MOSFET.
# Disabled at boot (pin low) so servos are never energized into an unknown
# position.
SERVO_POWER_PIN = 24

# Arduino serial. USB device path is stable via udev rule (see §6.1); fall
# back to /dev/ttyACM0 if the rule isn't installed yet.
SERVO_SERIAL_DEV = "/dev/turret-arduino"
SERVO_SERIAL_BAUD = 115200
```

The MOSFET is wired so a low signal cuts the gate and the servos lose
power. This must be the case at boot — never let the supply latch on with
the Pi still coming up.

---

## 4. MQTT contract (extends `core_service_implimentation.md` §2.3)

| Topic | Direction | Retained | Payload | Notes |
|---|---|---|---|---|
| `turret/motion_ctrl/enable` | hub → worker | yes | `{ "enabled": bool }` | Single source of truth for whether the servo PSU is energized and aim commands are honored. |
| `turret/motion_ctrl/aim` | UI / hub → worker | no | `{ "pan": -1..1, "tilt": -1..1 }` | Already exists. UI rate-limits to ~20 Hz. Worker further throttles to its serial cadence. |
| `turret/motion_ctrl/state` | worker → all | no, periodic | `{ "pan": -1..1, "tilt": -1..1, "enabled": bool, "ts": float }` | Already exists (hub stub-echoes today). Worker takes over publishing at ≤10 Hz. UI sidebar's `TurretAngle` reads it. |
| `turret/motion_ctrl/calibration` | worker → all | yes | see §6.3 | Effective calibration in use. Republished on boot and on edit. Editing path is deferred to phase 3. |
| `turret/+/health` | worker → all | no | `{ "service": "motion-ctrl", "status": "up\|degraded\|down", "detail": str }` | Existing health channel. `degraded` when serial disconnected. |

Coordinate convention: `+pan = right`, `+tilt = up`, both from the
camera's point of view. UI already uses this. Worker applies a per-axis
`invert` flag from calibration before mapping to microseconds.

---

## 5. Hub responsibilities

The hub is unchanged in shape but gains new publish triggers:

1. **Mode matrix** (already in core plan §5): publish `motion_ctrl/enable`
   on every mode transition. Auto-aim / auto-sentry / monitor-with-aim
   modes set it true. Idle / trailcam-snapshot set it false.
2. **Manual UI control**: a new `POST /api/motion-ctrl/enable` route
   (mirrors `/api/arm`). The hub treats UI-driven enable identically to
   mode-driven enable; both publish the same retained topic.
3. **Auto-aim aim publishes**: when in auto-aim/auto-sentry, derive
   normalized aim from the detection centroid relative to image center and
   publish to `turret/motion_ctrl/aim` at the inference cadence (~5-10 Hz).
   No smoothing in the hub — that's the worker's job (§6.4).
4. **Stop echoing aim → state**: the current stub echo in `Hub._handle_aim`
   is removed once the real worker is publishing `motion_ctrl/state`.

---

## 6. Worker design (`pi/src/motion_ctrl/`)

A small standalone service. Same shape as `pi/src/fire_control/` so the
two share patterns: GPIO init, MQTT subscriptions, GLib main loop,
health publish, signal-driven shutdown.

### 6.1 Files

```
main.py            # MQTT wiring + GLib loop
servo_link.py      # pyserial wrapper; <pan_us, tilt_us> framing
calibration.py     # load/save JSON; normalized [-1,1] -> microseconds
worker.py          # state machine: enabled/disabled, latest aim, slew limiter
udev/99-turret-arduino.rules   # /dev/turret-arduino symlink
```

A udev rule maps the Arduino's vendor/product to a stable `/dev/turret-arduino`
symlink so the worker doesn't race against `/dev/ttyACM*` numbering.

### 6.2 State machine

```
DOWN ── enable=true ──► CENTERING ── center confirmed ──► READY ◄──► aim
  ▲                                                          │
  └──── enable=false  ◄─────────────────────────────────────┘
                         (cut PSU GPIO immediately)
```

- **DOWN**: PSU GPIO low. Serial open if possible. Aim messages dropped (logged once per N).
- **CENTERING**: PSU still off. Worker writes `<1500, 1500>` to serial so the controller's commanded position matches the parked physical position before power-up. Then PSU GPIO high. 200 ms settle delay.
- **READY**: aim commands accepted, throttled, slewed, written to serial.

Energize-with-known-position avoids a startup snap if the controller had
been left at a non-center commanded value across reboots.

### 6.3 Calibration file

`/var/lib/turret/motion_ctrl_calibration.json`:

```json
{
  "pan":  { "min_us": 1000, "max_us": 2000, "center_us": 1500, "invert": false },
  "tilt": { "min_us": 1000, "max_us": 2000, "center_us": 1500, "invert": false },
  "max_slew_us_per_tick": 40,
  "tick_hz": 50
}
```

Mapping: `pulse_us = clamp(center_us + axis * span, min_us, max_us)`
where `span = (max_us - min_us) / 2` and `axis` is signed-inverted per
flag. Defaults are conservative SG90 territory; real values are
discovered with a calibration utility (phase 3).

### 6.4 Throttle and slew

- **Inbound coalescing**: keep only the latest aim seen since the last tick.
- **Tick rate**: `tick_hz = 50` (20 ms). Each tick computes desired
  microseconds from latest aim, applies slew cap, writes one frame to
  serial. This protects the Arduino's serial buffer regardless of how
  bursty the publishers are.
- **Slew cap**: `max_slew_us_per_tick = 40`. Caps angular velocity so a
  full-range jump takes ~25 ticks (~500 ms). Prevents servo strain and
  power dip during snap commands.
- **Watchdog**: if no aim received in `aim_idle_s = 2.0` while enabled,
  hold the last commanded position. **Do not auto-center** — silent
  re-centering during a real engagement is worse than drift.

### 6.5 Failsafes

| Event | Action |
|---|---|
| `SIGTERM` / `SIGINT` | PSU GPIO low, send `<1500,1500>`, close serial, exit. |
| Serial write fails | Mark health `degraded`, attempt reopen every 1 s, drop aim commands while down. |
| `enable=false` arrives while READY | Cut PSU GPIO immediately. Do not bother re-centering — power's gone, servos relax anyway. |
| Aim payload out of `[-1, 1]` | Clamp, log once per N. (Defense in depth — UI and hub already clamp.) |
| Aim arrives at >100 Hz | Coalesce silently; tick rate is the throttle. |

---

## 7. UI implications

Mostly already in place:

- The control pad (`ControlPad.tsx`) and `useThrottledAim` hook already
  publish normalized `pan/tilt`. No frontend change needed for the
  coordinate side.
- `TurretAngle.tsx` already reads `motion_ctrl/state` from the store. It
  goes from "showing the hub's stub echo" to "showing the actual servo
  feedback" with no code change.
- Add an **Aim On** toggle next to the existing **Arm** toggle on the
  dashboard header. Same pattern as `ArmToggle.tsx`. Posts to the new
  `/api/motion-ctrl/enable`. Disabled (greyed) while in auto-aim modes
  because the hub owns enable in those modes.
- Optional later: a calibration panel under Settings exposing the JSON
  file via a new GET/PUT pair on `/api/motion-ctrl/calibration`.

---

## 8. Implementation phases

**Phase 1 — manual control, no auto.**
Pi worker (§6) reads `motion_ctrl/aim`, drives serial, gates PSU on
`motion_ctrl/enable`. UI gets the **Aim On** toggle. Done when: with
**Aim On** lit, dragging the joystick moves the physical servos and
`TurretAngle` matches what the worker publishes.

**Phase 2 — hub auto-aim publishes.**
Auto-aim/auto-sentry handlers translate detection centroid → normalized
aim and publish. Tested with synthetic detections via `mosquitto_pub`
(same pattern as Step F of the core plan). Done when: a synthetic
detection causes a correctly-shaped aim message that visibly moves the
servos.

**Phase 3 — calibration.**
Calibration JSON loadable + editable. New `/api/motion-ctrl/calibration`
GET/PUT, retained MQTT publish, Settings panel UI. Done when: changing
`pan.center_us` in the UI immediately recenters the servo.

**Phase 4 — polish.**
Slew tuning per servo, "locked-on for N frames" gate before fire (the
risk flagged in core plan §11), per-mode max-slew tweaks (slow for
sentry tracking, fast for manual).

---

## 9. Open questions for the user

1. **Confirm pin 24** for servo PSU enable, or pick a different free BCM pin if the wiring layout suggests otherwise.
2. **Confirm Arduino baud** (115200 assumed). Whatever the firmware was flashed with.
3. **Manual control gating**: should picking up the joystick auto-enable PSU, or must the user click **Aim On** first? (Recommendation: explicit toggle. Symmetric with the fire **Arm**, and it makes "I just want to look at the video without buzzing servos" a one-click state.)
4. **Modes that auto-enable PSU**: confirm the matrix is auto-aim and auto-sentry only, with monitor and trailcam keeping servos cold. (Per core plan §5 — restating to make sure it still holds now that there's a worker behind it.)

---

## 10. Out of scope (for now)

- Closed-loop position feedback. The Arduino reports nothing back; `motion_ctrl/state` is what the worker *commanded*, not measured. Fine for v1.
- Smooth path planning / coordinated pan+tilt easing. Slew limiter is per-axis only.
- Multiple aim sources arbitration beyond "last writer wins". If both UI and hub publish aim simultaneously, results are interleaved. Acceptable because the modes that publish auto-aim should also disable the manual control pad in the UI.
- Hot-replug of the Arduino. Worker reconnects on serial error but does not detect physical replug events explicitly — udev handles the symlink, the next write attempt either succeeds or marks degraded.


## Responses to questions
1. Use BCM pin 27
2. Yes use 115200 
3. Go with recommendation for an explicit toggle. I do want to be able to see video without servo noise
4. Yes, the stated behavior is correct. 


### Install 
To finish on the Pi
make -C deploy deploy-pi
ssh nathan@testcam-64bit.local
sudo apt install python3-serial
lsusb            # find Arduino vendor/product, edit rules file if needed
sudo install -m 644 ~/SonOfPAWD/pi/scripts/udev/99-turret-arduino.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
sudo usermod -aG dialout,gpio $USER  # logout/login after
exit
make -C deploy install-pi-units
ssh nathan@testcam-64bit.local 'systemctl --user enable --now motion-ctrl'

Then click AIM ON in the UI; the joystick should drive the servos. Phase 2 (auto-aim publishes from hub) and Phase 3 (calibration UI)
deferred per the plan.
