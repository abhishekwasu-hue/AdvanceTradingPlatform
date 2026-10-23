"""Readiness C13: the Learn section's lessons and the public marketing pages, as data (`lessons.json`, `site.json`).

Education only. Every human-readable line passes the idea library's content guard (`app.idea_library.content.guard`):
no security names, no prices or other numbers, no buy / sell / target / stop wording and no performance claims. The tests
run the guard over both files, so a line that breaks the rule fails CI before it can be shown.

A lesson's `next` is an address inside the app ("Open Charts" -> `/charts`), checked against a plain path pattern; its
`glossary` names keys of the frontend glossary (`frontend/src/ui/glossary.ts`; a frontend test checks they exist).
Video links are not here: Abhi sets them in the admin console (`app/learn/settings.py`), empty by default.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.idea_library.content import guard

HERE = Path(__file__).resolve().parent
ID = r"^[a-z][a-z0-9-]{1,40}$"
APP_PATH = re.compile(r"^/[a-z0-9][a-z0-9/-]{0,60}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Group(_Strict):
    id: str = Field(pattern=ID)
    name: str = Field(min_length=1, max_length=60)


class NextStep(_Strict):
    label: str = Field(min_length=1, max_length=40)
    path: str

    @field_validator("path")
    @classmethod
    def _app_path(cls, value: str) -> str:
        if not APP_PATH.match(value):
            raise ValueError("next.path must be an address inside the app")
        return value


class Lesson(_Strict):
    id: str = Field(pattern=ID)
    group: str
    title: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=240)
    points: List[str] = Field(min_length=2, max_length=6)
    glossary: List[str] = Field(default_factory=list, max_length=4)
    next: Optional[NextStep] = None


class Header(_Strict):
    title: str
    subtitle: str
    frame: List[str] = Field(min_length=1)


class Library(_Strict):
    header: Header
    groups: List[Group] = Field(min_length=1)
    lessons: List[Lesson] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> "Library":
        groups = {g.id for g in self.groups}
        ids = [lesson.id for lesson in self.lessons]
        if len(set(ids)) != len(ids):
            raise ValueError("lesson ids must be unique")
        for lesson in self.lessons:
            if lesson.group not in groups:
                raise ValueError(f"lesson {lesson.id} names an unknown group {lesson.group!r}")
        return self

    def lesson(self, lesson_id: str) -> Optional[Lesson]:
        return next((lesson for lesson in self.lessons if lesson.id == lesson_id), None)


class SiteSection(_Strict):
    heading: str = Field(min_length=1, max_length=60)
    lines: List[str] = Field(min_length=1, max_length=6)


class SitePage(_Strict):
    slug: str = Field(pattern=ID)
    title: str = Field(min_length=1, max_length=60)
    description: str = Field(min_length=20, max_length=160)      # the meta description: search engines show about 155
    intro: str = Field(min_length=1, max_length=240)
    sections: List[SiteSection] = Field(min_length=1)


class Site(_Strict):
    pages: List[SitePage] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> "Site":
        slugs = [p.slug for p in self.pages]
        if len(set(slugs)) != len(slugs):
            raise ValueError("page slugs must be unique")
        return self

    def page(self, slug: str) -> Optional[SitePage]:
        return next((p for p in self.pages if p.slug == slug), None)


@lru_cache(maxsize=1)
def load() -> Library:
    return Library.model_validate(json.loads((HERE / "lessons.json").read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def load_site() -> Site:
    return Site.model_validate(json.loads((HERE / "site.json").read_text(encoding="utf-8")))


def readable_lines(library: Library) -> List[Tuple[str, str]]:
    """Every line a reader sees, with where it is."""
    h = library.header
    out: List[Tuple[str, str]] = [("header.title", h.title), ("header.subtitle", h.subtitle)]
    out += [(f"header.frame[{i}]", t) for i, t in enumerate(h.frame)]
    out += [(f"groups.{g.id}", g.name) for g in library.groups]
    for lesson in library.lessons:
        p = lesson.id
        out += [(f"{p}.title", lesson.title), (f"{p}.summary", lesson.summary)]
        out += [(f"{p}.points[{i}]", t) for i, t in enumerate(lesson.points)]
        if lesson.next is not None:
            out.append((f"{p}.next.label", lesson.next.label))
    return out


def site_lines(site: Site) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for page in site.pages:
        p = page.slug
        out += [(f"{p}.title", page.title), (f"{p}.description", page.description), (f"{p}.intro", page.intro)]
        for i, section in enumerate(page.sections):
            out.append((f"{p}.sections[{i}].heading", section.heading))
            out += [(f"{p}.sections[{i}].lines[{j}]", t) for j, t in enumerate(section.lines)]
    return out


def problems(lines: List[Tuple[str, str]]) -> List[str]:
    """Every guard problem ("where: problem"); empty = clean."""
    return [f"{where}: {p}" for where, text in lines for p in guard(text)]


def lessons_payload(library: Library, videos: Dict[str, str]) -> Dict[str, Any]:
    """The Learn page: the frame, the groups and each lesson with its video link when Abhi has set one."""
    return {
        "header": library.header.model_dump(),
        "groups": [g.model_dump() for g in library.groups],
        "lessons": [{**lesson.model_dump(), "video_url": videos.get(lesson.id) or None} for lesson in library.lessons],
    }


def site_payload(site: Site) -> Dict[str, Any]:
    return {"pages": [p.model_dump() for p in site.pages]}


__all__ = ["Library", "Lesson", "Site", "SitePage", "load", "load_site", "readable_lines", "site_lines", "problems",
           "lessons_payload", "site_payload"]
