# Plan 1 — Architecture Brainstorm (SonOfPAWD)

Target hardware: Jetson Nano (4 CPU cores, 4 GB shared RAM/VRAM), IMX477 CSI camera, HC-SR501 PIR on GPIO, GPIO-driven IR LEDs. Python-first, hardware acceleration preferred.

The Nano shapes most decisions:
- It has a real **NVENC** H.264/H.265 encoder reachable through GStreamer (`nvv4l2h264enc`, `nvv4l2h265enc`) and the `nvarguscamerasrc` element for the IMX477.
- CPU is weak; **anything that pickles or memcopies a full-res frame in Python will burn the budget fast**. Keep frames inside GStreamer / NVMM buffers as long as possible.
- TensorRT is the inference path; DeepStream is the "all-GStreamer" framework that ties capture → infer → encode together with zero-copy NVMM buffers.

---

## 1. Service decomposition

Mapping the seven parts in `brainstorm.md` to long-running services:

| Service | Responsibility | Lifetime |
|---|---|---|
| `sensor-wakeup` | Polls PIR (and later thermal); publishes motion events; controls IR LED GPIO. | Always on, very low CPU. |
| `camera-pipeline` | Owns the IMX477 + GStreamer graph (capture → tee → encode). Single owner of the camera. | Always on (or on-demand if power matters). |
| `streamer` | Exposes live video over the network (RTSP / WebRTC / MJPEG). Reads from the pipeline, never opens the camera itself. | On-demand, controlled by hub. |
| `recorder` | Writes motion-triggered clips to disk. Also reads from the pipeline. | Always on, but only writes when armed. |
| `inference` (later) | TensorRT classification on frames. Reads from the pipeline. | On-demand. |
| `motion-control` (later) | Pan/tilt + auxiliary GPIO. | Always on. |
| `hub` | Brain. Subscribes to events, decides what should be running, sends commands. | Always on. |

Run them as `systemd --user` units — gives you restart, logging via `journalctl`, dependency ordering, and per-service resource limits without inventing a process supervisor. The hub does logical control; systemd does process control.

**Critical constraint:** the IMX477 (via Argus) does not like multiple openers. Exactly **one** process should hold the camera. Other services consume from it via the IPC layer below — do not let `streamer` and `recorder` each open the camera.

---

## 2. Control-plane messaging (commands + events)

This is the small-message bus — "PIR fired", "start streaming", "service X is healthy". Three realistic options:

### Option A — ZeroMQ (your initial pick)
- Pattern mix: **PUB/SUB** for events (sensor → hub, service-status → hub), **REQ/REP** or **ROUTER/DEALER** for commands (hub → service).
- Pros: brokerless, low latency, pure Python via `pyzmq`, trivial to deploy on a single Nano, no extra daemon.
- Cons: no persistence, no last-will, no auth. PUB/SUB is fire-and-forget — if a subscriber isn't connected it misses the event. You write your own discovery (hard-coded ports work fine on one host).
- Verdict: **good default for this project.**

### Option B — MQTT (mosquitto broker)
- Pros: retained messages (subscribers get last known state on connect), QoS levels, last-will (great for "is the streamer alive?"), and trivial to bridge to a future website / phone client. `paho-mqtt` is the standard Python client.
- Cons: extra broker process; slightly higher latency than ZMQ on localhost (still sub-ms in practice).
- Verdict: **best choice if you want the future website / external clients to subscribe to the same topics the services already use.** The retained-message + last-will combo is genuinely nice for status display.

### Option C — Redis (pub/sub + Streams)
- Pros: one daemon gives you pub/sub, durable streams (consumer groups → great for "record every PIR event even if recorder restarts"), and a key-value store for shared state.
- Cons: more memory than mosquitto, overkill if you don't need persistence.

### Recommendation
- **Start with ZeroMQ** for sensor events and command RPC — minimum moving parts, fastest to get running.
- **Adopt MQTT later** when the website is in scope; it pays for itself the moment a browser needs to subscribe to status.
- Skip ROS2 unless you eventually want its full ecosystem (nav stack, tf, etc.). For seven services on one box it's heavy.

Keep **video out of the control bus**. The control bus is for JSON-ish small messages only.

---

## 3. Video data plane — capture and fan-out

This is the hot path. Three layers to decide:

### 3a. Capture
Use one GStreamer pipeline owned by `camera-pipeline`:

```
nvarguscamerasrc → nvvidconv → tee → [encode → muxer → fan-out branches]
```

`nvarguscamerasrc` keeps frames in NVMM (GPU/ISP) memory; `tee` lets you feed multiple consumers from one capture. Encode **once** with `nvv4l2h264enc` and reuse the H.264 bitstream for streaming, recording, and (if you want) inference preprocessing.

### 3b. Cross-process fan-out (camera-pipeline → other services)

Three realistic mechanisms, in order of preference:

1. **Don't fan out — collapse the pipeline.** The cheapest frame is the one you never copy across a process boundary. If `streamer` and `recorder` are always co-resident with `camera-pipeline`, build them as **branches of a single GStreamer pipeline** in one process. The "service" boundary becomes a Python module, not an OS process. You lose independent restart but gain massive performance.
   - This is what DeepStream does and is the Nano-native answer.

2. **GStreamer `shmsink` / `shmsrc`.** Lets a separate process attach to the same buffer pool over POSIX shared memory. Zero-copy on the bytes, small per-buffer metadata over a Unix socket. Works across processes; survives consumer restarts.

3. **RTSP / RTP loopback.** `camera-pipeline` runs an RTSP server (`gst-rtsp-server` has Python bindings) on `127.0.0.1`; every consumer is just an RTSP client. Slightly more overhead (RTP packetization) but the cleanest interface — your `streamer` service becomes "re-publish the loopback RTSP to the network" and `recorder` becomes "pull RTSP, write to mp4". Easy to debug with `gst-launch` or VLC.

**ImageZMQ:** workable for prototyping but it pickles numpy arrays — full uncompressed frames over TCP. On a Nano at 1080p this will dominate your CPU. Avoid for the production path; fine for a one-off debug viewer.

### 3c. Network streaming (consumer-facing)

| Transport | Latency | Browser-native? | Effort | Notes |
|---|---|---|---|---|
| **RTSP (H.264)** | ~150–300 ms | No (needs WebRTC gateway or `<video>` + HLS bridge) | Low | Best dev-loop choice; VLC/ffplay just work. |
| **MJPEG over HTTP** | ~100–200 ms | Yes (`<img>`) | Lowest | High bandwidth, no hardware decode in browser, but dead simple. |
| **WebRTC (H.264)** | **<150 ms** | Yes natively | High | Lowest latency to a browser. `aiortc` is the Python implementation; can pull frames from GStreamer via `appsink`. The right answer when the website lands. |
| **HLS / DASH** | 2–10 s | Yes natively | Medium | Don't use — latency is wrong for a turret. |

**Recommendation:** RTSP for now (debuggable, hardware-encoded), WebRTC when you build the website. The capture/encode pipeline doesn't change between the two — only the "publisher" leaf swaps.

### 3d. Recording (motion-triggered clips)

Use `splitmuxsink` after the H.264 encoder. It rotates files by duration or size and writes valid MP4 atoms even if you SIGTERM mid-clip. The hub arms/disarms recording over the control bus; `recorder` decides clip boundaries from PIR-event timestamps + a configurable trailing window (e.g. 10 s after last event). No re-encode — same NVENC bitstream is muxed straight to disk.

---

## 4. Inference path (later, but plan for it now)

- Capture pipeline already has frames in NVMM. Add a `tee` branch that downscales (`nvvidconv`) to model input size and feeds an `appsink` whose Python callback hands the buffer to TensorRT.
- TensorRT in Python via `tensorrt` + `pycuda` (or NVIDIA's `torch2trt` if you start from PyTorch).
- For "I want this to just work end-to-end with zero-copy", **DeepStream** (`nvinfer` element) is the path of least resistance on Jetson — it composes capture → infer → overlay → encode entirely in GStreamer with NVMM buffers. Cost: it's a heavier framework to learn, and you give up some flexibility.
- Inference results published as small JSON messages on the control bus (back to ZMQ/MQTT). Keep frames out of the control bus.

---

## 5. Recommended starting architecture

```
                  ┌───────────────────────────┐
   PIR GPIO ─────►│   sensor-wakeup (Python)  │──── PUB "motion" ──┐
                  └───────────────────────────┘                    │
                                                                   ▼
   IMX477 CSI                                              ┌────────────┐
       │                                                   │    hub     │
       ▼                                                   │  (Python)  │
  ┌─────────────────────────────────────────┐              └────────────┘
  │ camera-pipeline (Python + GStreamer)    │                  │  │  │
  │  nvarguscamerasrc → tee → nvv4l2h264enc │     REQ/REP cmds │  │  │
  │     ├──► RTSP server (loopback)         │◄─────────────────┘  │  │
  │     └──► splitmuxsink (only when armed) │◄────────────────────┘  │
  └─────────────────────────────────────────┘                        │
                │ rtsp://127.0.0.1:8554/live                         │
                ├──────────────────► streamer (re-publish to LAN)◄───┤
                └──────────────────► (later) inference / website ◄───┘
```

- **Control bus:** ZeroMQ (PUB/SUB for events, REQ/REP for commands). Add MQTT later when the website needs to subscribe.
- **Video bus:** loopback RTSP from a single GStreamer pipeline that owns the camera. Hardware-encoded once, consumed many times.
- **Process supervision:** `systemd --user` units, one per service.
- **Recording:** `splitmuxsink` branch of the same pipeline, gated by an "armed" flag the hub flips.
- **Streaming to LAN:** `streamer` service re-publishes loopback RTSP. Swap to WebRTC (`aiortc`) when the website ships.
- **Inference (later):** add a `tee` + `appsink` branch into TensorRT, or migrate to DeepStream if you want the whole graph GPU-resident.

### What this buys you
- One camera owner → no Argus contention.
- One NVENC encode → CPU stays cold even with multiple consumers.
- Services are independently restartable (systemd) and independently testable (each is just an RTSP/ZMQ client).
- Clear upgrade path: swap RTSP→WebRTC, add DeepStream, add MQTT — without touching the other services.

### Things to decide next
- Resolution / framerate / bitrate target (drives NVENC config and disk usage).
- LAN-only or internet-exposed? (Decides whether you need TLS + auth on RTSP/MQTT from day one.)
- Where recordings live — local SD, USB SSD, or NFS — and the retention policy.
- Whether `streamer`/`recorder` stay separate processes or collapse into the `camera-pipeline` process for the v1 (recommended for the Nano).
