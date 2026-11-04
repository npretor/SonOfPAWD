"""TensorRT runtime wrapper.

Loads a serialized engine, allocates host+device IO, exposes `infer()`.

CUDA Python bindings on JetPack 4.6 are inconsistent across community
images. We try the modern `cuda-python` package first, then PyCUDA, then
fail loudly. The two paths share a thin internal adapter so the rest of
the file does not branch.
"""
import logging
from typing import List

import numpy as np

import tensorrt as trt

log = logging.getLogger("inference.engine")

BACKEND = None
_cuda = None
_cudart = None
_pycuda = None

try:
    from cuda import cuda as _cuda  # type: ignore
    from cuda import cudart as _cudart  # type: ignore
    BACKEND = "cuda-python"
except ImportError:
    try:
        import pycuda.driver as _pycuda  # type: ignore
        import pycuda.autoinit  # noqa: F401
        BACKEND = "pycuda"
    except ImportError:
        BACKEND = None

if BACKEND is None:
    raise ImportError(
        "no CUDA Python bindings available; install python3-cuda or pycuda"
    )

log.info("CUDA backend: %s", BACKEND)

_TRT_LOGGER = trt.Logger(trt.Logger.WARNING)


def _check(err, msg=""):
    """cuda-python returns (status, *vals); raise on non-success."""
    if isinstance(err, tuple):
        status = err[0]
        rest = err[1:]
    else:
        status = err
        rest = ()
    # cuda-python uses .value enum; PyCUDA path doesn't go through here.
    if hasattr(status, "value"):
        ok_val = 0
        if int(status) != ok_val:
            raise RuntimeError(f"CUDA error {status} {msg}")
    return rest if len(rest) != 1 else rest[0]


class _Buffer:
    __slots__ = ("host", "device", "nbytes", "shape", "dtype", "is_input")

    def __init__(self, host, device, nbytes, shape, dtype, is_input):
        self.host = host
        self.device = device
        self.nbytes = nbytes
        self.shape = shape
        self.dtype = dtype
        self.is_input = is_input


class Engine:
    def __init__(self, path: str, input_size: int = 320):
        self.input_size = input_size
        self._runtime = trt.Runtime(_TRT_LOGGER)
        with open(path, "rb") as f:
            self._engine = self._runtime.deserialize_cuda_engine(f.read())
        if self._engine is None:
            raise RuntimeError(f"failed to load TRT engine: {path}")
        self._context = self._engine.create_execution_context()

        # Stream + buffers
        if BACKEND == "cuda-python":
            self._stream = _check(_cudart.cudaStreamCreate())
        else:
            self._stream = _pycuda.Stream()

        self._bindings: List[_Buffer] = []
        self._input_idx = -1
        self._output_idx = -1
        self._allocate()

        # Warm-up to surface CUDA init costs before the first real frame.
        dummy = np.zeros((3, input_size, input_size), dtype=np.float16)
        for _ in range(2):
            self.infer(dummy)

    # ----- IO allocation --------------------------------------------------

    def _allocate(self) -> None:
        n = self._engine.num_bindings
        ptrs = []
        for i in range(n):
            shape = tuple(self._engine.get_binding_shape(i))
            if -1 in shape:
                # Dynamic shape — set the input profile to the configured size.
                shape = (1, 3, self.input_size, self.input_size)
                self._context.set_binding_shape(i, shape)
            dtype = trt.nptype(self._engine.get_binding_dtype(i))
            size = int(np.prod(shape))
            host = np.empty(shape, dtype=dtype)
            nbytes = host.nbytes
            if BACKEND == "cuda-python":
                device = _check(_cudart.cudaMalloc(nbytes))
                ptrs.append(int(device))
            else:
                device = _pycuda.mem_alloc(nbytes)
                ptrs.append(int(device))
            is_input = self._engine.binding_is_input(i)
            buf = _Buffer(host, device, nbytes, shape, dtype, is_input)
            self._bindings.append(buf)
            if is_input:
                self._input_idx = i
            else:
                # Take the first output; YOLOv8 has a single (1, 84, 8400).
                if self._output_idx < 0:
                    self._output_idx = i
        self._binding_ptrs = ptrs

        if self._input_idx < 0 or self._output_idx < 0:
            raise RuntimeError("engine missing input or output binding")

        in_buf = self._bindings[self._input_idx]
        if in_buf.shape[-1] != self.input_size:
            log.warning(
                "engine input size %s != configured %d; using engine value",
                in_buf.shape, self.input_size,
            )
            self.input_size = in_buf.shape[-1]

    # ----- infer ----------------------------------------------------------

    def infer(self, chw_fp16: np.ndarray) -> np.ndarray:
        in_buf = self._bindings[self._input_idx]
        out_buf = self._bindings[self._output_idx]

        # Stage input into pinned-equivalent host buffer (np.empty for now).
        # YOLOv8n input is (1, 3, S, S); add the batch dim if needed.
        if chw_fp16.ndim == 3:
            arr = chw_fp16[np.newaxis, ...]
        else:
            arr = chw_fp16
        if arr.dtype != in_buf.dtype:
            arr = arr.astype(in_buf.dtype)
        np.copyto(in_buf.host, arr.reshape(in_buf.shape))

        if BACKEND == "cuda-python":
            _check(_cudart.cudaMemcpyAsync(
                in_buf.device, in_buf.host.ctypes.data, in_buf.nbytes,
                _cudart.cudaMemcpyKind.cudaMemcpyHostToDevice, self._stream,
            ))
            self._context.execute_async_v2(
                bindings=self._binding_ptrs,
                stream_handle=int(self._stream),
            )
            _check(_cudart.cudaMemcpyAsync(
                out_buf.host.ctypes.data, out_buf.device, out_buf.nbytes,
                _cudart.cudaMemcpyKind.cudaMemcpyDeviceToHost, self._stream,
            ))
            _check(_cudart.cudaStreamSynchronize(self._stream))
        else:
            _pycuda.memcpy_htod_async(in_buf.device, in_buf.host, self._stream)
            self._context.execute_async_v2(
                bindings=self._binding_ptrs,
                stream_handle=self._stream.handle,
            )
            _pycuda.memcpy_dtoh_async(out_buf.host, out_buf.device, self._stream)
            self._stream.synchronize()

        return out_buf.host

    # ----- close ----------------------------------------------------------

    def close(self) -> None:
        try:
            if BACKEND == "cuda-python":
                for b in self._bindings:
                    if b.device:
                        _cudart.cudaFree(b.device)
                _cudart.cudaStreamDestroy(self._stream)
            else:
                for b in self._bindings:
                    try:
                        b.device.free()
                    except Exception:
                        pass
        except Exception:
            log.exception("engine close: ignoring CUDA cleanup error")
        self._bindings = []
        self._context = None
        self._engine = None
        self._runtime = None
