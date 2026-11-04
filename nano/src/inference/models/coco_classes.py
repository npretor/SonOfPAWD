"""COCO class id <-> name table.

Indices follow the Ultralytics YOLOv8 COCO export (the model committed in
this repo). Verify with a known cat image during step 3 of the spec — if a
detection lands in roughly the right pixel region but maps to the wrong
name, the export indexing has shifted and this table needs to follow.
"""

COCO_NAMES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep",
    "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard",
    "sports ball", "kite", "baseball bat", "baseball glove", "skateboard",
    "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
    "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv",
    "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave",
    "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush",
]

assert len(COCO_NAMES) == 80, "COCO has 80 classes"

NAME_TO_ID = {n: i for i, n in enumerate(COCO_NAMES)}
ID_TO_NAME = {i: n for i, n in enumerate(COCO_NAMES)}
