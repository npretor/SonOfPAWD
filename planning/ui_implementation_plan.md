# UI Implementation Plan

Source: `user_interface_brainstorm.md`. Backend Flask, frontend React. Runs on the Nano alongside the hub. Talks to the rest of the system only over MQTT — Flask is a translator, not a second source of truth.

---

## 0. Clarification questions

Answer these before I start coding. The first three are load-bearing.

1. **Manual fire button — allowed in which modes?** My recommendation: only when `turret/fire/enable=true`, which today is only `auto-sentry`. So the UI's fire button is visible always but disabled unless `fire/enable=true`. Alternative: a dedicated "manual-fire" mode that arms fire without inference. Which do you want?

2. **Video latency target.** Sub-second (WebRTC, ~200–500 ms) or OK with 2–5 s (HLS)? If you plan to aim and fire through the UI, WebRTC is the only workable answer. HLS is fine if aiming is hands-off and the UI is mostly a dashboard. My recommendation: **WebRTC via `MediaMTX`** (pre-built Go binary, ARM64 release, single YAML config — bridges the Pi's RTSP to WebRTC with no code).

3. **Is a third-party streamer (MediaMTX or go2rtc) acceptable?** Writing our own WebRTC gateway is 1–2 weeks of work; MediaMTX is 30 minutes. If yes, it becomes the `nano/src/streamer` equivalent from `implimentation.md` step 7.

4. **Power usage metric — source?** Nano has no built-in reporting for whole-system power without an INA219 or similar. Options: (a) drop it for v1, (b) surface `tegrastats` CPU power estimates (approximate), (c) wait until you add a hardware monitor. My recommendation: (a) — the sidebar shows "power: —" for now.

5. **RSSI — which interface?** The Pi↔Nano link is Ethernet, so there's no RSSI there. If you mean WiFi on the Nano (for LAN client access), we can read `/proc/net/wireless`. Or skip until meaningful. My recommendation: skip for v1.

6. **Settings you want persistent (starter list).** My assumptions, please confirm/extend:
   - `motion_timeout_s` (maps to `PIR_INACTIVE_AFTER_S`, but hot-applied via MQTT)
   - `trailing_s` (recorder trailing window)
   - `target_class` (one of cat/raccoon/rat/deer)
   - `default_control_scheme` (joystick / keyboard / tilt)
   - `telemetry_poll_interval_s` (UI-only; how often the sidebar updates)

7. **UI auth.** LAN-only, no login? I'll assume no login in v1.

8. **Flask serving model.** Plain Flask dev server is fine for single-user LAN. I'll use `waitress` (pure Python, no C deps) so we're not shipping a dev server in a systemd unit. OK?

Blocking items: **Q1, Q2, Q3**. The rest have reasonable defaults.


Answers to clarification questions 
1. I want a manual-fire button that allows firing without inference. Also, add an arm toggle for armed/unarmed that is a separate button
2. MediaMTX sounds perfect, i need very low latency. Let's start with this
3. Yes MediaMTX is fine 
4. Display some of what tegrastats shows: here is an example capture: 
```
RAM 2321/3964MB (lfb 1x4MB) SWAP 1359/6078MB (cached 190MB) IRAM 0/252kB(lfb 252kB) CPU [31%@518,29%@518,off,off] EMC_FREQ 10%@1600 GR3D_FREQ 0%@614 APE 25 PLL@37C CPU@38C iwlwifi@47C PMIC@50C GPU@38.5C AO@47.5C thermal@38.25C POM_5V_IN 2559/2842 POM_5V_GPU 82/176 POM_5V_CPU 247/326
```

---
5. Nevermind, skip
6. Perfect, go with the starter list 
7. It's LAN only so no login. Latency will be too high to do anything else 
8. Sounds good


## 1. Architecture

```
Browser (React SPA)
    │
    │   HTTPS? no — http only, LAN
    │   ┌──────────────────────────────────────────────┐
    │   │  Flask backend (waitress) on :5000           │
    │   │                                              │
    │   │  HTTP  /api/*          -> commands, settings │
    │   │  WS    /socket.io      -> live state + logs  │
    │   │  HTTP  /               -> built React SPA    │
    │   │                                              │
    │   │  paho-mqtt subscribes + publishes on user's  │
    │   │  behalf; browser never sees MQTT creds       │
    │   └──────────────────────────────────────────────┘
    │                       │
    │                       ▼                          
    │              mosquitto on :1883 (auth'd)         
    │                                                  
    │   ┌──────────────────────────────────────────────┐
    │   │  MediaMTX on :8889 (WebRTC WHEP)             │
    │   │    ingest:  rtsp://pi:8554/live              │
    │   │    egress:  http(s)://nano:8889/live/whep    │
    │   └──────────────────────────────────────────────┘
    │
    └──> Video pane loads /whep feed directly from MediaMTX
```

**Why this shape:** Flask never passes video, MediaMTX never touches control. Each has one job. The browser talks to each separately, which keeps Flask's socket traffic tiny (it's only JSON, never frames).

---

## 2. Repo layout

```
nano/src/ui/
├── backend/
│   ├── __init__.py
│   ├── main.py              # waitress entry: builds Flask app, starts MQTT bridge
│   ├── app.py               # Flask app factory
│   ├── mqtt_bridge.py       # paho -> socketio rooms; socketio -> paho publish
│   ├── routes.py            # HTTP endpoints (settings, logs, health)
│   ├── settings.py          # JSON persistence at /var/lib/turret/ui_settings.json
│   └── logs.py              # journalctl --user-unit wrapper
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── socket.ts        # socket.io-client singleton
│       ├── api.ts           # fetch helpers
│       ├── routes/
│       │   ├── Dashboard.tsx
│       │   └── Settings.tsx
│       ├── components/
│       │   ├── Sidebar.tsx           # services, angle, telemetry
│       │   ├── ServiceHealth.tsx
│       │   ├── TurretAngle.tsx       # fake data, wired to MQTT state
│       │   ├── TelemetryPanel.tsx
│       │   ├── VideoPane.tsx         # WHEP client
│       │   ├── ModeSelect.tsx
│       │   ├── OverlayBadges.tsx     # mode/fps/res
│       │   ├── ControlPad.tsx        # joystick/keyboard/tilt switcher
│       │   ├── FireButton.tsx
│       │   └── StreamingLogs.tsx     # collapsible log tail
│       └── hooks/
│           ├── useHubState.ts        # socket.io subscriber
│           └── useThrottledAim.ts    # 20 Hz control throttle
└── static/                  # built bundle lives here; Flask serves
```

`package.json` produces artifacts into `nano/src/ui/static/` via `vite build`. That directory is served statically by Flask.

---

## 3. Backend (Flask)

Endpoints (JSON in/out unless stated):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Flask's own liveness |
| GET | `/api/settings` | Current settings from `ui_settings.json` |
| PUT | `/api/settings` | Replace settings (full object) |
| GET | `/api/logs/<service>?lines=200` | Last N journal lines; service ∈ camera-pipeline, sensor-wakeup, ir-led, fire-control, recorder, hub, ui-backend |
| POST | `/api/mode` | Body `{mode}` → publishes `turret/mode/set` |
| POST | `/api/ir` | Body `{on}` → publishes `turret/ir/manual` |
| POST | `/api/fire` | Empty body → publishes `turret/fire/request` (backend enforces: only when `fire/enable=true`) |
| POST | `/api/aim` | Body `{pan_deg, tilt_deg}` → publishes `turret/motion_ctrl/aim` (throttled upstream in the client) |
| GET | `/whep/*` | **Proxy** to MediaMTX; keeps one origin to simplify CORS. Optional — see §5. |

WebSocket events (socket.io on `/socket.io`):

- **server → client**
  - `hub_state` — last `turret/hub/state` payload (retained, sent on connect)
  - `mode_current` — last `turret/mode/current` (retained, sent on connect)
  - `telemetry` — every `turret/nano/telemetry`
  - `health` — every `turret/+/health` edge
  - `recorder_event` — every `turret/recorder/event`
  - `snapshot_event` — every `turret/snapshot/event`
  - `fire_event` — every `turret/fire/event`
  - `alert` — every `turret/alert`
  - `detection` — every `turret/inference/detection` (later)
  - `aim_state` — every `turret/motion_ctrl/state` (when the hub starts publishing this)

- **client → server** (uses HTTP POST instead to keep the command path HTTP; WS is receive-only).

**State bootstrap.** On WebSocket connect, Flask immediately re-emits the last known retained values (`hub_state`, `mode_current`) from its in-memory cache — so the browser has a usable UI within one round-trip, not after the next retained republish.

**Fire-button gating.** The backend tracks `turret/fire/enable` as retained. `POST /api/fire` returns 409 if `fire/enable` is false. The frontend mirrors the same gate visually.

---

## 4. Frontend (React)

Stack: **Vite + React + TypeScript + socket.io-client**. No Redux — state lives in a small Zustand store (or React context) updated by a single socket.io effect. Dependencies kept minimal:

- `react`, `react-dom`, `react-router-dom`
- `socket.io-client`
- `zustand` (state) — tiny, sidesteps reducer boilerplate
- `nipplejs` for the joystick
- No UI library by default — plain CSS Modules. If you want something richer later, `tailwindcss` swaps in without rewrites.

### 4.1 Dashboard (`/`)

Layout: 1/4 sidebar, 3/4 main.

**Sidebar sections (top to bottom):**

1. `<ServiceHealth>` — list of all known services from `hub_state.services`. Red/green dot + name. Updates on every `health` edge.
2. `<TurretAngle>` — two axis bars with limits. Values come from `aim_state`; if never seen, shows "—".
3. `<TelemetryPanel>` — CPU %, RAM %, temp °C, disk free, "power: —". Updates on every `telemetry` event (currently every 5 s; UI doesn't need to poll separately).

**Main area:**

1. `<OverlayBadges>` top-left over the video: current mode, fps (from MediaMTX stats endpoint), resolution.
2. `<VideoPane>` — takes the rest of the 3/4 width. Toggle button (play/pause) controls local WebRTC subscription; the Pi's camera pipeline keeps running regardless.
3. `<ModeSelect>` — dropdown; on change `POST /api/mode`. The displayed value comes from `mode_current`, not local state, so an external mode change is reflected.
4. `<FireButton>` — big red, disabled unless `fire/enable=true`. On click → `POST /api/fire`. Holds a 500 ms cooldown lockout on the client in addition to server-side rate limit.
5. `<ControlPad>` — scheme switcher (joystick | keyboard | tilt). Default joystick. Only one active at a time.
   - Joystick: `nipplejs` positioned right side; emits (angle, magnitude) → mapped to (pan_delta, tilt_delta).
   - Keyboard: arrow keys; while held, emits step commands.
   - Tilt: `DeviceOrientationEvent` on iOS/iPad (requires a one-time user-gesture permission prompt); maps beta/gamma to pan/tilt.
   - All three feed through `useThrottledAim` — max 20 Hz, drops intermediate values, always sends the latest on release.
6. `<StreamingLogs>` — collapsible bottom drawer. Dropdown of services, `GET /api/logs/<svc>?lines=200`, auto-refresh every 3 s while open.

### 4.2 Settings (`/settings`)

Form bound to `/api/settings`:

- Motion detection timeout (s), slider 1–30
- Recorder trailing window (s), slider 3–60
- Target class — select from {cat, raccoon, rat, deer}
- Default control scheme — select
- (Placeholder) Motion control speed — disabled input, label "coming later"
- Save button → `PUT /api/settings`. Backend writes JSON + republishes any hub-bound settings over MQTT (new topic `turret/hub/config` retained; hub picks up on the next payload).

Logs section on the same page = same `<StreamingLogs>` but expanded by default.

---

## 5. Video pipeline (MediaMTX)

One binary. One config file. Installed as a user systemd unit on the Nano.

`nano/scripts/install_mediamtx.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
VER="1.8.4"   # pin; known to work on ARM64
ARCH="linux_arm64v8"
sudo mkdir -p /opt/mediamtx
curl -L "https://github.com/bluenviron/mediamtx/releases/download/v${VER}/mediamtx_v${VER}_${ARCH}.tar.gz" \
    | sudo tar -C /opt/mediamtx -xz
sudo install -m 644 "$(dirname "$0")/../mediamtx/mediamtx.yml" /opt/mediamtx/mediamtx.yml
```

`nano/mediamtx/mediamtx.yml` (excerpt):
```yaml
paths:
  live:
    source: rtsp://testcam-64bit.local:8554/live
    sourceOnDemand: no
    # WebRTC WHEP on /live
webrtc: yes
webrtcAddress: :8889
rtspAddress: :8554   # repub as RTSP too for tooling
```

`nano/systemd/mediamtx.service`:
```
[Unit]
Description=MediaMTX (RTSP->WebRTC bridge)
After=network-online.target
[Service]
ExecStart=/opt/mediamtx/mediamtx /opt/mediamtx/mediamtx.yml
Restart=on-failure
[Install]
WantedBy=default.target
```

Browser side — minimal WHEP client (~100 lines, or pull `@eyevinn/webrtc-player` from npm). `VideoPane.tsx` owns the lifecycle: `start()` makes the WHEP request and attaches the stream to a `<video>`; `stop()` closes the RTCPeerConnection.

Latency expectation: 250–500 ms on the LAN. Good enough for manual aim.

---

## 6. New MQTT topics needed

Add to `common/messaging/topics.py`:

| Topic | Direction | Retained | Notes |
|---|---|---|---|
| `turret/hub/config` | UI → Hub | yes | `{motion_timeout_s, trailing_s, target_class}` — hub picks up and applies, republishes current effective config (same topic, retain) |
| `turret/motion_ctrl/state` | motion_ctrl → hub/UI | no | `{pan_deg, tilt_deg}` — for the sidebar's angle display. Until motion_ctrl exists, hub echoes the last `aim` command here so the UI has something to show. |

Nothing else new — the rest of the UI's needs are met by topics already in `topics.py`.

---

## 7. Hub additions required for UI to work

Small, lands in the Step C/later hub code:

- Subscribe `turret/hub/config`; when received, update the relevant state (debounce 250 ms), and republish effective config retained.
- Publish `turret/hub/config` retained at startup with current values, so the settings page shows real state on first load.
- Echo `turret/motion_ctrl/state` with the last received `aim` command, at most every 100 ms. Placeholder until the real motion_ctrl worker exists.
- Wire `turret/ir/manual` into the IR merge (ir_merge.py lands here alongside this step if not earlier).
- Gate `POST /api/fire` server-side: Flask tracks the retained `fire/enable`; hub drives that value based on mode.

---

## 8. Build order

### Step H.1 — Video only
Install MediaMTX, prove the Pi's RTSP comes out as WebRTC in a raw browser. No React, no Flask yet. Measure latency.

**Done when:** loading MediaMTX's built-in WHEP player in Chrome shows the Pi's video at < 1 s latency.

### Step H.2 — Flask skeleton + socket.io
`main.py`, `app.py`, `mqtt_bridge.py`. Bootstraps paho connection, bridges `hub_state`, `mode_current`, `telemetry` to socket.io. One health endpoint. Systemd unit `ui-backend.service`.

**Done when:** `curl http://nano:5000/api/health` returns ok; a hand-rolled websocket test client receives retained hub_state on connect and a telemetry event every 5 s.

### Step H.3 — React shell + Dashboard sidebar
Vite scaffold, routing, Zustand store, socket.io client. Sidebar wired to hub_state and telemetry — services list, telemetry panel. No video yet.

**Done when:** loading `http://nano:5000/` shows a live-updating sidebar that reflects actually-running services.

### Step H.4 — Mode select + video pane
Dropdown that POSTs mode; video pane with play/pause WHEP. Overlay badges (mode from store; fps/res from MediaMTX `/v3/paths/list`).

**Done when:** selecting "monitor" + clicking play shows Pi video with the mode badge overlay. Selecting "idle" cuts recording but video keeps streaming.

### Step H.5 — Fire button + control pad
Fire button (gated on retained `fire/enable`). Control pad with three schemes; default joystick. Throttled to 20 Hz.

**Done when:** with `auto-sentry` selected, fire button publishes `turret/fire/request` and the fire-control service responds (or rejects, if rate-limited). Joystick movement shows in the `TurretAngle` sidebar via the echo topic.

### Step H.6 — Settings page + logs drawer
`GET/PUT /api/settings`. Logs drawer pulls `/api/logs/<svc>`. Hub accepts `turret/hub/config` and applies.

**Done when:** changing "trailing_s" in Settings changes the actual clip tail length on the next arm cycle.

### Step H.7 — Polish & production startup
`ui-backend.service` via waitress. Mime types, static caching headers, one CSS pass for layout. Keyboard shortcuts on Dashboard (space = play/pause, etc., optional).

**Done when:** rebooting the Nano brings up a usable dashboard at `http://nano.local:5000` with no manual steps.

---

## 9. Deferred / flagged

- **TLS.** LAN only, no HTTPS. If you ever expose this beyond the LAN, that's a separate conversation (self-signed vs. proper cert, WebRTC STUN/TURN, MQTT TLS).
- **Mobile layout.** Desktop-first. I'll keep the CSS simple enough that a responsive pass later is tractable.
- **Browser MQTT direct.** Tempting (mosquitto listens on 9001 WS) but leaks creds. Not doing it.
- **Auth-gated fire.** A "confirm to fire" modal isn't in the brainstorm; I won't add one unless you want it.
- **Detection overlay on video.** When inference exists, we draw bboxes on a `<canvas>` layered over the `<video>`. The WHEP player exposes timestamps; detections include timestamps. That's Step F+ work.
- **Power metric + RSSI.** Pending Q4, Q5.

---

## 10. What I need from you before I start

- Answers to Q1, Q2, Q3.
- Confirm or trim the settings list in Q6.
- Everything else I'll take the default on unless you say otherwise.

After that I'll start at H.1 (MediaMTX) and stop for review before touching React.
