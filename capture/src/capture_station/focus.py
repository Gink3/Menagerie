"""Focus measurements use unedited native-resolution pixels, never preview scaling."""

import cv2
import numpy as np

METHOD = "laplacian_variance_gaussian3_v1"


def roi_pixels(shape, roi):
    height, width = shape[:2]
    x1, y1, x2, y2 = roi
    left, top = max(0, min(width - 1, int(x1 * width))), max(0, min(height - 1, int(y1 * height)))
    right, bottom = max(left + 1, min(width, round(x2 * width))), max(top + 1, min(height, round(y2 * height)))
    return left, top, right, bottom


def measure(frame, roi):
    left, top, right, bottom = roi_pixels(frame.shape, roi)
    crop = frame[top:bottom, left:right]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    usable = min(gray.shape) >= 16 and float(np.std(gray)) >= 4
    score = float(cv2.Laplacian(cv2.GaussianBlur(gray, (3, 3), 0), cv2.CV_64F).var()) if usable else None
    return dict(method=METHOD, score=score, roi=list(roi), pixels=[left, top, right, bottom],
                low_texture=not usable), crop
