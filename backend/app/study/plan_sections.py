"""M77 SX-6: sections 7-10 of a saved study - his trade plan, the pre-trade check card, scenarios and similar past setups
(owner order M77 PD-2.7 - PD-2.10; OPEN_QUESTIONS M77Q-5 / M77Q-6 defaults).

* Trade plan (7): the M75 W-6 plan panel is held in the page, so Save study sends the plan he has open as REFERENCES
  and his own inputs only - the instrument type, each leg's contract (expiry, strike, CE / PE / FUT), side and lots, the
  price he plans each leg at (his own input, as on the panel), the equity side / quantity / price, and the exits he set
  at entry. Every number of the plan is the server's: the contract and lot size from the instrument master
  (`fno_ticket.find_contract`), the payoff, max profit / loss, break-evens, Greeks and IV from the Options Builder's
  engine (`fno_basket._compute` -> `options_builder.routes._evaluate`, his own rate), the margin as ATP's ESTIMATE with
  the hedge (`fno_basket.hedge_margin`, labelled so: the broker's margin API is not used), measured from the study's own
  reference price (his live price, else the last close). His trigger plans on the underlying that were waiting at the
  study time (M75 W-8) are listed with their condition. No plan open -> "Not available: no trade plan was open".
* Scenarios (9): the plan's P&L at both edges of the next areas of the study (the nearest area above, the one price is
  inside, the nearest below - each with its own timeframe, never merged) and at each of his saved what-if scenarios
  (Settings `what_if`, M76 MP-6: his own spot move / IV change / days), today and at expiry, with the margin estimate.
* Pre-trade check (8): the check card the ticket showed (M76 MP-2) is signed by the server when it is returned
  (app/paper/pretrade_card.py); the study keeps a card only with a valid signature, of the same member, on this
  underlying, checked at or before the study time - with the lines he accepted by pressing Place and when (the server's
  own record, M76 MP-1 / MP-5). A card that does not verify is left out and said so.
* Similar past setups (10): AUD-T6 setup recall (app/setup_recall/service.py, unchanged) with the plan as the subject:
  the setup and side the order ticket records for these legs (the basket places hedge legs first, so its first leg's
  side), "not recorded" timeframe (a hand order records none), and - as the plan has no product - each holding shown on
  its own (intraday, carry forward). Only his own trades closed strictly before the study time.
Pure parts take the reads' results; the reads are self-scoped. Nothing here places, changes or blocks an order.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User

NO_PLAN = "no trade plan was open when the study was saved"
NO_LEGS = "the trade plan had no legs yet"
NO_EQUITY = "the plan's side, quantity or price was not set"
NOT_FNO = "this instrument has no option or futures contracts"
NO_PRICE = "no price to measure the plan from (no live price and no closed bar)"
NO_SCENARIO_PLAN = "no trade plan was open, so there are no scenarios"
NO_LEVELS = "the study has no next areas and you have no saved what-if scenarios"
NO_CARD = "no pre-trade check card was shown for this symbol before the study was saved"
CARD_UNVERIFIED = "the check card could not be confirmed as ATP's own, so it is left out"
NO_SETUP_PLAN = "no trade plan was open, so there is no setup to match"
EQUITY_SETUP = "an equity plan records no setup to match on yet"
CONTRACT_CHART = "the study is on a contract's own chart; a plan is measured on the underlying's chart"
UNDERLYING_EXCHANGES = ("NSE", "BSE", "NSE_INDEX", "BSE_INDEX")
FUTURES_ONLY = "a futures-only plan has no option model (IV, Greeks, value before expiry); its P&L at expiry is shown"
MARGIN_SOURCE = "ATP's estimate, not your broker's number (the broker's margin API is not used)"
EQUITY_MARGIN = "the full order value for delivery; for intraday only your broker can say"
MODEL_WORDS = "Model estimates (Black-Scholes, one volatility) for research; not a forecast."
CURVE_POINTS = 121
MAX_CARDS = 24
HOLDINGS = (("INTRADAY", "Intraday (MIS)"), ("SWING", "Carry forward (NRML)"))
RIGHT_WORDS = {"CE": "Call", "PE": "Put", "FUT": "Future"}
KIND_WORDS = {"OPTIONS": "Options", "FUTURE": "Future", "EQUITY": "Equity"}
EXIT_MEASURE_WORDS = {"POINTS": "points", "PCT": "%", "RUPEES": "rupees", "ATR": "ATR"}
COMBINED_WORDS = {"MTM_INR": "the whole position's P&L in rupees", "NET_PREMIUM": "the net premium", "SPOT": "the underlying's price"}


def _num(x: Any) -> float | None:
    from app.study.snapshot import _num as num
    return num(x)


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return None if value is None else str(value)


def _aware(when: datetime) -> datetime:
    return when if when.tzinfo else when.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------------------------------------------------
# His exits and trigger plans (his own words and numbers, as he set them)
# ---------------------------------------------------------------------------------------------------------------------
def exits_part(exits: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The exits he set at entry, as he set them (value + what it is measured in); a blank one is left out."""
    if not exits:
        return []
    out: list[dict[str, Any]] = []
    for key, name, measure in (("stop", "Exit if wrong", "stop_kind"), ("target", "Exit at gain", "target_kind"),
                               ("trail", "Trailing exit", "trail_kind")):
        value = _num(exits.get(key))
        if value is not None:
            out.append({"key": key, "name": name, "value": value, "measure": EXIT_MEASURE_WORDS.get(str(exits.get(measure)), None)})
    minutes = exits.get("time_minutes")
    if isinstance(minutes, int) and not isinstance(minutes, bool):
        out.append({"key": "time_minutes", "name": "Close after", "value": float(minutes), "measure": "minutes"})
    if isinstance(exits.get("time_at"), str) and exits["time_at"]:
        out.append({"key": "time_at", "name": "Close at a time", "value": None, "measure": None, "text": f"{exits['time_at']} IST"})
    if exits.get("combined") in COMBINED_WORDS:
        out.append({"key": "combined", "name": "Combined exit measured on", "value": None, "measure": None,
                    "text": COMBINED_WORDS[str(exits["combined"])]})
    return out


def trigger_part(view: Mapping[str, Any]) -> dict[str, Any]:
    """One of his waiting trigger plans (trigger_plans.view): its name, status and condition, as numbers."""
    c = view.get("condition") or {}
    return {"name": view.get("name"), "status": view.get("status_words") or view.get("status"), "level": _num(c.get("level")),
            "side": "above" if str(c.get("side")).upper() == "ABOVE" else "below",
            "by": "a closed candle" if str(c.get("mode")).upper() == "CLOSE" else "a touch",
            "timeframe_minutes": c.get("timeframe_minutes") if isinstance(c.get("timeframe_minutes"), int) else None,
            "window": f"{c['window_start']}-{c['window_end']} IST" if c.get("window_start") and c.get("window_end") else None,
            "valid_until": view.get("valid_until"), "armed_at": view.get("armed_at"), "orders": len(view.get("orders") or [])}


# ---------------------------------------------------------------------------------------------------------------------
# Trade plan + scenarios (pure: the resolved plan, the study's reference price and areas)
# ---------------------------------------------------------------------------------------------------------------------
def _thin(values: Sequence[Any] | None, every: int) -> list[float | None] | None:
    if values is None:
        return None
    return [_num(v) for i, v in enumerate(values) if i % every == 0 or i == len(values) - 1]


def _engine(builder_legs: list[dict[str, Any]], spot: float, when: datetime, rate: float | None) -> tuple[dict[str, Any] | None, str | None]:
    """The Options Builder's own numbers for the legs at `spot` (or the engine's own plain reason)."""
    from fastapi import HTTPException
    from pydantic import ValidationError

    from app.paper import fno_basket
    try:
        return fno_basket._compute([dict(leg) for leg in builder_legs], spot, when, rate), None
    except HTTPException as exc:
        return None, str(exc.detail)
    except (ValidationError, ValueError) as exc:
        return None, str(exc).splitlines()[0]


def _expiry_pnl(builder_legs: Sequence[Mapping[str, Any]], levels: Sequence[float]) -> list[float] | None:
    from app.options_builder import model
    from app.options_builder.payoff import compute_strategy_payoff_curve
    if len(model.expiries(builder_legs)) != 1:
        return None
    return [round(v, 2) for v in compute_strategy_payoff_curve(builder_legs, levels)]


def _today_pnl(engine_legs: Sequence[Mapping[str, Any]] | None, levels: Sequence[float], when: datetime, rate: float | None,
               days: float = 0.0, iv_shift: float = 0.0) -> list[float] | None:
    from app.core.config import RISK_FREE_RATE
    from app.options_builder import model
    if not engine_legs:
        return None
    legs = [{k: leg.get(k) for k in ("direction", "option_type", "strike", "premium", "lots", "lot_size", "expiry", "iv")} for leg in engine_legs]
    try:
        return [round(v, 2) for v in model.value_curve(legs, list(levels), as_of=when, days_forward=days, iv_shift=iv_shift,
                                                       r=RISK_FREE_RATE if rate is None else rate)]
    except ValueError:
        return None


def _equity_pnl(eq: Mapping[str, Any], levels: Sequence[float]) -> list[float]:
    from app.options_builder.payoff import compute_leg_payoff
    return [round(compute_leg_payoff(p, eq["side"], "FUT", 0.0, float(eq["price"]), float(eq["quantity"]), 1.0), 2) for p in levels]


def plan_parts(resolved: Mapping[str, Any] | None, ref: Mapping[str, Any], when: datetime, areas: Sequence[Mapping[str, Any]],
               what_if: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(trade plan section or {"reason"}, scenarios section or {"reason"}) - pure; the same inputs give the same parts."""
    if resolved is None:
        return {"reason": NO_PLAN}, {"reason": NO_SCENARIO_PLAN}
    if resolved.get("reason"):
        return {"reason": str(resolved["reason"])}, {"reason": f"{str(resolved['reason']).rstrip('.')}, so there are no scenarios"}
    when = _aware(when)
    spot = _num((ref or {}).get("value"))
    base = {"kind": resolved["kind"], "kind_words": KIND_WORDS.get(str(resolved["kind"]), str(resolved["kind"])),
            "underlying": resolved.get("underlying"), "exits": list(resolved.get("exits") or []),
            "triggers": list(resolved.get("triggers") or []), "triggers_reason": resolved.get("triggers_reason"),
            "spot": {"value": spot, "source": (ref or {}).get("source"), "as_of": (ref or {}).get("as_of")}}
    if resolved["kind"] == "EQUITY":
        eq = dict(resolved["equity"])
        value = round(float(eq["price"]) * float(eq["quantity"]), 2)
        plan = {**base, "equity": eq, "legs": [], "order_value": value,
                "margin": {"value": value, "source": "estimate", "basis": EQUITY_MARGIN},
                "max_profit": None, "max_loss": None, "breakevens": [float(eq["price"])], "payoff": None, "engine_reason": None}
        levels_parts = _scenario_levels(areas, what_if, spot)
        if not levels_parts:
            return plan, {"reason": NO_LEVELS}
        rows = []
        for lv in levels_parts:
            pnl = _equity_pnl(eq, [lv["price"]])[0]
            rows.append({**lv, "pnl_today": pnl, "pnl_expiry": None})
        return plan, {"rows": rows, "margin": plan["margin"], "today_words": "P&L at the price", "expiry_words": None,
                      "expiry_reason": "an equity position has no expiry", "spot": base["spot"]}
    from app.options_builder import model
    from app.paper import fno_basket
    builder_legs = [dict(leg) for leg in resolved["builder_legs"]]
    single = len(model.expiries(builder_legs)) == 1
    extremes = model.payoff_extremes(builder_legs) if single else None
    margin = fno_basket.hedge_margin(builder_legs)
    legs_out = [dict(leg) for leg in resolved["legs"]]
    plan = {**base, "legs": legs_out, "single_expiry": single,
            "net_premium": None, "max_profit": None, "max_loss": None, "unbounded_profit": None, "unbounded_loss": None,
            "breakevens": None, "greeks": None, "payoff": None, "engine_reason": None,
            "margin": {"value": _num(margin["with_hedge"].get("value")), "basis": margin["with_hedge"].get("basis"),
                       "without_hedge": _num((margin.get("without_hedge") or {}).get("value")), "benefit": _num(margin.get("benefit")),
                       "hedged": bool(margin.get("hedged")), "source": "estimate", "words": MARGIN_SOURCE},
            "rate": resolved.get("rate"), "rate_source": resolved.get("rate_source")}
    if extremes is not None:
        def finite(x: Any) -> float | None:
            v = _num(x)
            return None if v is None or math.isinf(v) else round(v, 2)
        plan.update({"max_profit": finite(extremes.get("max_profit")), "max_loss": finite(extremes.get("max_loss")),
                     "unbounded_profit": bool(extremes.get("unbounded_profit")), "unbounded_loss": bool(extremes.get("unbounded_loss")),
                     "breakevens": sorted({round(x, 2) for a, b in model.profitable_intervals(builder_legs) for x in (a, b)
                                           if 0 < x < math.inf})})
    from app.options_builder import metrics
    plan["net_premium"] = round(metrics.net_premium(builder_legs), 2) if any(leg["option_type"] != "FUT" for leg in builder_legs) else None
    engine: dict[str, Any] | None = None
    if spot is None:
        plan["engine_reason"] = NO_PRICE
    elif all(leg["option_type"] == "FUT" for leg in builder_legs):
        plan["engine_reason"] = FUTURES_ONLY
    else:
        engine, why = _engine(builder_legs, spot, when, resolved.get("rate"))
        plan["engine_reason"] = why
    if engine is not None:
        for row, leg in zip(legs_out, engine.get("legs") or [], strict=False):
            row["iv"] = _num(leg.get("iv"))
            row["iv_source"] = leg.get("iv_source")
            row["delta"] = _num((leg.get("greeks") or {}).get("delta"))
        plan["greeks"] = {k: _num(v) for k, v in ((engine.get("summary") or {}).get("greeks") or {}).items()
                          if k in ("delta", "gamma", "theta", "vega")}
        every = max(1, -(-len(engine.get("prices") or []) // CURVE_POINTS))
        plan["payoff"] = {"prices": _thin(engine.get("prices"), every), "expiry": _thin(engine.get("at_expiry") or engine.get("on_date"), every),
                          "today": _thin(engine.get("today"), every), "expiry_words": "at expiry" if engine.get("at_expiry") else "at the near expiry"}
        plan["disclaimer"] = MODEL_WORDS
    levels_parts = _scenario_levels(areas, what_if, spot)
    if not levels_parts:
        return plan, {"reason": NO_LEVELS}
    expiry = _expiry_pnl(builder_legs, [lv["price"] for lv in levels_parts])
    rows = []
    for i, lv in enumerate(levels_parts):
        shift = lv.get("what_if") or {}
        today = _today_pnl(engine.get("legs") if engine else None, [lv["price"]], when, resolved.get("rate"),
                           float(shift.get("days") or 0), float(shift.get("iv_change") or 0) / 100)
        rows.append({**lv, "pnl_today": today[0] if today else None, "pnl_expiry": expiry[i] if expiry else None})
    return plan, {"rows": rows, "margin": plan["margin"], "spot": base["spot"],
                  "today_words": "P&L now (the engine's model at the study time)" if engine else None,
                  "today_reason": None if engine else (plan["engine_reason"] or NO_PRICE),
                  "expiry_words": "P&L at expiry" if single else None,
                  "expiry_reason": None if single else "the legs expire on different dates"}


def _scenario_levels(areas: Sequence[Mapping[str, Any]], what_if: Sequence[Mapping[str, Any]], spot: float | None) -> list[dict[str, Any]]:
    """The prices the scenarios are read at: both edges of the next area above, the one price is inside and the next
    below (each keeps its own timeframe), then each of his saved what-if scenarios."""
    out: list[dict[str, Any]] = []
    picks: list[tuple[str, Mapping[str, Any]]] = []
    for side, words in (("above", "Next area above"), ("at", "Area price is inside"), ("below", "Next area below")):
        near = sorted((a for a in areas if a.get("side") == side), key=lambda a: (a.get("distance_points") or 0.0, str(a.get("label"))))
        if near:
            picks.append((words, near[0]))
    for words, a in picks:
        for edge, price in (("lower edge", a.get("low")), ("upper edge", a.get("high"))):
            value = _num(price)
            if value is not None and value > 0:
                out.append({"kind": "area", "name": f"{words} {a.get('label')} ({a.get('tf_words') or a.get('timeframe')}), {edge}",
                            "price": value, "area": a.get("label"), "role": a.get("role"), "what_if": None})
    for s in what_if:
        if spot is None:
            break
        move = _num(s.get("spot_move")) or 0.0
        value = round(spot + move, 2)
        if value <= 0:
            continue
        out.append({"kind": "what_if", "name": f"Your what-if: {s.get('name')}", "price": value, "area": None, "role": None,
                    "what_if": {"spot_move": _num(s.get("spot_move")), "iv_change": _num(s.get("iv_change")),
                                "days": s.get("days") if isinstance(s.get("days"), int) else None}})
    return out


# ---------------------------------------------------------------------------------------------------------------------
# The reads (the caller's own data only)
# ---------------------------------------------------------------------------------------------------------------------
async def resolve_plan(session: AsyncSession, user: User, exchange: str, symbol: str, plan: Mapping[str, Any] | None,
                       when: datetime) -> dict[str, Any] | None:
    """His open plan with every contract read from the instrument master and his own rate; None when no plan was open,
    {"reason"} when it cannot be read. Only on the underlying's own chart: a contract chart's prices are not the price
    the plan is measured from (as the workspace's what-if, M76 MP-6)."""
    if plan is None:
        return None
    if exchange.upper() not in UNDERLYING_EXCHANGES:
        return {"reason": CONTRACT_CHART}
    kind = str(plan.get("kind") or "")
    exits = exits_part(plan.get("exits"))
    triggers, triggers_reason = await _triggers(session, user, symbol, when)
    common = {"kind": kind, "exits": exits, "triggers": triggers, "triggers_reason": triggers_reason}
    if kind == "EQUITY":
        eq = plan.get("equity") or {}
        side, qty, price = eq.get("side"), eq.get("quantity"), _num(eq.get("price"))
        if side not in ("BUY", "SELL") or not isinstance(qty, int) or qty <= 0 or price is None or price <= 0:
            return {"reason": NO_EQUITY}
        return {**common, "underlying": symbol, "equity": {"side": side, "quantity": qty, "price": price}}
    legs = list(plan.get("legs") or [])
    if not legs:
        return {"reason": NO_LEGS}
    from app.option_chain import chain_view
    from app.paper import fno_basket
    try:
        underlying = chain_view.underlying_name(symbol)
    except ValueError:
        return {"reason": NOT_FNO}
    asks = [fno_basket.LegAsk(underlying=underlying, expiry=leg["expiry"], strike=None if leg["right"] == "FUT" else _num(leg.get("strike")),
                              right=leg["right"], side=leg["side"], lots=int(leg["lots"]), price=_num(leg.get("price"))) for leg in legs]
    contracts, errors = await fno_basket._contracts(session, asks)
    if errors:
        return {"reason": "; ".join(e.rstrip(".") for e in errors[:3])}
    missing = [fno_basket.label(a) for a in asks if a.price is None or a.price <= 0]
    if missing:
        return {"reason": f"no planned price for {', '.join(missing)}"}
    own = await chain_view.options_settings(session, user)
    rate = chain_view.rate_of(own)
    rate_source = "yours" if getattr(own, "risk_free_rate", None) is not None else "platform"
    builder = [fno_basket._builder_leg(a, c.lot_size, float(a.price or 0)) for a, c in zip(asks, contracts, strict=True) if c is not None]
    out_legs = [{"label": c.label, "side": a.side, "right": a.right, "right_words": RIGHT_WORDS.get(a.right, a.right),
                 "strike": a.strike, "expiry": a.expiry.isoformat(), "lots": a.lots, "lot_size": c.lot_size, "price": a.price,
                 "quantity": a.lots * c.lot_size} for a, c in zip(asks, contracts, strict=True) if c is not None]
    first = fno_basket.hedge_first(asks)[0][1]
    return {**common, "underlying": underlying, "legs": out_legs, "builder_legs": builder, "rate": rate, "rate_source": rate_source,
            "subject_side": "LONG" if first.side == "BUY" else "SHORT"}


async def _triggers(session: AsyncSession, user: User, symbol: str, when: datetime) -> tuple[list[dict[str, Any]], str | None]:
    from app.execution import trigger_plans as tp
    from app.option_chain import chain_view
    if not await tp.enabled(session, user.tenant_id):
        return [], "trigger plans are not available on this platform yet"
    try:
        name = chain_view.underlying_name(symbol)
    except ValueError:
        name = symbol.upper()
    rows = await tp.list_for_user(session, user, include_final=False)
    cut = _aware(when)
    found = [tp.view(r) for r in rows if str(r.underlying).upper() == name and r.armed_at is not None and _aware(r.armed_at) <= cut]
    if not found:
        return [], "no trigger plan of yours was waiting on this underlying"
    return [trigger_part(v) for v in found[:5]], None


async def what_if_read(session: AsyncSession, user: User) -> list[dict[str, Any]]:
    """His saved what-if scenarios (Settings `what_if`, M76 MP-6), in his order; none = an empty list."""
    from app.me import prefs
    try:
        value, _ = await prefs.read_one("what_if", user, session)
    except Exception:  # noqa: BLE001 - no saved scenarios to read: the scenarios use the next areas only
        return []
    return [dict(s) for s in (value or {}).get("scenarios") or [] if isinstance(s, Mapping) and s.get("name")]


def card_part(cards: Sequence[Mapping[str, Any]], accepted: Mapping[str, Mapping[str, str | None]]) -> dict[str, Any]:
    """The verified card(s) -> the section: per contract its findings (each marked accepted or not) and his accepted
    lines with the time Place was pressed. `accepted`: instrument -> {type: ISO time or None}. Pure."""
    from app.paper import pretrade_warnings as pw
    by: dict[str, dict[str, Any]] = {}
    for card in cards:
        inst = str(card["instrument"])
        part = by.setdefault(inst, {"instrument": inst, "contract": _contract_words(inst), "checked_at": card.get("checked_at"),
                                    "findings": [], "accepted": []})
        part["checked_at"] = max(str(part["checked_at"] or ""), str(card.get("checked_at") or "")) or None
        seen = {(f["id"], f["text"]) for f in part["findings"]}
        for f in card.get("findings") or []:
            key = (str(f.get("id")), str(f.get("text")))
            if key in seen:
                continue
            seen.add(key)
            part["findings"].append({"id": key[0], "name": pw.LABELS.get(key[0], key[0]), "severity": f.get("severity"), "text": key[1]})
    for inst, part in by.items():
        acc = accepted.get(inst) or {}
        for f in part["findings"]:
            f["accepted"] = f["id"] in acc
            f["accepted_at"] = acc.get(f["id"])
        part["accepted"] = [{"id": w, "name": pw.LABELS.get(w, w), "at": at} for w, at in sorted(acc.items())]
    return {"cards": [by[k] for k in sorted(by)]}


def _contract_words(instrument: str) -> str:
    parts = instrument.split("|")
    if len(parts) != 4:
        return instrument
    try:
        from app.paper.fno_ticket import contract_label
        return contract_label(parts[0], date.fromisoformat(parts[1]), float(parts[2]) if parts[2] else None, parts[3])
    except ValueError:
        return instrument


async def check_card_read(user: User, symbol: str, cards: Sequence[Mapping[str, Any]] | None, when: datetime) -> dict[str, Any]:
    """The check cards the page held for this symbol, kept only when the server's signature verifies (his own, this
    underlying, checked at or before the study time), with his accepted lines from the server's own record."""
    from app.option_chain import chain_view
    from app.paper import pretrade_card as pcard
    from app.paper import pretrade_warnings as pw
    if not cards:
        return {"reason": NO_CARD}
    try:
        name = chain_view.underlying_name(symbol)
    except ValueError:
        return {"reason": NO_CARD}
    cut = _aware(when)
    good = []
    for card in list(cards)[:MAX_CARDS]:
        if not pcard.verify(user, card):
            continue
        if str(card["instrument"]).split("|")[0] != name:
            continue
        try:
            at = datetime.fromisoformat(str(card["checked_at"]))
        except ValueError:
            continue
        if _aware(at) <= cut:
            good.append(card)
    if not good:
        return {"reason": CARD_UNVERIFIED}
    accepted = {inst: await pw.accepted_times(user, inst) for inst in sorted({str(c["instrument"]) for c in good})}
    return card_part(good, accepted)


async def similar_read(session: AsyncSession, user: User, resolved: Mapping[str, Any] | None, when: datetime) -> dict[str, Any]:
    """Setup recall (AUD-T6, unchanged) with the plan as the subject - each holding on its own (the plan has no product)."""
    from app.paper import fno_ticket
    from app.setup_recall import service as recall
    if resolved is None:
        return {"reason": NO_SETUP_PLAN}
    if resolved.get("reason"):
        return {"reason": f"{str(resolved['reason']).rstrip('.')}, so there is no setup to match"}
    if resolved.get("kind") == "EQUITY":
        return {"reason": EQUITY_SETUP}
    cut = _aware(when)
    setup, side = fno_ticket.STRATEGY_ID, str(resolved.get("subject_side") or "LONG")
    candidates = await recall._closed_before(session, user, setup, cut)
    timeframes = await recall._timeframes(session, user.tenant_id, [t.deployment_id for t in candidates])
    units = recall.build_units(candidates, timeframes)
    groups = []
    for holding, words in HOLDINGS:
        got = recall.recall(recall.Context(setup=setup, side=side, holding=holding, timeframe=None), cut, units)
        groups.append({"holding": holding, "holding_words": words, "line": got["line"], "count": got["count"],
                       "min_sample": got["min_sample"], "enough": got["enough"], "wins": got["wins"], "losses": got["losses"],
                       "flat": got["flat"], "avg_r": got["avg_r"],
                       "matches": [{k: m.get(k) for k in ("entered_at", "closed_at", "r", "result", "outcome", "how_closed", "mode")}
                                   for m in got["matches"]]})
    return {"subject": {"setup_words": "Hand order from the option / futures ticket", "side": side.lower(), "timeframe": "not recorded"},
            "as_of": cut.isoformat(), "groups": groups}


__all__ = ["exits_part", "trigger_part", "plan_parts", "resolve_plan", "what_if_read", "card_part", "check_card_read",
           "similar_read", "NO_PLAN", "NO_CARD", "CARD_UNVERIFIED", "NO_SETUP_PLAN", "NO_SCENARIO_PLAN", "NO_LEVELS"]
