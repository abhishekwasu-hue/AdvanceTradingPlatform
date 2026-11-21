"""P2-8 J1-2: the stored chart picture of an ATP trade at its entry and at its exit.

`entries.chart_png` draws the picture from the stored bars that END at the instant (nothing after it is drawn, so the
picture is the same whenever it is drawn). The first draw is stored in the private store (`app/chart_analyze/storage`,
per organisation, a server-made name) with its row in `journal_snapshots`; every later view, the reviews and the
exports read the stored file. The exit picture exists once the trade is closed. Erased with the account
(`retention.service._erase_journal`). Manual / imported trades keep their own drawing (`trade_chart.py`).
"""
from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JournalSnapshotRecord, TradeRecord, User
from app.journal import entries

logger = logging.getLogger(__name__)

POINTS = ("entry", "exit")
# A picture is kept only once its instant is this old: the bars around it are then in the store (a view seconds after
# the fill would keep a picture without the bar still forming). Before that it is drawn and shown, not kept.
KEEP_AFTER = timedelta(minutes=30)


async def stored(session: AsyncSession, user: User, trade_id: int, at: str) -> JournalSnapshotRecord | None:
    return await session.scalar(select(JournalSnapshotRecord).where(
        JournalSnapshotRecord.trade_id == trade_id, JournalSnapshotRecord.at == at,
        JournalSnapshotRecord.user_id == user.id, JournalSnapshotRecord.tenant_id == user.tenant_id))


async def _keep(session: AsyncSession, user: User, trade: TradeRecord, at: str, png: bytes) -> None:
    from app.chart_analyze import storage
    name = storage.new_name(user.tenant_id)
    try:
        storage.write(name, png)
    except storage.StorageUnavailable:
        return                                           # drawn, shown, not kept (no store on this server)
    session.add(JournalSnapshotRecord(tenant_id=user.tenant_id, user_id=user.id, trade_id=trade.id, at=at, image_path=name,
                                      size_bytes=len(png), sha256=hashlib.sha256(png).hexdigest()))
    try:
        await session.commit()
    except IntegrityError:                               # drawn twice at once: the first one is kept
        await session.rollback()
        storage.delete(name)


async def picture(session: AsyncSession, user: User, trade: TradeRecord, at: str) -> bytes:
    """The stored picture, or the first draw (stored now). Raises `entries.EntryError` when it cannot be drawn."""
    if at not in POINTS:
        raise entries.EntryError("at must be entry or exit")
    from app.chart_analyze import storage
    row = await stored(session, user, trade.id, at)
    if row is not None:
        data = storage.read(row.image_path)
        if data:
            return data
        await session.delete(row)                        # its file is gone: draw it again
        await session.commit()
    png = await entries.chart_png(session, user, entries.Ref(trade=trade), at)
    instant = entries._utc(trade.entry_time if at == "entry" else trade.exit_time)
    if instant is not None and datetime.now(UTC) - instant >= KEEP_AFTER:
        await _keep(session, user, trade, at, png)
    return png


def view(rows: list[JournalSnapshotRecord]) -> dict[str, bool]:
    """Which pictures are stored, for the entry payload."""
    have = {r.at for r in rows}
    return {at: at in have for at in POINTS}


__all__ = ["POINTS", "picture", "stored", "view"]
