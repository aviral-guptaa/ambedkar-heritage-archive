"""Image preprocessing for degraded historical scans.

The pipeline targets the failure modes named in the SIH brief: aged paper,
ink smudges, ink bleed, fading, stains, folds, warped surfaces, uneven
illumination and camera rotation. Every step is individually switchable and the
applied steps are reported alongside the OCR result so the UI can show what was
actually done.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps

#: Ordered recipe applied to every scan. ``best`` is used for manuscripts.
PRESETS: dict[str, list[str]] = {
    "none": [],
    "light": ["grayscale", "orient", "denoise"],
    "standard": [
        "grayscale",
        "orient",
        "denoise",
        "flatfield",
        "clahe",
        "sharpen",
    ],
    "manuscript": [
        "grayscale",
        "orient",
        "denoise",
        "flatfield",
        "clahe",
        "sharpen",
        "binarize_adaptive",
    ],
    "binarize": [
        "grayscale",
        "orient",
        "denoise",
        "flatfield",
        "binarize_otsu",
    ],
}

#: Text-block detection prefers a light (non-binarized) image.
PRESETS_FOR_MODE: dict[str, str] = {
    "document": "standard",
    "manuscript": "manuscript",
    "printed": "light",
}


@dataclass(slots=True)
class PreprocessResult:
    image: np.ndarray
    steps: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_png(self) -> bytes:
        ok, buf = cv2.imencode(".png", self.image)
        if not ok:  # pragma: no cover - cv2 failure
            raise RuntimeError("Failed to encode processed image")
        return buf.tobytes()


def decode_image(data: bytes) -> np.ndarray:
    """Decode bytes to a BGR array, honouring EXIF rotation."""
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        arr = np.array(im)
    if arr.ndim == 2:
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def estimate_skew(gray: np.ndarray) -> float:
    """Estimate page skew in degrees using the dominant text-line direction."""
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    lines = cv2.HoughLinesP(
        binary,
        rho=1,
        theta=np.pi / 1800,
        threshold=120,
        minLineLength=max(40, gray.shape[1] // 8),
        maxLineGap=20,
    )
    if lines is None or len(lines) == 0:
        return 0.0
    coords = np.asarray(lines).reshape(-1, 4).astype(np.float64)
    x1, y1, x2, y2 = coords[:, 0], coords[:, 1], coords[:, 2], coords[:, 3]
    angles = np.degrees(np.arctan2(y2 - y1, x2 - x1))
    angles = angles[np.abs(angles) < 25]
    if angles.size == 0:
        return 0.0
    return float(np.median(angles))


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def preprocess(
    data: bytes,
    *,
    preset: str = "standard",
    max_dimension: int = 2600,
    target_dpi: int | None = None,
) -> PreprocessResult:
    """Run the configured recipe over an encoded image."""
    steps: list[str] = []
    diagnostics: dict[str, Any] = {}
    bgr = decode_image(data)
    h0, w0 = bgr.shape[:2]
    diagnostics["input_size"] = [w0, h0]

    scale = 1.0
    if max_dimension and max(h0, w0) > max_dimension:
        scale = max_dimension / float(max(h0, w0))
        bgr = cv2.resize(
            bgr, (int(w0 * scale), int(h0 * scale)), interpolation=cv2.INTER_AREA
        )
        steps.append("downscale")
    if scale != 1.0:
        diagnostics["scale"] = round(scale, 4)
        diagnostics["output_size"] = [bgr.shape[1], bgr.shape[0]]

    gray = _to_gray(bgr)
    recipe = PRESETS.get(preset)
    if recipe is None:
        raise ValueError(f"Unknown preprocessing preset: {preset}")

    for step in recipe:
        if step == "grayscale":
            steps.append("grayscale")
        elif step == "orient":
            angle = estimate_skew(gray)
            diagnostics["skew_degrees"] = round(angle, 3)
            if abs(angle) >= 0.4:
                h, w = gray.shape[:2]
                m = cv2.getRotationMatrix2D((w / 2, h / 2), -angle, 1.0)
                gray = cv2.warpAffine(
                    gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
                )
                steps.append("deskew")
        elif step == "denoise":
            gray = cv2.fastNlMeansDenoising(gray, None, h=12, templateWindowSize=7, searchWindowSize=21)
            steps.append("denoise")
        elif step == "flatfield":
            # Estimate the paper/background illumination and divide it out.
            kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (max(31, gray.shape[1] // 20) | 1, max(31, gray.shape[1] // 20) | 1)
            )
            background = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
            background = cv2.GaussianBlur(background, (0, 0), sigmaX=25)
            bg_float = np.maximum(background.astype(np.float32), 1.0)
            corrected = cv2.divide(gray.astype(np.float32), bg_float, scale=255)
            gray = np.clip(corrected, 0, 255).astype(np.uint8)
            illumination = float(np.std(background.astype(np.float32)))
            diagnostics["illumination_std"] = round(illumination, 2)
            steps.append("flatfield")
        elif step == "clahe":
            clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
            gray = clahe.apply(gray)
            steps.append("clahe")
        elif step == "sharpen":
            blurred = cv2.GaussianBlur(gray, (0, 0), sigmaX=1.2)
            gray = cv2.addWeighted(gray, 1.6, blurred, -0.6, 0)
            steps.append("sharpen")
        elif step == "binarize_otsu":
            gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
            steps.append("binarize_otsu")
        elif step == "binarize_adaptive":
            block = max(31, (min(gray.shape[:2]) // 20) | 1)
            gray = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, 12
            )
            steps.append("binarize_adaptive")

    if gray.ndim == 2:
        out = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    else:
        out = gray
    diagnostics["recipe"] = preset
    if target_dpi:
        diagnostics["target_dpi"] = target_dpi
    return PreprocessResult(image=out, steps=steps, diagnostics=diagnostics)


# --------------------------------------------------------------------------- #
# layout analysis
# --------------------------------------------------------------------------- #

REGION_NAMES = ("title", "text", "table", "figure", "marginalia", "unknown")


def analyse_layout(gray: np.ndarray) -> dict[str, Any]:
    """Coarse layout analysis: text density profile + line/word/region counts."""
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    h, w = binary.shape[:2]
    density = float(np.count_nonzero(binary) / max(1, binary.size))

    kernel_h = max(3, h // 40) | 1
    row_profile = (binary > 0).sum(axis=1).astype(np.float32)
    smooth = cv2.GaussianBlur(row_profile.reshape(-1, 1), (1, kernel_h), 0).ravel()
    thresh = max(1.0, float(smooth.mean() * 0.25))
    ink_rows = smooth > thresh
    text_line_count = int(np.count_nonzero(np.diff(ink_rows.astype(np.int8)) == 1))

    col_profile = (binary > 0).sum(axis=0).astype(np.float32)
    col_thresh = max(1.0, float(col_profile.mean() * 0.15))
    col_ink = col_profile > col_thresh
    text_col_count = int(np.count_nonzero(np.diff(col_ink.astype(np.int8)) == 1))

    # Column detection via a coarse vertical-projection valley search.
    columns = 1
    if w > 400:
        valleys = _find_projection_valleys(col_profile)
        columns = 1 + len(valleys)

    dominant = "text"
    if density < 0.012:
        dominant = "unknown"
    elif text_line_count <= 3 and density > 0.05:
        dominant = "figure" if density > 0.15 else "title"

    return {
        "ink_density": round(density, 5),
        "text_lines": text_line_count,
        "text_blocks": max(1, text_col_count // 3) if col_ink.any() else 0,
        "columns": columns,
        "dominant_region": dominant,
        "orientation": "portrait" if h >= w else "landscape",
        "page_size_px": [int(w), int(h)],
    }


def _find_projection_valleys(profile: np.ndarray, min_gap_frac: float = 0.08) -> list[int]:
    if profile.size < 10:
        return []
    smooth = cv2.GaussianBlur(profile.reshape(1, -1), (0, 0), sigmaX=3).ravel()
    norm = smooth / max(1.0, float(smooth.max()))
    threshold = max(0.02, float(norm.mean() * 0.5))
    min_gap = int(len(norm) * min_gap_frac)
    valleys: list[int] = []
    i = 0
    while i < len(norm):
        if norm[i] <= threshold:
            j = i
            while j < len(norm) and norm[j] <= threshold:
                j += 1
            if (j - i) >= min_gap and i > 0 and j < len(norm):
                valleys.append((i + j) // 2)
            i = j
        else:
            i += 1
    return valleys


def make_thumbnail(image: np.ndarray, max_side: int = 480) -> bytes:
    h, w = image.shape[:2]
    scale = min(1.0, max_side / float(max(h, w)))
    if scale < 1.0:
        image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    if not ok:  # pragma: no cover
        raise RuntimeError("thumbnail encode failed")
    return buf.tobytes()
