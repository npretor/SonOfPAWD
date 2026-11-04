# Remaining Pi services → Jetson Nano migration plan

Follows `camera_pipeline_nano_migration_plan.md` (camera-pipeline cut over and
committed in 4d7334e). The goal is to retire the Pi entirely.

## 0. Status — software done 2026-11-04 (phases A–D)

- **Done:**
  - sensor-wakeup, ir-led and motion-ctrl ported to `nano/src/` (history
    kept via `git mv`), and fire-control built in `nano/src/fire_control/`.
  - Jetson.GPIO wrapper in `nano/src/hw/`.
  - User units installed, enabled and running on the Nano.
  - Pi units stopped and **disabled** (including camera-pipeline).
  - `pi/`, `PI_HOST`, the Pi Makefile targets and the UI's SSH log path removed.
  - 14 unit tests (`make -C deploy test`).
- **Hardware as built:** servo PSU, fire trigger/enable and IR LED outputs
  all drive **optoisolators with pull-downs and indicator LEDs**, so §3's
  pull-down requirement is met for the outputs.
- **New finding: sysfs pins hold their value when the owning process dies.**
  BCM24 stayed HIGH after `kill -9`, so the pull-downs alone don't cover a
  crash. Every actuator unit now has
  `ExecStopPost=python3 -m nano.src.hw.force_low <pins>`. Verified: after
  `kill -9` of fire-control with enable and trigger HIGH, both pins were
  forced low and released, and the service restarted.
- **Live-verified over MQTT** (nothing powered):
  - IR on/off.
  - fire-control:
    - fail-closed until the hub heartbeat;
    - 300 ms pulse held 301 ms;
    - auto-sentry rejected without `fire/enable`;
    - disarm mid-pulse cut the trigger at 168 ms, trigger before enable.
  - motion-ctrl kept the PSU low with enable=true and no Arduino.
  - No spurious PIR events with the input unconnected.
- **Remaining (needs you):**
  1. `nano/scripts/host_prep.sh` (sudo): adds `dialout`, installs the udev
     rule, creates `/var/lib/turret/motion_ctrl`. Then reboot for the group change.
  2. Power down and move the wires to the same header pins:
     - PIR → pin 11
     - IR → pin 13
     - fire enable → pin 15
     - fire trigger → pin 16
     - servo PSU → pin 18
     - plus 5 V and GND
  3. Plug the Arduino into the Nano. Check `/dev/turret-arduino` exists.
  4. Start the hub. fire-control stays disabled without its heartbeat.
  5. Bench-test fire with the pump/solenoid supply disconnected, then re-check
     aim calibration with the IMX219's field of view.
  6. Optional: add a pull-down on the PIR input (BCM17); the Nano can't set one in software.

## 1. Inventory (checked 2026-11-04)

| Service | Running on Pi? | Hardware | Notes |
|---|---|---|---|
| `sensor-wakeup` | active | PIR on BCM 17 (header pin 11) | gpiozero `MotionSensor` → `turret/motion` |
| `ir-led` | active | IR illuminator on BCM 27 (pin 13) | `turret/ir/set` → GPIO |
| `motion-ctrl` | active, but **no Arduino attached** (no `/dev/ttyACM*` / `/dev/turret-arduino` on the Pi) | Arduino over USB serial; servo PSU MOSFET on BCM 24 (pin 18) | 50 Hz tick; calibration file at `/var/lib/turret/motion_ctrl_calibration.json`. That directory doesn't exist on the Pi, so the calibration was never persisted; the retained MQTT topic is the only copy |
| `fire-control` | **never built** | Spec'd at BCM 22/23 (pins 15/16) in `core_service_implimentation.md` §3.2 | Listed in `ui/backend/logs.py` as a Pi service, but no code exists. The hub already publishes `turret/fire/*` |
| `camera-pipeline` | inactive (migrated) | — | Pi-side files still in the repo; delete in §6 |

The Pi runs Python 3.13 and the Nano runs 3.6.9.

## 2. Nano readiness (verified)

| Check | Result |
|---|---|
| GPIO library | `Jetson.GPIO` 2.0.17 installed (`JETSON_NANO` detected) |
| GPIO permissions | `dlinano` is in the `gpio` group, so no root is needed |
| BCM numbering | `GPIO.setmode(GPIO.BCM)` accepts BCM 17, 27, 24, 22 and 23. The Nano's 40-pin header follows the Pi layout, so **each wire moves to the same physical pin number** |
| Edge detection | `add_event_detect` works on BCM 17 |
| pyserial | 3.5 installed |
| `dataclasses` (used by `calibration.py`) | Not in the Python 3.6 standard library, but the pip backport 0.8 is installed. All Pi service code compiles and imports under 3.6 |
| Serial permissions | **Missing**: `dlinano` is not in `dialout`, and the udev rule isn't installed |
| `/var/lib/turret` | **root:root 755**, so motion-ctrl can't save calibration as `dlinano` |
| Power | MAXN. The Arduino will draw from the Nano's USB, so the Nano needs the barrel-jack supply, not micro-USB |

## 3. Hardware safety: the one real difference from the Pi

**Pi GPIOs have internal pull-downs that are active at boot and whenever a
pin is released.** The design leans on that: "servo PSU is low at boot",
"fire pins low on any shutdown path". **The Nano gives no such guarantee**:
- Jetson.GPIO can't set internal pull-up/down resistors.
- The SoC pins' power-on state isn't documented to be a pull-down.
- `GPIO.cleanup()` and process exit return pins to floating inputs.

A floating MOSFET gate can switch on partially or fully.

Required before moving any actuator wire:
1. Add an **external pull-down (10k–100k) from gate to GND** on:
   - the servo PSU MOSFET (BCM 24)
   - the IR LED driver (BCM 27)
   - both future fire pins (BCM 22/23)
2. Add a pull-down on the PIR input (BCM 17), so a disconnected wire reads
   "no motion" instead of floating into false triggers.
3. **Measure with a meter or scope through a full Nano power cycle and a
   service crash**, confirming every actuator pin stays low. This includes
   the ~30 s of boot before any service runs.
4. Confirm the header drive is adequate. Nano header pins are 3.3 V with low
   drive current, so each must feed a transistor or MOSFET gate, never an
   LED or load directly. Check the IR LED wiring specifically.

The physical kill switch in series with the solenoid/pump supply stays, as
in core plan §3.2.

## 4. Code changes

### 4.1 GPIO shim: `nano/src/hw/gpio.py` (new, ~60 lines)
A thin Jetson.GPIO wrapper exposing the small slice of the gpiozero API these
services use, so the service code ports nearly verbatim:
- `DigitalOutput(bcm, initial=False)` with `.on()`, `.off()`, `.value` and
  `.close()`. `.close()` drives **low before** releasing the pin.
- `DigitalInput(bcm)` with `.value` (level read).
- `setmode(BCM)` once, process-wide.

The motion-ctrl `Worker` already takes `psu` duck-typed (`.on()`/`.off()`),
so it needs no changes.

### 4.2 Move the services
Use `git mv pi/src/{sensor_wakeup,ir_led,motion_ctrl} nano/src/`, then:
- Swap the `gpiozero` imports for `nano.src.hw.gpio`, and change
  `pi.src.motion_ctrl.*` imports to `nano.src.motion_ctrl.*`.
- **sensor-wakeup: fix an existing bug while porting.** Today
  `last_active_at` refreshes only on rising edges. A PIR that holds its
  output high during continuous motion (HC-SR501 in retrigger mode) is
  reported `active=false` after 2 s while still high, and never re-reported.
  Switch to level polling: in the existing 10 Hz loop, a high level refreshes
  `last_active_at`. That's simpler, needs no edge callback thread, and also
  matches non-retriggering sensors.
- Health topics move from `turret/pi/health` to `turret/nano/health`
  automatically (via the hostname-based host tag). The hub keys peers
  dynamically, and the UI has no hard-coded keys.
- `nano/requirements.txt`: add `Jetson.GPIO`, `pyserial` and
  `dataclasses; python_version < "3.7"`.

### 4.3 Units and host setup
- `nano/systemd/{sensor-wakeup,ir-led,motion-ctrl}.service` as **user units**,
  copied from the Pi units with `WorkingDirectory=%h/github/SonOfPAWD` and
  `After=mosquitto.service`. GPIO and serial access come from group
  membership, so unlike camera-pipeline they don't need system units.
- One-time host prep on the Nano (add to `nano/scripts/host_prep.sh`):
  ```
  sudo usermod -aG dialout dlinano        # then log out/in (or reboot)
  sudo install -m 644 pi/scripts/udev/99-turret-arduino.rules /etc/udev/rules.d/   # moved to nano/scripts/udev/
  sudo udevadm control --reload && sudo udevadm trigger
  sudo install -d -o dlinano -g dlinano /var/lib/turret/motion_ctrl
  ```
  Point `CALIBRATION_PATH` at `/var/lib/turret/motion_ctrl/calibration.json`.
  That's a dedicated directory, so `/var/lib/turret` itself (clips, models)
  keeps its current ownership.
- `deploy/Makefile`: add the three services to `NANO_SERVICES`. Once
  `PI_SERVICES` is empty, the Pi targets go (§6).
- `nano/src/ui/backend/logs.py`: `sensor-wakeup`, `ir-led` and `motion-ctrl`
  become `"user"`, and `fire-control` becomes `"user"` (it'll be built on the
  Nano).

### 4.4 Config (`common/config/hosts.py`)
- Keep the pin numbers: the BCM numbering means identical values and the same
  physical pins. Update the comments ("Pi GPIO" → "40-pin header GPIO,
  BCM numbering via Jetson.GPIO").
- Add `FIRE_ENABLE_PIN = 22`, `FIRE_TRIGGER_PIN = 23`, `FIRE_MAX_PULSE_MS`
  and `FIRE_MIN_INTERVAL_S` for §5 phase C.

## 5. Phased cutover

Each phase: disable the Pi unit, **power both boards down**, move the wires
(same pin numbers, plus a shared GND), boot, start the Nano unit, verify.
Never have the same wire on both headers at once.

**Phase A: sensor-wakeup + ir-led** (low risk; no moving parts)
1. Build §4.1, the §4.2 changes for these two, and their units.
2. `ssh nathan@testcam-64bit.local "systemctl --user disable --now sensor-wakeup ir-led"`.
3. Fit the pull-downs (§3), move the PIR (pin 11) and IR driver (pin 13).
   The PIR's 5 V and GND go to the same header pins (2/4, 6).
4. `make -C deploy install-nano-units`, then
   `systemctl --user enable --now sensor-wakeup ir-led`.
5. Verify:
   - Walking past the PIR gives `turret/motion active=true`, then `false`
     ~2 s after motion stops, and it stays `true` during continuous motion.
   - Monitor mode arms the recorder.
   - `turret/ir/set` toggles the LED.
   - Stopping the service turns the LED off.
   - The pin reads low through a reboot.

**Phase B: motion-ctrl**
1. Build the motion-ctrl part of §4.2, plus the host prep in §4.3.
2. Disable the Pi unit. Fit the PSU gate pull-down, move the PSU enable wire
   (pin 18), and plug the Arduino into the Nano's USB.
3. Confirm `/dev/turret-arduino` exists. If `lsusb` shows a vendor or product
   ID missing from the rule, add a line for it.
4. Start the service, then run the safety checks before enabling aim:
   - PSU pin low after boot, after `systemctl --user stop`, and after a
     `kill -9` (this is where the pull-down matters).
   - Enable → centre, then PSU on.
   - Disable → PSU off immediately.
5. **Re-calibrate.** Servo µs limits carry over through the retained
   `turret/motion_ctrl/calibration` topic, but the camera field of view
   changed with the IMX219, so the hub's aim/lock-on mapping needs checking.
   This was already flagged in the camera plan.

**Phase C: fire-control** (new, built directly on the Nano)
- Build `nano/src/fire_control/` to the spec in core plan §3.2, using the GPIO
  shim.
- It runs on the same host as the hub now, so the "hub last-will → drop
  enable" rule still applies (hub crash), but network partition is no longer
  a failure mode.
- Bench-test with the solenoid/pump supply disconnected first.

**Phase D: retire the Pi** (§6).

## 6. Pi retirement cleanup
Once phases A and B have been stable for a few days:
- Delete `pi/` (camera_pipeline, systemd, scripts, requirements, host_prep).
  Keep the udev rule under `nano/scripts/udev/`.
- `common/config/hosts.py`: remove `PI_HOST`.
- `deploy/Makefile`: remove the `PI_*` variables and the `*-pi` targets.
- `nano/src/ui/backend/logs.py`: remove the `"pi"` kind, the SSH branch and
  `PI_SSH_USER`.
- `nano/scripts/host_prep.sh`: drop the "scp the MQTT password to the Pi" step.
- Consider binding mosquitto to localhost only. With no off-box clients
  left, only the UI's browser traffic (via ui-backend) needs to come in from
  the LAN. That shrinks the attack surface.
- Update memory/docs: the Pi camera reboot note is obsolete.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Actuator energised by a floating pin at boot or after a crash | External pull-downs plus measured verification (§3). Phases B and C are gated on it |
| Nano USB/5 V budget with the Arduino attached under MAXN | Barrel-jack 5 V/4 A supply. Watch for undervoltage in `dmesg`, and if needed power the Arduino from the servo PSU side |
| Jetson.GPIO uses sysfs on L4T 32.x, so it's slower than the Pi | All three services are ≤ 50 Hz and use simple writes or 10 Hz polling, so it's not an issue |
| More load on the Nano (inference + camera + 3 small services) | These are tiny Python loops (~1–2% CPU each). The hub telemetry already tracks Nano load and temperature |
| Single point of failure: everything is on one board | Accepted trade-off. On balance it's simpler than the cross-host failure modes it removes |

## 8. Effort
- Phase A: about 2–3 h (shim, two ports, units, rewiring).
- Phase B: about 2 h, plus calibration time.
- Phase C: about half a day, plus bench testing.
- Phase D: about 1 h.
