"""Who sees a reference row (security fix: contributed reference rows are the contributor's organisation's own).

The fundamentals tables (companies, financial periods, shareholding patterns, corporate actions, qualitative factors,
calendar events, sector metrics) and the manual news events carry `tenant_id`:

* NULL - shared reference data: rows the platform fetched from a fundamentals provider (`ingest.refresh_from_provider`),
  the public news feed, and rows entered before this fix by an account that no longer exists;
* set  - entered by a member of that organisation (the contributor routes): only that organisation reads it.

A reader passes the caller's organisation (`viewer`); an anonymous read (`viewer=None`) sees shared rows only. Every
list / lookup in app/fundamentals/persistence.py and app/news_events/persistence.py takes `viewer` as a required
keyword, so a new reader has to decide whose rows it shows.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from fastapi import Depends, HTTPException

from app.auth.dependencies import get_current_user_optional


def visible(model: Any, viewer: Optional[int]) -> Any:
    """SQL condition: the shared rows, plus the viewer organisation's own."""
    if viewer is None:
        return model.tenant_id.is_(None)
    return (model.tenant_id.is_(None)) | (model.tenant_id == viewer)


async def viewer_tenant(user: Any = Depends(get_current_user_optional)) -> Optional[int]:
    """FastAPI dependency: the caller's organisation, or None for an anonymous read (shared rows only)."""
    return int(user.tenant_id) if user is not None and getattr(user, "tenant_id", None) is not None else None


def check_dates(*, as_of: Optional[date] = None, publication: Optional[date] = None, not_before: Optional[date] = None,
                today: Optional[date] = None, what: str = "as_of_date") -> None:
    """A contributed row's dates (422 otherwise): its own date is not in the future, and a cited publication date is
    neither in the future nor before the period it reports (`not_before`, e.g. the quarter end)."""
    if today is None:
        from datetime import datetime, timezone

        from app.market_data.calendar import IST
        today = datetime.now(timezone.utc).astimezone(IST).date()
    if as_of is not None and as_of > today:
        raise HTTPException(status_code=422, detail=f"{what} {as_of.isoformat()} is after today")
    if publication is not None:
        if publication > today:
            raise HTTPException(status_code=422, detail=f"source.publication_date {publication.isoformat()} is after today")
        if not_before is not None and publication < not_before:
            raise HTTPException(status_code=422, detail=f"source.publication_date {publication.isoformat()} is before the period it reports "
                                                        f"({not_before.isoformat()})")


__all__ = ["visible", "viewer_tenant", "check_dates"]
