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
