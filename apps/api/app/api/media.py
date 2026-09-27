"""Media, transcript and curated-story endpoints.

A media item is only shown as playable when its bytes are actually in the
preservation store. Transcripts are shown with the engine that produced them and
are labelled unreviewed until a human approves them, so an automatic transcript
is never presented as an authoritative one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentPrincipal, DbSession, not_found
from app.models.media import Media, MediaTranscript
from app.models.ops import Story, StoryChapter
from app.providers.storage import get_object_store
from app.schemas import (
    MediaListResponse,
    MediaOut,
    StoryChapterOut,
    StoryOut,
    TranscriptResponse,
    TranscriptSegment,
)

router = APIRouter(tags=["media"])


def _media_out(media: Media, transcript: MediaTranscript | None) -> MediaOut:
    store = get_object_store()
    return MediaOut(
        id=media.id,
        title=media.title,
        media_type=str(media.kind),
        url=store.url_for(media.storage_key) if media.storage_key else None,
        thumbnail_url=store.url_for(media.thumbnail_key) if media.thumbnail_key else None,
        duration_seconds=media.duration_seconds,
        date=media.media_date,
        description=media.description,
        source_name=None,
        rights=media.rights,
        is_demo=media.is_demo,
        transcript_available=bool(transcript and transcript.text),
        transcript_language=transcript.language if transcript else None,
    )


@router.get("/media", response_model=MediaListResponse, summary="List media")
def list_media(
    db: DbSession,
    _: CurrentPrincipal,
    kind: list[str] = Query(default=[]),
    language: list[str] = Query(default=[]),
    year_from: int | None = None,
    year_to: int | None = None,
    q: str | None = None,
    limit: int = Query(24, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> MediaListResponse:
    stmt = select(Media).where(Media.publication_status == "published")
    if kind:
        stmt = stmt.where(Media.kind.in_(kind))
    if language:
        stmt = stmt.where(Media.language.in_(language))
    if year_from is not None:
        stmt = stmt.where(Media.year.isnot(None), Media.year >= year_from)
    if year_to is not None:
        stmt = stmt.where(Media.year.isnot(None), Media.year <= year_to)
    if q:
        stmt = stmt.where(Media.title.ilike(f"%{q.strip()}%"))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Media.media_date.asc().nullslast(), Media.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    transcripts = {}
    if rows:
        transcripts = {
            t.media_id: t
            for t in db.scalars(
                select(MediaTranscript).where(
                    MediaTranscript.media_id.in_([m.id for m in rows])
                )
            ).all()
        }
    return MediaListResponse(
        items=[_media_out(m, transcripts.get(m.id)) for m in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.get("/media/{media_id}", response_model=MediaOut, summary="Media detail")
def get_media(media_id: str, db: DbSession, _: CurrentPrincipal) -> MediaOut:
    media = db.get(Media, media_id) or db.scalar(select(Media).where(Media.slug == media_id))
    if media is None or media.publication_status != "published":
        raise not_found("That media item")
    transcript = db.scalar(
        select(MediaTranscript).where(MediaTranscript.media_id == media.id)
    )
    return _media_out(media, transcript)


@router.get(
    "/media/{media_id}/transcript",
    response_model=TranscriptResponse,
    summary="Transcript with timing",
)
def get_transcript(media_id: str, db: DbSession, _: CurrentPrincipal) -> TranscriptResponse:
    media = db.get(Media, media_id) or db.scalar(select(Media).where(Media.slug == media_id))
    if media is None or media.publication_status != "published":
        raise not_found("That media item")
    transcript = db.scalar(
        select(MediaTranscript).where(MediaTranscript.media_id == media.id)
    )
    notices: list[str] = []
    if transcript is None or not transcript.text:
        return TranscriptResponse(
            media_id=media.id,
            language=media.language,
            engine="none",
            reviewed=False,
            segments=[],
            transcript=None,
            notices=[
                "No transcript is stored for this item. The archive does not generate one "
                "unless a speech-to-text provider is configured and its output is reviewed."
            ],
        )

    duration = media.duration_seconds or float(transcript.end_offset or 0.0)
    segments = [
        TranscriptSegment(
            index=i + 1,
            start_seconds=float(transcript.start_offset or 0.0),
            end_seconds=float(transcript.end_offset or duration),
            text=line,
            confidence=transcript.confidence,
        )
        for i, line in enumerate(transcript.text.splitlines())
        if line.strip()
    ]
    if not transcript.is_searchable:
        notices.append("This transcript is excluded from search because it is not approved.")
    notices.append(
        f"Produced by {transcript.provider or 'an unknown engine'}"
        + (f" ({transcript.model})" if transcript.model else "")
        + ". Treat it as unreviewed until an archivist confirms it."
    )
    return TranscriptResponse(
        media_id=media.id,
        language=transcript.language,
        engine=transcript.provider or "unknown",
        reviewed=bool(transcript.is_searchable),
        segments=segments,
        transcript=transcript.text,
        notices=notices,
    )


# --------------------------------------------------------------------------- #
# stories
# --------------------------------------------------------------------------- #


def _story_out(db: Session, story: Story, *, include_body: bool = False) -> StoryOut:
    store = get_object_store()
    chapters = db.scalars(
        select(StoryChapter)
        .where(StoryChapter.story_id == story.id)
        .order_by(StoryChapter.chapter_number)
    ).all()
    chapter_out: list[StoryChapterOut] = []
    for c in chapters:
        image = db.get(Media, c.image_media_id) if c.image_media_id else None
        chapter_out.append(
            StoryChapterOut(
                id=c.id,
                position=c.chapter_number,
                title=c.title,
                body=c.body if include_body else "",
                media_id=c.image_media_id or c.audio_media_id or c.video_media_id,
                document_id=c.document_id,
                target_type="event" if c.event_id else ("document" if c.document_id else None),
                target_id=c.event_id or c.document_id,
            )
        )
        if image is not None and image.thumbnail_key:
            chapter_out[-1].media_id = image.id
    hero = db.get(Media, story.cover_media_id) if story.cover_media_id else None
    return StoryOut(
        id=story.id,
        slug=story.slug,
        title=story.title,
        subtitle=story.subtitle,
        summary=story.summary,
        hero_image_url=store.url_for(hero.thumbnail_key or hero.storage_key)
        if hero is not None and (hero.thumbnail_key or hero.storage_key)
        else None,
        reading_minutes=story.estimated_minutes,
        chapters=chapter_out,
        published=story.is_published,
        updated_at=story.updated_at,
    )


@router.get("/stories", response_model=list[StoryOut], summary="List stories")
def list_stories(db: DbSession, _: CurrentPrincipal) -> list[StoryOut]:
    rows = db.scalars(
        select(Story).where(Story.is_published.is_(True)).order_by(Story.created_at.desc())
    ).all()
    if not rows:
        return []
    return [_story_out(db, s) for s in rows]


@router.get("/stories/{slug}", response_model=StoryOut, summary="Story with its chapters")
def get_story(slug: str, db: DbSession, _: CurrentPrincipal) -> StoryOut:
    story = db.scalar(select(Story).where(Story.slug == slug))
    if story is None or not story.is_published:
        raise not_found("That story")
    return _story_out(db, story, include_body=True)
