#!/usr/bin/env bash
# Build a TensorRT engine from a YOLOv8 ONNX file.
#
# We build inside the same container the inference service runs in, so the
# TRT version that wrote the engine matches the one that will load it.
# Re-runnable: stop the inference service first if it's running.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

ONNX_HOST="${1:-$REPO_ROOT/nano/src/inference/models/yolov8n.onnx}"
OUT_HOST="${2:-/var/lib/turret/models/yolov8n.engine}"
INPUT_SIZE="${INPUT_SIZE:-320}"
IMAGE="${INFERENCE_IMAGE:-turret-inference:r32.7.1}"

if [ ! -f "$ONNX_HOST" ]; then
    echo "ONNX not found: $ONNX_HOST"
    echo "Export it on a workstation with:"
    echo "  pip install ultralytics"
    echo "  yolo export model=yolov8n.pt format=onnx imgsz=$INPUT_SIZE half=True opset=13 simplify=True"
    echo "Then commit yolov8n.onnx to nano/src/inference/models/."
    exit 1
fi

OUT_DIR="$(dirname "$OUT_HOST")"
mkdir -p "$OUT_DIR"

ONNX_REL="${ONNX_HOST#"$REPO_ROOT"/}"
ONNX_CONT="/repo/$ONNX_REL"
OUT_CONT="/var/lib/turret/models/$(basename "$OUT_HOST")"

echo "== building TRT engine in container =="
echo "  image: $IMAGE"
echo "  onnx:  $ONNX_HOST"
echo "  out:   $OUT_HOST"
echo "  size:  ${INPUT_SIZE}x${INPUT_SIZE}"

# trtexec lives in /usr/src/tensorrt/bin/trtexec inside the L4T images.
docker run --rm \
    --runtime nvidia \
    -v "$REPO_ROOT":/repo:ro \
    -v "$OUT_DIR":/var/lib/turret/models \
    "$IMAGE" \
    /usr/src/tensorrt/bin/trtexec \
        --onnx="$ONNX_CONT" \
        --saveEngine="$OUT_CONT" \
        --fp16 \
        --workspace=1024

echo "engine: $OUT_HOST ($(stat -c %s "$OUT_HOST") bytes)"
