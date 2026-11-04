#!/usr/bin/env bash
# Build the derived inference container (base image + paho-mqtt).
# Idempotent; safe to re-run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

BASE_IMAGE="${INFERENCE_BASE_IMAGE:-dustynv/jetson-inference:r32.7.1}"
TAG="${INFERENCE_IMAGE:-turret-inference:r32.7.1}"

echo "== building $TAG from $BASE_IMAGE =="
docker build \
    --build-arg "BASE_IMAGE=$BASE_IMAGE" \
    -t "$TAG" \
    -f "$REPO_ROOT/nano/scripts/inference.Dockerfile" \
    "$REPO_ROOT/nano/scripts/"

echo "== runtime sanity check (CUDA/TRT need --runtime nvidia) =="
docker run --rm --runtime nvidia "$TAG" python3 -c "
import tensorrt, pycuda.driver, pycuda.autoinit, numpy, cv2, paho.mqtt.client
print('tensorrt', tensorrt.__version__)
print('cv2',      cv2.__version__)
print('numpy',    numpy.__version__)
print('paho',     'ok')
print('cuda dev count:', pycuda.driver.Device.count())
"

echo "== image ready =="
docker images "$TAG"
