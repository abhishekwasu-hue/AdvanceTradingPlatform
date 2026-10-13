"""N7 I (first slice): options analytics from recorded chain quotes, as of a time (flag options_analytics, off).

Fixtures are Black-76 prices at known IVs, so every number has an independent expected value: the ATM IV is the IV the
quotes were made with, the expected move is the ATM call + put premium, PCR and max pain follow the crafted open
interest, the risk reversal is the put IV minus the call IV at 5 % moneyness. Rows captured after `at` never change a
value at `at` (no look-ahead); IV rank / percentile need MIN_HISTORY sessions. The route reads the recorder's rows and
the organisation's own uploads - only the uploads under the bring-your-own-broker data tier (BT1b) - and only quotes
stored by the time asked (an upload made later never changes an earlier answer). The route's reads are bounded (capture
times, quotes, IN lists of at most IN_CHUNK keys) and rate-limited; max pain's prefix sums match the direct sum."""
import asyncio
import random
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.backtest import chain_recorder
from app.backtest.options import OptionChainSnapshotRow, black76
from app.core import rate_limit
from app.db.models import OptionChainSnapshotRecord
from app.options_analytics import analytics as A
from app.options_analytics import routes as OR
from app.options_analytics.routes import FLAG
from app.platform.controls import DEFAULT_OFF_FLAGS
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _auth
from tests.test_n1_e1_execution_core import _flag

AT = datetime(2026, 9, 28, 6, 0, tzinfo=timezone.utc)            # 11:30 IST, Monday
NEAR, FAR = date(2026, 10, 6), date(2026, 10, 27)
STRIKES = [float(k) for k in range(24000, 25001, 100)]


def _chain(at, spot=24500.0, iv=None, oi=None, *, underlying_ltp=True, expiries=(NEAR, FAR)):
    """Quotes at `at`: Black-76 premiums (r = 0, so forward = spot) at `iv(expiry, strike, right)` (percent)."""
    iv = iv or (lambda e, k, r: 14.0 if e == NEAR else 16.0)
    oi = oi or (lambda e, k, r: 1000.0)
    rows = []
    for e in expiries:
        t = ((datetime.combine(e, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=10)) - at).total_seconds() / (365 * 86400)
        for k in STRIKES:
            for right in ("CE", "PE"):
                v = iv(e, k, right)
                rows.append(OptionChainSnapshotRow(timestamp=at, expiry=e, strike=k, right=right, ltp=max(0.05, black76(right, spot, k, t, 0.0, v / 100)),
                                                   iv=v, oi=oi(e, k, right), underlying_ltp=spot if underlying_ltp else None))
    return rows


def test_atm_iv_term_structure_and_expected_move():
    rows = _chain(AT)
    out = A.analytics(rows, AT)
    near, far = out["expiries"][0], out["expiries"][1]
    assert out["spot"] == 24500.0 and near["atm_strike"] == 24500.0
    assert near["atm_iv"] == pytest.approx(0.14) and far["atm_iv"] == pytest.approx(0.16)
    assert [t["atm_iv"] for t in out["term_structure"]] == pytest.approx([0.14, 0.16]) and near["dte"] == 8
    ce = next(r for r in rows if r.expiry == NEAR and r.strike == 24500 and r.right == "CE")
    pe = next(r for r in rows if r.expiry == NEAR and r.strike == 24500 and r.right == "PE")
    assert near["expected_move"]["straddle"] == pytest.approx(ce.ltp + pe.ltp, abs=0.01)
    assert near["expected_move"]["pct"] == pytest.approx(100 * (ce.ltp + pe.ltp) / 24500, abs=1e-3)


def test_pcr_max_pain_and_skew_follow_the_crafted_chain():
    oi = lambda e, k, r: (5000.0 if (r == "PE" and k == 24200) or (r == "CE" and k == 24800) else 500.0) * (2 if r == "PE" else 1)   # noqa: E731
    smile = lambda e, k, r: 14.0 + abs(k - 24500) / 100 * (0.8 if k < 24500 else 0.3)                                       # noqa: E731
    out = A.analytics(_chain(AT, iv=smile, oi=oi), AT)
    near = out["expiries"][0]
    ce_oi = sum(oi(NEAR, k, "CE") for k in STRIKES)
    pe_oi = sum(oi(NEAR, k, "PE") for k in STRIKES)
    assert near["pcr"] == pytest.approx(pe_oi / ce_oi, abs=1e-4)

    def paid(s):
        return sum(oi(NEAR, k, "CE") * max(s - k, 0) + oi(NEAR, k, "PE") * max(k - s, 0) for k in STRIKES)
    assert near["max_pain"] == min(STRIKES, key=lambda s: (paid(s), s))
    rr = near["skew"]["risk_reversal"]
    put_k = min(STRIKES, key=lambda k: abs(k - 24500 * 0.95))
    call_k = min(STRIKES, key=lambda k: abs(k - 24500 * 1.05))
    assert rr == pytest.approx(smile(NEAR, put_k, "PE") / 100 - smile(NEAR, call_k, "CE") / 100, abs=1e-6) and rr > 0
    assert all(c["right"] == ("PE" if c["strike"] < 24500 else "CE") for c in near["skew"]["curve"])


def test_the_spot_falls_back_to_put_call_parity():
    out = A.analytics(_chain(AT, spot=24530.0, underlying_ltp=False), AT)
    assert out["spot"] == pytest.approx(24530.0, abs=1.0) and out["expiries"][0]["atm_strike"] == 24500.0


def test_rows_after_the_time_never_change_the_answer():
    base = _chain(AT) + _chain(AT - timedelta(days=1))
    later = _chain(AT + timedelta(minutes=5), spot=26000.0, iv=lambda e, k, r: 40.0) + _chain(AT + timedelta(days=3), iv=lambda e, k, r: 9.0)
    assert A.analytics(base + later, AT) == A.analytics(base, AT)
    stale = A.analytics(_chain(AT - timedelta(hours=2)), AT)                     # nothing within MAX_AGE: no answer, a note
    assert stale["spot"] is None and "No recorded quotes" in stale["note"]


def test_iv_rank_and_percentile_need_enough_sessions():
    days = [AT - timedelta(days=i) for i in range(40, -1, -1)]
    rows = []
    for i, d in enumerate(days):
        level = 10.0 + i * 0.25                                                  # a rising ATM IV: today is the highest
        rows += _chain(d, iv=lambda e, k, r, v=level: v, expiries=(FAR + timedelta(days=60),))
    out = A.analytics(rows, AT)
    assert out["iv"]["sessions"] == 41 and out["iv"]["iv_rank"] == pytest.approx(100.0) and out["iv"]["iv_percentile"] == pytest.approx(100.0)
    short = A.analytics(rows[-len(STRIKES) * 2 * 10:], AT)                       # the last 10 sessions only
    assert short["iv"]["iv_rank"] is None and "at least" in short["iv"]["note"]
    mid = A.analytics(rows, days[20])                                            # as of an earlier day: that day's history only
    assert mid["iv"]["sessions"] == 21 and mid["iv"]["current"] == pytest.approx(0.15)


# --- the route ------------------------------------------------------------------------------------------------------------------

def _seed(underlying, rows, source="worker", ingested=None):
    """Rows stored at `ingested` (default: as captured, like the recorder writing live)."""
    async def go():
        async with _session_factory() as session:
            session.add_all([OptionChainSnapshotRecord(underlying=underlying, expiry=r.expiry, captured_at=r.timestamp, strike=r.strike, right=r.right,
                                                       ltp=r.ltp, iv=r.iv, oi=r.oi, underlying_ltp=r.underlying_ltp, source=source,
                                                       ingested_at=ingested or r.timestamp) for r in rows])
            await session.commit()
    asyncio.run(go())


def test_the_route_needs_its_flag_and_reads_only_what_the_organisation_may_see():
    assert FLAG in DEFAULT_OFF_FLAGS
    name = f"OA{uuid.uuid4().hex[:6].upper()}"
    _seed(name, _chain(AT))
    headers = _auth(f"n7i1-{uuid.uuid4().hex[:6]}@example.com")
    tenant = client.get("/api/auth/me", headers=headers).json()["tenant_id"]
    url = f"/api/options/analytics?underlying={name}&at={AT.isoformat().replace('+', '%2B')}"
    off = client.get(url, headers=headers)
    assert off.status_code == 503 and off.headers.get("X-Feature-Disabled") == FLAG
    _flag(FLAG, tenant)
    body = client.get(url, headers=headers).json()
    assert body["underlying"] == name and body["expiries"][0]["atm_iv"] == pytest.approx(0.14) and "not a recommendation" in body["disclaimer"]
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace("+", "%2B")
    assert client.get(f"/api/options/analytics?underlying={name}&at={future}", headers=headers).status_code == 422
    # BT1b: under the bring-your-own-broker tier the recorder's rows are not this organisation's - its own uploads only
    _flag("byob_data_tier", tenant)
    assert client.get(url, headers=headers).json()["spot"] is None
    _seed(name, _chain(AT, iv=lambda e, k, r: 22.0), source=f"upload:{tenant}")
    own = client.get(url, headers=headers).json()
    assert own["expiries"][0]["atm_iv"] == pytest.approx(0.22) and "uploaded" in own["data"]
    other = _auth(f"n7i1-o-{uuid.uuid4().hex[:6]}@example.com")
    _flag(FLAG, client.get("/api/auth/me", headers=other).json()["tenant_id"])
    assert client.get(url, headers=other).json()["expiries"][0]["atm_iv"] == pytest.approx(0.14)     # never another organisation's upload


def _url(name, at):
    return f"/api/options/analytics?underlying={name}&at={at.isoformat().replace('+', '%2B')}"


def _member(prefix):
    headers = _auth(f"{prefix}-{uuid.uuid4().hex[:6]}@example.com")
    tenant = client.get("/api/auth/me", headers=headers).json()["tenant_id"]
    _flag(FLAG, tenant)
    return headers, tenant


def test_a_quote_counts_only_once_it_was_stored_by_the_time_asked():
    """An upload of older captures made after `at` never changes the answer at `at` (corrections only once ingested)."""
    rate_limit.reset("options_analytics")
    name = f"OB{uuid.uuid4().hex[:6].upper()}"
    headers, tenant = _member("n7i1-k")
    _flag("byob_data_tier", tenant)
    _seed(name, _chain(AT, iv=lambda e, k, r: 22.0), source=f"upload:{tenant}", ingested=AT + timedelta(minutes=5))
    assert client.get(_url(name, AT), headers=headers).json()["spot"] is None                       # stored after AT
    later = client.get(_url(name, AT + timedelta(minutes=10)), headers=headers).json()               # stored by then
    assert later["expiries"][0]["atm_iv"] == pytest.approx(0.22) and "stored" in later["known_by"]


def test_iv_history_through_the_route_and_the_in_list_chunks(monkeypatch):
    rate_limit.reset("options_analytics")
    name = f"OC{uuid.uuid4().hex[:6].upper()}"
    headers, _ = _member("n7i1-h")
    far = FAR + timedelta(days=60)
    for i in range(24, -1, -1):
        d = AT - timedelta(days=i)
        # two captures a session: only the last one of each session feeds IV history
        _seed(name, _chain(d - timedelta(hours=2), iv=lambda e, k, r: 50.0, expiries=(far,)) + _chain(d, iv=lambda e, k, r, v=10.0 + (24 - i) * 0.5: v, expiries=(far,)))
    body = client.get(_url(name, AT), headers=headers).json()
    assert body["iv"]["sessions"] == 25 and body["iv"]["iv_rank"] == pytest.approx(100.0) and body["iv"]["current"] == pytest.approx(0.22)
    assert body["iv"]["high"] == pytest.approx(0.22) and body["limits"] == []
    monkeypatch.setattr(OR, "IN_CHUNK", 3)                                       # many small IN lists: the same answer
    assert client.get(_url(name, AT), headers=headers).json()["iv"] == body["iv"]
    monkeypatch.setattr(OR, "MAX_ROWS", len(STRIKES) * 2 * 10)                   # the newest quotes only, and it says so
    cut = client.get(_url(name, AT), headers=headers).json()
    assert cut["iv"]["sessions"] == 10 and cut["expiries"][0]["atm_iv"] == pytest.approx(0.22) and "newest" in cut["limits"][0]
    monkeypatch.setattr(OR, "MAX_STAMPS", 5)
    assert "capture times" in client.get(_url(name, AT), headers=headers).json()["limits"][0]


def test_needed_stamps_keep_the_snapshot_and_each_sessions_last_window():
    day = [AT - timedelta(days=1, minutes=m) for m in (0, 10, 25, 45, 120)]       # last capture and two within 30 minutes
    now = [AT - timedelta(minutes=m) for m in (0, 20, 40)]
    old = [AT - timedelta(days=50)]
    got = OR.needed_stamps(day + now + old, AT, AT - timedelta(days=30))
    assert got == sorted([day[0], day[1], day[2], now[0], now[1]], reverse=True)


def test_max_pain_matches_the_direct_sum():
    rnd = random.Random(7)
    for _ in range(50):
        strikes = sorted(rnd.sample(range(100, 200), rnd.randint(1, 15)))
        snap = {}
        for k in strikes:
            for r in ("CE", "PE"):
                if rnd.random() < 0.8:
                    snap[(NEAR, float(k), r)] = A.Quote(NEAR, float(k), r, 1.0, None, float(rnd.randint(0, 500)), None, AT)
        legs = [(k, r, q.oi) for (e, k, r), q in snap.items() if q.oi]
        if not legs:
            assert A.max_pain(snap, NEAR) is None
            continue
        cands = sorted({k for k, _, _ in legs})

        def paid(s):
            return sum(oi * (max(s - k, 0) if r == "CE" else max(k - s, 0)) for k, r, oi in legs)
        assert A.max_pain(snap, NEAR) == min(cands, key=lambda s: (paid(s), s))


def test_the_route_is_rate_limited_per_user():
    rate_limit.reset("options_analytics")
    headers, _ = _member("n7i1-r")
    name = f"OD{uuid.uuid4().hex[:6].upper()}"
    codes = [client.get(_url(name, AT), headers=headers).status_code for _ in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429
    rate_limit.reset("options_analytics")


def test_rows_from_before_the_column_count_from_their_capture_only_when_the_recorder_wrote_them():
    """NULL ingested_at (a release before the column): the recorder's rows count from their capture, uploads never."""
    rate_limit.reset("options_analytics")
    name = f"OE{uuid.uuid4().hex[:6].upper()}"
    headers, tenant = _member("n7i1-n")

    _seed(name, _chain(AT))
    _seed(name, _chain(AT, iv=lambda e, k, r: 22.0), source=chain_recorder.upload_source(tenant))

    async def go():
        from sqlalchemy import update
        async with _session_factory() as session:
            await session.execute(update(OptionChainSnapshotRecord).where(OptionChainSnapshotRecord.underlying == name).values(ingested_at=None))
            await session.commit()
    asyncio.run(go())
    assert client.get(_url(name, AT), headers=headers).json()["expiries"][0]["atm_iv"] == pytest.approx(0.14)   # the recorder's only
    _flag("byob_data_tier", tenant)
    assert client.get(_url(name, AT), headers=headers).json()["spot"] is None                                  # the upload never
