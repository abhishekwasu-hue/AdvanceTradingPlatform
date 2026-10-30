"""REV-1 M3: /api/trades is paged (limit + offset) and carries the full row count in X-Total-Count."""
import asyncio
from datetime import UTC, datetime, timedelta

from app.db.models import TradeRecord
from app.trading.routes import TRADES_PAGE_DEFAULT, TRADES_PAGE_MAX
from tests.test_auth_api import _session_factory, client
from tests.test_trading_api import _register_user_and_get_id


def _seed(tenant_id: int, user_id: int, count: int) -> None:
    async def _go():
        async with _session_factory() as session:
            now = datetime.now(UTC)
            for i in range(count):
                session.add(TradeRecord(
                    tenant_id=tenant_id, user_id=user_id, mode="PAPER", symbol=f"S{i:03d}", strategy_id="s",
                    direction="LONG", entry_time=now - timedelta(minutes=i), entry_price=100.0, quantity=1,
                    stop_loss=98.0, target1=None,
                ))
            await session.commit()
    asyncio.run(_go())


def test_trades_pages_by_limit_and_offset_with_total():
    token, user_id, tenant_id = asyncio.run(_register_user_and_get_id("m3paging@example.com"))
    headers = {"Authorization": f"Bearer {token}"}
    _seed(tenant_id, user_id, 7)

    first = client.get("/api/trades?limit=3", headers=headers)
    assert first.status_code == 200
    assert first.headers["X-Total-Count"] == "7"
    assert [t["symbol"] for t in first.json()] == ["S000", "S001", "S002"]        # newest first

    second = client.get("/api/trades?limit=3&offset=3", headers=headers).json()
    last = client.get("/api/trades?limit=3&offset=6", headers=headers).json()
    assert [t["symbol"] for t in second] == ["S003", "S004", "S005"]
    assert [t["symbol"] for t in last] == ["S006"]
    assert client.get("/api/trades?limit=3&offset=9", headers=headers).json() == []


def test_trades_default_page_and_bounds():
    token, user_id, tenant_id = asyncio.run(_register_user_and_get_id("m3bounds@example.com"))
    headers = {"Authorization": f"Bearer {token}"}
    _seed(tenant_id, user_id, 2)
    plain = client.get("/api/trades", headers=headers)
    assert plain.headers["X-Total-Count"] == "2" and len(plain.json()) == 2
    assert 0 < TRADES_PAGE_DEFAULT <= TRADES_PAGE_MAX
    assert client.get(f"/api/trades?limit={TRADES_PAGE_MAX + 1}", headers=headers).status_code == 422
    assert client.get("/api/trades?limit=0", headers=headers).status_code == 422
    assert client.get("/api/trades?offset=-1", headers=headers).status_code == 422


def test_trades_total_counts_only_the_callers_tenant():
    _token_a, user_a, tenant_a = asyncio.run(_register_user_and_get_id("m3tenanta@example.com"))
    token_b, user_b, tenant_b = asyncio.run(_register_user_and_get_id("m3tenantb@example.com"))
    _seed(tenant_a, user_a, 4)
    _seed(tenant_b, user_b, 1)
    answer = client.get("/api/trades?limit=2", headers={"Authorization": f"Bearer {token_b}"})
    assert answer.headers["X-Total-Count"] == "1"
    assert [t["symbol"] for t in answer.json()] == ["S000"]
