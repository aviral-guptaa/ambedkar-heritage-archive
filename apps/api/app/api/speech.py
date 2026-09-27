"""Speech and translation endpoints for the kiosk.

Every provider is reported truthfully. If text-to-speech is not configured the
endpoint returns ``503`` with an actionable message instead of failing silently,
and the browser Web Speech API stays the documented kiosk fallback.
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.deps import CurrentPrincipal
from app.providers.base import ProviderUnavailable
from app.providers.speech import (
    get_stt_provider,
    get_translation_provider,
    get_tts_provider,
    speech_capability,
)

router = APIRouter(tags=["speech"])


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    source_language: str = "auto"
    target_language: str = Field(min_length=2, max_length=8)


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1500)
    language: str = "en"
    voice: str | None = None


class SpeakResponse(BaseModel):
    audio_base64: str
    mime_type: str
    provider: str
    model: str | None = None
    language: str
    notice: str


@router.get("/speech/capabilities", summary="Speech and translation providers")
def capabilities(_: CurrentPrincipal) -> dict[str, object]:
    return speech_capability()


@router.post("/speech/translate", summary="Translate text")
def translate(payload: TranslateRequest, _: CurrentPrincipal) -> dict[str, object]:
    provider = get_translation_provider()
    if not provider.is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Machine translation is not configured, so the archive shows the "
                "original text untranslated. Set TRANSLATION_PROVIDER to enable it."
            ),
        )
    try:
        result = provider.translate(
            payload.text, payload.source_language, payload.target_language
        )
    except ProviderUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return {
        "text": result.text,
        "source_language": result.source_language,
        "target_language": result.target_language,
        "provider": result.provider,
        "model": result.model,
        "notice": (
            "Machine translation is an interpretation, not an archive source. "
            "The original is always preserved."
        ),
    }


@router.post("/speech/speak", response_model=SpeakResponse, summary="Synthesise speech")
def speak(payload: SpeakRequest, _: CurrentPrincipal) -> SpeakResponse:
    provider = get_tts_provider()
    if not provider.is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No text-to-speech provider is configured. The kiosk falls back to the "
                "browser speech synthesis; to hear audio on this server set "
                f"TTS_PROVIDER=piper, system or openai_compatible. Configured voices: "
                f"{settings.tts_voice_map}"
            ),
        )
    if not provider.supports(payload.language):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"{provider.name} has no voice for '{payload.language}'. "
                f"Configured languages: {sorted(settings.tts_voice_map)}"
            ),
        )
    try:
        result = provider.synthesize(payload.text, language=payload.language)
    except ProviderUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return SpeakResponse(
        audio_base64=base64.b64encode(result.audio).decode("ascii"),
        mime_type=result.mime_type,
        provider=result.provider,
        model=result.model,
        language=payload.language,
        notice=(
            f"Synthesised by {result.provider}"
            + (f" ({result.model})" if result.model else "")
            + ". Spoken audio is a rendering of the cited text, not a recording."
        ),
    )


@router.post("/speech/transcribe", summary="Transcribe uploaded audio")
async def transcribe(
    _: CurrentPrincipal,
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
) -> dict[str, object]:
    provider = get_stt_provider()
    if not provider.is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Server-side speech-to-text is not configured. The kiosk uses the "
                "browser Web Speech API; to ingest recordings, upload the file through "
                "the document or media endpoints and transcribe it during processing."
            ),
        )
    audio = await file.read()
    if not audio:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="The uploaded file was empty."
        )
    try:
        result = provider.transcribe(audio, language=language)
    except ProviderUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return {
        "text": result.text,
        "language": result.language,
        "confidence": result.confidence,
        "provider": result.provider,
        "model": result.model,
        "duration_ms": result.duration_ms,
        "segments": list(result.segments),
        "notice": (
            "Automatic transcripts are unreviewed. An archivist must approve the page "
            "before it is cited or shown as a source."
        ),
    }
