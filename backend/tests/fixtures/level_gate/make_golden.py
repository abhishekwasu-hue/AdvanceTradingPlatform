"""Regenerates golden.json.gz from the Trade repo's OWN level-gate code (run by hand; the tests only read the file).

    git -C /path/to/trade archive <rev> | tar -x -C /tmp/trade_export        # a read-only export, never the repo itself
    python make_golden.py /tmp/trade_export <the full 40-character SHA of rev> golden.json.gz
    # The golden file is grandfathered in scripts/check_public_repo.py (Trade output, open question PR-2): it may not
    # change here; a Trade re-sync goes to the private fixtures store (docs/design/TRADE_TAKEOVER.md section 3).

Inputs are synthetic and seeded (no market data): multi-day 5-minute IST sessions with trends, pullbacks, impulsive
drives and volume (some series without volume), candidate areas around the session's own swing extremes, Daily states
for every trend case. Recorded, from Trade's functions:
* `bots_common.level_reaction`: `context_from_state`, `side_allowed`, `evaluate` (several settings), `describe`,
  `median_ranges`, `session_start_index`, `closed_bars`, `safe_evaluate`;
* `decision3.method`: `candle_read`, `contraction`, `power_shift`, `zone_study`, `zone_pattern`, `reversal_form`, `commitment`;
* `elliott.breaks`: `time_accepted`, `first_real_break` (with time acceptance), `break_from`;
* prefix replays (the live cycle: the session's closed bars up to every bar m) for the up / down / range contexts with the
  default and min R:R 1 settings - the immediate entry, `break_pending` and the no-look-ahead invariant;
* edge cases: volume partly zero / NaN, an overnight gap through the far edge at the session start, a one-session series
  (median range not yet finite), `describe` of the ERROR state, `commitment` / `reversal_form` at t = 0, `break_from` with
  `break_no_reclaim_bars` = 3 (time acceptance can only complete when the window is at least 3 bars);
* the defaults of the three settings modules for the keys ATP ports.

Trade's few non-English texts are translated with TRANSLATE below (the only place they appear); generation fails if any
non-ASCII letter outside the known symbols is left, so a new Trade message is noticed, not copied.
"""
import gzip
import json
import math
import re
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd

if len(sys.argv) != 4:
    raise SystemExit(__doc__)
TRADE_DIR, TRADE_REV, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
if not re.fullmatch(r"[0-9a-f]{40}", TRADE_REV):
    raise SystemExit("give the full 40-character Trade commit SHA (git -C <trade> rev-parse <rev>), not a short one")
sys.path.insert(0, TRADE_DIR)
from bots_common import level_reaction as LR  # noqa: E402
from decision3 import method as M  # noqa: E402
from decision3 import settings as S3  # noqa: E402
from elliott import breaks as BR  # noqa: E402
from elliott import settings as ES  # noqa: E402

# The reference implementation (Trade) writes these reasons in Marathi; ATP is English only, so they are mapped to English.
# The Marathi side is written with \u escapes (no Devanagari in ATP source - scripts/check_english_only.py).
TRANSLATE = [
    ("entry \u0935\u0947\u0933-\u0916\u093f\u0921\u0915\u0940\u092c\u093e\u0939\u0947\u0930", "outside the entry time window"),
    ("doji level \u0935\u0930 — \u092a\u0941\u0922\u091a\u094d\u092f\u093e candle \u091a\u0940 \u0935\u093e\u091f", "doji at the level - waiting for the next candle"),
    ("inside bar — \u092a\u0941\u0922\u091a\u094d\u092f\u093e candle \u091a\u0940 \u0935\u093e\u091f", "inside bar - waiting for the next candle"),
    ("candle \u0905\u091c\u0942\u0928 pullback-\u0926\u093f\u0936\u0947\u091a\u0940 — \u0935\u093e\u091f", "candle still in the pullback direction - waiting"),
    ("commitment candle \u0928\u093e\u0939\u0940 (reversal form \u0928\u093e\u0939\u0940)", "no commitment candle (no reversal form)"),
    (" (close level \u092a\u0932\u0940\u0915\u0921\u0947 \u091a\u0941\u0915\u0940\u091a\u094d\u092f\u093e \u092c\u093e\u091c\u0942\u0932\u093e)", " (close on the wrong side of the level)"),
    ("commitment candle dominant \u0928\u093e\u0939\u0940", "commitment candle not dominant"),
    ("× window \u0938\u0930\u093e\u0938\u0930\u0940", "x the window average"),
    ("commitment candle \u0928\u093e\u0939\u0940", "no commitment candle"),
    (": protected origin close \u0928\u0947 \u0924\u0941\u091f\u0932\u093e)", ": the protected origin was broken by a close)"),
    (": origin \u0927\u094b\u0915\u094d\u092f\u093e\u0924 — Q47)", ": origin at risk - Q47)"),
    (": impulse origin \u0924\u0941\u091f\u0932\u093e, parent correction \u091a\u093e\u0932\u0942)", ": impulse origin broken, a parent correction is running)"),
    (" (\u0926\u094b\u0928\u094d\u0939\u0940 \u0915\u0921\u093e)", " (both edges)"),
    ("\u0906\u091c\u091a\u0947 \u092c\u0902\u0926 candles \u0928\u093e\u0939\u0940\u0924", "no closed candles today"),
    (" — next candle \u091a\u0940 \u0935\u093e\u091f", " - waiting for the next candle"),
]
ALLOWED_NON_ASCII = set("·×–")


def tr(text):
    for a, b in TRANSLATE:
        text = text.replace(a, b)
    bad = {ch for ch in text if ord(ch) > 127 and ch not in ALLOWED_NON_ASCII}
    if bad:
        raise SystemExit(f"untranslated Trade text: {text!r}")
    return text


def norm(x):
    """JSON-safe, deterministic: numpy -> Python, tuples -> lists, sets -> sorted lists, NaN / inf -> None."""
    if isinstance(x, dict):
        return {str(k): norm(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [norm(v) for v in x]
    if isinstance(x, np.ndarray):
        return [norm(v) for v in x.tolist()]
    if isinstance(x, (set, frozenset)):
        return sorted(norm(v) for v in x)
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        v = float(x)
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(x, str):
        return tr(x)
    if isinstance(x, pd.Timestamp):
        return str(x)
    return x


# --- inputs ------------------------------------------------------------------------------------------------------------

def session_bars(seed, days=3, per_day=75, volume=True, start="2026-09-21"):
    """5-minute IST bars (timestamp = bar start, naive IST), a random walk with regime drifts, pullbacks into old
    extremes and occasional wide drives; volume follows the body size (with noise)."""
    rng = np.random.default_rng(seed)
    rows = []
    price = 22000.0 + 500 * rng.standard_normal()
    for day in pd.bdate_range(start, periods=days):
        drift = rng.choice([-1.0, 1.0]) * rng.uniform(0.2, 1.2)
        for i in range(per_day):
            ts = day + pd.Timedelta(hours=9, minutes=15 + 5 * i)
            if i % 20 == 0:
                drift = -drift if rng.random() < 0.6 else drift
            shock = rng.standard_normal() * 9.0 + drift * 3.0
            if rng.random() < 0.06:
                shock *= rng.uniform(2.5, 4.0)                            # a wide drive
            o = price + rng.standard_normal() * 2.0
            c = o + shock
            h = max(o, c) + abs(rng.standard_normal()) * 5.0
            lo = min(o, c) - abs(rng.standard_normal()) * 5.0
            v = (1000 + 80 * abs(c - o)) * rng.uniform(0.5, 1.6)
            rows.append({"timestamp": ts, "open": round(o, 2), "high": round(h, 2), "low": round(lo, 2), "close": round(c, 2),
                         "volume": round(v, 0)})
            price = c
    df = pd.DataFrame(rows)
    return df if volume else df.drop(columns=["volume"])


def holes_bars(seed):
    """Volume partly zero and NaN (a feed gap)."""
    df = session_bars(seed)
    rng = np.random.default_rng(seed)
    vol = df["volume"].to_numpy(float)
    vol[rng.choice(len(vol), 40, replace=False)] = 0.0
    vol[rng.choice(len(vol), 25, replace=False)] = np.nan
    return df.assign(volume=vol), []


def gap_bars(seed):
    """An overnight gap through a demand area's far edge at the session start (the arrival candle opens below it)."""
    base = session_bars(seed, days=2)
    rng = np.random.default_rng(seed)
    prev = float(base["close"].iloc[-1])
    mr = float((base["high"] - base["low"]).median())
    lo, hi = round(prev - 2.0 * mr, 2), round(prev - 1.0 * mr, 2)
    day = pd.Timestamp(base["timestamp"].iloc[-1]).normalize() + pd.Timedelta(days=1)
    rows, price = [], lo - 0.5 * mr
    first = {"timestamp": day + pd.Timedelta(hours=9, minutes=15), "open": round(lo - 0.6 * mr, 2), "high": round((lo + hi) / 2, 2),
             "low": round(lo - mr, 2), "close": round(price, 2), "volume": 5000.0}
    rows.append(first)
    for i in range(1, 75):
        o = price + rng.standard_normal() * 2.0
        c = o + rng.standard_normal() * 9.0
        rows.append({"timestamp": day + pd.Timedelta(hours=9, minutes=15 + 5 * i), "open": round(o, 2),
                     "high": round(max(o, c) + abs(rng.standard_normal()) * 5, 2), "low": round(min(o, c) - abs(rng.standard_normal()) * 5, 2),
                     "close": round(c, 2), "volume": round((1000 + 80 * abs(c - o)) * rng.uniform(0.5, 1.6), 0)})
        price = c
    gap_area = (lo, hi)
    return pd.concat([base, pd.DataFrame(rows)], ignore_index=True), [gap_area, (round(lo + 0.3 * mr, 2), round(hi + 0.3 * mr, 2))]


def candidate_areas(df, start, rng, k=10):
    """Areas around swing extremes BEFORE today's session and inside today's range: demand under lows, supply over highs."""
    hist = df.iloc[:start]
    today = df.iloc[start:]
    lo_t, hi_t = float(today["low"].min()), float(today["high"].max())
    ref = hist if len(hist) else today                                   # a one-session series has no history
    mr = float((ref["high"] - ref["low"]).median())
    out = []
    picks = rng.choice(len(today), size=min(k, len(today)), replace=False)
    for p in sorted(int(x) for x in picks):
        bar = today.iloc[p]
        base = float(bar["low"]) if rng.random() < 0.5 else float(bar["high"])
        width = mr * rng.uniform(0.2, 0.8)
        out.append((round(base - width / 2, 2), round(base + width / 2, 2)))
    out.append((round(lo_t - 3 * mr, 2), round(lo_t - 2.5 * mr, 2)))      # far below today
    out.append((round(hi_t + 2.5 * mr, 2), round(hi_t + 3 * mr, 2)))      # far above today
    return out


def state(trend, band=None, phase=None, counter=None, protected=None, origin_risk=False, mature=False):
    return SimpleNamespace(trend=trend, band=band, phase=phase, counter=counter,
                           protected=None if protected is None else SimpleNamespace(price=protected),
                           origin_risk=origin_risk, mature=mature)


def states_for(df):
    mid = float(df["close"].iloc[-1])
    return {
        "up": state("UP", phase="impulse", protected=mid - 400.1234),
        "down": state("DOWN", phase="impulse", protected=mid + 400.5678),
        "range": state("RANGE", band=(mid - 120.0, mid + 120.0)),
        "neutral": state("NEUTRAL"),
        "unknown": state("UNKNOWN"),
        "up_origin_broken": state("UP", phase="origin_broken"),
        "down_origin_risk": state("DOWN", origin_risk=True),
        "up_parent_correction": state("UP", counter="correction"),
        "range_no_band": state("RANGE"),
    }


SETTINGS = {"default": {}, "rr1": {"min_rr": 1.0}, "next_candle": {"min_rr": 1.0, "wait_next_candle": True},
            "long_wait": {"min_rr": 1.5, "max_armed_bars": 14, "approach_min_wide": 3}}


def arrays(df):
    A = {k: df[k].to_numpy(float) for k in ("open", "high", "low", "close")}
    A["volume"] = df["volume"].to_numpy(float) if "volume" in df.columns else None
    return A


def main():
    golden = {"trade_revision": TRADE_REV, "source": "Trade bots_common/level_reaction.py, decision3/method.py, elliott/breaks.py",
              "settings": {"gate": norm(LR.DEFAULTS), "method": norm({k: S3.DEFAULTS[k] for k in METHOD_KEYS}),
                           "breaks": norm({k: ES.DEFAULTS[k] for k in BREAK_KEYS})},
              "series": [], "contexts": [], "side_allowed": [], "evaluate": [], "prefix": [], "methods": [], "breaks": [], "misc": {}}
    es = ES.DEFAULTS
    s3 = S3.DEFAULTS
    series_specs = [(11, True), (12, True), (13, True), (14, False), (15, True), (16, True), (17, False), (18, True),
                    (19, True), (20, True), (21, "holes"), (22, "gap"), (23, "short")]
    for sid, (seed, vol) in enumerate(series_specs):
        extra = []
        if vol == "holes":
            df, extra = holes_bars(seed)
        elif vol == "gap":
            df, extra = gap_bars(seed)
        elif vol == "short":
            df = session_bars(seed, days=1)                           # one session: the median range is NaN at first
        else:
            df = session_bars(seed, volume=vol)
        start = LR.session_start_index(df)
        golden["series"].append({"seed": seed, "volume": vol, "session_start": start,
                                 "bars": {k: [str(x) for x in df["timestamp"]] if k == "timestamp" else [float(x) for x in df[k]]
                                          for k in df.columns}})
        rng = np.random.default_rng(seed + 1000)
        areas = extra + candidate_areas(df, start, rng)
        sts = states_for(df)
        ctxs = {name: LR.context_from_state(st, {}, asof="2026-09-22") for name, st in sts.items()}
        for name, st in sts.items():
            for over in ({}, {"allow_parent_correction": True}):
                golden["contexts"].append({"series": sid, "name": name, "state": norm(vars(st) | {"protected": None if st.protected is None
                                                                                     else st.protected.price}),
                                           "settings": over, "ctx": norm(LR.context_from_state(st, over, asof="2026-09-22"))})
        for name in ("up", "down", "range", "neutral"):
            for area in areas[:4]:
                for side in (LR.BULL, LR.BEAR):
                    golden["side_allowed"].append({"series": sid, "ctx": name, "side": side, "area": list(area),
                                                   "out": bool(LR.side_allowed(ctxs[name], side, area))})
        for ai, area in enumerate(areas):
            others = [a for j, a in enumerate(areas) if j != ai]
            for cname in ("up", "down", "range", "neutral", "up_parent_correction"):
                for sname, over in SETTINGS.items():
                    if cname in ("neutral", "up_parent_correction") and sname != "default":
                        continue
                    r = LR.evaluate(df, area, ctxs[cname], others, start, over, s3, es)
                    golden["evaluate"].append({"series": sid, "area": list(area), "others": [list(a) for a in others], "ctx": cname,
                                               "ctx_value": norm(ctxs[cname]), "settings": over, "out": norm(r),
                                               "describe": tr(LR.describe(r))})
            for cname in ("up", "down"):                                  # no other area: "no target area"
                r = LR.evaluate(df, area, ctxs[cname], [], start, {"min_rr": 1.0}, s3, es)
                golden["evaluate"].append({"series": sid, "area": list(area), "others": [], "ctx": cname, "ctx_value": norm(ctxs[cname]),
                                           "settings": {"min_rr": 1.0}, "out": norm(r), "describe": tr(LR.describe(r))})
        # Prefix replays (the live cycle: today's closed bars up to m), for every bar of the session: the immediate entry,
        # break_pending and the no-look-ahead invariant "entry at m <=> a CONFIRMED event at m in the full replay".
        n = len(df)
        for cname in ("up", "down", "range"):
            for sname, over in (("default", {}), ("rr1", {"min_rr": 1.0})):
                ranked = []                                              # one area per group: a confirmation first, else armed
                for ai, area in enumerate(areas):
                    others = [a for j, a in enumerate(areas) if j != ai]
                    full = LR.evaluate(df, area, ctxs[cname], others, start, over, s3, es)
                    kinds = {e[1] for e in full["events"]}
                    score = 3 if (extra and ai == 0) else (2 if LR.CONFIRMED in kinds else (1 if LR.ARMED in kinds else 0))
                    if score:
                        ranked.append((-score, ai, area, others))
                picked = [(area, others) for _, _, area, others in sorted(ranked)[:1]]
                for area, others in picked:
                    for m in range(start, n):
                        r = LR.evaluate(df.iloc[:m + 1], area, ctxs[cname], others, start, over, s3, es)
                        golden["prefix"].append({"series": sid, "area": list(area), "others": [list(a) for a in others], "ctx": cname,
                                                 "settings": over, "m": m, "out": norm(r)})
        A = arrays(df)
        mr = LR.median_ranges(A, 20)
        lv = {"lo": float(areas[0][0]), "hi": float(areas[0][1])}
        for t in range(start + 2, n, 3):
            a = max(start, t - int(rng.integers(2, 12)))
            for d in (M.UP, M.DOWN):
                ok, why, cm = M.commitment(A, t, d, lv, s3, None, win=a)
                ok2, why2, cm2 = M.commitment(A, t, d, lv, s3, list(df["timestamp"]))
                k = {"k_open_bar": a, "k_ext": float(df["low"].iloc[a] if d == 1 else df["high"].iloc[a]), "k_ext_bar": a,
                     "origin_bar": max(0, a - 6), "i_end_bar": max(0, a - 1)}
                golden["methods"].append({
                    "series": sid, "t": t, "a": a, "d": d, "lvl": lv, "k": k,
                    "candle_read": norm(M.candle_read(A, t, s3)),
                    "contraction": bool(M.contraction(A, t, d, None, s3, lv)), "contraction_k": bool(M.contraction(A, t, d, k, s3)),
                    "power_shift": norm(M.power_shift(A, t, d, None, s3, lv)), "power_shift_k": norm(M.power_shift(A, t, d, k, s3)),
                    "power_shift_strict3": norm(M.power_shift(A, t, d, None, dict(s3, power_a_rule="strict3"), lv)),
                    "zone_study": norm(M.zone_study(A, a, t, d, s3, mr=mr[t])), "zone_study_nomr": norm(M.zone_study(A, a, t, d, s3)),
                    "zone_pattern": norm(M.zone_pattern(A, a, t, d, s3, mr[t])),
                    "reversal_form": norm(M.reversal_form(A, t, d, s3)),
                    "reversal_form_tss": norm(M.reversal_form(A, t, d, s3, list(df["timestamp"]))),
                    "commitment": norm([ok, why, cm]), "commitment_tss": norm([ok2, why2, cm2]),
                    "commitment_extreme": norm(M.commitment(A, t, d, lv, dict(s3, commit_beyond="extreme"), None, win=a)),
                })
        frame = df.reset_index(drop=True)
        for j in range(max(20, start - 40), n, 2):
            c = float(df["close"].iloc[j])
            for side in ("below", "above"):
                for off in (0.0, 4.0, 15.0):
                    level = round(c + off, 2) if side == "below" else round(c - off, 2)
                    golden["breaks"].append({
                        "series": sid, "j": j, "level": level, "side": side,
                        "break_from": norm(BR.break_from(frame, j, level, side, es)),
                        "break_from_k3": norm(BR.break_from(frame, j, level, side, dict(es, break_no_reclaim_bars=3))),
                        "break_from_no_accept": norm(BR.break_from(frame, j, level, side, dict(es, break_accept_closes=0))),
                        "first_real_break": norm(BR.first_real_break(frame, max(0, j - 10), level, side, es, end=min(n - 1, j + 6))),
                        "time_accepted": bool(BR.time_accepted(frame["close"].to_numpy(float), j, level, side, 3, max(0, j - 10)))})
        golden["misc"].setdefault("median_ranges", []).append(norm(mr))
    # closed_bars / session_start / safe_evaluate / no bars
    df = session_bars(31, days=2)
    aware = df.assign(timestamp=pd.to_datetime(df["timestamp"]).dt.tz_localize("Asia/Kolkata").dt.tz_convert("UTC"))
    golden["misc"]["closed_bars"] = {
        "bars": {k: [str(x) for x in df["timestamp"]] if k == "timestamp" else [float(x) for x in df[k]] for k in df.columns},
        "now": "2026-09-22 10:02:00+05:30", "minutes": 5,
        "naive_rows": len(LR.closed_bars(df, 5, pd.Timestamp("2026-09-22 10:02:00"))),
        "aware_rows": len(LR.closed_bars(aware, 5, pd.Timestamp("2026-09-22 10:02:00+05:30"))),
        "aware_last": str(LR.closed_bars(aware, 5, pd.Timestamp("2026-09-22 10:02:00+05:30"))["timestamp"].iloc[-1]),
        "session_start_last": LR.session_start_index(df), "session_start_day1": LR.session_start_index(df, "2026-09-21"),
        "session_start_missing": LR.session_start_index(df, "2026-10-01")}
    A0 = arrays(df)
    golden["misc"]["t0"] = {"commitment": norm(M.commitment(A0, 0, M.UP, {"lo": 0.0, "hi": 1e9}, s3, None, win=0)),
                            "reversal_form": norm(M.reversal_form(A0, 0, M.DOWN, s3))}
    err = LR.safe_evaluate(df.drop(columns=["close"]), (1.0, 2.0), {"ok": True}, (), 0)
    golden["misc"]["describe_error"] = [tr(LR.describe(err)), tr(LR.describe(err, (100.0, 200.0)))]
    golden["misc"]["no_bars"] = norm(LR.evaluate(df.iloc[:0], (1.0, 2.0), {"ok": True, "sides": {LR.BULL}}, (), 0))
    golden["misc"]["safe_evaluate_error"] = norm(LR.safe_evaluate(df.drop(columns=["close"]), (1.0, 2.0), {"ok": True}, (), 0))
    counts = {}
    for case in golden["evaluate"]:
        counts[case["out"]["state"]] = counts.get(case["out"]["state"], 0) + 1
    golden["state_counts"] = counts
    golden["prefix_entries"] = sum(1 for c in golden["prefix"] if c["out"]["entry"])
    golden["prefix_break_pending"] = sum(1 for c in golden["prefix"] if c["out"].get("break_pending"))
    payload = json.dumps(golden, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with open(OUT, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as fh:   # same bytes every run
        fh.write(payload)
    print(json.dumps({"prefix": len(golden["prefix"]), "prefix_entries": golden["prefix_entries"],
                      "prefix_break_pending": golden["prefix_break_pending"], "evaluate": len(golden["evaluate"]), "states": counts, "methods": len(golden["methods"]),
                      "breaks": len(golden["breaks"])}))


METHOD_KEYS = ["power_shift_min", "power_a_rule", "commit_dom_mult", "study_min_bars", "pattern_eq_mr", "power_overlap", "commit_body",
               "commit_beyond", "pin_wick_min", "star_body_max", "tweezer_tol", "commit_close_frac", "entry_start", "entry_end",
               "candle_avg_n", "doji_body_max", "power_fallback_bars"]
BREAK_KEYS = ["median_range_n", "break_buffer_mr", "break_displacement_confirm", "break_close_loc", "strength_min",
              "break_no_reclaim_bars", "break_accept_closes"]

if __name__ == "__main__":
    main()
