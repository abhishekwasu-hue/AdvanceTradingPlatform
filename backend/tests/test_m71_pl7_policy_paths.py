"""M71 PL-7 review fixes: the data-use windows at every research path (walk-forward / optimise routes, the AI research
job, the Copilot draft test), every spelling of an underlying, fail-closed policy reading, recorded end-of-day premiums
only on daily bars (the over-limit path prices by the model), and the migration up / down. Synthetic data only."""
import asyncio
import importlib.util
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.backtest.data_policy import HoldoutError
from app.core import config
from app.db.models import DataUseWindowRecord, MdTenantCandleRecord, User
from app.market_data.calendar import IST
from app.market_lake import data_use, seed_import, seed_store
from tests.test_auth_api import _session_factory, client
from tests.test_data_h_lake_backfill import _tenant_name, _user
from tests.test_m71_pl7_seed_import import FRI, NOW, THU, make_data
from tests.utils import make_series, noisy_uptrend

BACKEND = Path(__file__).resolve().parents[1]
R = MdTenantCandleRecord


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def owner(monkeypatch):
    user = _user(f"pl7p-{datetime.now(UTC).timestamp()}@example.com")
    monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_ID", user.tenant_id)
    monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_NAME", _tenant_name(user.tenant_id))
    monkeypatch.setattr(config, "HISTORY_OWNER_USER_ID", None)
    monkeypatch.setattr(config, "HISTORY_BACKFILL_FO_VOLUME", "withhold")
    monkeypatch.setattr(config, "TRADE_DATA_POLICY_SCOPE", ("NIFTY",))
    return user


def _seal(tenant_id: int, start: date, end: date | None = None, kind: str = "sealed", scope: str = "NIFTY") -> None:
    async def go():
        async with _session_factory() as session:
            session.add(DataUseWindowRecord(tenant_id=tenant_id, kind=kind, scope=scope, start_date=start, end_date=end, label=kind))
            await session.commit()
    _run(go())


def _user_by_email(email: str) -> User:
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(User).where(User.email == email))
    return _run(go())


def _headers(email: str) -> tuple[dict, User]:
    from tests.test_auth_api import _register
    headers = {"Authorization": f"Bearer {_register(email)}"}
    return headers, _user_by_email(email)


def _candles(df: pd.DataFrame) -> list[dict]:
    return [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume}
            for ts, r in df.iterrows()]


# --- (2) every spelling of an underlying --------------------------------------------------------------------------------
@pytest.mark.parametrize("spelling", ["NIFTY", "nifty", "NIFTY 50", "NIFTY50", "Nifty 50", "NSE:NIFTY 50", "NSE_INDEX|Nifty 50",
                                      "NSE_INDEX:NIFTY50", "NFO:NIFTY:CE:2023-03-30:17000", "NIFTY24NOVFUT", "NIFTY24N2124000CE",
                                      "NIFTY23MAR17000PE"])
def test_every_spelling_of_nifty_resolves_to_its_underlying(spelling):
    assert data_use.underlying_of(spelling) == "NIFTY"


def test_other_underlyings_keep_their_own_name_and_unreadable_ones_are_refused():
    assert data_use.underlying_of("NIFTY BANK") == "BANKNIFTY" and data_use.underlying_of("NSE_INDEX|Nifty Bank") == "BANKNIFTY"
    assert data_use.underlying_of("BANKNIFTY23MARFUT") == "BANKNIFTY" and data_use.underlying_of("NSE:RELIANCE") == "RELIANCE"
    assert data_use.underlying_of("") is None and data_use.underlying_of("???") is None
    _, user = _headers(f"pl7-spell-{datetime.now(UTC).timestamp()}@example.com")
    _seal(user.tenant_id, date(2000, 1, 1))

    async def go(symbol, refused_only=True):
        async with _session_factory() as session:
            return await data_use.windows_for(session, user.tenant_id, symbol, refused_only=refused_only)
    for spelling in ("NIFTY50", "NSE:NIFTY 50", "NSE_INDEX|Nifty 50", "NIFTY24NOVFUT"):
        assert [w.kind for w in _run(go(spelling))] == ["sealed"]               # none escapes the NIFTY scope
    with pytest.raises(HoldoutError, match="could not be matched"):
        _run(go("???"))                                                         # research: refused, never passed
    assert _run(go("???", refused_only=False)) == []                            # display: no tags, nothing hidden


# --- (7) fail closed ----------------------------------------------------------------------------------------------------
def test_a_policy_that_parses_to_no_window_refuses_the_import_and_the_check(tmp_path, owner):
    make_data(tmp_path)
    (tmp_path / "README.md").write_text("# data\n\n## Data-use rules\n\n| Window | Dates | Use |\n|---|---|---|\n| HOLDOUT | soon | ? |\n")
    report = _run(seed_import.run(_session_factory, data_dir=str(tmp_path), now=NOW))
    assert report.status == "policy_unreadable" and "no readable window" in report.notes[0]
    assert _run(_count_seeded(owner.tenant_id)) == 0                            # nothing imported
    assert seed_import.check(str(tmp_path), now=NOW).status == "policy_unreadable"
    assert _run(seed_import._main(["check", "--data-dir", str(tmp_path)])) == 3


def test_no_policy_at_all_is_refused_while_one_is_required(tmp_path, owner, monkeypatch):
    make_data(tmp_path)
    (tmp_path / "README.md").unlink()
    assert _run(seed_import.run(_session_factory, data_dir=str(tmp_path), now=NOW)).status == "policy_unreadable"
    monkeypatch.setattr(config, "TRADE_DATA_POLICY_REQUIRED", False)
    assert _run(seed_import.run(_session_factory, data_dir=str(tmp_path), now=NOW)).status == "ok"


async def _count_seeded(tenant_id: int) -> int:
    async with _session_factory() as session:
        return int(await session.scalar(select(sa.func.count()).select_from(R).where(R.tenant_id == tenant_id, R.source.like("seed:%"))) or 0)


def test_the_folder_list_comes_from_the_host_setting_not_the_code(tmp_path, owner, monkeypatch):
    make_data(tmp_path)
    monkeypatch.setattr(config, "TRADE_DATA_FOLDERS", ("sample_c",))
    report = seed_import.check(str(tmp_path), now=NOW)
    assert {f.path.split("/")[0] for f in report.files} == {"sample_c"}
    monkeypatch.setattr(config, "TRADE_DATA_FOLDERS", ())
    assert {f.path.split("/")[0] for f in seed_import.check(str(tmp_path), now=NOW).files} == {"sample_a", "sample_b", "sample_c", "sample_d"}


# --- (1) / (8) the research paths ---------------------------------------------------------------------------------------
def test_walk_forward_and_optimise_routes_refuse_the_sealed_rows():
    headers, user = _headers(f"pl7-routes-{datetime.now(UTC).timestamp()}@example.com")
    df = make_series(noisy_uptrend(900, seed=11))
    candles = _candles(df)
    body = {"strategy_id": "ema_rsi_scalper_1m", "symbol": "NIFTY 50", "base_timeframe": "1min", "candles": candles}
    assert client.post("/api/backtest/walk-forward?folds=2", headers=headers, json=body).status_code == 200   # no window yet
    last_day = df.index[-1].tz_convert("Asia/Kolkata").date() if df.index.tz is not None else df.index[-1].date()
    _seal(user.tenant_id, last_day)                                             # the newest session is sealed
    part = client.post("/api/backtest/walk-forward?folds=2", headers=headers, json=body)
    assert part.status_code == 200, part.text
    assert part.json()["data_policy"]["bars_left_out"] > 0 and "sealed" in part.json()["data_policy"]["reason"]
    _seal(user.tenant_id, date(2000, 1, 1), kind="contaminated")               # now every bar is refused
    wf = client.post("/api/backtest/walk-forward?folds=2", headers=headers, json=body)
    assert wf.status_code == 400 and "bars left out" in wf.json()["detail"]
    opt = client.post("/api/backtest/optimize", headers=headers, json={**body, "symbol": "NIFTY50", "param_grid": {"rsi_period": [7, 14]}})
    assert opt.status_code == 400 and "bars left out" in opt.json()["detail"]
    other, _ = _headers(f"pl7-routes-other-{datetime.now(UTC).timestamp()}@example.com")
    assert client.post("/api/backtest/walk-forward?folds=2", headers=other, json=body).status_code == 200    # another organisation


def test_the_copilot_draft_test_refuses_sealed_rows(monkeypatch):
    from app.ai.tools import ToolContext, market
    from tests.test_hc3b_research_loop import _draft, _frame
    _, user = _headers(f"pl7-draft-{datetime.now(UTC).timestamp()}@example.com")
    frame = _frame()

    async def fake_frame(ctx, symbol, exchange, tf, days):
        return frame, "broker:fake", {"words": "", "line": ""}
    monkeypatch.setattr(market, "_backtest_frame", fake_frame)
    draft = {**_draft("d", 9, 21), "timeframe": "15min"}

    async def run():
        async with _session_factory() as session:
            ctx = ToolContext(session, user.tenant_id, user)
            return await market.backtest_draft(ctx, market._DraftBacktestArgs(config=draft, symbol="NIFTY 50"))
    cut = (frame.index[len(frame) // 2]).tz_convert("Asia/Kolkata").date()
    _seal(user.tenant_id, cut)
    half = _run(run())
    assert half.ok and half.data["data_policy_bars_left_out"] > 0 and half.data["to"] < str(cut)
    _seal(user.tenant_id, date(2000, 1, 1), end=cut - timedelta(days=1), kind="contaminated")
    refused = _run(run())
    assert not refused.ok and "bars left out" in (refused.error or "")


def test_the_ai_research_job_never_searches_sealed_rows(monkeypatch):
    from tests.test_hc3b_research_api import _flags
    from tests.test_hc3b_research_loop import _draft
    from tests.test_hc3c_research_jobs import _clear_queue, _enqueue, _patch, _row, _step
    from tests.test_phase_l_ai import _owner
    _flags(ai_research=True)
    try:
        _, me = _owner(f"pl7-research-{datetime.now(UTC).timestamp()}@example.com")
        _clear_queue()
        _patch(monkeypatch, [json.dumps(_draft("a", 9, 21)), "null"])
        _seal(me["tenant_id"], date(2000, 1, 1))
        sid = _enqueue(me["tenant_id"], me.get("user_id", me["id"]))
        assert _step() == sid
        row = _row(sid)
        assert row.status == "failed" and "0 bars" in (row.error or "")
    finally:
        _flags(ai_research=False)


# --- (3) / (4) / (5) recorded premiums: daily only, over-limit -> model ----------------------------------------------------
def _option_body(tf: str, days: list[date]):
    from app.backtest.routes import BacktestBody
    candles = [{"timestamp": datetime.combine(d, time(9, 15), tzinfo=IST).isoformat(), "open": 17000, "high": 17100, "low": 16900,
                "close": 17050, "volume": 0} for d in days]
    return BacktestBody.model_validate({"strategy_id": "swing_ema_pullback_d", "symbol": "NIFTY", "base_timeframe": tf,
                                        "candles": candles, "options": {}})


def _build(body, tenant_id):
    from app.backtest.routes import BacktestRunner
    from app.core.models import RiskConfig

    async def go():
        async with _session_factory() as session:
            return await BacktestRunner.build(body, RiskConfig(), session, tenant_id=tenant_id)
    return _run(go())


def test_the_runner_uses_recorded_premiums_on_daily_bars_only_and_falls_back_on_too_many(tmp_path, owner, monkeypatch):
    from app.backtest.options import StoredEodPricer, pricing_counts
    make_data(tmp_path)
    assert _run(seed_import.run(_session_factory, data_dir=str(tmp_path), now=NOW)).status == "ok"
    daily = _build(_option_body("1D", [THU, FRI]), owner.tenant_id)
    assert isinstance(daily.pricer, StoredEodPricer) and daily.pricing_note is None
    at_thu = datetime.combine(THU, time(9, 15), tzinfo=IST)
    assert daily.pricer.price("CE", 17000.0, date(2023, 3, 30), spot=17050.0, at=at_thu) == 91.0
    assert pricing_counts(daily.pricer) == {"recorded_quote_hits": 0, "recorded_eod_hits": 1, "model_prices": 0}
    intraday = _build(_option_body("5min", [THU, FRI]), owner.tenant_id)
    assert intraday.pricer is None and "daily bars only" in intraday.pricing_note          # the model, and it says so
    monkeypatch.setattr(seed_store, "MAX_PREMIUM_ROWS", 0)
    over = _build(_option_body("1D", [THU, FRI]), owner.tenant_id)
    assert over.pricer is None and "priced by the model" in over.pricing_note                 # never an HTTP 500
    snap = _build(_option_body("1D", [THU, FRI]).model_copy(update={"options": _option_body("1D", [THU]).options.model_copy(
        update={"pricing": "snapshots"})}), owner.tenant_id)
    assert snap.pricer is not None and snap.pricer.name == "snapshots"


def test_snapshot_chain_counts_recorded_hits_apart_from_model_prices_and_black76_comes_last():
    from app.backtest.options import OptionChainSnapshotRow, SnapshotPricer, StoredEodPricer, SyntheticPricer, VolatilityModel, pricing_counts
    close = datetime.combine(THU, time(15, 30), tzinfo=IST).astimezone(UTC)
    eod = StoredEodPricer([OptionChainSnapshotRow(timestamp=close, expiry=date(2023, 3, 30), strike=17000, right="CE", ltp=91.0)],
                          fallback=SyntheticPricer(VolatilityModel(fixed_iv=0.15)))
    chain = SnapshotPricer([], fallback=eod)  # type: ignore[arg-type]
    at = datetime.combine(THU, time(9, 15), tzinfo=IST)
    assert chain.price("CE", 17000.0, date(2023, 3, 30), spot=17050.0, at=at) == 91.0
    chain.price("CE", 17500.0, date(2023, 3, 30), spot=17050.0, at=at)
    assert pricing_counts(chain) == {"recorded_quote_hits": 0, "recorded_eod_hits": 1, "model_prices": 1}
    from app.backtest.options_engine import _recorded_disclaimer
    text = _recorded_disclaimer(chain)
    assert "1 from your recorded end-of-day premiums" in text and "1 priced by the model" in text


def test_a_special_session_bar_ends_at_the_end_of_its_day():
    sunday = date(2023, 3, 12)
    assert seed_import.session_end_utc(sunday, "NSE", set()) == datetime.combine(sunday, time(23, 59), tzinfo=IST).astimezone(UTC)
    assert seed_import.session_end_utc(THU, "NSE", set()) == datetime.combine(THU, time(15, 30), tzinfo=IST).astimezone(UTC)


# --- the migration up / down ----------------------------------------------------------------------------------------------
def test_migration_allows_missing_volume_adds_the_windows_table_and_goes_down():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = BACKEND / "alembic/versions/c5e7a9b1d3f6_m71_pl7_seed_import.py"
    spec = importlib.util.spec_from_file_location("m71_pl7_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "8d2f4a6c0e19" and module.ROLLBACK_SAFE is False
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE tenants (id INTEGER PRIMARY KEY, name VARCHAR(255))"))
        conn.execute(sa.text("CREATE TABLE md_tenant_candles (tenant_id INTEGER, instrument_key VARCHAR(64), timeframe VARCHAR(8), ts TIMESTAMP, "
                             "source VARCHAR(30), version INTEGER, open NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC, "
                             "volume BIGINT NOT NULL, oi BIGINT, ingested_at TIMESTAMP, "
                             "PRIMARY KEY (tenant_id, instrument_key, timeframe, ts, source, version))"))
        conn.execute(sa.text("INSERT INTO md_tenant_candles VALUES (1, 'K', '1m', '2023-03-06 03:46:00', 'broker:x', 1, 1, 1, 1, 1, 7, NULL, "
                             "'2023-03-06 04:00:00')"))
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        conn.execute(sa.text("INSERT INTO md_tenant_candles VALUES (1, 'K', '1m', '2023-03-06 03:47:00', 'seed:a', 1, 1, 1, 1, 1, NULL, NULL, "
                             "'2023-03-06 04:00:00')"))
        assert {c["name"] for c in sa.inspect(conn).get_columns("data_use_windows")} >= {"tenant_id", "kind", "scope", "start_date", "end_date"}
        assert [r[0] for r in conn.execute(sa.text("SELECT volume FROM md_tenant_candles ORDER BY ts"))] == [7, None]   # stored value kept
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
        assert [r[0] for r in conn.execute(sa.text("SELECT volume FROM md_tenant_candles ORDER BY ts"))] == [7, 0]      # 0 only where NULL
        assert "data_use_windows" not in sa.inspect(conn).get_table_names()
        cols = {c["name"]: c for c in sa.inspect(conn).get_columns("md_tenant_candles")}
        assert not cols["volume"]["nullable"]
