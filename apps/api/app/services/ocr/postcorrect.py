"""Post-OCR correction.

OCR of Devanagari and 1940s type produces a predictable class of errors: digit
and glyph confusion, broken conjuncts, spurious word breaks and Latin/Unicode
look-alikes. This module repairs the *mechanical* errors only.

Hard rules, because the archive must never invent words:

* the original OCR text is always stored untouched alongside the correction;
* only characters actually present in the input are ever emitted;
* an LLM may only be asked to normalise characters, never to paraphrase, and its
  output is rejected if it is not a near-anonymous character-level edit of the
  input (see ``_plausibility``);
* a diff is recorded so a human reviewer can accept or reject the change.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.base import ProviderUnavailable
from app.providers.llm import get_llm_provider

log = get_logger(__name__)

#: Devanagari digits as they most often appear in scans and their ASCII forms.
_DIGIT_MAP = {
    "०": "0", "१": "1", "२": "2", "३": "3", "४": "4",
    "५": "5", "६": "6", "७": "7", "८": "8", "९": "9",
}

#: Latin look-alikes that appear when a glyph is mis-mapped in OCR output.
_CONFUSABLES = {
    "ﬁ": "fi", "ﬂ": "fl", "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", "‐": "-", "…": "...",
    " ": " ", " ": " ", " ": " ", "": "",
    "‘": "'", "‚": "'", "′": "'",
}

_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_LONE_PUNCT = re.compile(r"\n[|,;:.\-]{1,}\s*\n")


@dataclass(slots=True)
class Correction:
    """A proposed correction plus the evidence needed to review it."""

    text: str
    changed: bool
    changes: list[dict[str, Any]] = field(default_factory=list)
    method: str = "rules"
    provider: str | None = None
    model: str | None = None
    rejected: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "changed": self.changed,
            "method": self.method,
            "provider": self.provider,
            "model": self.model,
            "rejected": self.rejected,
            "change_count": len(self.changes),
            "changes": self.changes[:50],
        }


def _plausibility(before: str, after: str) -> float:
    """How structurally similar two strings are (0-1)."""
    return SequenceMatcher(None, before, after).ratio()


def apply_character_rules(text: str, *, language: str | None = None) -> Correction:
    """Deterministic character-level repair. Always safe to run."""
    if not text.strip():
        return Correction(text=text, changed=False)

    original = text
    current = unicodedata.normalize("NFC", text)
    changes: list[dict[str, Any]] = []

    for rule, mapping in (("devanagari_digits", _DIGIT_MAP), ("confusables", _CONFUSABLES)):
        for src, dst in mapping.items():
            if src in current:
                changes.append({"rule": rule, "from": src, "to": dst, "count": current.count(src)})
                current = current.replace(src, dst)

    normalised = _MULTI_SPACE.sub(" ", current)
    if normalised != current:
        changes.append({"rule": "collapse_spaces"})
    current = normalised

    current = _LONE_PUNCT.sub("\n", current)
    current = _MULTI_NEWLINE.sub("\n\n", current)
    current = "\n".join(line.strip() for line in current.splitlines()).strip()

    if current == original:
        return Correction(text=current, changed=False, changes=[])
    return Correction(text=current, changed=True, changes=changes or [{"rule": "whitespace"}])


def correct_with_llm(
    text: str, *, language: str | None = None, model: str | None = None
) -> Correction:
    """Ask an LLM to normalise characters, then verify it really only did that."""
    provider = get_llm_provider()
    system = (
        "You repair OCR output from historical Indian-language scans. "
        "You may ONLY fix OCR artefacts: wrong characters, broken conjuncts, "
        "spurious spaces, and Latin/Devanagari look-alike confusion. "
        "You must NOT translate, summarise, expand, shorten, or reword anything, "
        "and you must NOT add or remove sentences. "
        "Preserve the original language and paragraph breaks. "
        "Output only the corrected text."
    )
    user = (
        f"Language: {language or 'unknown'}\n\n"
        f"OCR TEXT:\n{text}\n\nCORRECTED TEXT:"
    )
    try:
        result = provider.generate(
            [("user", user)],  # type: ignore[list-item]
            system=system,
            max_tokens=min(settings.llm_max_tokens, 4000),
            temperature=0.0,
        )
    except ProviderUnavailable as exc:
        return Correction(
            text=text, changed=False, method="llm", provider=provider.name, rejected=str(exc)
        )

    candidate = result.text.strip()
    if not candidate:
        return Correction(
            text=text,
            changed=False,
            method="llm",
            provider=provider.name,
            rejected="empty response",
        )

    ratio = _plausibility(text.strip(), candidate)
    length_ratio = len(candidate) / max(1, len(text.strip()))
    if ratio < settings.ocr_correction_min_similarity:
        return Correction(
            text=text,
            changed=False,
            method="llm",
            provider=provider.name,
            model=result.model,
            rejected=f"response diverged too far from the source text (similarity {ratio:.2f})",
        )
    if not (settings.ocr_correction_min_length_ratio <= length_ratio <= settings.ocr_correction_max_length_ratio):
        return Correction(
            text=text,
            changed=False,
            method="llm",
            provider=provider.name,
            model=result.model,
            rejected=f"length changed by {length_ratio:.2f}x, which suggests rewriting",
        )
    return Correction(
        text=candidate,
        changed=candidate.strip() != text.strip(),
        changes=[{"rule": "llm_character_normalisation", "applied": True, "similarity": round(ratio, 3)}],
        method="llm",
        provider=provider.name,
        model=result.model,
    )


def correct_text(text: str, *, language: str | None = None) -> Correction | None:
    """Run the configured correction pipeline. Returns ``None`` if nothing changed."""
    if not text.strip():
        return None
    rules = apply_character_rules(text, language=language)
    current = rules.text
    if not rules.changed:
        return None
    if settings.post_ocr_correction_provider == "llm":
        llm = correct_with_llm(current, language=language)
        if llm.rejected:
            log.info("llm correction rejected", reason=llm.rejected)
            return rules
        if llm.changed:
            llm.changes = [*rules.changes, *llm.changes]
            return llm
    return rules
