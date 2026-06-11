"""ML-based detection of existing solar panels (YOLOv8 instance segmentation).

Uses the MIT-licensed model `finloop/yolov8s-seg-solar-panels` from Hugging
Face (YOLOv8s-seg fine-tuned on aerial PV imagery). Weights (~24 MB) are
downloaded once and cached. If ultralytics/torch or the download is
unavailable, the caller falls back to the classical-CV heuristic in detect.py.
"""

from typing import List, Optional, Tuple

import cv2
import numpy as np

from .detect import DetectionResult

HF_REPO = "finloop/yolov8s-seg-solar-panels"
HF_FILE = "best.pt"

_model = None
_load_failed = False


def available() -> bool:
    return load_model() is not None


def load_model():
    global _model, _load_failed
    if _model is not None or _load_failed:
        return _model
    try:
        from huggingface_hub import hf_hub_download
        from ultralytics import YOLO
        weights = hf_hub_download(HF_REPO, HF_FILE)
        _model = YOLO(weights)
    except Exception as exc:                       # any failure -> heuristic fallback
        print(f"  (ML detector unavailable, falling back to CV heuristic: {exc})")
        _load_failed = True
    return _model


def detect_panels_ml(img: np.ndarray, roof_px: List[Tuple[int, int]],
                     resolution: float, conf: float = 0.30,
                     min_coverage: float = 0.04) -> Optional[DetectionResult]:
    """Run YOLO segmentation; count only detections overlapping the roof outline."""
    model = load_model()
    if model is None:
        return None

    h, w = img.shape[:2]
    roof_mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(roof_mask, [np.array(roof_px, np.int32)], 255)
    roof_area_px = int(np.count_nonzero(roof_mask))
    if roof_area_px == 0:
        return DetectionResult(False, 0.0, [])

    results = model.predict(img, conf=conf, imgsz=960, verbose=False)
    panel_mask = np.zeros((h, w), np.uint8)
    boxes = []
    for r in results:
        if r.masks is not None:
            for poly in r.masks.xy:                # polygon per instance, px coords
                pts = np.array(poly, np.int32)
                if len(pts) >= 3:
                    cv2.fillPoly(panel_mask, [pts], 255)
                    boxes.append(cv2.minAreaRect(pts.astype(np.float32)))
        elif r.boxes is not None:                  # box-only fallback
            for b in r.boxes.xyxy.cpu().numpy():
                x1, y1, x2, y2 = b[:4]
                cv2.rectangle(panel_mask, (int(x1), int(y1)), (int(x2), int(y2)), 255, -1)
                boxes.append(((float(x1 + x2) / 2, float(y1 + y2) / 2),
                              (float(x2 - x1), float(y2 - y1)), 0.0))

    on_roof = cv2.bitwise_and(panel_mask, roof_mask)
    panel_px = int(np.count_nonzero(on_roof))
    coverage = panel_px / roof_area_px
    has = (coverage >= min_coverage
           or panel_px * resolution * resolution >= 25.0)   # absolute floor
    return DetectionResult(has, coverage, boxes)
