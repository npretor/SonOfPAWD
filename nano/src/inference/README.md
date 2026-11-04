# inference (Phase 1)

TensorRT YOLOv8n object detection on the Nano. Reads frames from
MediaMTX RTSP, publishes detections for `auto-aim` mode.

## MQTT contract

| Topic | Direction | Payload |
|---|---|---|
| `turret/inference/enable` (retained) | sub | `{"enabled": bool}` |
| `turret/hub/config` (retained) | sub | `{"target_class": str, ...}` — read for the class allow-list |
| `turret/inference/detection` | pub | `{"detections": [{"class","score","bbox"}], "ts": ms}` |
| `turret/nano/health` | pub | `{"service":"inference","status":"up\|degraded\|down","detail":""}` |

`bbox` is `[x0, y0, x1, y1]`, normalized `[0,1]`, top-left origin, never inverted.

## Class allow-list

Phase 1 hard-codes `target_class = "cat"` if `turret/hub/config` hasn't
landed yet. Once the hub publishes a config, that takes over. Unknown
class names log a warning and fall back to `cat`.

## Engine

`/var/lib/turret/models/yolov8n.engine` is built on the Nano from
`models/yolov8n.onnx` via `nano/scripts/build_engine.sh`. The `.engine`
file is GPU-arch-specific and **must not** be committed.

## Health

- **up** — engine loaded, RTSP reading, ≥ 50% of target FPS over the last 5s.
- **degraded** — RTSP source down, sustained low FPS, or repeated infer errors.
- **down** — last-will only, on hard process death.

## Frame loop

- Default 10 Hz (`INFER_HZ`), 320×320 input, FP16.
- Source pipeline: `appsink max-buffers=1 drop=true` for freshest-frame.
- No GLib loop in `main.py` — paho's threaded callbacks + `while` is enough.

## Runtime: container

PyCUDA on JetPack 4.6 / Python 3.6 doesn't pip-install cleanly, so the
service runs inside an L4T container that ships PyCUDA + TensorRT
pre-built.

Image (built locally on the Nano via `nano/scripts/build_inference_image.sh`):
- Base: `dustynv/jetson-inference:r32.7.1` (override via `INFERENCE_BASE_IMAGE`)
- Adds `paho-mqtt` (the only missing dep)
- Tag: `turret-inference:r32.7.1`

`run_inference_container.sh` launches it with `--runtime nvidia
--network host --ipc host`, mounting the repo `:ro`, the engine dir,
and the MQTT password. The systemd unit calls this script.

`engine.py` tries `cuda-python` first, then PyCUDA — inside the L4T
container PyCUDA wins. The fallback path is for hardware variance.

## Person-inhibit

Not implemented in Phase 1. Phase 1 only enables `auto-aim`, not
`auto-sentry` (no auto-fire), so person-inhibit is not gating. Phase 2
will add it before sentry-mode auto-fire is allowed.
