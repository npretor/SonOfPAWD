# camera-pipeline → Jetson Nano migration plan

First step of consolidating all services onto the Nano. Scope here is
camera-pipeline only; sensor-wakeup, ir-led and motion-ctrl stay on the Pi
for now (see §8).

## 1. Camera compatibility — verified 2026-11-04

| Check | Result |
|---|---|
| Sensor | **Sony IMX219** (`vi-output, imx219 8-0010`), stock JetPack driver `imx219_v2.0.6` |
| Port | CAM1 (i2c bus 8). CAM0 probe fails (`-121`, nothing attached) — expected. Argus `sensor-id=0` (only enumerated sensor) |
| L4T | R32.6.1 (JetPack 4.6) |
| Modes | 0: 3264x2464@21, 1: 3264x1848@28, **2: 1920x1080@30**, 3: 1640x1232@30, 4/5: 1280x720@60 |
| Capture + HW encode | `nvarguscamerasrc sensor-id=0 → 1080p30 → nvv4l2h264enc 6 Mbps`: 150 frames in 4.99 s, H.264 Constrained Baseline (WebRTC-friendly) |
| Publish to MediaMTX | `rtspclientsink` (GStreamer 1.14.5) → throwaway MediaMTX 1.8.4 on :18554: accepted as publisher |
| Consume | Inference's exact decode chain (`rtspsrc → nvv4l2decoder → nvvidconv 640x384`) read it back at **30.2 fps** |
| Cost | ~11% CPU for the gst process, ~17% for nvargus-daemon, ~190 MB RSS in nvargus-daemon |

Conclusion: compatible, with no extra drivers, device-tree changes, or
packages needed. The current `CAMERA_*` settings (1920x1080, 30 fps,
6 Mbps) work unchanged.

Not present, and **not needed** with the design below: the `GstRtspServer`
GI typelib (`gir1.2-gst-rtsp-server-1.0`) that the Pi service relies on.

## 2. Current vs. target architecture

Current:
```
Pi:   IMX477 → rpicam-vid → fdsrc → gst-rtsp-server :8554/live
Nano: MediaMTX live ← pulls rtsp://testcam-64bit.local:8554/live
        → WebRTC (UI), → rtsp://127.0.0.1:8554/live (inference)
      recorder ← pulls DIRECTLY from the Pi (common RTSP_URL)
```

Target:
```
Nano: IMX219 → nvarguscamerasrc → nvv4l2h264enc → rtspclientsink
        → MediaMTX :8554/live (publisher path)
            → WebRTC (UI), inference, recorder  (all via 127.0.0.1)
```

Why push into MediaMTX instead of running our own RTSP server:
- MediaMTX already owns :8554 on the Nano, so a second RTSP server would need another port and a re-pull.
- It drops the need for the GstRtspServer Python bindings.
- There's a single Argus consumer, and every reader fans out from MediaMTX.
- It fixes the known "Pi camera-pipeline stuck after Nano reboot" issue.
  The publisher is local, and if MediaMTX goes away the pipeline errors out,
  the process exits, and systemd restarts it.

Alternative considered: MediaMTX `runOnInit` running `gst-launch-1.0`
directly. It's simpler, but we'd lose the MQTT health/status publishing and
the per-service journal/UI log entry. Rejected.

## 3. Code changes

### 3.1 New service: `nano/src/camera_pipeline/main.py` — **built**
Port of the Pi service. It keeps the MQTT health/status publishing and signal
handling, but replaces the rpicam subprocess and RTSP server with a single
`Gst.parse_launch`:

```
nvarguscamerasrc name=src sensor-id={CAMERA_SENSOR_ID} sensor-mode={CAMERA_SENSOR_MODE}
  ! video/x-raw(memory:NVMM),width={W},height={H},framerate={FPS}/1
  ! queue max-size-buffers=2 leaky=downstream      # decouple camera from publish back-pressure
  [! nvvidconv flip-method={CAMERA_FLIP}]          # only if CAMERA_FLIP != 0
  ! nvv4l2h264enc bitrate={BITRATE} control-rate=1 iframeinterval={FPS}
                  insert-sps-pps=true preset-level=1
  ! h264parse config-interval=1
  ! rtspclientsink location=rtsp://127.0.0.1:{RTSP_PORT}{RTSP_PATH} protocols=tcp
```

- **Waits for MediaMTX** (TCP probe of :8554) before opening the camera.
  While MediaMTX is down the service sits in this wait instead of exiting,
  so an outage doesn't cause a rapid restart loop.
- **Two frame-flow watchdogs** (pad probes):
  - Camera side: no frames within 10 s of start, or a gap over 3 s, exits
    with code 3.
  - Encoder output side: camera fine but no output for 5 s exits with code 4.
  - The leaky queue keeps a blocked publisher from stalling the camera, which
    is what lets the two watchdogs tell the failures apart.
- **Bus errors are classified by source element**: errors from the camera
  exit 3, errors from `rtspclientsink` exit 4, anything else exits 1. An
  unrequested EOS exits 3.
- **Clean stop on SIGTERM**: sends EOS and waits up to 2 s, then sets the
  pipeline to NULL and exits 0. If teardown hangs more than 5 s (Argus
  wedged), it force-exits with code 3.
- **Healthy marker**: after 30 s of streaming it writes `/run/camera-pipeline/healthy`,
  which resets the recovery helper's failure counter (§3.6).
- The status payload keeps the old keys, plus `encoder: nvv4l2h264enc`,
  `sensor: imx219` and `sensor_mode`.
- `--rtsp-url` and `--state-dir` flags exist for testing against a
  throwaway MediaMTX.
- Runs on host Python 3.6 with system `python3-gi`; no container.

Exit-code contract: 0 = clean, 1 = other, **3 = camera/Argus**, 4 = publish (MediaMTX).

Tested 2026-11-04 against a throwaway MediaMTX on :18554:

| Test | Result |
|---|---|
| Started before MediaMTX existed | Waited, then streamed |
| Consumer via inference's decode chain | 30.14 fps, 0 dropped |
| SIGTERM | Exit 0; Argus "Done Success" |
| MediaMTX killed mid-stream | Exit 4; Argus released cleanly |
| Camera held by another process | Exit 3. Teardown hung and was force-exited; the next run worked without a daemon restart |
| 30 s run | Healthy marker written |

### 3.2 `common/config/hosts.py`
- `RTSP_URL` → `rtsp://127.0.0.1:8554/live`. The recorder then reads from
  MediaMTX instead of reaching out to the Pi.
- Add `CAMERA_SENSOR_ID = 0`, `CAMERA_SENSOR_MODE = 2` and `CAMERA_FLIP = 0`.
  Always pin the mode: per `scripts/test_camera.sh`, letting Argus pick a
  full-resolution mode exhausted NVMM and panicked the Nano on the IMX477.
- Delete `NANO_RTSP_PORT` / `NANO_RTSP_URL` (no references).
- Keep `PI_HOST`, which the remaining Pi services still use.

### 3.3 `nano/mediamtx/mediamtx.yml`
- `live:` → `source: publisher` (instead of pulling from the Pi).
- Re-install it to `/opt/mediamtx/mediamtx.yml`. **Note: the deployed copy
  has already drifted** and is missing the `live_low` path. Install it with
  `sudo install -m 644 nano/mediamtx/mediamtx.yml /opt/mediamtx/`, then
  restart mediamtx.

### 3.4 `nano/systemd/system/camera-pipeline.service` — **built** (system unit)
This is a **system** unit running `User=dlinano`, unlike the other Nano
services, which are user units. That's what lets a root-privileged cleanup
step run after it stops, and lets it be ordered after `nvargus-daemon` at boot.
The account itself gets no standing extra permissions.
- `After=` and `Wants=nvargus-daemon.service`.
- `Restart=always`, `RestartSec=3`, `StartLimitIntervalSec=0`. systemd never
  gives up restarting; escalation is the helper's job, because only it can
  tell a camera failure from a MediaMTX outage.
- `ExecStopPost=+/usr/local/lib/turret/camera-recover.sh` (`+` = runs as root).
- `RuntimeDirectory=camera-pipeline` with `RuntimeDirectoryPreserve=yes`
  holds the healthy marker.
- `OOMScoreAdjust=-500`: when memory runs out, the system kills something else
  first. Being force-killed mid-capture is a classic way to wedge Argus.
- `TimeoutStopSec=60`, so systemd doesn't force-kill a clean shutdown or the
  helper's back-off.
- It can't be ordered after `mediamtx`, because a system unit can't depend on
  a user unit. The MediaMTX wait (§3.1) covers that.
- `@USER@` and `@REPO@` are placeholders, filled in by
  `make -C deploy install-camera-unit`.
- All directives used are supported by this Nano's systemd 237.

### 3.5 Other edits — **done**
- `deploy/Makefile`:
  - `camera-pipeline` is removed from `PI_SERVICES`, and `mediamtx` is added
    to `NANO_SERVICES`.
  - New targets: `install-camera-unit`, `restart-camera`, `stop-camera`,
    `logs-camera` and `status-camera`. Installing and restarting need `sudo`.
- `nano/src/ui/backend/logs.py`: `"camera-pipeline": "system"`, so the UI
  reads it with `journalctl -u`.

### 3.6 Recovery: `nano/scripts/camera_recover.sh` — **built** (options 2 + 3)
Runs as root after every stop and reads `SERVICE_RESULT`, `EXIT_CODE` and
`EXIT_STATUS` from systemd:

| How it stopped | Action |
|---|---|
| Clean (`success`), exit 1 or exit 4 | Nothing; systemd restarts the service |
| Exit 3, signal (OOM kill, SIGKILL), core dump (segfault), stop timeout | Treated as Argus possibly wedged: `systemctl --no-block restart nvargus-daemon` and bump the consecutive-failure counter |
| 5th consecutive Argus-suspect failure | `systemctl --no-block reboot`, but **at most once per hour** |
| Still failing after a camera reboot in the past hour | No reboot. Restart the daemon with a 30 s back-off and log "check the sensor/ribbon", so an unplugged camera doesn't reboot-loop the turret |

- The healthy marker (30 s of good streaming) resets the counter.
- The counter lives in `/run`, so it clears on boot. The last-reboot timestamp
  persists in `/var/lib/turret-camera-recover/`.
- `--no-block` matters: a blocking restart of nvargus-daemon from inside our
  own stop job can deadlock, because we're ordered after it.
- Security:
  - The helper is installed root-owned at `/usr/local/lib/turret/`, never run
    from the user-writable repo; otherwise the user could edit code that runs
    as root.
  - It writes only into root-owned directories, and only tests and unlinks
    the marker in the user-owned directory, so a symlink planted there can't
    redirect a root write.
  - Re-run `install-camera-unit` after editing the helper.
- Dry-run tested (`DRY_RUN=1`) through every branch above, including a
  planted symlink.
- Tunable via env in the unit: `MAX_FAILS`, `REBOOT_MIN_INTERVAL_S` and
  `BACKOFF_S`.
- `scripts/test_camera.sh`, `scripts/test_rtsp.sh`: change IMX477 to IMX219,
  `sensor-id=1` to `0`, and use mode 2 at 1920x1080@30. They currently assume
  the old camera.
- Health: the topic changes from `turret/pi/health` to `turret/nano/health`
  automatically (host tag comes from the hostname), so the hub key becomes
  `nano/camera-pipeline`. The UI has no hard-coded camera key. A stale
  `pi/camera-pipeline` entry may sit in hub state until the hub restarts.

## 4. Cutover sequence

1. ~~Build 3.1–3.6 and prototype against a test MediaMTX~~ — done 2026-11-04.
2. Stop the Pi camera service for good:
   `ssh nathan@testcam-64bit.local "systemctl --user disable --now camera-pipeline"`.
3. Install the new mediamtx.yml and restart mediamtx:
   `sudo install -m 644 nano/mediamtx/mediamtx.yml /opt/mediamtx/ && systemctl --user restart mediamtx`.
4. `make -C deploy install-camera-unit && sudo systemctl start camera-pipeline`,
   then `make -C deploy install-nano-units` so the user units are current.
5. `systemctl --user restart recorder inference`. The recorder now reads
   `127.0.0.1:8554` (new `RTSP_URL`) instead of the Pi.
6. Verify:
   - The MediaMTX log shows `is publishing to path 'live'`.
   - The UI shows live video and the bounding-box overlay lines up.
   - Recorder arm → clip in `/var/lib/turret/clips`.
   - Inference logs detections.
7. Reboot tests:
   - Reboot the Nano and confirm video returns with no manual steps.
     This is the regression the Pi setup had.
   - `systemctl --user restart mediamtx` and confirm camera-pipeline recovers
     on its own.
8. After a few days stable, delete `pi/src/camera_pipeline/`,
   `pi/systemd/camera-pipeline.service` and `pi/scripts/{start_video_rpicam,test_pipeline_rtsp}.sh`.
   Drop `rpicam-apps` and the GStreamer RTSP packages from `pi/scripts/host_prep.sh`.
   Update the memory note about Pi reboot recovery.

Rollback: re-enable the Pi unit and revert `mediamtx.yml` `source:`. The two
can't run together, because `source: publisher` and `source: rtsp://…` are
mutually exclusive.

## 5. Hardware and optics issues that affect other services

These aren't code issues, but they change behaviour downstream. Decide them
before cutover:

- **Field of view / aim calibration.** *Decided: stay at 1920x1080 (mode 2).*
  That mode is a center crop of the IMX219 sensor, so the field of view differs
  from the IMX477 setup. Any pixel→angle mapping in
  `pi/src/motion_ctrl/calibration.py` and hub lock-on needs re-calibrating.
- **Boresight.** The camera now ribbons to the Nano rather than the Pi.
  Confirm cable length and routing still let the camera move with (or stay
  fixed relative to) the turret as before.
- **IR night mode.** *Confirmed: NoIR IMX219*, so the `ir-led` illuminator works.
  The trade-off is that daytime colour will look washed out or pinkish, because
  nothing filters out infrared light. Check that inference accuracy holds up
  on daytime footage; if it doesn't, tune white balance with nvarguscamerasrc
  `wbmode`.
- **Orientation.** If it's mounted rotated, set `CAMERA_FLIP` (nvvidconv `flip-method`).

## 6. Risks

| Risk | Mitigation |
|---|---|
| nvargus-daemon wedges after an unclean pipeline exit (common on JetPack 4.x) | Clean EOS on stop (§3.1). OOM protection and a generous stop timeout (§3.4). Root helper restarts the daemon, then reboots as a last resort (§3.6). Remaining gap: once the hourly reboot is spent, recovery relies on daemon restarts every ~30 s. A hardware-level hang (VI/CSI) that only a reboot clears stays down until the next allowed reboot; the helper logs why |
| Electrical noise on the CSI ribbon from the servo/pump wiring | Shielded ribbon, routed away from motor and pump wiring. The helper's back-off and logging make it diagnosable |
| Memory pressure: 4 GB shared with TensorRT inference, mediamtx, UI, recorder | Pinned mode 2 keeps NVMM small (~190 MB in Argus measured). Watch `tegrastats` during a soak test with inference running |
| Thermal: camera + NVENC + NVDEC + TensorRT all on one SoC | Soak test 1 h under MAXN while watching `tegrastats` temps. The hub telemetry already reports Nano stats |
| Network latency removed, but the encoder now competes with inference for the memory bus | Measure glass-to-detection latency before and after. Expect it to improve, since there's no Wi-Fi hop |

## 7. Effort
Roughly half a day of code (the new service is about 150 lines, plus config
and unit edits), plus the calibration work from §5.

## 8. Later phases (outline only)
- **ir-led, sensor-wakeup**: PIR and the IR LED move to the Nano's 40-pin
  header using `Jetson.GPIO` (apt `python3-jetson-gpio`) instead of
  `gpiozero`. BCM pin numbers don't carry over; remap them in `hosts.py`.
  The Nano's header is 3.3 V like the Pi's.
- **motion-ctrl**: the Arduino is USB serial, so the move is mostly the
  udev rule plus `pyserial`. Move the servo PSU enable pin (GPIO 24) with it.
- After that, the Pi can be retired. Remove `PI_HOST`, the `*-pi` Makefile
  targets, and the `"pi"` kind in `logs.py`.
