"""M71 PL-7: seeding the private store from the owner's private data checkout (app/market_lake/seed_import.py), the
data-use windows (app/market_lake/data_use.py, app/backtest/data_policy.py) and the recorded end-of-day option premiums
(app/market_lake/seed_store.py, app/backtest/options.StoredEodPricer).

Every file here is SYNTHETIC (generated below, invented prices); no real data is read. Covered: an absent directory is a
clean no-op; owner-only tenant (refused when the owner is not confirmed); missing volume stays NULL (never 0); an
idempotent re-run (same sha256 -> nothing rewritten); gaps recorded so the backfill plans only what is missing; sealed
rows refused by the optimiser / research paths and shown by the chart path; recorded premiums first, the model only for
what has none.
"""
import asyncio
import gzip
import io
from datetime import UTC, date, datetime, time, timedelta

import pandas as pd
import pytest
from sqlalchemy import select

from app.backtest import data_policy as DP
from app.backtest.options import StoredEodPricer, SyntheticPricer, VolatilityModel
from app.core import config
from app.db.models import DataUseWindowRecord, HistoryImportFileRecord, LakeBackfillWindowRecord, MdBarFlagRecord, MdTenantCandleRecord
from app.market_data.calendar import IST
from app.market_lake import backfill as bf
from app.market_lake import data_use, seed_import, seed_store, tenant_store
from tests.test_auth_api import _session_factory
from tests.test_data_h_lake_backfill import _count, _tenant_name, _user

R = MdTenantCandleRecord
W = LakeBackfillWindowRecord
WEEK1 = [date(2023, 2, 27) + timedelta(days=i) for i in range(5)]          # Mon..Fri, every session full
MON, TUE, WED, THU, FRI = (date(2023, 3, 6) + timedelta(days=i) for i in range(5))
NOW = datetime(2023, 3, 10, 17, 0, tzinfo=IST)                              # after Friday's close
README = """# synthetic data

## Data-use rules (synthetic)

| Window | Dates | Use |
|---|---|---|
| IS | 2001 - 2003 | calibration |
| CONTAMINATED | 2023-03-01 - 2023-03-02 | regression only |
| HOLDOUT (sealed) | 2023-03-10 onward | never |
"""


def _run(coro):
    return asyncio.run(coro)


def _minutes(day: date, base: float, n: int = 375) -> list[dict]:
    start = datetime.combine(day, time(9, 15))
    return [{"timestamp": (start + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"), "open": base + i * 0.1, "high": base + i * 0.1 + 1,
             "low": base + i * 0.1 - 1, "close": base + i * 0.1 + 0.5} for i in range(n)]


def _write_csv_gz(path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    pd.DataFrame(rows).to_csv(buf, index=False)
    path.write_bytes(gzip.compress(buf.getvalue().encode()))


def make_data(root) -> None:
    """A synthetic private checkout: NIFTY 1m (no volume column), a futures 5m file (volume, contract, a bar after the
    close), a commodity 30m file (no volume), an F&O bhavcopy month (a traded option, an untraded one, a future) and
    the policy README."""
    (root / "README.md").write_text(README)
    rows = []
    for d in WEEK1 + [MON, TUE, FRI]:
        rows += _minutes(d, 100.0)
    rows += _minutes(THU, 100.0, n=370)                                         # five minutes missing; WED missing
    _write_csv_gz(root / "upstox" / "NIFTY_1m_synthetic.csv.gz", rows)
    fut = [{"timestamp": pd.Timestamp(f"{FRI} 15:{m:02d}:00"), "open": 200.0, "high": 201.0, "low": 199.0, "close": 200.5, "volume": 50 + m,
            "oi": 1000, "contract": "NIFTY FUT 30 MAR 23"} for m in (20, 25, 30)]
    (root / "futures").mkdir(parents=True)
    pd.DataFrame(fut).to_parquet(root / "futures" / "oe_futures_5min_NIFTY_synthetic.parquet")
    (root / "major_levels_candles").mkdir(parents=True)
    pd.DataFrame([{"timestamp": f"{FRI}T{h:02d}:00:00+05:30", "open": 50.0, "high": 51.0, "low": 49.0, "close": 50.5,
                   "contract": "GOLD FUT 05 APR 23"} for h in (10, 11)]).to_csv(root / "major_levels_candles" / "GOLD_30m_synthetic.csv", index=False)
    fo = pd.DataFrame([
        {"date": str(THU), "symbol": "NIFTY", "instrument": "OPT", "expiry": "2023-03-30", "strike": 17000.0, "opt_type": "CE",
         "open": 90.0, "high": 95.0, "low": 85.0, "close": 91.0, "settle": 91.0, "volume": 10.0, "volume_unit": "contracts", "oi": 5.0},
        {"date": str(THU), "symbol": "NIFTY", "instrument": "OPT", "expiry": "2023-03-30", "strike": 17100.0, "opt_type": "CE",
         "open": 0.0, "high": 0.0, "low": 0.0, "close": 40.0, "settle": 40.0, "volume": 0.0, "volume_unit": "contracts", "oi": 0.0},
        {"date": str(THU), "symbol": "NIFTY", "instrument": "FUT", "expiry": "2023-03-30", "strike": 0.0, "opt_type": "XX",
         "open": 170.0, "high": 172.0, "low": 169.0, "close": 171.0, "settle": 171.0, "volume": 3.0, "volume_unit": "contracts", "oi": 9.0},
    ])
    (root / "nse_fo_bhavcopy" / "NIFTY").mkdir(parents=True)
    fo.to_parquet(root / "nse_fo_bhavcopy" / "NIFTY" / "2023-03.parquet")


@pytest.fixture()
def owner(monkeypatch):
    user = _user(f"pl7-{datetime.now(UTC).timestamp()}@example.com")
    monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_ID", user.tenant_id)
    monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_NAME", _tenant_name(user.tenant_id))
    monkeypatch.setattr(config, "HISTORY_OWNER_USER_ID", None)
    monkeypatch.setattr(config, "HISTORY_BACKFILL_FO_VOLUME", "withhold")
    monkeypatch.setattr(config, "TRADE_DATA_POLICY_SCOPE", ("NIFTY",))
    return user


def _import(root):
    return _run(seed_import.run(_session_factory, data_dir=str(root), now=NOW))


def test_absent_or_empty_directory_is_a_clean_no_op(tmp_path, owner):
    before = _count(R, R.source.like("seed:%"))
    for target in (tmp_path / "missing", tmp_path):                             # missing; present but empty
        report = _import(target)
        assert report.status == "not_available" and not report.files
    assert seed_import.check(str(tmp_path / "missing")).status == "not_available"
    assert _count(R, R.source.like("seed:%")) == before


def test_refused_without_the_confirmed_owner_nothing_written(tmp_path, owner, monkeypatch):
    make_data(tmp_path)
    monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_NAME", "not the owner's name")
    report = _import(tmp_path)
    assert report.status == "not_configured" and not report.files
    assert _count(R, R.tenant_id == owner.tenant_id) == 0


def test_seed_import_owner_only_volume_kept_missing_flags_and_idempotent(tmp_path, owner):
    make_data(tmp_path)
    other = _user(f"pl7-other-{datetime.now(UTC).timestamp()}@example.com")
    report = _import(tmp_path)
    assert report.status == "ok", report.as_dict()
    done = {f.path: f for f in report.files if f.status == "done"}
    assert set(done) == {"upstox/NIFTY_1m_synthetic.csv.gz", "futures/oe_futures_5min_NIFTY_synthetic.parquet",
                         "major_levels_candles/GOLD_30m_synthetic.csv", "nse_fo_bhavcopy/NIFTY/2023-03.parquet"}
    # owner-only: every seeded row is in the owner's private store, none in another organisation's, and the M55 T4
    # shared index scope (another organisation reading the owner's index rows) never sees a seeded row
    assert _count(R, R.source.like("seed:%"), R.tenant_id == other.tenant_id) == 0
    from app.market_lake import own_bars

    async def shared_read():
        async with _session_factory() as session:
            scope = own_bars.SharedScope(owner.tenant_id, owner.id)
            lo = datetime.combine(WEEK1[0], time(0), tzinfo=IST).astimezone(UTC)
            return (await own_bars.fetch_rows(session, scope, "NSE_INDEX:NIFTY50", "1m", lo, lo + timedelta(days=14)),
                    await own_bars.has_rows(session, scope, "NSE_INDEX:NIFTY50"))
    assert _run(shared_read()) == ([], False)
    assert _count(R, R.tenant_id == owner.tenant_id, R.instrument_key == "NSE_INDEX:NIFTY50", R.timeframe == "1m") == 8 * 375 + 370
    # missing volume stays missing (never 0): no volume column -> NULL; F&O volume withheld -> NULL; given -> kept
    assert _count(R, R.tenant_id == owner.tenant_id, R.instrument_key == "NSE_INDEX:NIFTY50", R.volume.is_not(None)) == 0
    assert _count(R, R.tenant_id == owner.tenant_id, R.instrument_key == "MCX:GOLD:FUT:2023-04-05", R.timeframe == "30m", R.volume.is_(None)) == 2
    assert _count(R, R.tenant_id == owner.tenant_id, R.instrument_key.like("NFO:NIFTY:%"), R.timeframe == "day", R.volume.is_not(None)) == 0
    assert _count(R, R.tenant_id == owner.tenant_id, R.instrument_key == "NFO:NIFTY:FUT:2023-03-30", R.timeframe == "5m", R.volume == 0) == 0
    assert _count(R, R.tenant_id == owner.tenant_id, R.instrument_key == "NFO:NIFTY:FUT:2023-03-30", R.timeframe == "5m", R.volume > 0) == 3
    # the untraded option is left out (its close is not a trade)
    assert _count(R, R.tenant_id == owner.tenant_id, R.instrument_key == "NFO:NIFTY:CE:2023-03-30:17100") == 0
    assert _count(R, R.tenant_id == owner.tenant_id, R.instrument_key == "NFO:NIFTY:CE:2023-03-30:17000") == 1
    # END labels: the first NIFTY bar (start 09:15 IST) ends 09:16 IST; Daily bars end at the 15:30 IST close
    async def first_end():
        async with _session_factory() as session:
            return await session.scalar(select(R.ts).where(R.tenant_id == owner.tenant_id, R.instrument_key == "NSE_INDEX:NIFTY50").order_by(R.ts).limit(1))
    first = _run(first_end())
    assert (first if first.tzinfo else first.replace(tzinfo=UTC)) == datetime.combine(WEEK1[0], time(9, 16), tzinfo=IST).astimezone(UTC)
    assert seed_import.session_end_utc(THU) == datetime.combine(THU, time(15, 30), tzinfo=IST).astimezone(UTC)
    # DH3: the futures bar that starts at the close is flagged post_close (the readers drop it)
    assert _count(MdBarFlagRecord, MdBarFlagRecord.tenant_id == owner.tenant_id, MdBarFlagRecord.instrument_key == "NFO:NIFTY:FUT:2023-03-30",
                  MdBarFlagRecord.kind == "post_close") == 1
    # per-file bookkeeping: source label, sha256, rows, first / last
    rec = _run(_file_record("trade-data:upstox/NIFTY_1m_synthetic.csv.gz"))
    assert rec.status == "done" and len(rec.sha256) == 64 and rec.rows_read == 8 * 375 + 370 and rec.tenant_id == owner.tenant_id
    assert rec.dataset == "seed" and rec.period == "1m" and rec.first_ts is not None and rec.last_ts is not None
    # idempotent: the same files again -> skipped, nothing rewritten
    rows_before = _count(R, R.tenant_id == owner.tenant_id)
    again = _import(tmp_path)
    assert {f.status for f in again.files if f.path in done} == {"skipped"}
    assert sum(f.inserted for f in again.files) == 0 and _count(R, R.tenant_id == owner.tenant_id) == rows_before
    assert _count(HistoryImportFileRecord, HistoryImportFileRecord.object_key.like("trade-data:%"), HistoryImportFileRecord.tenant_id == owner.tenant_id) == 4
    # a changed file (new sha256) is read again: its new bar is written, identical bars skipped
    pd.DataFrame([{"timestamp": f"{FRI}T12:00:00+05:30", "open": 50.0, "high": 51.0, "low": 49.0, "close": 50.5,
                   "contract": "GOLD FUT 05 APR 23"}]).to_csv(tmp_path / "major_levels_candles" / "GOLD_30m_synthetic.csv", mode="a", header=False, index=False)
    third = _import(tmp_path)
    gold = next(f for f in third.files if f.path.endswith("GOLD_30m_synthetic.csv"))
    assert gold.status == "done" and gold.inserted == 1 and gold.skipped == 2


async def _file_record(key: str) -> HistoryImportFileRecord:
    async with _session_factory() as session:
        return await session.scalar(select(HistoryImportFileRecord).where(HistoryImportFileRecord.object_key == key))


def test_gaps_recorded_so_the_backfill_plans_only_what_is_missing(tmp_path, owner, monkeypatch):
    make_data(tmp_path)
    report = _import(tmp_path)
    assert report.windows["series"]["NSE_INDEX:NIFTY50"] == {"done": 1, "gap": 1}

    async def rows():
        async with _session_factory() as session:
            return list(await session.scalars(select(W).where(W.tenant_id == owner.tenant_id, W.instrument_key == "NSE_INDEX:NIFTY50")))
    by_start = {r.window_start: r for r in _run(rows())}
    assert by_start[WEEK1[0]].status == "done"                                  # every session complete: never asked
    gap = by_start[MON]
    assert gap.status == "gap" and bf.gap_range(gap.error) == (WED, THU)        # only the missing session and the short one
    monkeypatch.setattr(config, "MARKET_BACKFILL_INTRADAY_DAYS", 30)

    async def planned():
        async with _session_factory() as session:
            return await bf.plan(session, owner.tenant_id, owner.id, [bf.to_symbol("NIFTY")], FRI)
    todo = {p.start for p in _run(planned())}
    assert MON not in todo and WEEK1[0] not in todo                            # the seeded windows are not planned again
    assert todo and max(todo) < WEEK1[0]                                        # only the older, unseeded windows remain


def test_sealed_rows_refused_by_optimisation_and_research_shown_by_charts(tmp_path, owner, monkeypatch):
    make_data(tmp_path)
    report = _import(tmp_path)
    assert report.policy["kinds"] == {"in_sample": 1, "contaminated": 1, "sealed": 1}

    async def windows():
        async with _session_factory() as session:
            return await data_use.windows_for(session, owner.tenant_id, "NIFTY", refused_only=True)
    refused = _run(windows())
    assert {w.kind for w in refused} == {"sealed", "contaminated"}
    assert _run(_windows_other_symbol(owner.tenant_id)) == []                   # scope: the policy names NIFTY only
    # the optimiser never runs a sealed / contaminated bar and says why
    import app.backtest.optimizer as opt
    from app.backtest.optimizer import optimize
    from app.core.models import RiskConfig
    from app.strategy_engine.registry import registry
    from tests.utils import make_series, noisy_uptrend
    monkeypatch.delenv("BACKTEST_HOLDOUT_START", raising=False)
    df = make_series(noisy_uptrend(400, seed=11, period=20), start="2022-02-06", freq="1D")
    seen = []
    real = opt.run_backtest

    def spy(strategy, frame, *a, **k):
        seen.append((frame.index.min(), frame.index.max(), len(frame)))
        return real(strategy, frame, *a, **k)
    monkeypatch.setattr(opt, "run_backtest", spy)
    result = optimize(registry.get("swing_ema_pullback_d"), df, "NIFTY", "day", RiskConfig(), {"ema_fast": [10, 20]}, split=0.6,
                      use_windows=refused)
    sealed_from = pd.Timestamp("2023-03-10")
    contaminated = (pd.Timestamp("2023-03-01"), pd.Timestamp("2023-03-02"))
    assert max(s[1] for s in seen) < sealed_from
    assert result["data_policy_bars_excluded"] == 5 and "sealed" in result["data_policy"]       # 2 contaminated + 3 sealed days
    allowed = DP.filter_allowed(df, None, refused)
    assert not ((allowed.index >= contaminated[0]) & (allowed.index <= contaminated[1])).any()
    with pytest.raises(DP.HoldoutError, match="reserves"):
        DP.check_range("2023-03-05", "2023-03-12", None, refused)              # the research window check
    with pytest.raises(ValueError, match="bars left out"):
        optimize(registry.get("swing_ema_pullback_d"), df[df.index >= sealed_from], "NIFTY", "day", RiskConfig(), {"ema_fast": [10]},
                 use_windows=refused)
    # the chart path shows the sealed day's bars (nothing hidden) and the store's reader tags them
    async def chart():
        async with _session_factory() as session:
            lo = datetime.combine(FRI, time(9, 0), tzinfo=IST).astimezone(UTC)     # UTC: the test database compares text
            hi = datetime.combine(FRI, time(16, 0), tzinfo=IST).astimezone(UTC)
            bars = await tenant_store.candles(session, owner.tenant_id, "NSE_INDEX:NIFTY50", "5m", lo, hi, symbol="NIFTY", kind="INDEX")
            tagged = await seed_store.series(session, owner.tenant_id, "NSE_INDEX:NIFTY50", "1m", lo, hi)
            return bars, tagged
    bars, tagged = _run(chart())
    assert len(bars) == 75 and all(b.volume is None for b in bars)             # shown, volume still missing
    assert len(tagged["bars"]) == 375 and tagged["tags"] == [{"kind": "sealed", "from": str(FRI), "to": str(FRI), "refused_for_research": True}]


async def _windows_other_symbol(tenant_id: int):
    async with _session_factory() as session:
        return await data_use.windows_for(session, tenant_id, "BANKNIFTY")


def test_policy_windows_belong_to_the_owner_only_and_reload_idempotently(tmp_path, owner):
    make_data(tmp_path)
    _import(tmp_path)
    _import(tmp_path)
    assert _count(DataUseWindowRecord, DataUseWindowRecord.tenant_id == owner.tenant_id) == 3
    other = _user(f"pl7-pol-{datetime.now(UTC).timestamp()}@example.com")

    async def other_windows():
        async with _session_factory() as session:
            return await data_use.windows_for(session, other.tenant_id, "NIFTY")
    assert _run(other_windows()) == []


def test_policy_parsing_is_generic():
    windows = data_use.parse_readme(README, ["NIFTY"])
    assert [(w.kind, w.start, w.end) for w in windows] == [
        ("in_sample", date(2001, 1, 1), date(2003, 12, 31)), ("contaminated", date(2023, 3, 1), date(2023, 3, 2)),
        ("sealed", date(2023, 3, 10), None)]
    assert data_use.parse_readme("| Window | Dates | Use |\n|---|---|---|\n| HOLDOUT | 2020 onward | x |\n", ["*"]) == []   # no data-use heading
    assert data_use.parse_span("2022-01 - 2024-02") == (date(2022, 1, 1), date(2024, 2, 29))
    assert data_use.underlying_of("NSE_INDEX:NIFTY50") == "NIFTY" and data_use.underlying_of("NFO:NIFTY:CE:2023-03-30:17000") == "NIFTY"


def test_recorded_premiums_first_the_model_only_where_none_is_recorded(tmp_path, owner):
    make_data(tmp_path)
    _import(tmp_path)

    async def premiums():
        async with _session_factory() as session:
            return await seed_store.option_premiums(session, owner.tenant_id, "NIFTY", datetime.combine(THU, time(0), tzinfo=IST),
                                                    datetime.combine(FRI, time(23), tzinfo=IST))
    rows = _run(premiums())
    assert [(r.strike, r.right, r.ltp) for r in rows] == [(17000.0, "CE", 91.0)]
    with pytest.raises(PermissionError):
        _run(seed_store.option_premiums(None, None, "NIFTY", datetime.now(UTC), datetime.now(UTC)))  # type: ignore[arg-type]
    pricer = StoredEodPricer(rows, fallback=SyntheticPricer(VolatilityModel(fixed_iv=0.15)))
    after_close = datetime.combine(FRI, time(9, 15), tzinfo=IST).astimezone(UTC)
    before_close = datetime.combine(THU, time(15, 0), tzinfo=IST).astimezone(UTC)
    assert pricer.price("CE", 17000.0, date(2023, 3, 30), spot=17050.0, at=after_close) == 91.0      # recorded
    pricer.price("CE", 17000.0, date(2023, 3, 30), spot=17050.0, at=before_close)                  # not known yet: model
    pricer.price("CE", 17200.0, date(2023, 3, 30), spot=17050.0, at=after_close)                   # not recorded: model
    assert pricer.hits == 1 and pricer.fallbacks == 2 and "recorded end-of-day" in pricer.describe()
