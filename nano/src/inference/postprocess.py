"""YOLOv8 output decode, NMS, and inverse-letterbox projection.

Pure functions; no state. The decoder expects the Ultralytics YOLOv8
output layout `(1, 84, N)` — 4 box coords (xywh, model-space pixels) plus
80 class scores per anchor.
"""
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

from nano.src.inference.models.coco_classes import ID_TO_NAME


def decode_yolov8(
    output: np.ndarray,
    conf_thresh: float = 0.25,
    iou_thresh: float = 0.45,
    allow_classes: Optional[Iterable[int]] = None,
) -> List[Tuple[int, float, Tuple[float, float, float, float]]]:
    """Returns list of (class_id, score, (x0, y0, x1, y1)) in model-space pixels.

    Coordinates are still in the letterboxed image's coordinate system;
    `project_to_normalized` undoes the letterbox.
    """
    # Squeeze batch and pull (84, N) — supports (1, 84, N) and (84, N).
    if output.ndim == 3:
        out = output[0]
    else:
        out = output
    if out.shape[0] != 84:
        # Some exports come out as (N, 84); transpose into (84, N).
        if out.shape[1] == 84:
            out = out.T
        else:
            raise ValueError(f"unexpected YOLOv8 output shape: {output.shape}")

    boxes = out[0:4, :]            # (4, N) xywh
    scores_per_class = out[4:, :]  # (80, N)

    # Per-anchor best class.
    cls_ids = np.argmax(scores_per_class, axis=0)
    cls_scores = scores_per_class[cls_ids, np.arange(scores_per_class.shape[1])]

    keep = cls_scores >= conf_thresh
    if allow_classes is not None:
        allow = np.fromiter(allow_classes, dtype=np.int64)
        keep &= np.isin(cls_ids, allow)
    if not np.any(keep):
        return []

    boxes = boxes[:, keep].astype(np.float32)
    cls_ids = cls_ids[keep].astype(np.int64)
    cls_scores = cls_scores[keep].astype(np.float32)

    # xywh (center) -> xyxy
    cx, cy, bw, bh = boxes[0], boxes[1], boxes[2], boxes[3]
    x0 = cx - bw / 2
    y0 = cy - bh / 2
    x1 = cx + bw / 2
    y1 = cy + bh / 2
    xyxy = np.stack([x0, y0, x1, y1], axis=1)  # (M, 4)

    # Per-class NMS so detections of different classes never suppress each other.
    out_dets: List[Tuple[int, float, Tuple[float, float, float, float]]] = []
    for c in np.unique(cls_ids):
        idx = np.where(cls_ids == c)[0]
        kept = _nms(xyxy[idx], cls_scores[idx], iou_thresh)
        for k in kept:
            i = idx[k]
            out_dets.append((
                int(cls_ids[i]),
                float(cls_scores[i]),
                (float(xyxy[i, 0]), float(xyxy[i, 1]),
                 float(xyxy[i, 2]), float(xyxy[i, 3])),
            ))
    out_dets.sort(key=lambda d: d[1], reverse=True)
    return out_dets


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_thresh: float) -> List[int]:
    """Greedy NMS, vectorized. Returns indices into the input arrays."""
    if len(boxes) == 0:
        return []
    x0 = boxes[:, 0]; y0 = boxes[:, 1]; x1 = boxes[:, 2]; y1 = boxes[:, 3]
    areas = np.maximum(0.0, x1 - x0) * np.maximum(0.0, y1 - y0)
    order = scores.argsort()[::-1]
    kept: List[int] = []
    while order.size > 0:
        i = int(order[0])
        kept.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx0 = np.maximum(x0[i], x0[rest])
        yy0 = np.maximum(y0[i], y0[rest])
        xx1 = np.minimum(x1[i], x1[rest])
        yy1 = np.minimum(y1[i], y1[rest])
        inter = np.maximum(0.0, xx1 - xx0) * np.maximum(0.0, yy1 - yy0)
        union = areas[i] + areas[rest] - inter
        iou = np.where(union > 0, inter / union, 0.0)
        order = rest[iou < iou_thresh]
    return kept


def project_to_normalized(
    dets: Sequence[Tuple[int, float, Tuple[float, float, float, float]]],
    scale: float,
    pad: Tuple[int, int],
    orig_size: Tuple[int, int],
) -> List[dict]:
    """Invert letterbox -> normalized [0,1] bbox in the original frame.

    `orig_size` is (W, H) in original-frame pixels.
    Output matches the canonical wire format:
        {"class": str, "score": float, "bbox": [x0, y0, x1, y1]}
    bbox is normalized to [0, 1], top-left origin, never inverted.
    """
    pad_x, pad_y = pad
    ow, oh = orig_size
    out: List[dict] = []
    for cls_id, score, (x0, y0, x1, y1) in dets:
        # Undo padding then scale.
        ux0 = (x0 - pad_x) / scale
        uy0 = (y0 - pad_y) / scale
        ux1 = (x1 - pad_x) / scale
        uy1 = (y1 - pad_y) / scale
        # Clamp into the original frame and normalize.
        ux0 = max(0.0, min(ow, ux0)) / ow
        uy0 = max(0.0, min(oh, uy0)) / oh
        ux1 = max(0.0, min(ow, ux1)) / ow
        uy1 = max(0.0, min(oh, uy1)) / oh
        if ux1 <= ux0 or uy1 <= uy0:
            continue
        name = ID_TO_NAME.get(cls_id, str(cls_id))
        out.append({
            "class": name,
            "score": round(score, 4),
            "bbox": [round(ux0, 5), round(uy0, 5), round(ux1, 5), round(uy1, 5)],
        })
    return out
