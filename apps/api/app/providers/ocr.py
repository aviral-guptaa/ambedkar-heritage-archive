"""OCR providers.

Two real engines are supported:

* ``tesseract`` — the reference open-source OCR. Multilingual trained data
  (``eng``, ``hin``, ``mar``, ``pan``, ``guj``, ``san``, ... ) makes it the
  default for Indian-language historical material.
* ``rapidocr`` — PP-OCR models executed through onnxruntime. Useful where
  Tesseract is unavailable; the recognition head is configurable because the
  bundled default is Chinese-only and must not be trusted for Devanagari.

If neither engine is installed the provider raises ``ProviderUnavailable`` and
the API reports ``available: false``. It never returns invented text.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.base import (
    OCRBlock,
    OCRProvider,
    OCRResult,
    ProviderUnavailable,
)
from app.services.ocr.preprocess import (
    PRESETS,
    PRESETS_FOR_MODE,
    analyse_layout,
    decode_image,
    preprocess,
)

log = get_logger(__name__)

TESSERACT_LANG_MAP = {
    "en": "eng",
    "hi": "hin",
    "mr": "mar",
    "gu": "guj",
    "pa": "pan",
    "bn": "ben",
    "or": "ori",
    "ta": "tam",
    "te": "tel",
    "kn": "kan",
    "ml": "mal",
    "ur": "urd",
    "sa": "san",
    "sa ": "san",
    "si": "sin",
    "as": "asm",
    "ne": "nep",
    "bo": "bod",
    "doi": "dgr",
    "ks": "kas",
    "mai": "mai",
    "sat": "sat",
    "kok": "kok",
    "mni": "mni",
}


def normalise_tesseract_lang(language: str | None) -> str:
    if not language:
        return settings.ocr_default_language
    parts: list[str] = []
    for chunk in re_split_langs(language):
        mapped = TESSERACT_LANG_MAP.get(chunk.lower(), chunk.lower()[:3])
        if mapped not in parts:
            parts.append(mapped)
    return "+".join(parts) or settings.ocr_default_language


def re_split_langs(language: str) -> list[str]:
    return [p for p in language.replace(";", ",").replace("+", ",").split(",") if p.strip()]


class TesseractOCRProvider:
    """Tesseract wrapper. Requires the ``tesseract`` binary on PATH."""

    name = "tesseract"

    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary or shutil.which("tesseract") or ""
        self._version: str | None = None

    def is_available(self) -> bool:
        return bool(self.binary) and Path(self.binary).exists()

    def version(self) -> str | None:
        if not self.is_available():
            return None
        if self._version is None:
            try:
                out = subprocess.run(
                    [self.binary, "--version"], capture_output=True, text=True, timeout=15
                )
                self._version = (out.stdout or out.stderr).splitlines()[0].strip()
            except Exception:  # noqa: BLE001  # pragma: no cover
                self._version = "unknown"
        return self._version

    def languages(self) -> list[str]:
        if not self.is_available():
            return []
        try:
            out = subprocess.run(
                [self.binary, "--list-langs"], capture_output=True, text=True, timeout=20
            )
            return [ln.strip() for ln in out.stdout.splitlines()[1:] if ln.strip()]
        except Exception:  # noqa: BLE001  # pragma: no cover
            return []

    def _installed(self) -> set[str]:
        return set(self.languages())

    def recognize(self, image_bytes: bytes, language: str | None = None) -> OCRResult:
        if not self.is_available():
            raise ProviderUnavailable(
                "Tesseract is not installed on this machine. Install it with "
                "`brew install tesseract tesseract-lang` (macOS) or "
                "`apt install tesseract-ocr tesseract-ocr-all` (Debian)."
            )
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = self.binary
        started = time.perf_counter()
        lang = normalise_tesseract_lang(language)
        installed = self._installed()
        if installed:
            requested = lang.split("+")
            available = [l for l in requested if l in installed]
            if not available:
                # Honest failure: report which language data is missing.
                raise ProviderUnavailable(
                    f"Tesseract language data for '{lang}' is not installed. "
                    f"Installed: {', '.join(sorted(installed)[:12])}…"
                )
            lang = "+".join(available)

        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as im:
            im = im.convert("RGB")
            config = "--oem 1 --psm 3 -c preserve_interword_spaces=1"
            text = pytesseract.image_to_string(im, lang=lang, config=config)
            data = pytesseract.image_to_data(
                im, lang=lang, output_type=pytesseract.Output.DICT, config=config
            )

        blocks: list[OCRBlock] = []
        confidences: list[float] = []
        n = len(data.get("text", []))
        for i in range(n):
            word = (data["text"][i] or "").strip()
            if not word:
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1.0
            if conf < 0:
                continue
            confidences.append(conf / 100.0)
            x, y, w, h = (
                int(data["left"][i]),
                int(data["top"][i]),
                int(data["width"][i]),
                int(data["height"][i]),
            )
            blocks.append(
                OCRBlock(
                    text=word,
                    confidence=round(conf / 100.0, 4),
                    box=[[x, y], [x + w, y], [x + w, y + h], [x, y + h]],
                    language=lang.split("+")[0],
                    block_type="word",
                )
            )
        gray = cv2.cvtColor(np.array(im.convert("RGB")), cv2.COLOR_RGB2GRAY)
        layout = analyse_layout(gray)
        return OCRResult(
            text=text,
            confidence=round(float(np.mean(confidences)), 4) if confidences else 0.0,
            engine=self.name,
            engine_version=self.version(),
            language=lang,
            blocks=blocks,
            layout=layout,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


class RapidOCRProvider:
    """PP-OCR via onnxruntime.

    ``OCR_RAPIDOCR_REC_MODEL`` may point at a recognition head for the script you
    actually need (e.g. a Devanagari model). The bundled default is Chinese-only
    and reports low confidence on other scripts, which the UI surfaces rather
    than hiding.
    """

    name = "rapidocr"

    def __init__(self) -> None:
        self._engine: Any | None = None
        self._init_error: str | None = None

    def is_available(self) -> bool:
        try:
            import rapidocr_onnxruntime  # noqa: F401
        except Exception:  # noqa: BLE001
            return False
        return True

    def _ensure_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        if self._init_error:
            raise ProviderUnavailable(self._init_error)
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception as exc:  # noqa: BLE001
            self._init_error = f"rapidocr-onnxruntime is not installed ({exc})."
            raise ProviderUnavailable(self._init_error) from exc
        kwargs: dict[str, Any] = {}
        rec_model = getattr(settings, "rapidocr_rec_model", "") or ""
        if rec_model:
            kwargs["rec_model_path"] = rec_model
        try:
            self._engine = RapidOCR(**kwargs)
        except Exception as exc:  # noqa: BLE001
            self._init_error = f"RapidOCR engine failed to initialise: {exc}"
            raise ProviderUnavailable(self._init_error) from exc
        return self._engine

    def languages(self) -> list[str]:
        return ["zh", "en", "multi"]

    def recognize(self, image_bytes: bytes, language: str | None = None) -> OCRResult:
        engine = self._ensure_engine()
        started = time.perf_counter()
        bgr = decode_image(image_bytes)
        result = engine(bgr)
        rows = result[0] if isinstance(result, tuple) else result
        blocks: list[OCRBlock] = []
        confidences: list[float] = []
        text_parts: list[str] = []
        if rows:
            for row in rows:
                box, text, score = row[0], row[1], float(row[2])
                if not text:
                    continue
                text_parts.append(str(text).strip())
                confidences.append(score)
                blocks.append(
                    OCRBlock(
                        text=str(text).strip(),
                        confidence=round(score, 4),
                        box=[[float(p[0]), float(p[1])] for p in box],
                        block_type="line",
                    )
                )
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        return OCRResult(
            text="\n".join(text_parts),
            confidence=round(float(np.mean(confidences)), 4) if confidences else 0.0,
            engine=self.name,
            engine_version="onnxruntime",
            language=language,
            blocks=blocks,
            layout=analyse_layout(gray),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


class NullOCRProvider:
    """Explicitly unavailable. Used so the API can answer truthfully."""

    name = "none"

    def is_available(self) -> bool:
        return False

    def languages(self) -> list[str]:
        return []

    def recognize(self, image_bytes: bytes, language: str | None = None) -> OCRResult:
        raise ProviderUnavailable(
            "No OCR engine is installed. Set OCR_PROVIDER and install Tesseract "
            "(`brew install tesseract tesseract-lang`) or `pip install rapidocr-onnxruntime`."
        )


@lru_cache(maxsize=1)
def get_ocr_provider() -> OCRProvider:
    choice = settings.ocr_provider
    if choice == "none":
        return NullOCRProvider()
    if choice == "tesseract":
        return TesseractOCRProvider()
    if choice == "rapidocr":
        return RapidOCRProvider()
    tess = TesseractOCRProvider()
    if tess.is_available():
        return tess
    rapid = RapidOCRProvider()
    if rapid.is_available():
        return rapid
    return NullOCRProvider()


def run_ocr(
    image_bytes: bytes,
    *,
    language: str | None = None,
    preset: str = "standard",
    mode: str = "document",
) -> dict[str, Any]:
    """Full OCR step: preprocess (optional) + engine + layout analysis."""
    provider = get_ocr_provider()
    if not provider.is_available():
        provider.recognize(image_bytes, language)  # raises ProviderUnavailable

    resolved_preset = preset or PRESETS_FOR_MODE.get(mode, "standard")
    pre: dict[str, Any] = {"applied": False, "recipe": None, "steps": [], "diagnostics": {}}
    engine_input = image_bytes
    if settings.ocr_preprocess:
        result = preprocess(
            image_bytes,
            preset=resolved_preset,
            max_dimension=settings.ocr_max_dimension,
        )
        engine_input = result.to_png()
        pre = {
            "applied": True,
            "recipe": resolved_preset,
            "steps": result.steps,
            "diagnostics": result.diagnostics,
        }
    ocr = provider.recognize(engine_input, language)
    return {
        "text": ocr.text,
        "confidence": ocr.confidence,
        "engine": ocr.engine,
        "engine_version": ocr.engine_version,
        "language": ocr.language,
        # OCRBlock is a slots dataclass, so vars() cannot read it; use the
        # field names directly.
        "blocks": [
            {
                "text": b.text,
                "confidence": b.confidence,
                "box": b.box,
                "language": b.language,
                "block_type": b.block_type,
            }
            for b in ocr.blocks
        ],
        "layout": ocr.layout,
        "duration_ms": ocr.duration_ms,
        "preprocessing": pre,
    }


def ocr_capability() -> dict[str, Any]:
    provider = get_ocr_provider()
    available = provider.is_available()
    detail = ""
    engines: list[dict[str, Any]] = []
    tess = TesseractOCRProvider()
    engines.append(
        {
            "name": "tesseract",
            "available": tess.is_available(),
            "version": tess.version(),
            "languages": tess.languages()[:40],
        }
    )
    rapid = RapidOCRProvider()
    engines.append({"name": "rapidocr", "available": rapid.is_available(), "version": None})
    if available:
        detail = f"{provider.name} active"
    else:
        detail = "No OCR engine installed. OCR features are disabled."
    return {
        "available": available,
        "provider": provider.name,
        "detail": detail,
        "engines": engines,
        "preprocessing_presets": sorted(PRESETS.keys()),
    }


def _tmp_write(data: bytes, suffix: str = ".png") -> str:  # pragma: no cover - helper
    fd, path = tempfile.mkstemp(suffix=suffix)
    with open(fd, "wb") as fh:  # noqa: PTH123
        fh.write(data)
    return path
