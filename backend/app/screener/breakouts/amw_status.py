"""Bounded operational status for the informational breakout worker."""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models import RuntimeCounterRecord

COUNTER = "amw_breakout_status"


def owner_key(user) -> str:
    return f"{user.tenant_id}/{user.id}"


async def write_status(session, key: str, value: dict) -> None:
    dialect = session.get_bind().dialect.name
    insert = {"postgresql": pg_insert, "sqlite": sqlite_insert}[dialect]
    now = datetime.now(UTC)
    stmt = insert(RuntimeCounterRecord).values(name=COUNTER, key=key, value=json.dumps(value), updated_at=now)
    await session.execute(stmt.on_conflict_do_update(
        index_elements=["name", "key"], set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at},
    ))
    await session.commit()


async def owner_status(session, user) -> dict:
    heartbeat, key = "worker", owner_key(user)
    query = select(RuntimeCounterRecord).where(RuntimeCounterRecord.name == COUNTER,
                                               RuntimeCounterRecord.key.in_((heartbeat, key)))
    rows = {row.key: row for row in (await session.scalars(query)).all()}
    worker = rows.get(heartbeat)
    now = datetime.now(UTC)
    if worker is None:
        available = False
    else:
        stamp = worker.updated_at
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        available = timedelta(0) <= now - stamp <= timedelta(minutes=2)
    status = rows.get(key)
    result = json.loads(status.value) if status else {}
    return {"worker_available": available, **result}
