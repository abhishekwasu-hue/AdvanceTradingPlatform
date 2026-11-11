"""Readiness C13: the Learn section's video links, set by the platform operator (SUPER_ADMIN) in the admin console.

Stored in one `platform_controls` row (`learn_settings`, no migration): `{"videos": {lesson_id: url}, "course_url": url,
"intro_video_url": url}`. Empty by default, so the Learn page shows no video button until Abhi adds his course links.
M95 F4: `intro_video_url` is the front door's 60-second intro video (the signed-out sign-in page shows a "Watch" link only
while it is set; read without a login from `GET /api/site/intro-video`). Only `https://` addresses are
accepted, each at most MAX_URL characters, and a video may only name a lesson that exists. The audit trail records which
lessons changed, never the addresses.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User

KEY = "learn_settings"
MAX_URL = 300


def clean_url(value: Optional[str]) -> str:
    """'' for empty; the trimmed address when it is a plain https link; ValueError otherwise."""
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) > MAX_URL:
        raise ValueError(f"A link may be at most {MAX_URL} characters.")
    parts = urlsplit(text)
    if parts.scheme != "https" or not parts.netloc or any(c.isspace() for c in text) or "@" in parts.netloc:
        raise ValueError("Use a full https:// link.")
    return text


class LearnSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    videos: Dict[str, str] = Field(default_factory=dict, max_length=60)
    course_url: str = ""
    # M95 F4: the front door's intro video. Left out of a request (an older admin page), the stored link is kept.
    intro_video_url: str = ""

    @field_validator("course_url", "intro_video_url")
    @classmethod
    def _course(cls, value: str) -> str:
        return clean_url(value)

    @field_validator("videos")
    @classmethod
    def _videos(cls, value: Dict[str, str]) -> Dict[str, str]:
        from app.learn import content
        library = content.load()
        out: Dict[str, str] = {}
        for lesson_id, url in value.items():
            if library.lesson(lesson_id) is None:
                raise ValueError(f"Unknown lesson {lesson_id!r}.")
            cleaned = clean_url(url)
            if cleaned:
                out[lesson_id] = cleaned
        return out


def _parse(raw: Dict[str, Any]) -> Dict[str, Any]:
    """A stored row read defensively: anything malformed reads as unset."""
    videos: Dict[str, str] = {}
    stored = raw.get("videos")
    if isinstance(stored, dict):
        for k, v in stored.items():
            try:
                cleaned = clean_url(v if isinstance(v, str) else "")
            except ValueError:
                continue
            if cleaned:
                videos[str(k)] = cleaned
    return {"videos": videos, "course_url": _stored_url(raw, "course_url"), "intro_video_url": _stored_url(raw, "intro_video_url")}


def _stored_url(raw: Dict[str, Any], key: str) -> str:
    """One stored link, '' when missing or malformed."""
    value = raw.get(key)
    try:
        return clean_url(value if isinstance(value, str) else "")
    except ValueError:
        return ""


async def read(session: AsyncSession) -> Dict[str, Any]:
    from app.platform.controls import _get
    return _parse(await _get(session, KEY))


async def write(session: AsyncSession, admin: User, body: LearnSettingsIn) -> Dict[str, Any]:
    from app.audit.log import write_audit_log
    from app.platform.controls import _set
    before = await read(session)
    videos: Dict[str, str] = dict(sorted(body.videos.items()))
    old: Dict[str, str] = before["videos"]
    intro = body.intro_video_url if "intro_video_url" in body.model_fields_set else before["intro_video_url"]
    value: Dict[str, Any] = {"videos": videos, "course_url": body.course_url, "intro_video_url": intro}
    await _set(session, KEY, value, admin)
    changed = sorted(k for k in set(old) | set(videos) if old.get(k) != videos.get(k))
    course = "course link changed" if before["course_url"] != body.course_url else "course link unchanged"
    intro_note = "intro video changed" if before["intro_video_url"] != intro else "intro video unchanged"
    await write_audit_log(session, None, admin.id, "learn_settings_changed",
                          f"videos changed for: {', '.join(changed) or 'none'}; {course}; {intro_note}")
    await session.commit()
    return value


__all__ = ["KEY", "LearnSettingsIn", "clean_url", "read", "write"]
