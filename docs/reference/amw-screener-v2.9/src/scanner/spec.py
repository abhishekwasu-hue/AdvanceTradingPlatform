"""Universal Scanner Specification (amw.scanner.v1).

A scanner is stored as a Strategy Lab strategy (language "visual" for IR-built scanners, or any Lab language for
code scanners), so it reuses immutable versions (sha256), tenant scoping, sandboxed execution, scans and backtests.
The scanner ENVELOPE adds the fields the Lab spec does not have; it is stored under `spec["scanner"]` and validated
here. The LOGIC is the shared, validated IR (src/lab/ir.py) - the same contract used by the visual builder, the
natural-language builder and the templates. The deterministic engine, never an LLM, decides which symbols qualify.

Condition outcomes are three-state: PASS / FAIL / UNKNOWN (missing data). A mandatory UNKNOWN never qualifies.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from ..lab.contract import StrategySpec
from ..lab.ir import PREDICATES, render_strategy, validate_strategy_ir

SCANNER_SCHEMA = "amw.scanner.v1"
TIMEFRAMES = ("1W", "1D", "1H", "30m", "15m", "5m")


class Alerts(BaseModel):
    enabled: bool = False
    channels: list[Literal["in_app", "telegram", "email"]] = ["in_app"]


class Envelope(BaseModel):
    schema_: str = Field(default=SCANNER_SCHEMA, alias="schema")
    market: Literal["IN"] = "IN"
    exchange: Literal["NSE"] = "NSE"
    universe: str = "Nifty 50"
    instrument_filters: dict = Field(default_factory=dict)        # e.g. {"min_avg_traded_value": 1e8}
    timeframe: Literal["1W", "1D", "1H", "30m", "15m", "5m"] = "1D"
    htf: list[Literal["1W", "1D", "1H", "30m", "15m"]] = []
    frequency: Literal["manual", "daily", "interval"] = "manual"
    at_time: str | None = None                                    # daily: "HH:MM" exchange time
    interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    session: tuple[str, str] | None = None                       # intraday evaluation window, e.g. ("09:30", "15:00")
    incomplete_candle_policy: Literal["exclude"] = "exclude"     # forming candles are never evaluated
    data_quality: Literal["OK_only", "OK_or_STALE"] = "OK_only"
    expiry_bars: int = Field(default=3, ge=1, le=200)
    alerts: Alerts = Alerts()
    risk: dict = Field(default_factory=dict)                      # informational (stop/target come from the IR)

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _check(self):
        order = {t: i for i, t in enumerate(TIMEFRAMES)}
        bad = [h for h in self.htf if order[h] >= order[self.timeframe]]
        if bad:
            raise ValueError(f"htf {bad} must be HIGHER than the scanner timeframe {self.timeframe}")
        if self.frequency == "daily" and not self.at_time:
            raise ValueError("daily frequency needs at_time (HH:MM)")
        if self.frequency == "interval" and not self.interval_minutes:
            raise ValueError("interval frequency needs interval_minutes")
        if self.session and self.timeframe in ("1D", "1W"):
            raise ValueError("session restrictions apply to intraday timeframes only")
        return self


def build(name: str, ir: dict, envelope: dict | None = None, description: str = "") -> dict:
    """Return {"spec": <Lab spec incl. scanner envelope>, "ir": ir, "errors": [...]} without storing anything."""
    errors: list[str] = []
    try:
        env = Envelope.model_validate(envelope or {})
    except ValidationError as e:
        return {"spec": None, "ir": ir, "errors": [f"envelope: {x['loc'][-1] if x['loc'] else ''} {x['msg']}" for x in e.errors()]}
    errors += validate_strategy_ir(ir)
    if not (ir.get("long") or ir.get("short")):
        errors.append("scanner needs at least one side (long or short) with conditions")
    used_htf = _htf_used(ir)
    missing = sorted(used_htf - set(env.htf))
    if missing:
        errors.append(f"conditions reference higher timeframe(s) {missing} that are not in envelope.htf")
    spec = StrategySpec(language="visual", timeframes=[env.timeframe], htf=list(env.htf), universe=env.universe,
                        data_quality=env.data_quality).model_dump()
    spec["scanner"] = {**env.model_dump(by_alias=True), "name": name, "description": description}
    return {"spec": spec, "ir": ir, "errors": errors}


def _htf_used(ir: dict) -> set[str]:
    out: set[str] = set()

    def walk(x):
        if isinstance(x, dict):
            if x.get("t") == "pred" and x.get("name") in ("htf_trend_is", "htf_close_above_ema"):
                out.add((x.get("args") or {}).get("tf") or PREDICATES[x["name"]]["args"]["tf"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(ir)
    return out


def save(svc, user_id: int, name: str, ir: dict, envelope: dict | None = None, description: str = "",
         sid: str | None = None, publish: bool = True, note: str = "") -> dict:
    """Create (or update the draft of) a scanner and optionally freeze a new immutable version."""
    b = build(name, ir, envelope, description)
    if b["errors"]:
        raise ValueError("; ".join(b["errors"]))
    store = svc.store
    if sid is None:
        from ..saas.entitlements import check
        check(store, user_id, "lab_strategies", len(store.lab_list(user_id)) + 1)
        sid = store.lab_create(user_id, name, "visual", description, None, ir, b["spec"])
    elif not store.lab_save_draft(user_id, sid, None, ir, b["spec"]):
        raise KeyError("scanner not found")
    version = store.lab_publish(user_id, sid, note) if publish else None
    return {"scanner_id": sid, "version": version, "readable": render_strategy(ir)}


def duplicate(svc, user_id: int, sid: str, new_name: str) -> str:
    st = svc.store.lab_get(user_id, sid)
    if st is None:
        raise KeyError("scanner not found")
    spec = dict(st["draft_spec"] or {})
    if "scanner" in spec:
        spec["scanner"] = {**spec["scanner"], "name": new_name}
    return svc.store.lab_create(user_id, new_name, st["language"], st.get("description") or "", st["draft_source"],
                                st["draft_ir"], spec)


def outcome(passed) -> str:
    return "UNKNOWN" if passed is None else "PASS" if passed else "FAIL"


def is_scanner(row: dict) -> bool:
    return isinstance((row.get("spec") or row.get("draft_spec") or {}).get("scanner"), dict)


def share(svc, user_id: int, sid: str, to_username: str) -> str:
    """Send a COPY of a scanner's rules + envelope to another user (Q-012 assumption: user-to-user copy). No signals,
    data, versions or credentials are shared; the recipient gets a draft named "[shared] ..." and publishes it
    themselves. Recorded in the audit log."""
    st = svc.store.lab_get(user_id, sid)
    if st is None:
        raise KeyError("scanner not found")
    rec = svc.store.user_by_username(to_username)
    if rec is None or rec["id"] == user_id:
        raise KeyError("recipient not found")
    sender = (svc.store.get_user(user_id) or {}).get("username", str(user_id))
    spec = dict(st["draft_spec"] or {})
    name = f"[shared] {st['name']}"[:100]
    if "scanner" in spec:
        spec["scanner"] = {**spec["scanner"], "name": name, "alerts": {"enabled": False, "channels": ["in_app"]}}
    new = svc.store.lab_create(rec["id"], name, st["language"], f"Shared by {sender}. " + (st.get("description") or ""),
                               st["draft_source"], st["draft_ir"], spec)
    svc.store.audit("scanner_share", f"user {user_id} shared {sid} -> user {rec['id']} as {new}", str(user_id))
    return new

