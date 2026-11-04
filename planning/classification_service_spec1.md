# Classification Service — Implementation Spec v1

Implementation-ready spec for **Phase 1** of `classification_service_plan.md`:
real-time object detection on the Nano, YOLOv8n FP16 on COCO, cat-only target,
publishing the multi-detection contract the hub already consumes.

Phase 2 (raccoon/rat fine-tune), Phase 3 (INT8), and Phase 4 (extra classes)
are out of scope here and get their own specs.

---

## 0. Assumptions

Resolved from the plan's recommendations because answers weren't captured:

- **Hardware:** original Jetson Nano 4GB Maxwell, JetPack 4.6.x,
  TensorRT 8.0, Python 3.6 (matches every other Nano-side service in this
  repo).
- **Frame source:** local MediaMTX RTSP egress at
  `rtsp://127.0.0.1:8554/live`.
- **Phase 1 classes:** `cat` only (COCO id 15). Allow-list pulled from
  `state.target_class` once it lands; in the meantime hard-coded to `cat`.
- **Lighting:** daylight only. IR night handling is Phase 2.
- **Person-inhibit:** flagged but **not gating Phase 1** because Phase 1
  does not enable `auto-sentry` (`auto-aim` only). This spec leaves a
  marked `TODO(person-inhibit)` hook for Phase 2.

If any of these is wrong, fix before starting — the §6 model-build step
in particular is hardware-specific.

---

## 1. Phase 1 done criteria

A real cat in good daylight, framed by the camera, must:

1. Produce ≥1 detection per frame at score ≥ 0.5, sustained.
2. Drive a stable bounding box on the dashboard (`BoxOverlay`).
3. With `mode=auto-aim` and `target_class=cat`, drive the servos to
   center on the cat.
4. Hold ≥10 FPS detection rate over a 60 s test (CPU+GPU not thermally
   throttled).

If any of these fails, Phase 1 is not done.

---

## 2. File layout

```
nano/src/inference/
  __init__.py
  main.py            # ~120 LOC — MQTT wiring, frame loop, signal handlers
  source.py          # ~80 LOC  — GStreamer appsink RTSP reader, drops to freshest frame
  engine.py          # ~120 LOC — TRT runtime: load engine, allocate IO, infer()
  preprocess.py      # ~50 LOC  — letterbox, BGR→RGB, HWC→CHW, FP16 normalize
  postprocess.py     # ~120 LOC — YOLOv8 output decode + NMS + class allow-list
  models/
    yolov8n.onnx     # ~12 MB, committed to git (Phase 2 may override)
    coco_classes.py  # COCO class id → name (cat=15)
  README.md          # contract: enable/detection/health, allow-list

nano/systemd/
  inference.service  # new

nano/scripts/
  host_prep.sh       # extended: build TRT engine on first boot
  build_engine.sh    # standalone re-runnable engine builder
```

Total new code: ~600 LOC excluding model files.

---

## 3. Dependencies (Nano)

`nano/scripts/host_prep.sh` adds:

```
sudo apt install -y \
    python3-numpy \
    python3-opencv \
    python3-libnvinfer \
    python3-libnvinfer-dev
```

`python3-libnvinfer` ships TRT Python bindings on JetPack 4.6. PyCUDA on
JetPack 4.6 is fragile; we use the **CUDA Python bindings from `cuda-python`**
if available, falling back to PyCUDA. Resolve in `engine.py` import block:

```python
try:
    import cuda.cuda as cuda
    import cuda.cudart as cudart
    BACKEND = "cuda-python"
except ImportError:
    import pycuda.driver as cuda
    import pycuda.autoinit  # noqa: F401
    BACKEND = "pycuda"
```

If neither is present, the service logs a clear "no CUDA python bindings"
error at startup and exits. **Pick one and document it** during Phase 1
implementation; the fallback is for hardware variance, not production.

---

## 4. Module-by-module spec

### 4.1 `source.py`

GStreamer pipeline reader. Single class `RtspSource`.

```
rtspsrc location=<URL> latency=100
  ! rtph264depay ! h264parse ! nvv4l2decoder
  ! nvvidconv ! video/x-raw,format=BGRx,width=640,height=384
  ! videoconvert ! video/x-raw,format=BGR
  ! appsink name=sink emit-signals=true sync=false max-buffers=1 drop=true
```

`max-buffers=1 drop=true` is the freshest-frame strategy: detection cadence
decouples from stream cadence, no buffering backlog.

API:
```python
class RtspSource:
    def __init__(self, url: str): ...
    def start(self) -> None: ...               # raises if pipeline doesn't go to PLAYING in 5s
    def read(self, timeout_s: float = 0.5) -> Optional[np.ndarray]: ...
    def stop(self) -> None: ...
    def healthy(self) -> bool: ...             # true if a frame was read in the last 2s
```

`read` returns BGR uint8 (H, W, 3). On EOS or error, returns None and
flips healthy() to false; `main.py` reconnects with 1 s backoff.

### 4.2 `preprocess.py`

Pure functions. No state.

```python
def letterbox(img: np.ndarray, size: int = 320) -> Tuple[np.ndarray, float, Tuple[int, int]]:
    """Returns (padded_image, scale, (pad_x, pad_y)) for inverse mapping."""

def to_chw_fp16(img: np.ndarray) -> np.ndarray:
    """BGR uint8 HWC → RGB float16 CHW, normalized to [0,1]."""
```

Two return values from `letterbox` are needed by postprocess to invert the
padding when projecting boxes back to normalized [0,1] in the *original*
frame.

### 4.3 `engine.py`

Loads `.engine`, manages IO buffers, exposes `infer(input_chw_fp16) -> output_np`.

API:
```python
class Engine:
    def __init__(self, path: str, input_size: int = 320): ...
    def infer(self, chw_fp16: np.ndarray) -> np.ndarray: ...   # returns raw YOLO output
    def close(self) -> None: ...
```

YOLOv8n exported via Ultralytics gives an output tensor of shape
`(1, 84, 8400)` — 80 classes + 4 box coords, 8400 anchor outputs. The
`postprocess` module knows this layout; `engine` is layout-agnostic.

Warm-up: run two dummy inferences in `__init__` to surface CUDA init
costs before the first real frame.

### 4.4 `postprocess.py`

Pure functions. Implements YOLOv8 decode (xywh + class scores in one tensor),
class-confidence NMS, and inverse-letterbox projection.

```python
def decode_yolov8(
    output: np.ndarray,        # (1, 84, 8400) FP16
    conf_thresh: float = 0.25,
    iou_thresh: float = 0.45,
    allow_classes: Optional[Iterable[int]] = None,
) -> List[Detection]: ...

def project_to_normalized(
    dets: List[Detection],
    scale: float,
    pad: Tuple[int, int],
    orig_size: Tuple[int, int],   # (W, H)
) -> List[dict]: ...
```

Output of `project_to_normalized` matches the canonical wire format:
`[{class: str, score: float, bbox: [x0, y0, x1, y1]}, ...]`, normalized
[0,1] top-left origin. Class id → name via `coco_classes.py`.

NMS is per-class, score-sorted, vectorized in numpy. With `allow_classes`
in use, we filter *before* NMS so the cost stays small even on a noisy
model.

### 4.5 `main.py`

Wiring. The whole service.

```python
def main() -> int:
    cfg = load_config()                 # constants below
    engine = Engine(cfg.engine_path)
    source = RtspSource(cfg.rtsp_url); source.start()
    mqtt = MqttClient(service_name="inference")

    enabled = False
    target_class = "cat"

    def on_enable(_t, p): nonlocal enabled; enabled = bool(p.get("enabled"))
    def on_config(_t, p):
        tc = p.get("target_class")
        if tc: nonlocal target_class; target_class = tc

    mqtt.connect()
    mqtt.subscribe(TOPIC_INFER_ENABLE, on_enable)
    mqtt.subscribe(TOPIC_HUB_CONFIG, on_config)   # for target_class

    stop = threading.Event()
    signal.signal(SIGINT, lambda *_: stop.set())
    signal.signal(SIGTERM, lambda *_: stop.set())

    period = 1.0 / cfg.infer_hz
    next_run = time.monotonic()
    last_health = 0.0
    while not stop.is_set():
        if not enabled:
            stop.wait(0.1); continue
        frame = source.read(timeout_s=0.5)
        if frame is None:
            publish_health(mqtt, "degraded", "rtsp source")
            time.sleep(0.5); continue
        now = time.monotonic()
        if now < next_run:
            stop.wait(next_run - now); continue
        next_run = now + period

        chw, scale, pad = preprocess(frame)
        out = engine.infer(chw)
        dets = decode_yolov8(out, allow_classes={CLASS_CAT})
        dets = project_to_normalized(dets, scale, pad, frame_wh(frame))
        if dets:
            mqtt.publish(TOPIC_INFER_DETECT, {"detections": dets})

        if now - last_health > 5.0:
            publish_health(mqtt, "up", f"{cfg.infer_hz}fps target")
            last_health = now

    source.stop()
    engine.close()
    mqtt.disconnect()
    return 0
```

Constants live as module-level dataclass `InferConfig`:
```python
INFER_HZ = 10
RTSP_URL = "rtsp://127.0.0.1:8554/live"
ENGINE_PATH = "/var/lib/turret/models/yolov8n.engine"
CLASS_CAT = 15
SCORE_THRESH = 0.25
IOU_THRESH = 0.45
INPUT_SIZE = 320
```

**No GLib loop** — paho's threaded callbacks + a plain `while` is enough.
The hub uses GLib because it has timers and cross-thread marshaling needs;
inference doesn't.

---

## 5. Model artifacts

### 5.1 ONNX export (run once, on a workstation)

Ultralytics CLI:
```
pip install ultralytics
yolo export model=yolov8n.pt format=onnx imgsz=320 half=True opset=13 simplify=True
```

Produces `yolov8n.onnx` (~12 MB). Commit to `nano/src/inference/models/`.
**Don't commit `.engine`** — it's GPU-arch-specific and rebuilds in
seconds.

### 5.2 Engine build (on the Nano, first boot)

`nano/scripts/build_engine.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
ONNX=${1:-$REPO/nano/src/inference/models/yolov8n.onnx}
OUT=${2:-/var/lib/turret/models/yolov8n.engine}
sudo mkdir -p "$(dirname "$OUT")"
sudo chown "$USER":"$USER" "$(dirname "$OUT")"
/usr/src/tensorrt/bin/trtexec \
    --onnx="$ONNX" \
    --saveEngine="$OUT" \
    --fp16 \
    --workspace=1024 \
    --explicitBatch \
    --shapes=images:1x3x320x320
echo "engine: $OUT ($(stat -c %s "$OUT") bytes)"
```

`nano/scripts/host_prep.sh` calls this if the engine is missing:
```bash
echo "== TensorRT engine =="
if [ ! -f /var/lib/turret/models/yolov8n.engine ]; then
    "$REPO_ROOT/nano/scripts/build_engine.sh"
else
    echo "  /var/lib/turret/models/yolov8n.engine exists, skipping"
fi
```

Build time on Nano 4GB: 2–4 minutes. Uses ~1 GB RAM during build; close
the recorder/UI services first if memory is tight.

### 5.3 COCO classes module

Tiny:
```python
# nano/src/inference/models/coco_classes.py
COCO_NAMES = ["person", "bicycle", ..., "cat", ..., "toothbrush"]  # 80 entries
NAME_TO_ID = {n: i for i, n in enumerate(COCO_NAMES)}
```

Hard-coded; spec doesn't grow new classes until Phase 2.

---

## 6. Systemd unit

`nano/systemd/inference.service`:
```
[Unit]
Description=SonOfPAWD inference (TensorRT object detection)
After=network-online.target mediamtx.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -m nano.src.inference.main
WorkingDirectory=%h/SonOfPAWD
Restart=on-failure
RestartSec=3
# Engine load + warm-up takes ~5s; allow extra for cold boot.
TimeoutStartSec=30

[Install]
WantedBy=default.target
```

`deploy/Makefile` extends `NANO_SERVICES`:
```
NANO_SERVICES := recorder hub ui-backend inference
```

---

## 7. MQTT contract (already in `common/messaging/topics.py`)

| Topic | We | Payload |
|---|---|---|
| `turret/inference/enable` | sub (retained) | `{"enabled": bool}` |
| `turret/hub/config` | sub (retained) | `{"target_class": str, ...}` — read for class allow-list |
| `turret/inference/detection` | pub | `{"detections": [{"class","score","bbox"}], "ts": ms}` |
| `turret/nano/health` | pub | `{"service":"inference","status":"up|degraded|down","detail":""}` |

Health states:
- **up** — engine loaded, RTSP reading, ≥10 FPS over the last 5 s.
- **degraded** — RTSP source down, or sustained <5 FPS, or engine infer failing intermittently.
- **down** — last-will only; happens on hard process death.

---

## 8. Implementation order

Sequence is non-negotiable; each step has a smoke test before the next.

1. **`source.py` standalone.** Run a script that opens RTSP and prints frame
   shape + FPS for 10 s. **Done when:** prints `frame=(384,640,3) fps≈20`.

2. **Engine build + load.** Run `build_engine.sh`, then a script that
   `Engine(path)` + runs `infer(zeros((3,320,320), fp16))` 100 times and
   prints mean latency. **Done when:** mean latency < 50 ms.

3. **`postprocess.decode_yolov8` against a known image.** Use a stock cat
   photo, run end-to-end, assert at least one `cat` detection at score
   > 0.5. Print bbox. **Done when:** detection lands in roughly the right
   pixel region.

4. **`main.py` wired up, MQTT subscribed, no detections yet.** Run service
   under systemd; verify `turret/inference/enable=true` flips it from idle
   into the loop, and detections start flowing on `turret/inference/detection`.
   **Done when:** `mosquitto_sub -t turret/inference/detection` shows
   messages with reasonable `bbox` values when a cat is in frame.

5. **End-to-end servo aim.** With `mode=auto-aim`, `target_class=cat`,
   point the camera at a cat. **Done when:** the servos track the cat.

6. **Sustained 60s test.** Run for a minute with continuous detections.
   **Done when:** FPS holds ≥ 10, no degraded-health publishes, RAM
   stable, GPU temp < 75 °C in `tegrastats`.

If step 6 fails on FPS, drop input size to 256 first; if still failing,
fall back to YOLOv5n per the model table in the plan (§4).

---

## 9. Frontend changes

None for Phase 1. `BoxOverlay` already renders the multi-detection
payload; `Sidebar`'s health-aggregation already lights up by service name
once `inference` publishes health.

The only optional touch: add `inference` to the known-services list in
`nano/src/ui/backend/logs.py` so Logs Drawer can pick it up. ~3 line
edit.

---

## 10. Things likely to bite during first boot

- **TRT engine fails to build with "ONNX op not supported".** Means the
  ONNX export used a too-new opset. Re-export with `opset=13` (the
  command in §5.1 already pins this).
- **`nvv4l2decoder` not found.** JetPack 4.6.x ships it under
  `gstreamer1.0-plugins-bad` plus `nvidia-l4t-gstreamer`. Add to apt
  list in host_prep if missing.
- **CUDA OOM at engine build.** Stop `recorder` and `ui-backend` services
  during the build:
  `systemctl --user stop recorder ui-backend && build_engine.sh`.
- **First inference takes 2 s (cold CUDA).** This is normal; the warm-up
  in `Engine.__init__` covers it. If service startup times out, bump
  `TimeoutStartSec` in the unit.
- **Detections at the right pixel but wrong class id.** Likely cause:
  COCO class indices off-by-one between Ultralytics export and our
  hard-coded table. Verify with a known cat image during step 3.

---

## 11. Out of scope for this spec

- Multi-class allow-list driven by a config (Phase 2).
- Person-inhibit fire suppression (Phase 2 — gates `auto-sentry`).
- INT8 calibration (Phase 3).
- Model swap UI / "which engine is loaded" surface (Phase 3).
- Tracker (DeepSORT/ByteTrack) for ID persistence (out of scope per plan §10).

---

## 12. Deliverable summary

When this spec is implemented:
- `nano/src/inference/` is a complete service (~600 LOC + 12 MB ONNX).
- `nano/scripts/build_engine.sh` reproducibly produces `.engine` from
  ONNX on the Nano.
- `nano/systemd/inference.service` runs under user systemd.
- The dashboard shows real cat boxes; auto-aim mode actually tracks cats.
- Health publishes flow into the Sidebar as `inference: up`.
- The codepath for "raccoon" exists but the model can't see one yet —
  that's Phase 2's problem.
