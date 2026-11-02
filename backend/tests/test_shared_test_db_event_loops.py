"""The suite's one in-memory database (tests/test_auth_api.py: SQLite `:memory:` on a StaticPool) lives on ONE connection
that every test reuses, while each test runs its own event loop (`asyncio.run`). SQLAlchemy's async adapter guards that
connection with an asyncio.Lock; the first time two queries overlap, the lock binds itself to that test's loop. The next
overlap in any LATER loop then raised "bound to a different event loop", the connection was invalidated, the pool opened a
fresh, EMPTY `:memory:` database, and every following test failed with "no such table" (main, 1 Nov: 718 failed, 2108
errors, first at tests/test_pw4_live.py). tests/conftest.py gives the connection a fresh lock on checkout in a new loop."""
import asyncio

from sqlalchemy import text

from tests.test_auth_api import _session_factory


async def _overlap() -> int:
    """Two queries on the one shared connection that are made to overlap: the second waits for the connection's lock."""
    async with _session_factory() as first, _session_factory() as second:
        await first.execute(text("SELECT 1"))
        raw = (await (await first.connection()).get_raw_connection()).dbapi_connection
        held = asyncio.Event()

        async def hold():
            async with raw._execute_mutex:
                held.set()
                await asyncio.sleep(0.05)

        async def wait_for_it():
            await held.wait()
            return (await second.execute(text("SELECT count(*) FROM platform_controls"))).scalar_one()

        _, count = await asyncio.gather(hold(), wait_for_it())
        return count


def test_queries_that_overlap_in_two_event_loops_keep_the_shared_database():
    first = asyncio.run(_overlap())
    second = asyncio.run(_overlap())               # a new loop: the connection's lock is not the first loop's any more
    assert first == second

    async def tables():
        async with _session_factory() as session:
            return (await session.execute(text("SELECT count(*) FROM sqlite_master WHERE name = 'platform_controls'"))).scalar_one()
    assert asyncio.run(tables()) == 1             # still the same database, not a fresh empty one
