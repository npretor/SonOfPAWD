# Plan 2 — Split-Host Architecture: Pi Captures, Jetson Computes

Companion to `plan1.md`. Same project goals, but the IMX477 will not stabilize on the Jetson Nano, so the camera moves to a Raspberry Pi that streams video to the Nano over the LAN. The Nano keeps every other responsibility.

This plan treats the Pi as a **camera appliance**: one job, boringly reliable, replaceable. The Nano stays the brain.

---

## 0. Why this works

The IMX477 is the Raspberry Pi HQ Camera module — it is *designed* for the Pi's libcamera stack and has years of production use there. On the Pi, `rpicam-vid` / `libcamera-vid` owns the sensor cleanly, the Pi's own H.264 encoder (Pi 4 and earlier) or its CPU (Pi 5) produces a standard H.264 bitstream, and everything downstream (GStreamer, ffmpeg, VLC, DeepStream) consumes it without caring where it came from.

The Nano's remaining job — decode H.264 → NVMM → record / infer — is exactly what NVDEC + `nvv4l2decoder` are for, and it is a very well-trodden path (DeepStream's canonical examples start from an RTSP source, not from `nvarguscamerasrc`). So you are trading one broken thing (Argus + IMX477 on Nano) for one well-supported thing (RTSP in on Nano) **without losing hardware acceleration anywhere in the pipeline**.

---

## 1. Responsibility split

| Service | Host | Notes |
|---|---|---|
| `camera-pipeline` | **Pi** | Owns IMX477 via libcamera. Encodes H.264. Publishes one loopback-style stream on the LAN. |
| `sensor-wakeup` (PIR, later thermal) | **Pi** (recommended) | PIR should be physically near the camera (same FOV). Keeps GPIO wiring on one board. |
| IR-LED GPIO | **Pi** | Same reason — lives on the turret head with the camera. |
| `recorder` | **Nano** | Pulls the Pi's stream, writes MP4s with `splitmuxsink`. No re-encode. |
| `streamer` (LAN re-publisher / WebRTC gateway later) | **Nano** | Pi's stream is the source of truth; Nano re-publishes for browsers / LAN clients. |
| `inference` (later) | **Nano** | `nvv4l2decoder` → NVMM → TensorRT / DeepStream. |
| `motion-control` (later) | **Nano** (or Pi, see §7) | Depends on where the motors are wired. |
| `hub` | **Nano** | Brain. Talks to the Pi's services over the LAN control bus. |

**Key inversion vs `plan1.md`:** the "single camera owner" constraint is still there, it just moved off-box. The Nano never touches the sensor; it only consumes an H.264 stream.

---

## 2. Which Pi

Three realistic choices, each with a different trade-off:

| Pi | HW H.264 encode? | CPU | Verdict for this role |
|---|---|---|---|
| **Pi 4B (2 GB+)** | Yes, via V4L2 M2M | Modest | **Best fit.** Hardware encode at 1080p30 is free CPU; libcamera + IMX477 works out of the box. |
| Pi 5 | **No** (HW encoder removed) | Much faster | Works, but now you're burning Pi CPU on x264 software encode. Fine at 1080p30; noisier and hotter. |
| Pi Zero 2 W | Yes | Weak, WiFi-only | Marginal at 1080p30; WiFi adds jitter. OK if you accept 720p and want the smallest head unit. |
| Pi 3B+ | Yes | Old | Works, but older libcamera support and slower USB/network. Use only if you already have one. |

**Recommendation: Pi 4B (2 GB is enough).** Hardware H.264 encode is the whole point of putting a Pi there; losing it on Pi 5 defeats the purpose unless you've already got one.

Wire the Pi to the same switch as the Nano over **Ethernet**, not WiFi. Direct Cat5/6 between the two boards (either via a switch or back-to-back with modern auto-MDIX) eliminates the single biggest source of latency and stutter in this architecture.

---

## 3. Video data plane — Pi side

### 3a. Capture + encode on the Pi

Two viable tools; pick one and stop:

**Option A — `rpicam-vid` piped to a streamer (simplest).**
```
rpicam-vid -t 0 --width 1920 --height 1080 --framerate 30 \
  --codec h264 --inline --listen -o tcp://0.0.0.0:8888
```
One-liner, starts on boot via systemd, produces raw H.264 over TCP. Easy to debug (`ffplay tcp://pi:8888`). No container / no RTP — you are feeding a naked H.264 elementary stream.

**Option B — GStreamer with `libcamerasrc` + `v4l2h264enc` + `rtspserver` (recommended).**
```
libcamerasrc ! video/x-raw,width=1920,height=1080,framerate=30/1
  ! v4l2convert ! v4l2h264enc extra-controls="controls,h264_profile=4,video_bitrate=6000000"
  ! h264parse config-interval=1 ! rtph264pay name=pay0 pt=96
```
Wrapped in `gst-rtsp-server` (the Pi has Python bindings). This matches the shape of `plan1.md` exactly: the Nano just points at `rtsp://pi.local:8554/live` instead of `rtsp://127.0.0.1:8554/live`. **Everything downstream on the Nano stays identical to plan1.**

Pick **Option B**. The interface contract the Nano depends on is "an RTSP URL serving H.264". If that contract is stable, the Nano code never has to know whether the camera lives on 127.0.0.1 or on another box.

### 3b. Transport choice (Pi → Nano)

| Transport | Latency | Reliability on LAN | Notes |
|---|---|---|---|
| **RTSP/RTP over TCP** | ~100–200 ms | Retransmits, no frame loss | Cleanest; use when starting. |
| **RTSP/RTP over UDP** | ~40–100 ms | Drops on congestion | Lowest latency; fine on a dedicated Cat5 between the two boards. |
| **SRT** | ~80–150 ms | ARQ; great on lossy links | Worth it if you ever go WiFi. Overkill on a wire. |
| **Plain TCP H.264 ES** (Option A above) | ~50–100 ms | TCP | Simplest, but non-standard — you lose RTSP's "just point VLC at it" debuggability. |
| MJPEG over HTTP | ~80–150 ms | TCP | Bandwidth-heavy; no hardware decode on Nano. Avoid. |

**Recommendation:** RTSP over TCP for day one (easy to debug, VLC works). Flip to RTSP/UDP once the system is running, if and only if latency measurements say you need to.

### 3c. Bandwidth sanity check

1080p30 H.264 at a reasonable bitrate (6 Mbps) is ~0.75 MB/s. Fast Ethernet (100 Mbps) is ~12 MB/s. You are using **~6%** of a Fast Ethernet link. Network is not a bottleneck at any realistic quality setting.

---

## 4. Video data plane — Nano side

The Nano now has exactly one job in the capture graph: **decode and fan out**. The pipeline becomes:

```
rtspsrc location=rtsp://pi.local:8554/live protocols=tcp
  ! rtph264depay ! h264parse
  ! tee name=t

  t. ! queue ! nvv4l2decoder ! <inference branch, later>

  t. ! queue ! splitmuxsink ...         # recorder (no re-encode)

  t. ! queue ! rtph264pay ! <LAN republish or WebRTC>    # streamer
```

Notes on each branch:

- **Recorder** takes the encoded H.264 straight to `splitmuxsink`. **No decode, no re-encode** — the bytes off the wire are the bytes on disk. This is actually *simpler* than plan1's in-process recorder.
- **Streamer** (LAN re-publish) can likewise be pure passthrough: re-packetize and hand to `gst-rtsp-server` on the Nano's LAN interface, or to `aiortc` for WebRTC. No NVENC involvement at all for this path — you've already paid for encode on the Pi.
- **Inference** is the only consumer that needs decode. `nvv4l2decoder` outputs NVMM frames exactly as `nvarguscamerasrc` would have, so the DeepStream / TensorRT branch from `plan1.md` §4 is unchanged.

**Net effect on the Nano's CPU/GPU budget:** you gave up one NVENC encode (moved to Pi), gained one NVDEC decode (Nano), and everything else is identical. NVDEC on the Nano is cheaper than NVENC was, so the Nano is *less* loaded than in plan1 until inference comes online.

---

## 5. Control plane across two hosts

`plan1.md` assumed everything ran on one box with localhost ZMQ. Now the bus spans two boards.

### Changes from plan1

- **ZMQ still works** — `tcp://pi.local:5556` is as easy as `tcp://127.0.0.1:5556`. The Pi's `sensor-wakeup` PUBs motion events; the Nano `hub` SUBs. The hub's commands to the Pi's camera service go over REQ/REP the same way.
- **MQTT becomes more attractive sooner.** Two-host pub/sub + retained messages + last-will ("is the Pi up?") pays for itself faster than in the single-box plan. Run `mosquitto` on the Nano; both boards and any future browser talk to it.
- **Clock sync matters now.** PIR events on the Pi need timestamps the Nano can compare to frame PTS. Run `chrony` on both; have the Pi sync to the Nano (or both to the LAN router). Sub-10 ms skew is trivial on a wire and more than enough for "clip started N seconds after motion".
- **Service discovery.** Hard-code `pi.local` / `nano.local` in config. mDNS (`avahi-daemon`, on by default on Pi OS and Ubuntu) makes `.local` names resolvable with zero config. Don't build a discovery protocol.

### What the hub now watches

| Signal | Source | Why it matters |
|---|---|---|
| RTSP stream alive | Nano's own `rtspsrc` state | Detects Pi crash / network drop. |
| Pi heartbeat over MQTT (last-will) | Pi | Distinguishes "camera crashed" from "network glitched". |
| PIR event | Pi → MQTT/ZMQ | Same as plan1. |
| Recorder / streamer health | Nano-local | Same as plan1. |

---

## 6. Failure modes this introduces (and how to handle them)

These are new risks you didn't have in the single-box plan. Worth knowing up front.

1. **Pi boot vs Nano boot ordering.** Nano comes up first and tries to open `rtsp://pi.local`, fails forever. Fix: `rtspsrc` has `retry` and `timeout` properties; wrap the camera-pipeline consumer in a GStreamer bus-watch that re-plays the pipeline on EOS/ERROR. `systemd` `Restart=on-failure` on both sides covers the rest.
2. **Network blip mid-recording.** `splitmuxsink` will close the current clip cleanly on EOS; the wrapper restarts and opens a new one. You lose a few seconds, not the file. Acceptable.
3. **Clock skew → wrong clip boundaries.** Covered by `chrony` in §5.
4. **PoE / power.** Two boards = two power supplies = two failure points. If this ships as a real turret, consider a PoE splitter on the Pi so the whole head runs off one Ethernet cable. Out of scope for now, but worth noting.
5. **libcamera version churn.** Pi OS updates have historically broken `libcamera` + `gst-libcamera` compatibility. Pin the Pi to a known-good Pi OS release and don't `apt upgrade` the camera stack casually.
6. **IMX477 tuning files.** The Pi ships tuning JSONs for the HQ camera. If you're using a bare IMX477 module (not the HQ housing), you may need to point `libcamera` at the right tuning file explicitly. Verify day one.

---

## 7. Where does PIR + motion-control live?

Two defensible answers:

**Pi-side (recommended):** PIR, IR LEDs, and pan/tilt motors all live physically at the turret head. Wiring them to the Pi keeps the head self-contained: one Ethernet cable down, everything else on the head. The Pi publishes sensor events and subscribes to motor commands; the Nano never touches GPIO.

**Nano-side:** Nano keeps GPIO as in plan1. You now have two boards with GPIO responsibilities and a longer wiring harness from the Nano up to the head. Simpler code-change from plan1, worse mechanical story.

Go with Pi-side unless there's a reason the Nano has to drive a motor directly (e.g. a realtime control loop from inference → servo that can't tolerate the LAN hop — unlikely at the framerates in play here).

---

## 8. Recommended starting architecture

```
  ┌─────────────────────── Raspberry Pi 4 (turret head) ───────────────────────┐
  │                                                                            │
  │   IMX477 CSI ─► libcamerasrc ─► v4l2h264enc ─► gst-rtsp-server             │
  │                                                    │                       │
  │   PIR GPIO  ─► sensor-wakeup (Python) ─► MQTT PUB "motion"                 │
  │                                                                            │
  │   IR LED GPIO ◄─── MQTT SUB "ir/set"                                       │
  │                                                                            │
  └──────────────────────────────┬─────────────────────────────────────────────┘
                                 │ Ethernet (direct or switch)
                                 │   rtsp://pi.local:8554/live   (H.264)
                                 │   mqtt://nano.local:1883      (control + events)
  ┌──────────────────────────────┴─────────────────────────────────────────────┐
  │                                                                            │
  │   Jetson Nano                                                              │
  │                                                                            │
  │   rtspsrc ─► h264parse ─► tee ─┬─► splitmuxsink (recorder, armed by hub)   │
  │                                 ├─► rtph264pay ─► LAN RTSP / WebRTC        │
  │                                 └─► nvv4l2decoder ─► TensorRT (later)      │
  │                                                                            │
  │   hub (Python) ─► MQTT pub/sub, arms recorder, commands Pi, health checks  │
  │                                                                            │
  └────────────────────────────────────────────────────────────────────────────┘
```

- **One camera owner** — still true, it's just on the Pi now.
- **One encode, many consumers** — still true; the encode moved to the Pi, but recorder and LAN streamer are both passthrough.
- **One decode** — only when inference turns on.
- **Independent restarts** — each host has its own `systemd` tree. A Pi reboot is contained to "stream drops, recorder closes current clip, Nano reconnects".

---

## 9. Build order (replaces `development_steps1.md` steps 0–1)

Only the first two steps change; steps 2–5 in `development_steps1.md` carry over almost verbatim, with "ZMQ" optionally becoming "MQTT" and hostnames becoming cross-host.

### Step 0 — prove the Pi side with no Python

On the Pi:
```
rpicam-vid -t 0 --width 1920 --height 1080 --framerate 30 --inline \
  --codec h264 -o - | \
  gst-launch-1.0 fdsrc ! h264parse ! rtph264pay ! udpsink host=<nano-ip> port=5000
```
On the Nano (or your laptop):
```
gst-launch-1.0 udpsrc port=5000 caps="application/x-rtp,encoding-name=H264,payload=96" \
  ! rtph264depay ! h264parse ! avdec_h264 ! autovideosink
```
If you see video, the **entire** high-risk part of this architecture is validated. Total time: ~1 hour including Pi flash.

**Done when:** live video from the IMX477 appears on the Nano (or any viewer) over the LAN.

### Step 1 — RTSP server on Pi, systemd unit

Replace the `udpsink` one-liner with a `gst-rtsp-server` Python script; run it under `systemd`. Now the Nano consumes a proper RTSP URL, which is the stable contract the rest of the system depends on.

**Done when:** VLC on your laptop opens `rtsp://pi.local:8554/live` after a fresh Pi reboot, no manual commands.

### Step 2 onward — unchanged from `development_steps1.md`

- Step 2 (`sensor-wakeup`): now runs on the Pi. PUBs over MQTT (or ZMQ) to the Nano.
- Step 3 (recording): `splitmuxsink` branch on the Nano, armed by PIR events from the Pi. No re-encode.
- Step 4 (streamer): Nano re-publishes the Pi's feed to the LAN / browser. Already half-built from step 1.
- Step 5 (hub): same as plan1, just knows about two hosts.

---

## 10. What this plan gives up vs `plan1.md`

- **One more box to manage.** Two OSes, two SD cards, two `apt upgrade` schedules.
- **Network is now critical path.** Wired Ethernet reduces this to near-zero, but it's a new failure class.
- **Small added latency** — tens of ms at most on a wire. Still well under the 150 ms WebRTC target.
- **Physical packaging harder** — two boards, two power rails, more cables.

## 11. What this plan gives you

- **A camera that actually works.** The single biggest risk in plan1 is eliminated on day one.
- **Simpler Nano code.** No Argus, no `nvarguscamerasrc` tuning, no IMX477 driver fights. The Nano reads an RTSP URL — that's a solved problem with five working examples in every GStreamer tutorial.
- **Cheaper recording path.** Passthrough MP4 mux, no NVENC involvement.
- **Clean upgrade path.** Swap the Pi for a better camera node later (another Pi, an IP camera, a second angle) without touching the Nano.
- **Stable interface contract.** Everything downstream of `rtsp://pi.local:8554/live` is identical to plan1, so most of plan1's thinking (service decomposition, control bus, inference hook-in, recording strategy) carries over unchanged.

---

## 12. Things to decide next

- Which Pi model is actually on hand — Pi 4 strongly preferred (§2).
- Wired vs shared-switch vs direct-cable between Pi and Nano (§2).
- ZMQ vs MQTT from day one. Cross-host makes MQTT worth adopting immediately (§5).
- PIR / IR-LED / motor wiring: on the Pi (recommended) or stay on the Nano (§7).
- Power: two bricks, or PoE + splitter to make the head a single-cable unit (§6.4).
- Whether to keep trying the Nano IMX477 path in parallel, or commit fully to the split-host design. Recommend committing — the sunk cost of debugging Argus is not going to get smaller.
