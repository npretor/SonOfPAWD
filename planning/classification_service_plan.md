# Classification Service Plan

Concrete plan in response to `classification_service_brainstorm.md`. Pins down
the service shape, the model family, the frame-tap, and the deploy story so
implementation can start without re-litigating any of it.

The brainstorm uses "classification" but what the system actually needs —
based on the contract `motor_control_plan.md` Phase 2 already locked in — is
**object detection**. See §2 for the difference and why it matters here. For
the rest of this doc, the service is named **inference** (matches the
existing `nano/src/inference/` stub, the `turret/inference/...` topics, and
the `infer-control` mode-side-effects).

---

## 1. Locked decisions

| # | Decision |
|---|---|
| 1 | The service does **object detection**, not whole-image classification. We need bounding boxes to drive the turret centroid; a class label alone is useless for aim. |
| 2 | Runs on the **Nano**, not the Pi. The Pi has no GPU; the Nano has the only TensorRT-capable hardware on the system. |
| 3 | Frame source is the **Nano's local MediaMTX RTSP egress** (`rtsp://localhost:8554/live`), not the Pi directly. MediaMTX already re-publishes the Pi feed; tapping that loopback avoids racing the Pi's RTSP server with the UI. |
| 4 | Single-process Python service (`nano/src/inference/`) following the same shape as `pi/src/motion_ctrl/` — MQTT subscriptions, paho callbacks, signal-driven shutdown, health publish. |
| 5 | Model family: **YOLO-class single-stage detectors**, exported to ONNX, then compiled to a TensorRT FP16 engine on the device. Default is **YOLOv8n** for the v1 cat-only build. Other families (RT-DETR, YOLO-NAS) are listed in §4 but not the v1 path. |
| 6 | Classes are filtered post-NMS by an allow-list pulled from `turret/hub/config.target_class` plus a small fixed set. The hub still owns "which class is the target"; the inference service just publishes everything that survives NMS in the allow-list. |
| 7 | Inference cadence is **frame-skipped to ~10 FPS**, not full stream rate. Phase 4 lock-on already assumes ~10 Hz; running detection at 25–30 Hz wastes power and clock. |
| 8 | TensorRT engines live at `/var/lib/turret/models/<model>.engine`. Engines are GPU-arch-specific, so they are **built on the Nano** during `host_prep.sh`, not committed to git. ONNX files are committed (small, portable). |

---

## 2. Classification vs detection — short answer

| | Classification | Detection |
|---|---|---|
| Input | one image | one image |
| Output | one label (or top-k) per image | a list of `{class, score, bbox}` per image |
| For the turret | tells you "there's a cat in frame" | tells you "there's a cat at pixel (cx, cy)" |
| What the hub already consumes | nothing | `{detections: [{class, score, bbox}], ts}` (Phase 2 + multi-detection update) |

A classifier alone cannot drive aim. We could bolt a classifier onto a hand-
rolled saliency/motion crop, but every modern detector already gives both
"what" and "where" in one pass for less compute than that, so there's no
reason to. The brainstorm's references to "Yolo" already are detectors —
the term "classification" was loose.

---

## 3. TensorRT — what it cares about

There is no fixed model-size requirement. TRT will compile any supported
graph; the practical limits on this hardware are:

1. **Device memory.** Jetson Nano 4GB shares system RAM with the GPU. An
   engine + workspace + frame buffers + JetPack baseline (~1 GB) leaves
   roughly 2 GB for the model. Anything ≤ 50 MB ONNX FP16 is comfortable;
   100+ MB starts crowding.
2. **Supported ops.** The TRT version on the device determines what ONNX
   opsets and ops are convertible. Newer detectors (e.g. RT-DETR) may need
   plugin builds. **YOLOv5n / YOLOv8n / YOLOv4-tiny all convert cleanly on
   TRT 8.x** with the export tooling Ultralytics ships — this is the boring
   path and we should take it.
3. **Precision.** FP16 is the sweet spot on Maxwell/Ampere Jetsons:
   ~2× throughput vs FP32, accuracy loss negligible for our targets. INT8
   is faster still but needs a calibration pass — defer to phase 4.
4. **Workspace.** TRT wants ≥256 MB workspace at build time. Set explicitly;
   the default on JetPack 4.x is too small for some YOLO ops.

There is **no minimum size**. There is only a maximum dictated by what
fits and stays interactive.

---

## 4. Model options

What we ship, ranked for our deployment. All numbers are FP16 on Jetson
Nano 4GB at the input resolution noted; trim ~20% for thermal-throttled
periods.

| Model | mAP (COCO) | ~FPS @ 320 | Notes |
|---|---|---|---|
| **YOLOv8n** | 37.3 | 22–28 | Default. Latest small YOLO, good ecosystem, ONNX export is one CLI line. |
| YOLOv5n | 28.4 | 28–34 | Older, faster, slightly worse accuracy. Backup if v8 export gives us trouble on this JetPack. |
| YOLOv4-tiny | 22.0 | 35–45 | Fastest. Drop here only if FPS becomes the constraint. |
| YOLO-NAS S | 47.5 | 8–12 | Best accuracy in this size class. Probably too slow on Nano 4GB at acceptable resolution. |
| MobileNet-SSD v2 | ~22 | 30–35 | Old reliable, ships in TensorRT samples. Worse classes for animals than YOLO. |
| RT-DETR-L | 53 | 4–6 | Transformer-based, very accurate, way too slow on Nano. Mention so it's not the default surprise. |

**Class coverage matters more than mAP.** COCO has `cat` (#15) and `dog`
but **no raccoon, rat, or gopher**. For animals beyond cat we have to
fine-tune or pick a different base:

| Source | Has raccoon | Has rat | Has gopher |
|---|---|---|---|
| COCO | no | no | no |
| Open Images v7 | yes (~5k images) | yes | no |
| iNaturalist 2021 | yes | yes | yes (sparse) |
| Roboflow Universe | yes (curated combos) | varies | varies |

**Phase 1 ships YOLOv8n COCO, cat-only.** Phase 2 fine-tunes on a
hand-curated raccoon + rat dataset (Open Images extract). Gopher is a
phase-3 problem because of dataset sparsity.

---

## 5. Service shape (`nano/src/inference/`)

```
main.py            # MQTT wiring + frame loop + signal handlers
engine.py          # TensorRT engine load + bindings + infer step
source.py          # GStreamer appsink RTSP reader, BGR np.ndarray frames
preprocess.py      # letterbox resize + HWC->CHW + float16 normalize
postprocess.py     # YOLO output decode + NMS + class allow-list
README.md          # contract: enable/detection topics, allow-list source
```

### 5.1 Frame source

`source.py` opens `rtsp://localhost:8554/live` via GStreamer:

```
rtspsrc location=rtsp://127.0.0.1:8554/live latency=100
  ! rtph264depay ! h264parse ! nvv4l2decoder
  ! nvvidconv ! video/x-raw,format=BGRx,width=640,height=384
  ! videoconvert ! video/x-raw,format=BGR
  ! appsink emit-signals=true max-buffers=1 drop=true
```

`max-buffers=1 drop=true` means we always read the freshest frame;
detection frame-rate is decoupled from stream frame-rate. The
`nvv4l2decoder` keeps decode on the GPU side. Resolution into the model
(letterboxed to 320×320 inside `preprocess.py`) is independent of this
intermediate size.

### 5.2 Engine

`engine.py` uses `tensorrt.Runtime` to load `.engine` files; bindings are
`pycuda.driver` or `cuda-python`. ONNX files at
`/var/lib/turret/models/yolov8n.onnx` are compiled at first boot:

```
trtexec --onnx=yolov8n.onnx --saveEngine=yolov8n.engine \
        --fp16 --workspace=1024 --explicitBatch
```

This step lands in `nano/scripts/host_prep.sh`. We do **not** commit
.engine files (GPU-arch specific) but we **do** commit ONNX (portable,
~12 MB for v8n).

### 5.3 Loop (~10 FPS detection cadence)

```
while not stop.is_set():
    frame = source.read()
    if frame is None: continue
    if (now - last_run_at) < (1/INFER_HZ):
        continue        # frame-skip; pick the freshest at the next tick
    dets = postprocess(engine.infer(preprocess(frame)))
    dets = filter_by_class(dets, allowed_classes)
    if dets:
        publish_detection(dets)
    last_run_at = now
```

`INFER_HZ = 10` matches the lock-on gate's expected cadence (4 frames in
1.5s = 2.6 Hz minimum, comfortable headroom).

### 5.4 MQTT contract (already lives in `common/messaging/topics.py`)

| Topic | Direction | Retained | Payload | Notes |
|---|---|---|---|---|
| `turret/inference/enable` | hub → inference | yes | `{ "enabled": bool }` | Hub flips on auto-aim/auto-sentry entry. Service runs the loop only while true; otherwise idles. |
| `turret/inference/detection` | inference → all | no | `{ "detections": [{class, score, bbox}], ts }` | Multi-detection contract from Phase 2 update. `bbox` is `[x0,y0,x1,y1]` normalized [0,1], top-left origin. |
| `turret/+/health` | inference → all | no | `{service: "inference", status: "up\|degraded\|down"}` | `degraded` when RTSP source disconnected or engine fails to load. |

The hub already subscribes detection (Phase 2). Nothing new on the hub
side except surfacing inference health on the dashboard, which falls out
of the existing health-aggregation logic for free.

### 5.5 Allow-list

The set of classes we publish is the union of:
- the user's current `target_class` (from `turret/hub/config`)
- a small fixed pre-fire-warning set (`person` to *suppress* fire — see §8 safety)

We publish all classes in this set that pass the score threshold; the
hub picks one for aim and the lock-on gate filters to `target_class`
again. We do **not** post-filter to a single class in the inference
service — multi-detection rendering needs the others, and the hub does
the final selection cheaply.

---

## 6. Performance envelope (Nano 4GB original Maxwell)

Realistic numbers, FP16, 320 input, single class allow-list:

- **Latency per frame**: 35–50 ms inference + 5–10 ms pre/post.
- **End-to-end (camera → detection MQTT)**: 350–500 ms. Camera+encode is
  ~150 ms, RTSP transit is ~50 ms, decode+infer is ~100 ms, MQTT publish
  is ~5 ms. The bulk of the latency is the camera pipeline, not us.
- **CPU**: one core at ~50% (GStreamer + Python), GPU at 60–80%.
- **Memory**: ~700 MB resident.
- **Thermal**: sustained inference at 10 FPS is fine on the stock heatsink
  in the brainstorm's "garden" environment as long as ambient stays under
  35 °C. Watch `tegrastats` once during long-run smoke testing.

If we end up on a **Jetson Orin Nano** instead of the original, every
number above gets ~6× better and we can revisit precision/resolution
defaults — see open question 1.

---

## 7. Implementation phases

**Phase 1 — cat detection on COCO.**
YOLOv8n ONNX exported, engine built by `host_prep.sh`, service running,
publishing detections at ~10 Hz, Dashboard renders boxes (already wired).
Done when: a real cat in frame produces a stable bounding box and the
turret tracks it in `auto-aim` mode.

**Phase 2 — fine-tuned raccoon + rat model.**
Curate ~3–5k labeled raccoon images (Open Images extract + Roboflow
Universe) + ~1k rat. Fine-tune YOLOv8n on top of COCO weights. Re-export
ONNX, swap the model symlink, rebuild engine. Done when: a raccoon at
night under IR illumination reliably detects with score > 0.6.

**Phase 3 — INT8 calibration.**
Calibration dataset of 200–500 representative night/day frames from our
own deployment. Re-build engine with `--int8`. Done when: detection
quality holds within ~3 mAP of the FP16 build at ≥1.4× the FPS.

**Phase 4 — multi-class + gopher.**
Add gopher, possum, deer (whatever shows up at our test sites) once
phase 2 is stable. Out of scope until we have data.

---

## 8. Safety and footguns

- **`person` detection is mandatory once we ship.** Auto-fire must hard
  inhibit if any detection in the frame is `person` with score > 0.4,
  regardless of target class. The lock-on gate doesn't know about this
  yet — it only filters by target class. Add a separate "fire inhibit"
  channel in lock-on (or pre-filter detections in the hub) **before**
  enabling auto-fire on a real deployment. Flag this as a gating
  requirement for any `auto-sentry` use outside of bench tests.
- **Confidence calibration is class-dependent.** A model that's 0.9 on a
  cat may be 0.65 on a raccoon for the same visual obviousness. The
  `SCORE_FIRE_MIN = 0.65` baked into `LockonGate` is a v1 guess; expect
  to tune per class once phase 2 lands.
- **IR night frames look nothing like daylight training data.** A model
  trained on RGB daylight will underperform under IR illumination even
  on the same animal. Phase 2 should include night frames in the
  fine-tune set.

---

## 9. UI implications

Mostly already done by Phase 2 + Phase 4 of motor_control_plan:

- Bounding boxes draw over the WHEP video (`BoxOverlay.tsx`).
- Lock-on badge flashes when the gate fires (`LockonBadge.tsx`).
- Add a small "inference" health row to `Sidebar` — already free if
  health-aggregation lights up by service name; just confirm the service
  publishes `service: "inference"` in its health payload.
- Optional: a "models" row in Settings showing which engine is loaded
  (`yolov8n.engine`, `yolov8n-finetune-raccoon.engine`, etc.) and its
  size + build date. Defer to phase 3.

---

## 10. Out of scope (for now)

- Tracker-based ID persistence (DeepSORT, ByteTrack). Detection-only is
  enough for a single-target turret; adding a tracker improves stability
  but at >2× the compute and we need that headroom for a custom model.
- Multi-camera. There's one camera on the Pi, period.
- On-device training or active learning. Fine-tunes happen on a workstation;
  the Nano runs engines, not autograd.
- Post-detection scene reasoning ("is the cat sitting still" vs "running
  away"). Not relevant to the turret's job.

---

## 11. Open questions for the user

1. **Which Jetson exactly?** "Jetson Nano" today usually means one of:
   (a) the original 4GB / 2GB Maxwell board (JetPack 4.6.x, TRT 8.0,
   Python 3.6); (b) the Orin Nano 4GB / 8GB Ampere (JetPack 5/6, TRT
   8.5+, Python 3.8/3.10). The performance numbers in §6 assume (a). If
   it's (b), we can run a much heavier model (YOLOv8s or even m) at the
   same latency budget.

2. **Animals beyond cat — exhaustive list?** Raccoon and rat are
   confirmed from the brainstorm. Gopher is "maybe later." Anything
   else in the v1 scope (possum, deer, fox, neighborhood dog you
   *don't* want to fire on)? The list shapes whether phase 2 is one
   fine-tune or several.

3. **Day-only or also IR night?** The Pi has IR LED illumination. If
   night detection matters in v1, the phase-1 model will visibly suck
   on IR frames and we should plan the fine-tune dataset to include
   night samples from day one. If night is a phase-3 nicety, we can
   ship daylight-only and keep dataset curation simpler.

4. **Acceptance threshold for "good enough."** Are we shipping if a cat
   in good light gets a stable box at ≥10 FPS, or do you want a higher
   bar (e.g. raccoon under IR at ≥0.7 confidence)? Sets the gating
   criterion for phase 1 → phase 2.

5. **Person-inhibit before any auto-fire?** Confirm the §8 safety rule
   should land before `auto-sentry` is offered to users at all.
   (Recommendation: yes, gating requirement.)

---

## 12. Install / first-boot additions

`nano/scripts/host_prep.sh` gets:

```
echo "== TensorRT model build =="
sudo mkdir -p /var/lib/turret/models
sudo chown "$USER":"$USER" /var/lib/turret/models
if [ ! -f /var/lib/turret/models/yolov8n.engine ]; then
    /usr/src/tensorrt/bin/trtexec \
        --onnx=$REPO/nano/src/inference/models/yolov8n.onnx \
        --saveEngine=/var/lib/turret/models/yolov8n.engine \
        --fp16 --workspace=1024 --explicitBatch
fi
```

`pi/scripts/host_prep.sh` is unchanged — the Pi has no inference role.

`deploy/Makefile` gets:
- `NANO_SERVICES := recorder hub ui-backend inference` (add inference)
- `models-pull` target that fetches a known-good ONNX from a release
  artifact bucket the first time. (Or commit it; YOLOv8n.onnx is ~12 MB,
  borderline acceptable in git.)

### Answers to user questions 
1. Jetson Nano 4GB for now. I'll upgrade to the Orin later, but it would make the rpi unecessary
2. Let's stick with cat for now, and i'll work on model testing and fine-tuning later, that will be a bigger challenge 
3. I will be doing IR night detections, but i can test in the daytime for now
4. For now if a cat in good light gets a detection. 
5. No person inhibit. This just has a water jet/cannon, it's not a danger to people.  


mosquitto_pub -h localhost -u turret -P "$(sudo cat /etc/turret/mqtt.password)" -t turret/inference/enable -m '{"enabled":true}' -r                                                                                  
mosquitto_sub -h localhost -u turret -P "$(sudo cat /etc/turret/mqtt.password)" -t turret/inference/detection  