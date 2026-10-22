"""F&O workspace 8b, slice 3: the auto journal of the user's closed PAPER F&O trades (app/paper/fno_journal.py). Both
routes need `ui_v2` + the PAPER account (404 otherwise). Neither reaches an order path: they read trades and write only
the caller's own journal rows.

- `POST /api/paper/fno/journal/link`   after the user placed the ticket / basket he opened with "Trade this setup", notes
                                       HIS signal on the journal entries of HIS trades just placed from it.
- `GET  /api/paper/fno/journal/drafts` the journal drafts of his closed PAPER F&O trades (facts only + the signal's
                                       structure words). Creates a missing journal entry from the trade row; nothing else.
"""
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.paper import fno_journal
from app.paper.fno_routes import _enabled

router = APIRouter(prefix="/api/paper/fno/journal", tags=["paper"])
journal_rate_limit = user_rate_limit("paper_fno_journal", limit=60, window_seconds=60)


class LinkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal: str = Field(min_length=3, max_length=24, pattern=r"^[a-z]+:[0-9]+$")
    trade_ids: List[int] = Field(min_length=1, max_length=fno_journal.MAX_LINK)


@router.post("/link", dependencies=[Depends(journal_rate_limit)])
async def journal_link(body: LinkBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    return await fno_journal.link(session, user, body.signal, body.trade_ids)


@router.get("/drafts", dependencies=[Depends(journal_rate_limit)])
async def journal_drafts(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    return await fno_journal.drafts(session, user)
