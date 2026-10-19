"""Report and chart-renderer tests on SIMULATED fixture data: every format is generated and parsed back; chart
annotations sit on the computed candle/price; owner-only access; snapshot match/mismatch is reported."""
import io
import json
import re
import time

import pandas as pd
import pytest

from src.data.fixture_provider import FixtureProvider
from src.reports import builder
from src.reports.chart_png import render_chart
from src.services.app_services import Services


@pytest.fixture(scope="module")
def svc_sig(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rep")
    svc = Services(db_url=f"sqlite:///{tmp}/r.db", provider=FixtureProvider(end="2026-10-16"))
    uni = pd.DataFrame({"symbol": list("ABCDEFGH"), "ticker": [f"{x}.NS" for x in "ABCDEFGH"], "company": "", "industry": ""})
    j = svc.start_scan(uni, "t", ["breakout", "pullback", "reversal", "trend_continuation"])
    while j.status in ("queued", "running"):
        time.sleep(0.05)
    hist = svc.store.signal_history(svc.local_user)
    assert hist, "fixture scan produced no stored signals"
    return svc, hist[0]["key"]


def test_all_formats_generate_and_parse(svc_sig):
    svc, key = svc_sig
    u = svc.local_user
    out = {f: builder.generate(svc, u, key, f, "detailed") for f in builder.FORMATS}
    js = json.loads(out["json"][1])
    rec = svc.store.get_signal(key, u)
    assert js["summary"]["signal_ref"] == key and js["summary"]["data_snapshot"] == rec["payload"]["data_snapshot"]
    assert js["summary"]["chart_data_matches_signal_snapshot"] is True and js["conditions"]
    csv_text = out["csv"][1].decode("utf-8-sig")
    assert "condition," in csv_text and key in csv_text
    xl = pd.read_excel(io.BytesIO(out["xlsx"][1]), sheet_name=None)
    assert {"Signal", "Conditions", "Score", "Bars", "Disclaimer"} <= set(xl)
    assert out["png"][1][:8] == b"\x89PNG\r\n\x1a\n"
    pdf = out["pdf"][1]
    assert pdf[:5] == b"%PDF-" and len(pdf) > 20000
    for f, (name, _, meta) in out.items():
        assert re.search(rf"_{key[:8]}_detailed_r{meta['report_id']}\.{f}$", name) and "/" not in name and meta["bytes"] > 0
    reps = svc.store.report_list(u)
    assert {r["format"] for r in reps} >= set(builder.FORMATS) and {r["status"] for r in reps} == {"COMPLETED"}
    fn, data = builder.read_report(svc, u, reps[0]["id"])
    assert data and fn == reps[0]["filename"]


def test_reports_are_owner_only(svc_sig):
    svc, key = svc_sig
    other, _ = svc.store.create_user("intruder")
    with pytest.raises(KeyError):
        builder.generate(svc, other, key, "json")
    rid = svc.store.report_list(svc.local_user)[0]["id"]
    with pytest.raises(KeyError):
        builder.read_report(svc, other, rid)


def test_snapshot_mismatch_is_reported(svc_sig, monkeypatch):
    """Provider data revised after the signal -> the report flags the mismatch. The stored record itself cannot be
    edited (A-005 immutability trigger)."""
    import pytest as _pt
    from sqlalchemy import select, update
    from sqlalchemy.exc import DatabaseError
    from src.storage.db import signals
    svc, key = svc_sig
    with _pt.raises(DatabaseError, match="immutable"):
        with svc.store.engine.begin() as c:
            p = dict(c.execute(select(signals.c.payload).where(signals.c.key == key)).scalar())
            c.execute(update(signals).where(signals.c.key == key).values(payload={**p, "data_snapshot": "x"}))
    real = svc._provider.fetch_many

    def revised(tickers, tf):
        out = real(tickers, tf)
        return {k: (v.assign(Close=v.Close * 1.01, High=v.High * 1.01) if v is not None else v) for k, v in out.items()}
    monkeypatch.setattr(svc._provider, "fetch_many", revised)
    js = json.loads(builder.generate(svc, svc.local_user, key, "json")[1])
    assert js["summary"]["chart_data_matches_signal_snapshot"] is False


def test_chart_annotations_match_computed_candles(cfg):
    from src.analysis.context import analyze_tf
    from tests.conftest import make_ohlc, zigzag
    df = make_ohlc(zigzag([100, 120, 105, 130, 112, 140, 118], seg=12), spread=0.6)
    t = analyze_tf(df, "1D", cfg)
    png, man = render_chart(t, 80, {"stop": 110.0, "target": 150.0, "signal_time": t.time()})
    assert png[:4] == b"\x89PNG"
    swings = [m for m in man if m["kind"].startswith("swing_")]
    assert swings
    for m in swings:
        bar = df.loc[[str(i)[:16] == m["time"] for i in df.index]].iloc[0]
        assert m["price"] in (pytest.approx(bar.High), pytest.approx(bar.Low))
        assert m["confirmed_time"] > m["time"]                     # marker shown only for confirmed swings
    for m in [m for m in man if m["kind"] in ("BOS", "CHoCH")]:
        bar = df.loc[[str(i)[:16] == m["time"] for i in df.index]].iloc[0]
        assert (bar.Close - m["price"]) * (1 if m["direction"] == "bullish" else -1) > 0   # close beyond level on that candle
    assert {"stop", "target", "signal"} <= {m["kind"] for m in man}
    assert [m for m in man if m["kind"] == "signal"][0]["time"] == t.time()


def test_invalid_format_and_unknown_signal(svc_sig):
    svc, key = svc_sig
    with pytest.raises(ValueError):
        builder.generate(svc, svc.local_user, key, "exe")
    with pytest.raises(KeyError):
        builder.generate(svc, svc.local_user, "nope", "json")
    assert builder.safe_name("AMW", "../../etc", "1D", ext="pdf") == "AMW_..-..-etc_1D.pdf"


def test_each_report_generation_has_its_own_file(svc_sig):
    """A-009: generating the same report twice must not overwrite the first file."""
    svc, key = svc_sig
    n1, d1, m1 = builder.generate(svc, svc.local_user, key, "json")
    n2, d2, m2 = builder.generate(svc, svc.local_user, key, "json")
    assert n1 != n2
    assert builder.read_report(svc, svc.local_user, m1["report_id"])[0] == n1
    assert builder.read_report(svc, svc.local_user, m2["report_id"])[0] == n2
