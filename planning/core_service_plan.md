# Core Service Implementation Plan

Companion to `core_service_spec.md`. This is the hub/brain: the Nano-side orchestrator that reads the current mode, reacts to motion, and drives the recorder, IR LED, (later) inference, and (later) fire control. Also exposes telemetry for the forthcoming Flask UI.

This plan lands in the `nano/src/hub/` directory (currently empty except `__init__.py`). The `inference/` and Flask UI come **after** the hub is real.

---

## 0. Clarification questions (answer before I implement)

These are the ones that materially change the design. Everything below assumes a default answer; I've flagged which.

1. **Mode transport.** Web UI → hub over MQTT on `turret/mode/set`, or Flask running in-process with the hub (shared memory)? *Assumed: separate Flask process, MQTT topic — keeps the web layer replaceable.*
2. **Mode persistence across hub restart.** Does mode survive a reboot, or does the Nano come up in `idle` every time? *Assumed: persist last mode to `/var/lib/turret/mode.json`; boot into that mode.*
3. **Trailcam image source.** The Pi only publishes H.264 over RTSP today — no stills. Options: (a) hub grabs a keyframe from the RTSP stream and decodes one frame to JPEG, (b) add a Pi-side snapshot service that calls `rpicam-still` on demand, (c) pull a still from the recorder's most recent clip. *Assumed: (a), JPEG snapshot via GStreamer `appsink` on the same RTSP feed — zero new Pi code.*
4. **Auto-aim target class.** Hardcoded "person", or configurable per-mode via the UI? *Assumed: configurable, default "person", stored in `mode.json`.*
5. **Fire control hardware and interlocks.** What actually fires (servo? solenoid? Nerf trigger?), where is it wired (Pi GPIO? Nano GPIO? USB?), and what's the safety story (hardware arm switch, software confirm, both)? *No assumption — I need an answer. I will not build auto-sentry without an explicit interlock spec.*
6. **Pan/tilt hardware and control loop.** Same questions: what hardware, which host owns the GPIO/serial, and does inference command motors directly or route through the hub? *Assumed for now: out of scope for this phase — auto-aim reduces to "log detections + draw overlay"; actual motor control is a later service.*
7. **Inference runtime.** DeepStream, plain TensorRT, ONNX via `onnxruntime`, or CPU OpenCV DNN as v1? *Assumed: defer — hub exposes an MQTT contract and inference plugs in later. v1 hub has no detection.*
8. **Telemetry delivery to the UI.** MQTT publish on an interval + Flask subscribes, or Flask polls the hub's in-process state? *Assumed: hub publishes `turret/nano/telemetry` every 5 s; Flask subscribes. Same channel the UI uses for everything else.*
9. **Clip retention.** Who deletes old clips? Hub, or a separate janitor, or never (manual)? *Assumed: hub runs a cheap rotation — keep last N GB or M clips, configurable, default "keep until < 5 GB free then drop oldest."*
10. **Fire topic authorization.** If the fire command goes over MQTT and the broker is LAN-open, anyone on the LAN can fire. Do we add auth on the broker before enabling auto-sentry, or rely on "LAN is trusted"? *Assumed: gate auto-sentry behind an MQTT username/password and refuse to enter `auto-sentry` mode if the broker isn't authenticated.*
11. **Idle mode semantics.** Does `idle` silence motion handling entirely (no clip, no IR), or does it still record passively? Spec says "does nothing, waits." *Assumed: idle = full silence. Motion events are logged but produce no side effects.*
12. **Per-mode IR policy.** Currently the PIR triggers everything; should IR be on continuously in monitor/auto-aim, or only during active recording? *Assumed: IR follows `recorder armed` state — on while a clip is being captured, off otherwise. Trailcam: IR on for the few seconds around the still grab.*

Stop-the-line items: **Q5 and Q6**. Everything else has a reasonable default.

---

## 1. What the hub actually does

Core responsibilities, grouped:

- **Mode machine.** Holds the current mode (`idle`, `monitor`, `trailcam`, `auto-aim`, `auto-sentry`) and responds to `turret/mode/set`. One mode at a time.
- **Motion reaction per mode.** Maps `turret/motion active=true` to the appropriate action(s): arm recorder, toggle IR, request snapshot, run detection, fire (if interlocked).
- **Service orchestration.** Publishes `turret/recorder/arm`, `turret/ir/set`, `turret/inference/enable`, `turret/snapshot/request` as mode dictates.
- **Health aggregation.** Subscribes to `turret/+/health`; keeps a dict of last-seen status per `host/service`. Publishes the aggregate on `turret/hub/state` for the UI.
- **Telemetry.** Every 5 s, publishes Nano temperature, CPU, RAM, disk free (on `CLIPS_DIR`). Read from `/sys/class/thermal/...`, `/proc/stat`, `/proc/meminfo`, `shutil.disk_usage`. No extra deps.
- **Retention.** On each `clip_closed` event, check free space on `CLIPS_DIR`; drop oldest clips until threshold is met.
- **Mode persistence.** Write `{"mode": "...", "target_class": "..."}` to `/var/lib/turret/mode.json` on change; read at startup.

Explicit non-responsibilities:

- Does **not** run inference itself. If/when inference is added, it's a separate service the hub enables/disables via MQTT.
- Does **not** talk to motors directly. If pan/tilt is added, that's another service.
- Does **not** serve HTTP. Flask is its own process.

---

## 2. Module layout inside `nano/src/hub/`

```
nano/src/hub/
├── __init__.py
├── main.py            # entry point; wires everything to the GLib (or asyncio) loop
├── modes.py           # Mode enum + mode-specific side-effect tables
├── state.py           # in-memory state: current mode, armed flag, last motion ts, peers' health
├── telemetry.py       # cpu/ram/temp/disk readers + periodic publish
├── retention.py       # clip rotation policy
└── persistence.py     # read/write /var/lib/turret/mode.json
```

Keep it flat. No `services/` or `controllers/` subdirectories — the whole hub is small enough that one flat package is easier to navigate than a layered one.

---

## 3. MQTT contract additions

New topics (add to `common/messaging/topics.py`):

| Topic | Dir | Publisher | Payload |
|---|---|---|---|
| `turret/mode/set` | UI → Hub | Flask | `{ts, mode: "idle"\|"monitor"\|"trailcam"\|"auto-aim"\|"auto-sentry", target_class?: "person"}` |
| `turret/mode/current` | Hub → UI | Hub (retained) | `{ts, mode, target_class}` — retained so the UI can render immediately |
| `turret/hub/state` | Hub → UI | Hub | aggregate health: `{ts, services: {"pi/camera-pipeline": "up", ...}, armed: bool, last_motion_ts}` |
| `turret/nano/telemetry` | Hub → UI | Hub | `{ts, temp_c, cpu_pct, ram_pct, disk_free_bytes, disk_total_bytes}` |
| `turret/snapshot/request` | Hub → ? | Hub | `{ts}` — for trailcam. Exact responder is Q3-dependent. |
| `turret/snapshot/event` | ? → Hub/UI | snapshot worker | `{ts, path}` |
| `turret/inference/enable` | Hub → Inference | Hub (retained) | `{ts, enabled: bool, target_class}` |
| `turret/inference/detection` | Inference → Hub | (later) | `{ts, class, score, bbox:[x,y,w,h]}` |
| `turret/fire/request` | Hub → Fire svc | Hub | `{ts, confidence, reason}` — **only emitted in auto-sentry with interlocks, per Q5** |

`retain=true` on `turret/mode/current` so a late-subscribing UI sees the truth immediately without having to ask.

---

## 4. Mode → action matrix

What actually fires on `motion active=true` in each mode:

| Mode | Recorder | IR LED | Snapshot | Inference | Fire |
|---|---|---|---|---|---|
| `idle` | — | — | — | — | — |
| `monitor` | arm (trailing 10 s) | on while armed | — | — | — |
| `trailcam` | — | on briefly (2 s) | 1 JPEG | — | — |
| `auto-aim` | arm (trailing 10 s) | on while armed | — | enabled, publishes detections (overlay/log only) | — |
| `auto-sentry` | arm (trailing 10 s) | on while armed | — | enabled | fire on centered detection + interlocks |

On `motion active=false` — nothing, the recorder owns its own trailing window (as the recorder module already does). The hub's job is to publish the *arm* edges, not count seconds.

---

## 5. State management

Hub runs on a single GLib main loop (to match the recorder's style and keep cross-service code consistent), with paho callbacks marshalled onto it via `GLib.idle_add`. All state mutations happen on the loop thread.

Core state (in `state.py`):

```python
@dataclass
class HubState:
    mode: Mode = Mode.IDLE
    target_class: str = "person"
    recorder_armed: bool = False
    last_motion_ts: float = 0.0
    peers: dict[str, str] = field(default_factory=dict)  # "host/service" -> "up"/"down"
```

A single `apply(event)` function takes one of `{mode_set, motion, peer_health, clip_closed, detection, telemetry_tick}` and mutates state + emits MQTT as needed. This keeps the reaction logic inspectable and testable without hardware.

---

## 6. Telemetry readers (no new deps)

- **Temperature.** `/sys/class/thermal/thermal_zone0/temp` → millidegrees C. Divide by 1000.
- **CPU %.** Read `/proc/stat`'s first line twice, 1 s apart, compute busy delta / total delta. Cache prev sample between ticks.
- **RAM %.** `/proc/meminfo` → `1 - MemAvailable/MemTotal`.
- **Disk.** `shutil.disk_usage(CLIPS_DIR)`.

5 s publish cadence. All reads are < 1 ms; runs inline on the GLib loop.

---

## 7. Persistence

- **Path.** `/var/lib/turret/mode.json` (same directory as `clips/`, so `host_prep.sh` already ensures the parent exists).
- **Shape.** `{"mode": "...", "target_class": "..."}`.
- **Write-then-rename** on every change to avoid half-written files.
- **Missing/corrupt file** → default to `idle` and log a warning; do not crash. Idle is the safe mode.

---

## 8. Retention

On every `clip_closed`:

1. `shutil.disk_usage(CLIPS_DIR)`.
2. If free < `CLIP_FREE_FLOOR_BYTES` (default 5 GB), list `*.mkv` in `CLIPS_DIR` by mtime ascending and delete until free ≥ floor.
3. Log each deletion.

Hard-caps: never delete a file modified in the last 60 s, never delete the most recent clip regardless of threshold. Defense against clock skew or a disk-fill loop.

Add these constants to `common/config/hosts.py` — they're tuning knobs, not hub internals.

---

## 9. Failure modes and interlocks

- **Broker down at boot.** Hub must not crash. paho's auto-reconnect already handles this; just ensure nothing in startup requires a successful publish.
- **Pi dies mid-recording.** Recorder handles RTSP EOS and reconnects; hub only observes `turret/pi/health = down` and should leave `recorder_armed = false` state alone (the recorder's arm-on-reconnect logic re-applies it).
- **`kill -9` on hub.** Last-will fires `turret/nano/health = down`. `ir-led` on the Pi should subscribe to that and force the LED off (this is the "not yet implemented" item from `input_output_plan.md` §5).
- **Auto-sentry interlock.** Before publishing `turret/fire/request`, hub requires all of: (a) mode is `auto-sentry`, (b) detection centered within configurable pixel tolerance, (c) confidence ≥ threshold, (d) broker connection has credentials, (e) last fire was ≥ `MIN_FIRE_INTERVAL_S` ago. If any fail, log the reason — never fire silently.

---

## 10. Build order

Small, each step leaves the tree in a working state.

1. **`state.py` + `modes.py` + `persistence.py` + unit tests** that pump events through `apply()` and assert the emitted MQTT calls (collected into a fake client). No hardware, no broker. Fastest loop for getting the logic right.
2. **`telemetry.py`** standalone: a CLI that reads + prints the metrics once. Verifies the `/sys` and `/proc` paths on this specific Nano.
3. **`main.py`** wires MQTT in + GLib loop + telemetry timer + retention hook. Deploy as `nano/systemd/hub.service`. **Done when:** hub starts, publishes `turret/mode/current = idle` retained, and `mosquitto_sub turret/nano/telemetry -v` on the Nano shows values every 5 s.
4. **Monitor mode end-to-end.** Publish `{"mode":"monitor"}` on `turret/mode/set`. Wave at PIR. Expected chain: `turret/motion active=true` → hub publishes `turret/recorder/arm armed=true` → recorder starts clip → hub publishes `turret/ir/set on=true` → trailing window → recorder closes clip → hub publishes `turret/ir/set on=false`. All existing services, new orchestrator — no new workers.
5. **Trailcam snapshot path.** Add a minimal snapshot worker (inside hub or as `nano/src/snapshot/`, TBD by Q3) that opens the RTSP feed on demand, pulls one decoded frame, writes JPEG to `/var/lib/turret/snapshots/`, publishes `turret/snapshot/event`.
6. **Auto-aim stub.** `turret/inference/enable` goes out correctly, but there's no inference service yet. Hub logs what it *would* do on detection. **Stops here until Q7 is answered.**
7. **Auto-sentry.** Blocked on Q5 (fire hardware + interlock). Do not implement until the interlock spec exists.
8. **Retention.** Add after monitor mode works; easier to reason about once clips are landing reliably.
9. **Flask UI.** Out of scope for this plan — separate document once hub is stable.

---

## 11. Open risks

- **Mode thrash from flapping PIR.** If `motion` toggles quickly, hub will bounce between arm/disarm. The recorder already owns the trailing window so this is mostly fine; but IR LED will flap unless we add a minimum "on" duration. Cheap fix: latch IR on for at least `TRAILING_SECONDS` whenever armed goes high.
- **Snapshot decoding cost.** One keyframe/sec at 1080p is not free on the Nano CPU. If trailcam is used heavily, move to `nvv4l2decoder` → `appsink`. For v1 the cost is acceptable since it's triggered only on motion edges.
- **MQTT is not ordered across topics.** `recorder/arm` and `ir/set` may be delivered out of order to subscribers. The services are idempotent, so it doesn't matter operationally, but don't write logic that assumes "I set X before Y, so Y handler can trust X." Route those through the hub's own state.
- **Telemetry disk stat ≠ clips dir filesystem** if `/var/lib/turret/clips` is a mount point. `shutil.disk_usage` handles this correctly (it stat's the target path's filesystem) — noting here so it doesn't get "refactored" into `df /`.

---

## 12. Summary of asks before I start coding

- Answer **Q5** (fire hardware + interlock) — blocks auto-sentry entirely.
- Answer **Q6** (pan/tilt hardware) — determines auto-aim scope.
- Confirm or override the assumed defaults on Q1–Q4 and Q7–Q12.
- Confirm the build order in §10: I plan to ship through step 4 (monitor mode e2e) as a single deliverable, then stop and review before moving on.
