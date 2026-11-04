# Derived image: jetson-inference base + paho-mqtt for the inference service.
# Build:
#   docker build -t turret-inference:r32.7.1 -f nano/scripts/inference.Dockerfile nano/scripts/
ARG BASE_IMAGE=dustynv/jetson-inference:r32.7.1
FROM ${BASE_IMAGE}

# paho-mqtt is the only dep missing from the base image; everything else
# (numpy, opencv, tensorrt, pycuda, gstreamer plugins) ships with l4t/jetson-inference.
RUN python3 -m pip install --no-cache-dir 'paho-mqtt>=1.6,<2.0'

# Build-time check: paho + numpy only. Anything that links against the
# CUDA stack (cv2, tensorrt, pycuda) cannot be imported during
# `docker build` — libcuda/libcublas are bind-mounted by the nvidia
# runtime at *run* time only. Runtime sanity check happens in
# build_inference_image.sh after the build finishes.
RUN python3 -c "import paho.mqtt.client; import numpy; \
    print('paho ok'); print('numpy', numpy.__version__)"
