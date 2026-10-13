"""Small, bounded adjustments; acquisition measurements remain untouched."""

import math
from PIL import ImageEnhance


def adjust(image, exposure=0.0, contrast=1.0, color=1.0, sharpness=1.0):
    values = (exposure, contrast, color, sharpness)
    if not all(math.isfinite(float(v)) for v in values) or not (-2 <= exposure <= 2 and .5 <= contrast <= 1.5 and 0 <= color <= 2 and 0 <= sharpness <= 2):
        raise ValueError("Choose exposure −2 to +2, contrast 0.5–1.5, color/sharpness 0–2.")
    image = ImageEnhance.Brightness(image).enhance(2 ** exposure)
    image = ImageEnhance.Contrast(image).enhance(contrast)
    image = ImageEnhance.Color(image).enhance(color)
    return ImageEnhance.Sharpness(image).enhance(sharpness)


def plain_background_crop(image, tolerance=30, padding=0.10):
    """Suggest an exclusive pixel crop; never modify the image or apply the crop.

    Assumes the border is mostly a single backdrop color. Only border-connected
    matching pixels count as background, preserving matching areas inside a mini.
    """
    import cv2
    import numpy as np

    if not 5 <= float(tolerance) <= 80 or not 0 <= float(padding) <= .5:
        raise ValueError("Choose background tolerance 5–80 and padding 0–50%.")
    sample = image.convert("RGB")
    sample.thumbnail((1600, 1600))
    pixels = np.asarray(sample).astype(np.float32)
    height, width = pixels.shape[:2]
    if min(width, height) < 16:
        raise ValueError("The photo is too small to detect a miniature.")
    border = np.concatenate((pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]))
    background = np.median(border, axis=0)
    if np.percentile(np.max(np.abs(border - background), axis=1), 90) > tolerance:
        raise ValueError("The background is not uniform around the photo edges. Try a manual crop.")
    matching = (np.max(np.abs(pixels - background), axis=2) <= tolerance).astype(np.uint8)
    _, labels = cv2.connectedComponents(matching, connectivity=8)
    edge_labels = np.unique(np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1])))
    edge_labels = edge_labels[edge_labels != 0]
    foreground = (~np.isin(labels, edge_labels)).astype(np.uint8)
    count, components, stats, _ = cv2.connectedComponentsWithStats(foreground, connectivity=8)
    minimum_area = max(9, int(width * height * .00001))
    keep = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= minimum_area]
    mask = np.isin(components, keep)
    if not mask.any() or mask.mean() > .90:
        raise ValueError("Could not distinguish the miniature from the background. Try another tolerance or crop manually.")
    ys, xs = np.where(mask)
    left, top, right, bottom = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    margin = max(right - left, bottom - top) * padding
    sx, sy = image.width / width, image.height / height
    return (max(0, math.floor((left - margin) * sx)),
            max(0, math.floor((top - margin) * sy)),
            min(image.width, math.ceil((right + margin) * sx)),
            min(image.height, math.ceil((bottom + margin) * sy)))
