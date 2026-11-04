"""YOLOv8 letterbox + tensor preprocessing.

Pure functions; no state.
"""
from typing import Tuple

import cv2
import numpy as np


def letterbox(img: np.ndarray, size: int = 320) -> Tuple[np.ndarray, float, Tuple[int, int]]:
    """Resize-with-padding to (size, size).

    Returns (padded_image, scale, (pad_x, pad_y)) so postprocess can invert
    the transform when projecting predicted boxes back to the original
    frame.

    `pad_x` and `pad_y` are the *left/top* pad widths in pixels of the
    padded image. The right/bottom pad sizes are implied by `size` and the
    scaled image dims; postprocess only needs the offset.
    """
    h, w = img.shape[:2]
    scale = min(size / w, size / h)
    new_w, new_h = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    pad_x = (size - new_w) // 2
    pad_y = (size - new_h) // 2
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
    return canvas, scale, (pad_x, pad_y)


def to_chw_fp16(img: np.ndarray) -> np.ndarray:
    """BGR uint8 HWC -> RGB float16 CHW, normalized to [0, 1].

    Returns a contiguous (3, H, W) float16 array suitable for direct copy
    into a TensorRT input binding.
    """
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    chw = np.ascontiguousarray(rgb.transpose(2, 0, 1))
    return (chw.astype(np.float16) / np.float16(255.0))
