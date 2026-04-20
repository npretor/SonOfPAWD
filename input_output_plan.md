# I/O Bring-Up Plan — PIR (input) + IR LED (output) on the Pi

Scope: take the two already-written services (`pi/src/sensor_wakeup`, `pi/src/ir_led`) from "code compiles" to "wired hardware verified end-to-end over MQTT." Software is done; this is a hardware integration plan.

Current state (inventory):

- `pi/src/sensor_wakeup/main.py` — `gpiozero.MotionSensor(PIR_PIN)`, publishes `turret/motion` with `{sensor:"pir", active:bool}`, debounced by `PIR_INACTIVE_AFTER_S` (2.0 s default).
- `pi/src/ir_led/main.py` — `gpiozero.DigitalOutputDevice(IR_LED_PIN, active_high=True, initial_value=False)`, subscribes `turret/ir/set`, forces LED off on shutdown.
- `common/config/hosts.py`: `PIR_PIN = 17`, `IR_LED_PIN = 27` (BCM numbering). Change here if wiring differs.
- Systemd units exist: `pi/systemd/sensor-wakeup.service`, `pi/systemd/ir-led.service`.

Unknowns to resolve before touching hardware:

- Which PIR module is being used (HC-SR501 vs. AM312 vs. other)? Determines supply voltage and output level.
- Which IR LED(s)? Current draw? A single 100 mA emitter can go direct-ish from a GPIO through a series resistor; an array will need a MOSFET.
- Is there a common ground between PIR board, LED driver, and Pi? (Required — do not skip.)

---

## 1. Wiring

Pi GPIO is 3.3 V logic. Inputs are **not** 5 V tolerant.

### PIR (input → Pi)

| PIR pin | Goes to | Notes |
|---|---|---|
| VCC | Pi 5 V (pin 2 or 4) for HC-SR501; Pi 3.3 V for AM312 | HC-SR501 runs on 5 V but its OUT is 3.3 V (regulator on board) — safe. Double-check your specific module with a meter before powering. |
| GND | Pi GND (pin 6 or 9) | Shared ground mandatory. |
| OUT | Pi BCM 17 (header pin 11) | Expect 3.3 V high / 0 V low. Verify with a meter before connecting if module is unknown. |

HC-SR501 pots: set **sensitivity** to mid, **time delay** to minimum (fully CCW). The module has its own re-trigger timer; the service also debounces. Keeping the module's timer short lets the software own the debouncing behavior.

Jumper on HC-SR501: set to **H** (repeat trigger) so continuous motion keeps the output high instead of pulsing.

### IR LED (output ← Pi)

A Pi GPIO can sink/source ~16 mA safely. That's fine for a single low-power indicator LED, **not enough for an IR illuminator**. Plan for a MOSFET driver.

| Component | Value / note |
|---|---|
| MOSFET | Logic-level N-channel (e.g. 2N7000 for < 200 mA, or AO3400 / IRLZ44N for higher). Threshold V_GS(th) must be ≤ 2.5 V so 3.3 V GPIO fully turns it on. |
| Gate resistor | 220 Ω between BCM 27 (header pin 13) and MOSFET gate. |
| Gate pulldown | 10 kΩ from gate to GND. Keeps LED off while the Pi boots and GPIOs float. |
| LED + series resistor | Sized for the LED's forward voltage and desired current. Anode to LED supply rail, cathode to MOSFET drain. |
| LED supply | Separate 5 V (or 12 V for arrays) rail if current > ~300 mA. **Common ground with the Pi.** |
| Flyback/decoupling | 100 nF across LED supply; a small electrolytic (10–100 μF) close to the LED if it's an array. |

Pinout summary:

```
Pi BCM 17 (pin 11) ─────────── PIR OUT
Pi 5 V (pin 2)    ─────────── PIR VCC      (or 3.3 V pin 1 for AM312)
Pi GND (pin 6)    ─────┬───── PIR GND
                       └───── LED-supply GND

Pi BCM 27 (pin 13) ──[220 Ω]──┬── MOSFET gate
                               └──[10 kΩ]── GND
MOSFET source ─────────────────── GND
MOSFET drain  ─────────────────── LED cathode
LED anode     ──[R_series]─────── LED supply +V
```

Record the final wiring in a `pi/docs/wiring.md` (photo + table) so the next person doesn't have to reverse-engineer it.

---

## 2. Bench checks before running services

Run each as the `nathan` user on the Pi. Stop the services first to avoid GPIO contention:

```
systemctl --user stop sensor-wakeup ir-led
```

### 2.1 PIR standalone read

```
python3 - <<'PY'
from gpiozero import MotionSensor
import time
pir = MotionSensor(17)
for _ in range(30):
    print("motion" if pir.motion_detected else "still")
    time.sleep(0.5)
PY
```

Expected: mostly "still", flips to "motion" for a second or two when you wave. If it stays stuck high: the PIR's on-board timer is too long (turn its delay pot CCW) or it's still warming up (first ~30 s after power-up are unreliable — wait, don't debug).

### 2.2 IR LED standalone drive

```
python3 - <<'PY'
from gpiozero import DigitalOutputDevice
import time
led = DigitalOutputDevice(27, active_high=True, initial_value=False)
for _ in range(3):
    led.on();  time.sleep(0.5)
    led.off(); time.sleep(0.5)
led.close()
PY
```

Expected: visible blink if it's a visible LED; for IR, view through a phone camera (most phone cameras pass near-IR and it shows up purple/white). If it stays on after the script exits, the MOSFET is latching — check the gate pulldown.

If either bench check fails, **stop and fix wiring before proceeding.** Running the systemd service won't help you debug a hardware problem.

---

## 3. Service-level checks

Restart the services:

```
systemctl --user start sensor-wakeup ir-led
systemctl --user status sensor-wakeup ir-led --no-pager
```

Both should be `active (running)` with a line like `PIR on BCM pin 17 ready` / `IR LED on BCM pin 27 ready` in the journal.

### 3.1 PIR → MQTT

On the Nano:

```
mosquitto_sub -h localhost -t 'turret/motion' -v
```

Wave at the PIR. Expected sequence:

```
turret/motion {"ts":..., "sensor":"pir", "active":true}
turret/motion {"ts":..., "sensor":"pir", "active":false}   # ~2 s after last motion
```

Gotcha: the service only publishes edges. If you see `active:true` and never a matching `active:false`, motion is still being detected (or the PIR output is stuck high — see 2.1).

### 3.2 MQTT → IR LED

From the Nano:

```
mosquitto_pub -h localhost -t turret/ir/set -m '{"on":true}'
mosquitto_pub -h localhost -t turret/ir/set -m '{"on":false}'
```

Expected: LED follows immediately; Pi journal shows `IR LED -> on` / `IR LED -> off`.

### 3.3 Shutdown safety

With the LED on, kill the service:

```
systemctl --user stop ir-led
```

LED must go off. If it stays on, the `try/finally` in `ir_led/main.py` didn't run (likely SIGKILL vs. SIGTERM). Try `kill -9 $(pgrep -f ir_led.main)` to confirm the failure mode — a `kill -9` *will* leave the LED on; that's a known hole and is why the hub's last-will (§5) also drives the LED off.

---

## 4. End-to-end hook-up (no hub yet)

Until the hub service exists, the PIR and LED are two independent MQTT citizens. Simulate the hub with a 5-line bridge to prove the full loop:

```
python3 - <<'PY'
import json, paho.mqtt.client as mqtt
c = mqtt.Client()
c.connect("localhost", 1883, 30)
def on_msg(_c, _u, msg):
    active = json.loads(msg.payload).get("active", False)
    c.publish("turret/ir/set", json.dumps({"on": active}))
c.on_message = on_msg
c.subscribe("turret/motion")
c.loop_forever()
PY
```

Wave at the PIR → LED turns on → stop waving → 2 s later LED turns off. That's the full input-output path verified end-to-end before the hub is written.

Delete the bridge once the hub lands — it's scaffolding.

---

## 5. Integration with the rest of the system

Once 3 and 4 pass:

- **Hub (Nano, step 6 of implimentation.md)** will be the real subscriber of `turret/motion` and publisher of `turret/ir/set` and `turret/recorder/arm`. The bridge above becomes the hub.
- **Last-will for IR safety.** Hub should subscribe to its own health topic and, if it sees itself go down (shouldn't happen; it's for peer hubs later) or on any clean shutdown, publish `{"on": false}` to `turret/ir/set`. Additionally, `ir-led` should subscribe to `turret/nano/health` and force-off on Nano "down" — prevents a crashed Nano from leaving IR on indefinitely. **This hook is not yet implemented** in `ir_led/main.py`; flag for the hub work.
- **Recorder arming** driven by the same motion edge — the hub applies the 10-second trailing window, not the sensor service.

---

## 6. Risks and known gaps

- **PIR warm-up.** First 30–60 s after power-up is garbage. The service has no suppression window; it will publish whatever it sees. If boot-time false positives are a problem, add a "suppress publish for first N seconds" guard in `sensor_wakeup/main.py`.
- **No publish on startup.** Service doesn't publish an initial `active:false`. Subscribers that come up later see nothing until the first motion. Not a bug today but worth knowing.
- **`kill -9` leaves LED on.** Documented above; real mitigation lives in the hub via its `turret/nano/health` last-will, plus an `ir-led` subscriber for that topic.
- **GPIO contention.** If both the service and a bench script hold BCM 27 at once, `gpiozero` raises `GPIOBusy`. Always `systemctl --user stop ir-led` before manual bench tests.
- **BCM vs. BOARD numbering.** `gpiozero` uses BCM. If someone re-wires to header-pin numbers in their head, BCM 17 (pin 11) and BCM 27 (pin 13) are easy to swap. Keep `common/config/hosts.py` as the single truth.

---

## 7. Sign-off checklist

Step 3 (PIR) is done when:

- [ ] HC-SR501/AM312 wired per §1, measured output ≤ 3.3 V.
- [ ] Bench script (§2.1) shows clean idle + motion transitions.
- [ ] `mosquitto_sub -h <nano> -t turret/motion -v` shows a matched `active:true` / `active:false` pair per wave (§3.1).
- [ ] Service survives a PIR unplug/replug without crashing.

Step 4 (IR LED) is done when:

- [ ] MOSFET driver wired per §1 with gate pulldown present.
- [ ] Bench script (§2.2) blinks the LED visibly (or through a phone camera for IR).
- [ ] `mosquitto_pub ... {"on":true/false}` toggles the LED and logs `IR LED -> on/off` (§3.2).
- [ ] `systemctl --user stop ir-led` turns the LED off within 1 s.

End-to-end (prep for step 6) is done when:

- [ ] The 5-line bridge in §4 ties motion → LED with the expected 2 s trailing delay.
- [ ] Wiring documented in `pi/docs/wiring.md` with a photo.
