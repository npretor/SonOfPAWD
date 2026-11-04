# Development Steps 1 — Build Order for SonOfPAWD

Companion to `plan1.md`. Answers the question "which piece do I build first?"

## The principle

Build in the order of **most uncertainty first**. The camera pipeline is the only piece where the Jetson Nano hardware can say "no" in a way that invalidates the architecture. Everything else — ZMQ, GPIO polling, systemd units, a controller — is well-trodden Python that will work however you write it. De-risk the GStreamer / NVENC / Argus stack on day one; the rest is assembly.

## Why not the hub first

The hub coordinates services. With nothing to coordinate, you'd be writing it against imagined interfaces, then rewriting once reality hits. Hubs are easy to write *after* the pieces exist and hard to get right *before*. Build it last.

## Why not the streamer first

The streamer is a thin wrapper around the camera pipeline. If the pipeline works, the streamer is ~50 lines. If the pipeline doesn't work on your hardware, the streamer can't exist. So the streamer isn't really a separate first step — it falls out of step 1.

---

## Step 0 — `gst-launch` one-liner, no Python

Prove on the actual Nano that capture + NVENC + loopback RTSP works end-to-end. Something like:

```bash
gst-launch-1.0 nvarguscamerasrc ! 'video/x-raw(memory:NVMM),width=1920,height=1080,framerate=30/1' \
  ! nvv4l2h264enc iframeinterval=30 ! h264parse ! rtph264pay name=pay0 pt=96
```

…wrapped in `gst-rtsp-server`'s `test-launch` binary. Open it from VLC on your laptop.

**This is the highest-risk, highest-information step in the whole project.** If `nvarguscamerasrc` doesn't see the IMX477, or NVENC chokes, you find out in an hour, not week three.

**Done when:** VLC on your laptop pulls a live stream from the Nano over the LAN.

## Step 1 — Python `camera-pipeline` service

Same pipeline, but built with `Gst.parse_launch` or programmatically with `Gst.ElementFactory`, hosted by `gst-rtsp-server` Python bindings. Run as a `systemd --user` unit. Still no PIR, no hub.

**Done when:** `systemctl --user start camera-pipeline` and VLC can pull the stream. Service restarts cleanly on failure.

## Step 2 — `sensor-wakeup` service

Independent of step 1, so you could even do this in parallel. PIR on GPIO, prints events, then publishes them on a ZMQ PUB socket. Trivial, but it's the other half of the system and gives you motion events to look at.

**Done when:** A separate `zmq SUB` test script prints `motion` events as you wave at the sensor.

## Step 3 — recording branch

Add `tee → valve → splitmuxsink` to the camera pipeline. Drive the valve from a small Python loop that subscribes to the PIR ZMQ topic (see `plan1.md` §3d for the valve + timer + `split-after` pattern).

At this point you have a **working motion-triggered DVR with no hub at all** — the recorder subscribes directly to the sensor.

**Done when:** Waving at the sensor produces a clip on disk that ends ~10s after the last motion event and plays cleanly in VLC.

## Step 4 — streamer service

Split it out from the camera-pipeline process if/when you actually want independent restart. Until then, the loopback RTSP you built in step 1 already *is* your streamer.

**Done when:** A separate `streamer` systemd unit re-publishes the loopback RTSP to the LAN, and you can `systemctl restart` it without disturbing the camera-pipeline process.

## Step 5 — hub

Now there are real services with real states to coordinate (arm/disarm recording, start/stop streaming, health checks). The hub's interfaces write themselves because you've already discovered what the services need.

**Done when:** Hub can arm/disarm recording over ZMQ REQ/REP, knows which services are alive (heartbeat or last-message timestamp), and gracefully handles a service crash + restart.

---

## What comes after

Once the five steps above are solid, the "later" items from `brainstorm.md` slot in:

- **Inference** — add a `tee` + `appsink` branch into TensorRT, or migrate to DeepStream.
- **Motion control** — new service, GPIO + stepper/servo driver, controlled by hub.
- **Website** — at this point the case for adding MQTT alongside ZMQ becomes concrete (browser clients subscribing to status), per `plan1.md` §2.
