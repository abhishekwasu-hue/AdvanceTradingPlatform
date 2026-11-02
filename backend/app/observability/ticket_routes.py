"""M76 MP-0: `POST /api/ui/ticket-metrics` - one order ticket's report (docs/design/M76_REVIEW.md section 8, "Metrics").

The ticket (single order or basket) sends it once, when Place is pressed or when a ticket closes after a Review without
Place; the ticket never waits for it (fire and forget). The body is closed enums and bounded numbers only - how many
warning surfaces ("cards") and lines were on screen, the time from opening the ticket and from its last Review to Place,
the time of the pre-trade checks and how many were still pending. It only feeds Prometheus series in /metrics: nothing
is stored, no user id, no instrument, no price, no text. Logged in (the ticket is), rate-limited per user. Read-only
for trading: it touches no order, position, limit or exit.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.rate_limit import user_rate_limit
from app.observability.metrics import TICKET_CARDS, TICKET_CHECKS, TICKET_LINES, TICKET_PENDING_AT_PLACE, TICKET_REPORTS, TICKET_TO_PLACE

router = APIRouter(prefix="/api/ui", tags=["observability"])

# Module-level so tests can target it through `app.dependency_overrides`.
ticket_metrics_rate_limit = user_rate_limit("ticket_metrics", limit=120, window_seconds=60)

_MAX_MS = 3_600_000   # one hour: a ticket left open longer still counts, in the top bucket


class TicketMetricsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    surface: Literal["ticket", "basket"]
    device: Literal["phone", "desktop"] = "desktop"
    outcome: Literal["placed", "closed"]
    cards_shown: int = Field(ge=0, le=50)
    lines_total: int = Field(ge=0, le=200)
    lines_visible: int = Field(ge=0, le=200)
    ms_open_to_place: float | None = Field(default=None, ge=0, le=_MAX_MS)
    ms_review_to_place: float | None = Field(default=None, ge=0, le=_MAX_MS)
    checks_ms: float | None = Field(default=None, ge=0, le=60_000)
    pending_at_place: int = Field(default=0, ge=0, le=50)


@router.post("/ticket-metrics", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(ticket_metrics_rate_limit)])
async def ticket_metrics(body: TicketMetricsBody) -> Response:
    TICKET_REPORTS.labels(surface=body.surface, device=body.device, outcome=body.outcome).inc()
    TICKET_CARDS.labels(surface=body.surface, outcome=body.outcome).observe(body.cards_shown)
    TICKET_LINES.labels(surface=body.surface, kind="total").observe(body.lines_total)
    TICKET_LINES.labels(surface=body.surface, kind="visible").observe(min(body.lines_visible, body.lines_total))
    if body.checks_ms is not None:
        TICKET_CHECKS.labels(surface=body.surface).observe(body.checks_ms / 1000)
    if body.outcome == "placed":
        if body.ms_open_to_place is not None:
            TICKET_TO_PLACE.labels(surface=body.surface, device=body.device, span="open_to_place").observe(body.ms_open_to_place / 1000)
        if body.ms_review_to_place is not None:
            TICKET_TO_PLACE.labels(surface=body.surface, device=body.device, span="review_to_place").observe(body.ms_review_to_place / 1000)
        if body.pending_at_place:
            TICKET_PENDING_AT_PLACE.labels(surface=body.surface).inc(body.pending_at_place)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
