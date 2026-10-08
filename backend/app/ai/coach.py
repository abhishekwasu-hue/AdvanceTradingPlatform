"""Phase AV: the trade coach - what a mentor sees when they read your journal.

`review()` takes the trader's closed trades (PAPER, LIVE or both) and returns the numbers a
professional checks first (win rate, average win and loss in rupees and in R, expectancy, profit
factor, drawdown, longest losing streak), the breakdowns that show where the money is made and lost
(strategy, hour of the day, weekday) and - the part a beginner needs most - behaviour flags, each
with the evidence and one concrete fix:

* revenge trading - a new entry within REVENGE_MINUTES of a losing exit the same day;
* overtrading - days over the trade limit, and more trades on losing days than on winning ones;
* daily loss limit breached;
* losses far beyond the planned stop (stop moved, gaps, slippage);
* small winners against big losers (cutting winners, letting losers run);
* holding losers longer than winners;
* a losing hour of the day, a losing strategy, a long losing streak;
* a sample too small to judge.

Pure and deterministic (no database, no AI): the routes load the trades; the copilot can hand the
result to an AI provider to narrate. Education, never advice on a particular security.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean, median
from typing import Dict, Iterable, List, Optional

from app.ai.interview import tr
from app.core.models import RiskConfig
from app.market_data.calendar import IST

REVENGE_MINUTES = 15
BIG_LOSS_R = 1.5
MIN_SAMPLE = 20
WEEKDAYS_EN = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAYS_MR = ("सोम", "मंगळ", "बुध", "गुरु", "शुक्र", "शनि", "रवि")
SEVERITY_POINTS = {"high": 15, "medium": 8, "low": 3, "good": 0}


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _get(t, name, default=None):
    return t.get(name, default) if isinstance(t, dict) else getattr(t, name, default)


def _row(t) -> Optional[dict]:
    entry, exit_ = _utc(_get(t, "entry_time")), _utc(_get(t, "exit_time"))
    pnl = _get(t, "pnl")
    if entry is None or exit_ is None or pnl is None:
        return None
    entry_price, stop, qty = _get(t, "entry_price"), _get(t, "stop_loss"), _get(t, "quantity")
    risk = abs(float(entry_price) - float(stop)) * abs(float(qty)) if None not in (entry_price, stop, qty) else 0.0
    return {"id": _get(t, "id"), "symbol": _get(t, "symbol"), "strategy": _get(t, "strategy_id") or "-", "mode": _get(t, "mode"),
            "entry": entry, "exit": exit_, "pnl": float(pnl), "risk": risk, "r": float(pnl) / risk if risk > 0 else None,
            "exit_reason": _get(t, "exit_reason") or "", "hold_min": max(0.0, (exit_ - entry).total_seconds() / 60.0),
            "day": exit_.astimezone(IST).date(), "entry_ist": entry.astimezone(IST)}


def _money(lang: str, v: float) -> str:
    sign = "-" if v < 0 else ""
    return f"{sign}₹{abs(v):,.0f}"


def _group(rows: List[dict], key) -> List[dict]:
    groups: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    out = []
    for k, g in groups.items():
        wins = sum(1 for r in g if r["pnl"] > 0)
        out.append({"key": k, "trades": len(g), "wins": wins, "win_rate": round(100 * wins / len(g), 1), "net_pnl": round(sum(r["pnl"] for r in g), 2),
                    "avg_pnl": round(sum(r["pnl"] for r in g) / len(g), 2)})
    return out


def _flag(fid: str, severity: str, title: str, text: str, tip: str, evidence: Optional[List] = None) -> dict:
    return {"id": fid, "severity": severity, "title": title, "text": text, "tip": tip, "evidence": evidence or []}


def review(trades: Iterable, lang: str = "mr", risk: Optional[RiskConfig] = None, *, days: Optional[int] = None,
           mode: str = "ALL") -> dict:
    lang = "mr" if lang == "mr" else "en"
    risk = risk or RiskConfig()
    rows = sorted((r for r in (_row(t) for t in trades) if r is not None), key=lambda r: r["exit"])
    period = {"days": days, "mode": mode, "from": rows[0]["day"].isoformat() if rows else None, "to": rows[-1]["day"].isoformat() if rows else None}
    if not rows:
        return {"period": period, "stats": {"trades": 0}, "flags": [], "focus": [tr(lang,
                "No closed trades yet. Once a strategy has run in PAPER, the coach shows patterns in your own trades (rules followed or broken); decisions are yours.",
                "अजून एकही बंद trade नाही. एखादी strategy PAPER वर चालल्यावर coach तुमच्याच trades मधले patterns दाखवेल (नियम पाळले की मोडले); निर्णय तुमचे.")],
                "by_strategy": [], "by_hour": [], "by_weekday": [], "equity": [], "score": None, "grade": None}

    wins = [r for r in rows if r["pnl"] > 0]
    losses = [r for r in rows if r["pnl"] <= 0]
    gross_win, gross_loss = sum(r["pnl"] for r in wins), sum(r["pnl"] for r in losses)
    net = gross_win + gross_loss
    rs = [r["r"] for r in rows if r["r"] is not None]
    win_rs = [r["r"] for r in wins if r["r"] is not None]
    loss_rs = [r["r"] for r in losses if r["r"] is not None]
    equity, peak, max_dd, cum = [], 0.0, 0.0, 0.0
    for r in rows:
        cum += r["pnl"]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        equity.append(round(cum, 2))
    streak = longest = 0
    for r in rows:
        streak = streak + 1 if r["pnl"] <= 0 else 0
        longest = max(longest, streak)
    by_day: Dict = defaultdict(list)
    for r in rows:
        by_day[r["day"]].append(r)
    stats = {
        "trades": len(rows), "wins": len(wins), "losses": len(losses), "win_rate": round(100 * len(wins) / len(rows), 1),
        "net_pnl": round(net, 2), "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss": round(gross_loss / len(losses), 2) if losses else 0.0,
        "profit_factor": round(gross_win / abs(gross_loss), 2) if gross_loss < 0 else None,
        "expectancy": round(net / len(rows), 2), "expectancy_r": round(mean(rs), 2) if rs else None,
        "avg_win_r": round(mean(win_rs), 2) if win_rs else None, "avg_loss_r": round(mean(loss_rs), 2) if loss_rs else None,
        "max_drawdown": round(max_dd, 2), "longest_losing_streak": longest, "trading_days": len(by_day),
        "best_day": round(max(sum(r["pnl"] for r in d) for d in by_day.values()), 2),
        "worst_day": round(min(sum(r["pnl"] for r in d) for d in by_day.values()), 2),
    }

    flags: List[dict] = []
    # Revenge trading: a new entry soon after a losing exit, same day.
    revenge = []
    by_entry = sorted(rows, key=lambda r: r["entry"])
    for loss in losses:
        for r in by_entry:
            gap = (r["entry"] - loss["exit"]).total_seconds() / 60.0
            if r is not loss and 0 <= gap <= REVENGE_MINUTES and r["day"] == loss["day"]:
                revenge.append(r)
                break
    if revenge:
        rv_net = sum(r["pnl"] for r in revenge)
        sev = "high" if len(revenge) >= 3 and rv_net < 0 else "medium"
        flags.append(_flag("revenge", sev, tr(lang, "Revenge trading", "Revenge trading (बदला घेण्यासाठी trade)"),
                           tr(lang, f"{len(revenge)} trades were entered within {REVENGE_MINUTES} minutes of a loss; together they made {_money(lang, rv_net)}.",
                              f"{len(revenge)} trades तोट्यानंतर {REVENGE_MINUTES} मिनिटांच्या आत घेतले; त्यांचा एकूण निकाल {_money(lang, rv_net)}."),
                           tr(lang, f"After a stop-out, wait {risk.stop_cooldown_minutes or 30} minutes - the platform's cool-down can enforce it (Risk settings).",
                              f"Stop लागल्यानंतर {risk.stop_cooldown_minutes or 30} मिनिटे थांबा - Risk settings मधला cool-down हे आपोआप पाळतो."),
                           [r["id"] for r in revenge][:10]))
    # Overtrading.
    counts = {d: len(g) for d, g in by_day.items()}
    over = [d for d, n in counts.items() if n > risk.max_trades_per_day]
    loss_days = [d for d, g in by_day.items() if sum(r["pnl"] for r in g) < 0]
    win_days = [d for d, g in by_day.items() if sum(r["pnl"] for r in g) > 0]
    more_on_losing = loss_days and win_days and mean(counts[d] for d in loss_days) > 1.5 * mean(counts[d] for d in win_days)
    if over or more_on_losing:
        text = []
        if over:
            text.append(tr(lang, f"{len(over)} day(s) above your limit of {risk.max_trades_per_day} trades.",
                           f"{len(over)} दिवस तुमच्या {risk.max_trades_per_day} trades च्या मर्यादेपेक्षा जास्त trades."))
        if more_on_losing:
            text.append(tr(lang, f"On losing days you averaged {mean(counts[d] for d in loss_days):.1f} trades vs {mean(counts[d] for d in win_days):.1f} on winning days - chasing losses.",
                           f"तोट्याच्या दिवशी सरासरी {mean(counts[d] for d in loss_days):.1f} trades, फायद्याच्या दिवशी {mean(counts[d] for d in win_days):.1f} - तोटा भरून काढण्याची घाई."))
        flags.append(_flag("overtrading", "high" if over and more_on_losing else "medium", tr(lang, "Overtrading", "जास्त trades (overtrading)"), " ".join(text),
                           tr(lang, "Decide the day's maximum trades before the open and stop there; two losses in a row = done for the day.",
                              "बाजार उघडण्याआधीच दिवसाचे जास्तीत जास्त trades ठरवा आणि तिथेच थांबा; सलग दोन तोटे = त्या दिवसाचे trading बंद."),
                           [d.isoformat() for d in over][:10]))
    # Daily loss limit.
    limit = risk.capital * risk.max_daily_loss_pct / 100.0
    breached = [d for d, g in by_day.items() if -sum(r["pnl"] for r in g) > limit] if limit > 0 else []
    if breached:
        flags.append(_flag("loss_limit", "high", tr(lang, "Daily loss limit broken", "दैनिक तोटा मर्यादा मोडली"),
                           tr(lang, f"{len(breached)} day(s) lost more than your limit of {_money(lang, limit)} ({risk.max_daily_loss_pct:g}% of capital).",
                              f"{len(breached)} दिवस तोटा तुमच्या {_money(lang, limit)} ({risk.max_daily_loss_pct:g}% भांडवल) मर्यादेपेक्षा जास्त."),
                           tr(lang, "The limit protects next week's capital. Let the platform stop you: keep the daily loss guard on and never raise it after a bad day.",
                              "ही मर्यादा पुढच्या आठवड्याचे भांडवल वाचवते. Platform ला थांबवू द्या: daily loss guard चालू ठेवा आणि वाईट दिवसानंतर ती कधीही वाढवू नका."),
                           [d.isoformat() for d in breached][:10]))
    # Losses far beyond the stop.
    big = [r for r in losses if r["r"] is not None and r["r"] <= -BIG_LOSS_R]
    if big:
        flags.append(_flag("stop_discipline", "high" if len(big) >= 3 else "medium", tr(lang, "Losses bigger than the stop", "Stop पेक्षा मोठे तोटे"),
                           tr(lang, f"{len(big)} losses were {BIG_LOSS_R}R or worse (worst {min(r['r'] for r in big):.1f}R) - the stop was moved, gapped or not placed.",
                              f"{len(big)} तोटे {BIG_LOSS_R}R किंवा त्याहून मोठे (सर्वात मोठा {min(r['r'] for r in big):.1f}R) - stop हलवला, gap आला किंवा stop ठेवलाच नाही."),
                           tr(lang, "Never widen a stop. Keep the broker-side stop order (the platform places it in LIVE) and size smaller before results or events.",
                              "Stop कधीही लांब करू नका. Broker कडचा stop order ठेवा (LIVE मध्ये platform तो लावतो) आणि results/events आधी size कमी करा."),
                           [r["id"] for r in big][:10]))
    # Small winners vs big losers.
    if win_rs and loss_rs and len(win_rs) >= 5 and len(loss_rs) >= 5 and mean(win_rs) < 0.8 * abs(mean(loss_rs)):
        flags.append(_flag("payoff", "medium", tr(lang, "Small winners, big losers", "छोटे फायदे, मोठे तोटे"),
                           tr(lang, f"Average win {mean(win_rs):.2f}R against average loss {mean(loss_rs):.2f}R.",
                              f"सरासरी फायदा {mean(win_rs):.2f}R, सरासरी तोटा {mean(loss_rs):.2f}R."),
                           tr(lang, "Let winners reach target 1 (at least 1.5R); use the trailing stop or break-even rule instead of exiting by hand.",
                              "फायद्याचे trades किमान target 1 (1.5R) पर्यंत जाऊ द्या; हाताने बाहेर पडण्याऐवजी trailing stop किंवा break-even नियम वापरा.")))
    # Holding losers longer than winners.
    if len(wins) >= 5 and len(losses) >= 5:
        w_hold, l_hold = median(r["hold_min"] for r in wins), median(r["hold_min"] for r in losses)
        if w_hold > 0 and l_hold > 2 * w_hold:
            flags.append(_flag("holding", "medium", tr(lang, "Holding losers, cutting winners", "तोट्याचे trades धरून ठेवणे"),
                               tr(lang, f"Losing trades are held {l_hold:.0f} min (median) vs {w_hold:.0f} min for winners - hoping instead of exiting.",
                                  f"तोट्याचे trades सरासरी {l_hold:.0f} मिनिटे, फायद्याचे {w_hold:.0f} मिनिटे धरले - बाहेर पडण्याऐवजी आशा."),
                               tr(lang, "The stop decides the exit, not hope. A time exit (Exit rules) closes a trade that is going nowhere.",
                                  "Exit stop ठरवतो, आशा नाही. Exit rules मधला time exit न हलणारा trade बंद करतो.")))
    # Hour of day.
    by_hour = sorted(_group(rows, lambda r: f"{r['entry_ist'].hour:02d}:00"), key=lambda g: g["key"])
    bad_hours = [g for g in by_hour if g["trades"] >= 3 and g["net_pnl"] < 0]
    if bad_hours:
        worst = min(bad_hours, key=lambda g: g["net_pnl"])
        flags.append(_flag("hour", "low", tr(lang, f"Your worst hour: {worst['key']}", f"तुमचा सर्वात वाईट तास: {worst['key']}"),
                           tr(lang, f"{worst['trades']} trades entered in the {worst['key']} hour made {_money(lang, worst['net_pnl'])} ({worst['win_rate']}% wins).",
                              f"{worst['key']} च्या तासात घेतलेल्या {worst['trades']} trades चा निकाल {_money(lang, worst['net_pnl'])} ({worst['win_rate']}% फायद्याचे)."),
                           tr(lang, "Skip that hour for two weeks and compare; 09:15-09:30 is often noise - the opening-range strategies wait for it on purpose.",
                              "दोन आठवडे तो तास टाळून पाहा; 09:15-09:30 बहुतेक गोंधळाचा असतो - opening-range strategies मुद्दाम त्याची वाट पाहतात."),
                           [worst["key"]]))
    # Strategy.
    by_strategy = sorted(_group(rows, lambda r: r["strategy"]), key=lambda g: g["net_pnl"], reverse=True)
    losers = [g for g in by_strategy if g["trades"] >= 5 and g["net_pnl"] < 0]
    if losers:
        worst = min(losers, key=lambda g: g["net_pnl"])
        flags.append(_flag("strategy", "medium", tr(lang, f"Losing strategy: {worst['key']}", f"तोट्याची strategy: {worst['key']}"),
                           tr(lang, f"{worst['trades']} trades, {worst['win_rate']}% wins, {_money(lang, worst['net_pnl'])}.",
                              f"{worst['trades']} trades, {worst['win_rate']}% फायद्याचे, {_money(lang, worst['net_pnl'])}."),
                           tr(lang, "Pause it and check its regime filter - a strategy run in the wrong kind of market (trend rules in a sideways one, reversion in a trend) loses by design. Re-test it on the chart first.",
                              "ती थांबवा आणि तिचा regime filter तपासा - चुकीच्या प्रकारच्या market मध्ये (sideways मध्ये trend नियम, trend मध्ये reversion) strategy तोटाच करते. आधी chart वर पुन्हा तपासा."),
                           [worst["key"]]))
    if longest > risk.max_consecutive_losses:
        flags.append(_flag("streak", "low", tr(lang, "Long losing streak", "सलग तोट्यांची मालिका"),
                           tr(lang, f"{longest} losses in a row, above your guard of {risk.max_consecutive_losses}.",
                              f"सलग {longest} तोटे, तुमच्या {risk.max_consecutive_losses} च्या मर्यादेपेक्षा जास्त."),
                           tr(lang, "Streaks happen even to good systems - which is why the size per trade stays small. Check the consecutive-loss guard is on.",
                              "चांगल्या system मध्येही अशा मालिका येतात - म्हणूनच प्रत्येक trade चा size लहान ठेवतात. Consecutive-loss guard चालू आहे का ते पाहा.")))
    # Good habits deserve saying too.
    if stats["profit_factor"] and stats["profit_factor"] >= 1.3 and not breached and not big:
        flags.append(_flag("good_discipline", "good", tr(lang, "Good: losses stay inside the plan", "चांगले: तोटे नियोजनाच्या आत"),
                           tr(lang, f"Profit factor {stats['profit_factor']}, no day over the loss limit, no loss beyond {BIG_LOSS_R}R.",
                              f"Profit factor {stats['profit_factor']}, कोणताही दिवस मर्यादेबाहेर नाही, {BIG_LOSS_R}R पेक्षा मोठा तोटा नाही."),
                           tr(lang, "Keep the size the same while the sample grows; scale up only after 50+ trades like these.",
                              "Trades वाढेपर्यंत size तसाच ठेवा; असे 50+ trades झाल्यावरच size वाढवा.")))
    if len(rows) < MIN_SAMPLE:
        flags.append(_flag("sample", "low", tr(lang, "Too few trades to judge", "निर्णयासाठी trades कमी"),
                           tr(lang, f"Only {len(rows)} closed trades; any edge (or flaw) is not reliable before about {MIN_SAMPLE}-30.",
                              f"फक्त {len(rows)} बंद trades; सुमारे {MIN_SAMPLE}-30 trades होईपर्यंत निष्कर्ष भरवशाचे नाहीत."),
                           tr(lang, "Keep paper trading the same rules; do not change three things at once.",
                              "त्याच नियमांनी PAPER trading चालू ठेवा; एकाच वेळी तीन गोष्टी बदलू नका.")))

    order = {"high": 0, "medium": 1, "low": 2, "good": 3}
    flags.sort(key=lambda f: order[f["severity"]])
    score = max(0, 100 - sum(SEVERITY_POINTS[f["severity"]] for f in flags))
    if stats["expectancy"] < 0:
        score = max(0, score - 10)
    grade = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 50 else "D"
    focus = [f["tip"] for f in flags if f["severity"] in ("high", "medium")][:3] or [f["tip"] for f in flags][:1]
    by_weekday = [{**g, "label": tr(lang, WEEKDAYS_EN[int(g["key"])], WEEKDAYS_MR[int(g["key"])])}
                  for g in sorted(_group(rows, lambda r: str(r["entry_ist"].weekday())), key=lambda g: g["key"])]
    return {"period": period, "stats": stats, "flags": flags, "focus": focus, "score": score, "grade": grade,
            "by_strategy": by_strategy, "by_hour": by_hour, "by_weekday": by_weekday, "equity": equity[-200:]}


def summary_lines(lang: str, result: dict) -> List[str]:
    """A few sentences for the copilot chat and the AI context."""
    s = result.get("stats") or {}
    if not s.get("trades"):
        return list(result.get("focus") or [])
    lines = [tr(lang, f"{s['trades']} closed trades: {s['win_rate']}% wins, net {_money(lang, s['net_pnl'])}, expectancy {_money(lang, s['expectancy'])} per trade"
                      + (f" ({s['expectancy_r']}R)" if s.get("expectancy_r") is not None else "") + f", grade {result.get('grade')}.",
                f"{s['trades']} बंद trades: {s['win_rate']}% फायद्याचे, निव्वळ {_money(lang, s['net_pnl'])}, प्रति trade अपेक्षित {_money(lang, s['expectancy'])}"
                + (f" ({s['expectancy_r']}R)" if s.get("expectancy_r") is not None else "") + f", श्रेणी {result.get('grade')}.")]
    for f in result.get("flags", [])[:3]:
        if f["severity"] != "good":
            lines.append(f"{f['title']}: {f['text']}")
    if result.get("focus"):
        lines.append(tr(lang, "Pattern in your trades: ", "तुमच्या trades मधला pattern: ") + result["focus"][0])
    return lines


__all__ = ["review", "summary_lines"]
