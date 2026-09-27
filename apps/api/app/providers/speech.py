"""Speech-to-text, text-to-speech and translation providers.

None of these are required for the core archive to work. When a provider is not
configured the capability endpoint reports ``available: false`` with an
explanation, and the UI disables the control with a reason instead of faking a
recording. The browser also has a first-party Web Speech path (used by the
front-end when ``STT_PROVIDER=none``) which needs no server model at all.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.base import (
    ProviderUnavailable,
    STTProvider,
    STTResult,
    TranslationProvider,
    TranslationResult,
    TTSProvider,
    TTSResult,
)

log = get_logger(__name__)

LANG_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "mr": "Marathi",
    "gu": "Gujarati",
    "bn": "Bengali",
    "pa": "Punjabi",
    "ta": "Tamil",
    "te": "Telugu",
    "kn": "Kannada",
    "ml": "Malayalam",
    "or": "Odia",
    "ur": "Urdu",
    "sa": "Sanskrit",
    "pi": "Pali",
}


# --------------------------------------------------------------------------- #
# STT
# --------------------------------------------------------------------------- #


class _HTTPWhisperSTT:
    """OpenAI-compatible ``/audio/transcriptions`` endpoint.

    Works with whisper.cpp's HTTP server, faster-whisper-server, Speaches and
    the OpenAI API itself — no vendor locked in.
    """

    name = "whisper_http"

    def __init__(self) -> None:
        self.base_url = settings.stt_base_url.rstrip("/")
        self.api_key = settings.stt_api_key
        self.model = settings.stt_model

    def is_available(self) -> bool:
        return bool(self.base_url)

    def health(self) -> tuple[bool, str]:
        try:
            with httpx.Client(timeout=4.0) as client:
                r = client.get(f"{self.base_url}/models")
            return (r.status_code < 500, f"HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            return (False, str(exc)[:120])

    def transcribe(self, audio_bytes: bytes, language: str | None = None) -> STTResult:
        if not self.is_available():
            raise ProviderUnavailable("STT_BASE_URL is not configured.")
        suffix = ".webm"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            path = tmp.name
        started = time.perf_counter()
        try:
            with open(path, "rb") as fh:  # noqa: PTH123
                files = {"file": (f"audio{suffix}", fh, "application/octet-stream")}
                data = {"model": self.model, "response_format": "verbose_json"}
                if language:
                    data["language"] = language
                headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
                with httpx.Client(timeout=180.0) as client:
                    r = client.post(
                        f"{self.base_url}/audio/transcriptions",
                        files=files,
                        data=data,
                        headers=headers,
                    )
                r.raise_for_status()
                payload = r.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderUnavailable(
                f"STT endpoint returned HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailable(f"STT endpoint unreachable: {exc}") from exc
        finally:
            Path(path).unlink(missing_ok=True)

        return STTResult(
            text=str(payload.get("text") or "").strip(),
            language=payload.get("language") or language,
            confidence=_avg_confidence(payload.get("segments")),
            provider=self.name,
            model=self.model,
            duration_ms=int((time.perf_counter() - started) * 1000),
            segments=[
                {
                    "start": s.get("start"),
                    "end": s.get("end"),
                    "text": s.get("text"),
                }
                for s in (payload.get("segments") or [])
            ],
        )


def _avg_confidence(segments: object) -> float | None:
    if not isinstance(segments, list) or not segments:
        return None
    values = [
        float(s["avg_logprob"]) for s in segments if isinstance(s, dict) and "avg_logprob" in s
    ]
    if not values:
        return None
    # Convert avg logprob (≤0) into a 0-1 proxy; documented as a proxy, not a
    # calibrated probability.
    import math

    return round(min(1.0, math.exp(sum(values) / len(values))), 4)


class BrowserSpeechSTT:
    """Documents that transcription happens in the browser via Web Speech API.

    Returns unavailable server-side; the front-end uses this capability value to
    decide whether to use the native browser recogniser.
    """

    name = "browser_web_speech"

    def is_available(self) -> bool:
        return True

    def transcribe(self, audio_bytes: bytes, language: str | None = None) -> STTResult:
        raise ProviderUnavailable(
            "Server-side STT is disabled. Set STT_PROVIDER=whisper_http to transcribe on the "
            "server, or use the browser's built-in speech recognition."
        )


# --------------------------------------------------------------------------- #
# TTS
# --------------------------------------------------------------------------- #


class PiperTTSProvider:
    """Piper. Prefers an HTTP endpoint, falls back to the local binary."""

    name = "piper"

    def __init__(self) -> None:
        self.base_url = settings.tts_base_url.rstrip("/")
        self.api_key = settings.tts_api_key
        self.binary = shutil.which("piper") or ""
        self.voice_dir = Path.home() / ".local" / "share" / "piper-voices"

    def is_available(self) -> bool:
        return bool(self.base_url) or bool(self.binary)

    def supports(self, language: str) -> bool:
        return language in settings.tts_voice_map

    def health(self) -> tuple[bool, str]:
        if self.base_url:
            try:
                with httpx.Client(timeout=4.0) as client:
                    r = client.get(f"{self.base_url}/voices")
                if r.status_code < 400:
                    return (True, f"HTTP {r.status_code} at {self.base_url}")
                return (False, f"HTTP {r.status_code}")
            except Exception as exc:  # noqa: BLE001
                if not self.binary:
                    return (False, str(exc)[:120])
        if self.binary:
            return (True, f"local binary at {self.binary}")
        return (False, "neither TTS_BASE_URL nor a piper binary is available")

    def synthesize(self, text: str, language: str) -> TTSResult:
        voice = settings.tts_voice_map.get(language)
        if not voice:
            raise ProviderUnavailable(
                f"No Piper voice configured for '{language}'. Add it to TTS_VOICE_MAP."
            )
        started = time.perf_counter()
        if self.base_url:
            try:
                with httpx.Client(timeout=120.0) as client:
                    r = client.post(
                        f"{self.base_url}/synthesize",
                        json={"text": text, "voice": voice},
                        headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                    )
                r.raise_for_status()
                return TTSResult(
                    audio=r.content,
                    mime_type=r.headers.get("content-type", "audio/wav"),
                    provider=self.name,
                    model=voice,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            except Exception as exc:  # noqa: BLE001
                if not self.binary:
                    raise ProviderUnavailable(f"Piper endpoint unreachable: {exc}") from exc
        if not self.binary:
            raise ProviderUnavailable("Piper is not installed (binary `piper` not on PATH).")
        model = self.voice_dir / f"{voice}.onnx"
        if not model.is_file():
            raise ProviderUnavailable(
                f"Piper voice file not found: {model}. Download the voice with "
                "`python3 -m piper.download_voices en_IN-x_low`."
            )
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.wav"
            proc = subprocess.run(
                [self.binary, "--model", str(model), "--output_file", str(out)],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=180,
            )
            if proc.returncode != 0 or not out.is_file():
                raise ProviderUnavailable(
                    f"piper failed (exit {proc.returncode}): {proc.stderr.decode()[:200]}"
                )
            return TTSResult(
                audio=out.read_bytes(),
                mime_type="audio/wav",
                provider=self.name,
                model=voice,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )


class SystemTTSProvider:
    """OS speech synthesiser (``say`` on macOS, ``espeak-ng`` on Linux).

    Genuinely useful for a kiosk with no model download. Language coverage
    depends entirely on the voices installed on the host, and
    :meth:`supports` reports that honestly.
    """

    name = "system"

    def __init__(self) -> None:
        self.mac_say = shutil.which("say")
        self.espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        self._voices: list[str] | None = None

    def is_available(self) -> bool:
        return bool(self.mac_say or self.espeak)

    def _available_voices(self) -> list[str]:
        if self._voices is not None:
            return self._voices
        voices: list[str] = []
        if self.mac_say:
            try:
                out = subprocess.run(
                    [self.mac_say, "-v", "?"], capture_output=True, text=True, timeout=15
                )
                voices = [ln.strip().split()[0] for ln in out.stderr.splitlines() if ln.strip()]
            except Exception:  # noqa: BLE001  # pragma: no cover
                voices = []
        self._voices = voices
        return voices

    def supports(self, language: str) -> bool:
        if self.espeak:
            return True
        prefix = f"{language}_"
        return any(v.startswith(prefix) for v in self._available_voices())

    def synthesize(self, text: str, language: str) -> TTSResult:
        started = time.perf_counter()
        if self.mac_say:
            voice = next(
                (v for v in self._available_voices() if v.startswith(f"{language}_")),
                None,
            )
            with tempfile.TemporaryDirectory() as tmpdir:
                out = Path(tmpdir) / "out.aiff"
                cmd = [self.mac_say, "-o", str(out), "--data-format=LEI16@22050"]
                if voice:
                    cmd += ["-v", voice]
                cmd.append(text)
                proc = subprocess.run(cmd, capture_output=True, timeout=300)
                if proc.returncode != 0 or not out.is_file():
                    raise ProviderUnavailable(
                        f"system TTS failed: {proc.stderr.decode()[:200]}"
                    )
                return TTSResult(
                    audio=out.read_bytes(),
                    mime_type="audio/aiff",
                    provider=self.name,
                    model=voice or "system-default",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
        if self.espeak:
            with tempfile.TemporaryDirectory() as tmpdir:
                out = Path(tmpdir) / "out.wav"
                proc = subprocess.run(
                    [self.espeak, "-v", language, "-w", str(out), text],
                    capture_output=True,
                    timeout=300,
                )
                if proc.returncode != 0 or not out.is_file():
                    raise ProviderUnavailable("espeak failed")
                return TTSResult(
                    audio=out.read_bytes(),
                    mime_type="audio/wav",
                    provider=self.name,
                    model="espeak-ng",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
        raise ProviderUnavailable("No system speech synthesiser found (looked for say/espeak).")


class OpenAICompatibleTTS:
    name = "openai_tts"

    def __init__(self) -> None:
        self.base_url = settings.tts_base_url.rstrip("/")
        self.api_key = settings.tts_api_key
        self.model = settings.tts_model

    def is_available(self) -> bool:
        return bool(self.base_url)

    def supports(self, language: str) -> bool:
        return language in settings.tts_voice_map

    def synthesize(self, text: str, language: str) -> TTSResult:
        voice = settings.tts_voice_map.get(language, language)
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=180.0) as client:
                r = client.post(
                    f"{self.base_url}/audio/speech",
                    json={"model": self.model, "voice": voice, "input": text},
                    headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                )
            r.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailable(f"TTS endpoint unreachable: {exc}") from exc
        return TTSResult(
            audio=r.content,
            mime_type=r.headers.get("content-type", "audio/mpeg"),
            provider=self.name,
            model=self.model,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


class NullTTS:
    name = "none"

    def is_available(self) -> bool:
        return False

    def supports(self, language: str) -> bool:
        return False

    def synthesize(self, text: str, language: str) -> TTSResult:
        raise ProviderUnavailable(
            "Text-to-speech is not configured. Set TTS_PROVIDER=system (uses the OS voice) "
            "or TTS_PROVIDER=piper, or use the browser's built-in speech synthesis."
        )


# --------------------------------------------------------------------------- #
# translation
# --------------------------------------------------------------------------- #


class LLMTranslationProvider:
    name = "llm"

    def __init__(self) -> None:
        from app.providers.llm import get_llm_provider

        self._llm = get_llm_provider()

    def is_available(self) -> bool:
        return getattr(self._llm, "name", "") != "extractive"

    def translate(self, text: str, source_language: str, target_language: str) -> TranslationResult:
        if not self.is_available():
            raise ProviderUnavailable(
                "LLM translation requires a generative LLM (LLM_PROVIDER=ollama or "
                "openai_compatible)."
            )
        from app.providers.base import LLMMessage

        started = time.perf_counter()
        result = self._llm.generate(  # type: ignore[call-arg]
            [
                LLMMessage(
                    role="system",
                    content=(
                        "You are a careful translator for a heritage archive. Translate the "
                        "user's text faithfully. Preserve proper nouns, article numbers and "
                        "quoted material exactly. Do not add commentary."
                    ),
                ),
                LLMMessage(
                    role="user",
                    content=(
                        f"Translate from {LANG_NAMES.get(source_language, source_language)} "
                        f"to {LANG_NAMES.get(target_language, target_language)}.\n\n{text}"
                    ),
                ),
            ],
            temperature=0.0,
            max_tokens=1200,
        )
        return TranslationResult(
            text=result.text,
            source_language=source_language,
            target_language=target_language,
            provider=self.name,
            model=result.model,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


class IndicNMTTranslationProvider:
    name = "indic_nmt"

    def __init__(self) -> None:
        self._pipeline = None
        self._error: str | None = None

    def is_available(self) -> bool:
        try:
            import transformers  # noqa: F401
        except Exception:  # noqa: BLE001
            return False
        return True

    def _ensure(self):  # noqa: ANN202
        if self._pipeline is not None:
            return self._pipeline
        try:
            from transformers import pipeline

            self._pipeline = pipeline("translation", model=settings.translation_model)
        except Exception as exc:  # noqa: BLE001
            self._error = f"indic translation model unavailable: {exc}"
            raise ProviderUnavailable(self._error) from exc
        return self._pipeline

    def translate(self, text: str, source_language: str, target_language: str) -> TranslationResult:
        started = time.perf_counter()
        pipe = self._ensure()
        out = pipe(f"<2{ target_language}> {text}")
        translated = out[0]["translation_text"]
        translated = translated.split(">", 1)[-1].strip() if ">" in translated else translated
        return TranslationResult(
            text=translated,
            source_language=source_language,
            target_language=target_language,
            provider=self.name,
            model=settings.translation_model,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


class NullTranslation:
    name = "none"

    def is_available(self) -> bool:
        return False

    def translate(self, text: str, source_language: str, target_language: str) -> TranslationResult:
        raise ProviderUnavailable(
            "Translation is not configured. Set TRANSLATION_PROVIDER=llm (with a local LLM) "
            "or indic_nmt. Original text is always preserved and served unchanged."
        )


# --------------------------------------------------------------------------- #
# factories
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def get_stt_provider() -> STTProvider:
    if settings.stt_provider in ("whisper_http", "openai_compatible"):
        return _HTTPWhisperSTT()
    return BrowserSpeechSTT()


@lru_cache(maxsize=1)
def get_tts_provider() -> TTSProvider:
    if settings.tts_provider == "piper":
        return PiperTTSProvider()
    if settings.tts_provider == "openai_compatible":
        return OpenAICompatibleTTS()
    if settings.tts_provider == "system":
        return SystemTTSProvider()
    return NullTTS()


@lru_cache(maxsize=1)
def get_translation_provider() -> TranslationProvider:
    if settings.translation_provider == "llm":
        return LLMTranslationProvider()
    if settings.translation_provider == "indic_nmt":
        return IndicNMTTranslationProvider()
    return NullTranslation()


def speech_capability() -> dict:
    stt = get_stt_provider()
    tts = get_tts_provider()
    tr = get_translation_provider()
    stt_note = "Browser Web Speech API (no server model required)."
    if hasattr(stt, "health"):
        ok, msg = stt.health()  # type: ignore[attr-defined]
        stt_note = f"Configured at {settings.stt_base_url}: {msg}" if ok else f"Unreachable: {msg}"
    tts_note = "Not configured."
    if hasattr(tts, "health"):
        ok, msg = tts.health()  # type: ignore[attr-defined]
        tts_note = msg
    elif tts.is_available():
        tts_note = f"{tts.name} available"
    return {
        "stt": {
            "available": stt.is_available(),
            "provider": stt.name,
            "model": settings.stt_model,
            "languages": settings.stt_languages,
            "detail": stt_note,
        },
        "tts": {
            "available": tts.is_available(),
            "provider": tts.name,
            "model": settings.tts_model,
            "languages": sorted(settings.tts_voice_map),
            "detail": tts_note,
        },
        "translation": {
            "available": tr.is_available(),
            "provider": tr.name,
            "model": settings.translation_model,
            "detail": (
                "Not configured. Original text is always preserved."
                if not tr.is_available()
                else f"{tr.name} / {settings.translation_model}"
            ),
        },
    }
