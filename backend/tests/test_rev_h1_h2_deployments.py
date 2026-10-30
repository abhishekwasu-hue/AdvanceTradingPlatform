"""REV-H1 / REV-H2 (REV-1 M1): starting a bot.

H1: every 409 on POST /deployments carries its own reason - a machine code plus a plain sentence with the next step - so
the page no longer reads every refusal as "already runs on paper".
H2: a STOPPED bot is history and no longer blocks a new bot on the same strategy + symbol (partial unique index over the
rows that are not STOPPED); two bots that are not stopped on one key are still refused, by the service check and by the
index itself. The migration round-trips on a scratch SQLite database.
"""
import asyncio
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

from app.db.models import StrategyDeploymentRecord
from tests.test_auth_api import _session_factory, client
from tests.test_contract_rules import _load_master
from tests.test_deployments_api import STRATEGY, _auth, _create, _me, _store_broker

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "f4b6d8a0c2e3_rev_h2_open_deployment_unique.py"


def _refusal(resp) -> dict:
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert set(detail) == {"code", "message"} and detail["message"].endswith(".")
    return detail


def _stop(headers, dep_id: int) -> None:
    assert client.post(f"/api/deployments/{dep_id}/stop", headers=headers, json={"reason": "done"}).status_code == 200


# --- H1: one reason per refusal ----------------------------------------------------------------------------------


def test_no_broker_is_its_own_reason():
    headers = _auth("rev-h1-nobroker@example.com")
    detail = _refusal(_create(headers))
    assert detail["code"] == "no_broker" and "Broker page" in detail["message"]


def test_named_broker_without_a_login_is_no_broker():
    headers = _auth("rev-h1-named@example.com")
    _store_broker(headers)
    detail = _refusal(_create(headers, broker_name="zerodha"))
    assert detail["code"] == "no_broker" and "zerodha" in detail["message"]


def test_no_contracts_in_the_instrument_list_is_its_own_reason():
    _load_master()
    headers = _auth("rev-h1-master@example.com")
    _store_broker(headers)
    detail = _refusal(_create(headers, symbol="TCS", instrument_kind="OPTION"))
    assert detail["code"] == "no_instrument_master" and "TCS options" in detail["message"]
    assert "master" not in detail["message"]                       # plain words, no internal table name


def test_broker_login_needed_for_live_is_its_own_reason():
    headers = _auth("rev-h1-login@example.com")
    _store_broker(headers)                                         # token UNKNOWN
    detail = _refusal(_create(headers, mode="LIVE", broker_name="upstox"))
    assert detail["code"] == "broker_login_needed" and "Log in to upstox again" in detail["message"]


def test_duplicate_active_bot_is_its_own_reason():
    headers = _auth("rev-h1-dup@example.com")
    _store_broker(headers)
    assert _create(headers).status_code == 201
    detail = _refusal(_create(headers))
    assert detail["code"] == "duplicate_active_bot" and "Stop that bot first" in detail["message"]


def test_every_reason_code_is_distinct():
    from app.deployments import routes
    codes = [getattr(routes, n) for n in dir(routes) if n.startswith("REFUSED_")]
    assert len(codes) == len(set(codes)) == 5


# --- H2: a stopped bot no longer blocks a new one ----------------------------------------------------------------


def test_after_a_stop_the_same_strategy_and_symbol_starts_again():
    headers = _auth("rev-h2-restart@example.com")
    _store_broker(headers)
    first = _create(headers)
    assert first.status_code == 201
    _stop(headers, first.json()["id"])
    second = _create(headers)
    assert second.status_code == 201, second.text
    assert second.json()["id"] != first.json()["id"]
    # The stopped bot stays as history.
    listed = client.get("/api/deployments?include_stopped=true", headers=headers).json()
    assert {d["id"]: d["status"] for d in listed} == {first.json()["id"]: "STOPPED", second.json()["id"]: "ACTIVE"}
    # Still only one bot that is not stopped: a third is refused while the second runs (or is paused).
    assert _refusal(_create(headers))["code"] == "duplicate_active_bot"
    assert client.post(f"/api/deployments/{second.json()['id']}/pause", headers=headers, json={}).status_code == 200
    assert _refusal(_create(headers))["code"] == "duplicate_active_bot"
    # And a stopped bot can then be removed (the existing DELETE), the running one cannot.
    assert client.delete(f"/api/deployments/{first.json()['id']}", headers=headers).status_code == 204
    assert client.delete(f"/api/deployments/{second.json()['id']}", headers=headers).status_code == 409


def test_the_index_refuses_two_bots_that_are_not_stopped():
    """The backstop behind the service check (a race between two starts): the database itself refuses."""
    headers = _auth("rev-h2-index@example.com")
    _store_broker(headers)
    tenant_id = _me(headers)["tenant_id"]

    def row(status: str) -> StrategyDeploymentRecord:
        return StrategyDeploymentRecord(tenant_id=tenant_id, strategy_id=STRATEGY, symbol="INFY", exchange="NSE", timeframe="1min",
                                        mode="PAPER", broker_name="upstox", status=status, instrument_kind="UNDERLYING")

    async def go():
        async with _session_factory() as session:
            session.add_all([row("STOPPED"), row("STOPPED"), row("ACTIVE")])
            await session.commit()                                 # stopped rows never collide
            session.add(row("PAUSED"))
            with pytest.raises(IntegrityError):
                await session.commit()
    asyncio.run(go())


# --- the migration --------------------------------------------------------------------------------------------------


def _migration():
    spec = importlib.util.spec_from_file_location("rev_h2_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_migration_is_the_single_head_and_lint_clean():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    heads = script.get_heads()
    assert len(heads) == 1
    assert "f4b6d8a0c2e3" in {r.revision for r in script.iterate_revisions(heads[0], "base")}
    spec = importlib.util.spec_from_file_location("migration_rollback_safety", BACKEND / "scripts" / "migration_rollback_safety.py")
    lint = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(lint)
    assert lint.lint() == []


def test_migration_upgrade_and_downgrade_on_sqlite():
    mig = _migration()
    engine = sa.create_engine("sqlite://")
    insert = sa.text("INSERT INTO strategy_deployments (tenant_id, strategy_id, symbol, mode, instrument_kind, status) "
                     "VALUES (1, 's', 'X', 'PAPER', 'UNDERLYING', :status)")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE strategy_deployments (id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, strategy_id VARCHAR(64) NOT NULL, "
            "symbol VARCHAR(40) NOT NULL, mode VARCHAR(10) NOT NULL, instrument_kind VARCHAR(12) NOT NULL, status VARCHAR(10) NOT NULL, "
            "CONSTRAINT uq_deployment_tenant_strategy_symbol_mode_kind UNIQUE (tenant_id, strategy_id, symbol, mode, instrument_kind))"))
        conn.execute(insert, {"status": "STOPPED"})
        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
        conn.execute(insert, {"status": "ACTIVE"})                 # a new bot beside the stopped one
        with pytest.raises(sa.exc.IntegrityError):
            with conn.begin_nested():
                conn.execute(insert, {"status": "PAUSED"})         # two bots that are not stopped: refused
        # The downgrade refuses while the full key would break, and deletes nothing.
        with pytest.raises(RuntimeError, match="remove the stopped duplicates"):
            with Operations.context(MigrationContext.configure(conn)):
                mig.downgrade()
        assert conn.execute(sa.text("SELECT COUNT(*) FROM strategy_deployments")).scalar_one() == 2
        conn.execute(sa.text("DELETE FROM strategy_deployments WHERE status = 'STOPPED'"))
        with Operations.context(MigrationContext.configure(conn)):
            mig.downgrade()
        names = {u["name"] for u in sa.inspect(conn).get_unique_constraints("strategy_deployments")}
        assert "uq_deployment_tenant_strategy_symbol_mode_kind" in names
        assert "uq_deployment_open_tenant_strategy_symbol_mode_kind" not in {i["name"] for i in sa.inspect(conn).get_indexes("strategy_deployments")}
        with pytest.raises(sa.exc.IntegrityError):
            with conn.begin_nested():
                conn.execute(insert, {"status": "STOPPED"})        # the full key is back: a stopped row collides again
