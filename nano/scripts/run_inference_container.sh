#!/usr/bin/env bash
# Launch the inference service inside an L4T container that ships PyCUDA +
# TensorRT (PyCUDA on JetPack 4.6 / Python 3.6 won't pip-install cleanly,
# so we run the service in a container instead).
#
# Image must match the host's L4T r32.7.1 to use --runtime nvidia.
# Defaults match what the user has run before; override via env var:
#     INFERENCE_IMAGE=nvcr.io/nvidia/l4t-ml:r32.7.1-py3 nano/scripts/run_inference_container.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

IMAGE="${INFERENCE_IMAGE:-turret-inference:r32.7.1}"
NAME="${INFERENCE_CONTAINER:-turret-inference}"
MODELS_DIR="${MODELS_DIR:-/var/lib/turret/models}"
MQTT_PW_FILE="${MQTT_PW_FILE:-/etc/turret/mqtt.password}"

if [ ! -f "$MQTT_PW_FILE" ]; then
    echo "MQTT password not found at $MQTT_PW_FILE — run nano/scripts/host_prep.sh first." >&2
    exit 2
fi

mkdir -p "$MODELS_DIR"

# If a previous run left the container around, remove it so --name is free.
docker rm -f "$NAME" >/dev/null 2>&1 || true

exec docker run --rm \
    --name "$NAME" \
    --runtime nvidia \
    --network host \
    --ipc host \
    -e PYTHONUNBUFFERED=1 \
    -e PYTHONPATH=/repo \
    -v "$REPO_ROOT":/repo:ro \
    -v "$MODELS_DIR":/var/lib/turret/models \
    -v "$MQTT_PW_FILE":/etc/turret/mqtt.password:ro \
    -w /repo \
    "$IMAGE" \
    python3 -m nano.src.inference.main
