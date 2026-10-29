"""AUD-G2.1: counts the SQL statements a block of code sends through an engine (an N+1 guard for list endpoints).

    with count_queries(engine) as counter:
        client.get("/api/positions", headers=h)
    assert counter.count <= 5

A SQLAlchemy `before_cursor_execute` listener on the engine (an AsyncEngine's sync engine); `statements` keeps the text
of each one so a failing assertion shows what ran. An executemany counts once (one round trip).
"""
from contextlib import contextmanager
from typing import Any, Iterator, List

from sqlalchemy import event


class QueryCounter:
    def __init__(self) -> None:
        self.statements: List[str] = []

    @property
    def count(self) -> int:
        return len(self.statements)

    def __call__(self, conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        self.statements.append(" ".join(str(statement).split())[:200])


@contextmanager
def count_queries(engine: Any) -> Iterator[QueryCounter]:
    sync_engine = getattr(engine, "sync_engine", engine)
    counter = QueryCounter()
    event.listen(sync_engine, "before_cursor_execute", counter)
    try:
        yield counter
    finally:
        event.remove(sync_engine, "before_cursor_execute", counter)
