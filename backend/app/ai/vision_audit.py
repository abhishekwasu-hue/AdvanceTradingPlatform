"""BYOK-V2 (docs/BYOK.md): the Vision audit of a monitoring-agent proposal, on the user's own key.

Flow (ADR-0006: Vision only comments - it never approves, rejects, snoozes, places, blocks or changes an order, a
proposal or a deployment):

1. `enqueue_for_proposal` - called by `monitor.raise_proposals` right after the proposal and its normal notification
   are written. It only inserts one `vision_reports` row (`pending`) for the deployment's creator, when the operator
   flag `vision_byok` is on and that user turned Vision on with proposals in the audit scope and at least one channel.
   No network, inside a savepoint: it can never fail or slow the proposal.
2. `drain` - run by the trading worker as a background task (never inside a cycle, never in the approve/reject path).
   Each row is claimed (`pending` -> `running`, a conditional UPDATE, so two replicas never audit twice), then:
   the chart is rendered server-side from the platform's own bars and the proposal's own levels (`chart_png`, a
   deterministic PNG, no network); `byok_allowed` is asked; when it says yes, ONE call goes to the provider the user
   chose, with the image and a minimal context, under THAT user's key (`byok.get_key` + `byok.open_key` - the row's
   user only, never anybody else's key). The call is metered on the user's key (`llm_calls.billed_to_user_key`).
3. The reply is parsed into the four judgements (Zones, Structure, Pullback & trigger, Entry/SL/Target as
   ✅ / ⚠️ / ❌ with a short note). Every number in the report comes from the engine's data (the proposal's trade,
   the bars' range) - digits in the model's notes are dropped, and a note that tries to instruct (approve, place an
   order, ...) is withheld. At most 12 lines.
4. The report is shown to that user only (GET /api/ai/byok/reports) and, when the user turned the Telegram channel on
   and linked a private chat, sent there as a photo with the caption.

No key, flag off, a cap reached or a provider error: the proposal and its normal notification are unchanged; the report
reads "Vision off - add your API key in Settings" or "Vision skipped: <reason>" (sanitised: never the key), and the
chart still goes to Telegram. The chart image is never stored - only its SHA-256.
"""
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import byok, chart_png, pricing
from app.ai import providers as ai_providers
from app.ai.metering import log_call
from app.ai.providers import Completion, ProviderError
from app.audit.log import write_audit_log
from app.db.models import (AiActionRecord, AlertChannelRecord, StrategyDeploymentRecord, TradeRecord, User, UserVisionPrefsRecord,
                           VisionReportRecord)

logger = logging.getLogger(__name__)

FEATURE = "vision_audit"
PROMPT_VERSION = "byok-vision-v2"
MAX_TOKENS = 400
EST_INPUT_TOKENS = 1600          # the image (a few hundred tokens on every provider) plus the short text
EST_OUTPUT_TOKENS = 600
MAX_LINES = 12
NOTE_MAX = 90
LINE_MAX = 160
CAPTION_MAX = 1024               # Telegram's limit for a photo caption
STALE_AFTER = timedelta(hours=2)          # a proposal older than this is not audited any more
RUNNING_TIMEOUT = timedelta(minutes=15)   # a claimed row whose worker died is closed as failed
DRAIN_LIMIT = 5
TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_TIMEOUT = 15.0

STATUS_PENDING, STATUS_RUNNING, STATUS_OK, STATUS_SKIPPED, STATUS_FAILED = "pending", "running", "ok", "skipped", "failed"
FINAL = (STATUS_OK, STATUS_SKIPPED, STATUS_FAILED)

HEADINGS: Tuple[Tuple[str, str], ...] = (("zones", "Zones"), ("structure", "Structure"), ("pullback_trigger", "Pullback & trigger"),
                                         ("entry_sl_target", "Entry/SL/Target"))
EMOJI = {"ok": "✅", "caution": "⚠️", "bad": "❌"}
EMOJI_SET = frozenset(EMOJI.values())
COMMENT_ONLY = "Comment only - Vision never approves, rejects or changes anything."
VISION_OFF_SWITCH = "Vision off - turn it on in Settings"
VISION_OFF_SCOPE = "Vision off for proposals - turn it on in Settings"
NOT_CREATOR = "Vision reviews proposals on deployments you created"
PENDING_LINE = "Vision report pending"
WITHHELD = "(comment withheld)"

SYSTEM = (
    "You review one candlestick chart for a trader. You only comment: you cannot approve, reject, place, cancel or change "
    "anything, and nothing you write is executed. Answer with ONE JSON object and nothing else, exactly these four keys:\n"
    '{"zones": {"verdict": "ok|caution|bad", "note": "..."}, "structure": {"verdict": "...", "note": "..."}, '
    '"pullback_trigger": {"verdict": "...", "note": "..."}, "entry_sl_target": {"verdict": "...", "note": "..."}}\n'
    "Each note is at most 12 plain English words and contains no numbers or prices (the platform adds its own numbers). "
    "zones: are the marked HIGH / LOW and trade levels at meaningful support or resistance; structure: trend and swing "
    "structure; pullback_trigger: is there a clean pullback and a trigger; entry_sl_target: do the ENTRY / SL / TGT lines "
    "suit that structure (verdict caution when there are none)."
)

_ALIASES: Dict[str, Tuple[str, ...]] = {
    "zones": ("zones", "zone"),
    "structure": ("structure",),
    "pullback_trigger": ("pullback_trigger", "pullback_and_trigger", "pullback", "trigger"),
    "entry_sl_target": ("entry_sl_target", "entry_sl_tp", "entry", "levels", "entry/sl/target"),
}
_LINE_PATTERNS: Dict[str, str] = {
    "zones": r"zones?",
    "structure": r"structure",
    "pullback_trigger": r"pull\s*-?\s*back(?:\s*(?:&|and)\s*trigger)?",
    "entry_sl_target": r"entry\s*/\s*sl\s*/\s*(?:target|tp)|entry",
}
_VERDICT_WORDS: Dict[str, str] = {
    "ok": "ok", "good": "ok", "pass": "ok", "yes": "ok", "valid": "ok", "positive": "ok", "✅": "ok",
    "caution": "caution", "warn": "caution", "warning": "caution", "mixed": "caution", "unclear": "caution", "neutral": "caution", "⚠️": "caution", "⚠": "caution",
    "bad": "bad", "fail": "bad", "no": "bad", "invalid": "bad", "negative": "bad", "❌": "bad",
}
# A note that tries to instruct the platform or the trader to act is withheld (the report only comments).
_INSTRUCTION = re.compile(r"(?i)\b(approv\w*|reject\w*|snooz\w*|execut\w*|place|placed|placing|orders?|cancel\w*|ignore|instructions?|"
                          r"system|status|decide|decided|decision|confirm\w*|override\w*|click\w*)\b")
_LEADING_VERDICT = re.compile(r"^\W*(?:(?:ok|good|pass|caution|warning|warn|mixed|unclear|neutral|bad|fail)\b)?\W*", re.IGNORECASE)
_NOTE_CHARS = re.compile(r"[^A-Za-z ,.'()/&-]")

CandleLoader = Callable[[AsyncSession, str, str, str, datetime], Awaitable[List[chart_png.Bar]]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# --- the lines the user reads ------------------------------------------------------------------------------------------

def skip_line(reason: str) -> str:
    """The one line shown when no report was written."""
    if reason == byok.REASON_NO_KEY:
        return byok.VISION_OFF_NO_KEY
    if reason == byok.REASON_VISION_OFF:
        return VISION_OFF_SWITCH
    return f"Vision skipped: {reason}"


def clean_note(raw: Any) -> str:
    """The model's short note, made safe: one line, no digits (numbers come from the engine only), only letters and
    simple punctuation, at most NOTE_MAX characters; an instruction ("approve this", "place order") is withheld."""
    text = str(raw or "")
    if _INSTRUCTION.search(text):
        return WITHHELD
    text = re.sub(r"[0-9]+(?:[.,][0-9]+)*", " ", text)
    text = _NOTE_CHARS.sub(" ", text)
    text = " ".join(text.split()).strip(" ,.-/&")
    if len(text) > NOTE_MAX:
        text = text[:NOTE_MAX].rsplit(" ", 1)[0].rstrip(" ,.-/&") + "..."
    return text or "no comment"


def _verdict(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    for token in ("✅", "⚠️", "⚠", "❌"):
        if token in text:
            return _VERDICT_WORDS[token]
    word = re.match(r"[a-z]+", text)
    return _VERDICT_WORDS.get(word.group(0), "caution") if word else "caution"


def _from_json(text: str) -> Optional[Dict[str, Tuple[str, str]]]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    lowered = {str(k).strip().lower().replace(" ", "_"): v for k, v in data.items()}
    found: Dict[str, Tuple[str, str]] = {}
    for key, aliases in _ALIASES.items():
        value = next((lowered[a] for a in aliases if a in lowered), None)
        if value is None:
            continue
        if isinstance(value, dict):
            found[key] = (_verdict(value.get("verdict")), clean_note(value.get("note")))
        else:
            found[key] = (_verdict(value), clean_note(_LEADING_VERDICT.sub("", str(value), count=1)))
    return found or None


def _from_lines(text: str) -> Optional[Dict[str, Tuple[str, str]]]:
    found: Dict[str, Tuple[str, str]] = {}
    for key, pattern in _LINE_PATTERNS.items():
        match = re.search(rf"(?im)^[^A-Za-z\n]*(?:{pattern})[^:\n]*[:\-–]\s*(?P<rest>.*)$", text)
        if match is None:
            continue
        rest = match.group("rest").strip()
        verdict = _verdict(rest)
        found[key] = (verdict, clean_note(_LEADING_VERDICT.sub("", rest, count=1)))
    return found or None


def parse_answer(text: Optional[str]) -> Optional[Dict[str, Tuple[str, str]]]:
    """{heading key: (ok|caution|bad, note)} from the model's answer (JSON first, then "Heading: ✅ note" lines); None
    when none of the four judgements can be read. Every other field the answer carries is ignored."""
    if not text:
        return None
    parsed = _from_json(text) or _from_lines(text)
    if not parsed:
        return None
    for key, _ in HEADINGS:
        parsed.setdefault(key, ("caution", "no comment"))
    return parsed


def _price(value: Optional[float]) -> str:
    return f"{float(value):.2f}"


@dataclass
class Context:
    """The engine's own data for one proposal: what is drawn and every number the report prints."""
    symbol: str
    exchange: str
    timeframe: str
    action: str
    rule: str
    direction: Optional[str] = None
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    bars: List[chart_png.Bar] = field(default_factory=list)

    @property
    def high(self) -> Optional[float]:
        return max(b.high for b in self.bars) if self.bars else None

    @property
    def low(self) -> Optional[float]:
        return min(b.low for b in self.bars) if self.bars else None

    @property
    def last_close(self) -> Optional[float]:
        return self.bars[-1].close if self.bars else None

    def levels(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for name, value in (("ENTRY", self.entry), ("SL", self.stop), ("TGT", self.target), ("HIGH", self.high), ("LOW", self.low)):
            if value is not None and value > 0:
                out[name] = float(value)
        return out

    def user_text(self) -> str:
        """The minimal context sent with the image: no account data, no reason text, no prices."""
        lines = [f"Symbol {self.symbol} ({self.exchange}), timeframe {self.timeframe}, the last {len(self.bars)} bars.",
                 f"The platform's monitoring agent proposes: {self.action.replace('_', ' ').lower()} (rule {self.rule}).",
                 f"Open position direction: {self.direction.lower() if self.direction else 'none'}.",
                 "Lines on the chart: ENTRY, SL and TGT are the open position's levels (when there is one); HIGH and LOW are the window's range.",
                 "Comment on the chart only. Answer with the JSON object."]
        return "\n".join(lines)


def format_report(parsed: Dict[str, Tuple[str, str]], ctx: Context, provider: str, model: str) -> str:
    """The Vision report: a header, the four judgements with the engine's numbers, and the comment-only footer."""
    numbers = {
        "zones": f" (range {_price(ctx.low)} - {_price(ctx.high)})" if ctx.low is not None and ctx.high is not None else "",
        "structure": f" (last close {_price(ctx.last_close)})" if ctx.last_close is not None else "",
        "pullback_trigger": "",
        "entry_sl_target": (f" (entry {_price(ctx.entry)} / SL {_price(ctx.stop)} / target {_price(ctx.target) if ctx.target else '-'})"
                            if ctx.entry is not None and ctx.stop is not None else " (no open position)"),
    }
    label = byok.PROVIDER_LABELS.get(provider, provider)
    lines = [f"Vision report - {ctx.symbol} {ctx.timeframe} ({label}, {model})"]
    for key, heading in HEADINGS:
        verdict, note = parsed[key]
        lines.append(f"{heading}: {EMOJI[verdict]} {note}{numbers[key]}")
    lines.append(COMMENT_ONLY)
    return "\n".join(line[:LINE_MAX] for line in lines[:MAX_LINES])


# --- the engine's data -------------------------------------------------------------------------------------------------

_LAKE_TIMEFRAMES = {"1min": "1m", "3min": "3m", "5min": "5m", "10min": "10m", "15min": "15m", "30min": "30m", "60min": "60m",
                    "1h": "60m", "day": "1d", "1d": "1d", "1day": "1d"}


async def lake_bars(session: AsyncSession, exchange: str, symbol: str, timeframe: str, now: datetime) -> List[chart_png.Bar]:
    """The platform's own recorded bars (market-data lake, `<EXCHANGE>:<SYMBOL>`): the deployment's timeframe, else
    one-minute bars. Read-only, no network."""
    from app.market_lake.asof import candles_as_of
    key = f"{(exchange or 'NSE').upper()}:{(symbol or '').upper()}"
    wanted = _LAKE_TIMEFRAMES.get((timeframe or "").lower(), (timeframe or "").lower())
    for tf in dict.fromkeys([wanted, "1m"]):
        lookback = timedelta(days=400) if tf == "1d" else timedelta(days=5)
        rows = await candles_as_of(session, key, tf, now - lookback, now)
        bars = chart_png.bars_from_rows(rows[-chart_png.MAX_BARS:])
        if bars:
            return bars
    return []


async def build_context(session: AsyncSession, action: AiActionRecord, *, now: datetime, loader: Optional[CandleLoader] = None) -> Optional[Context]:
    """Reads (never writes) the proposal's deployment, its trade (the proposal's own, else the deployment's open one)
    and the bars. None when the deployment is gone."""
    if action.deployment_id is None:
        return None
    dep = await session.get(StrategyDeploymentRecord, action.deployment_id)
    if dep is None or dep.tenant_id != action.tenant_id:
        return None
    trade: Optional[TradeRecord] = None
    if action.trade_id is not None:
        trade = await session.get(TradeRecord, action.trade_id)
    if trade is None:
        trade = await session.scalar(select(TradeRecord).where(TradeRecord.tenant_id == action.tenant_id, TradeRecord.deployment_id == dep.id,
                                                               TradeRecord.exit_time.is_(None)).order_by(TradeRecord.id.desc()).limit(1))
    if trade is not None and trade.tenant_id != action.tenant_id:
        trade = None
    bars = await (loader or lake_bars)(session, dep.exchange, dep.symbol, dep.timeframe, now)
    return Context(symbol=dep.symbol, exchange=dep.exchange, timeframe=dep.timeframe, action=action.action, rule=action.rule,
                   direction=trade.direction if trade is not None else None,
                   entry=float(trade.entry_price) if trade is not None else None,
                   stop=float(trade.stop_loss) if trade is not None and trade.stop_loss else None,
                   target=float(trade.target1) if trade is not None and trade.target1 else None,
                   bars=list(bars)[-chart_png.MAX_BARS:])


# --- queueing (inside raise_proposals; no network) ---------------------------------------------------------------------

async def _owner(session: AsyncSession, action: AiActionRecord) -> Optional[User]:
    """The one user whose key may audit this proposal: the creator of its deployment, in the same organisation."""
    if action.deployment_id is None:
        return None
    dep = await session.get(StrategyDeploymentRecord, action.deployment_id)
    if dep is None or dep.tenant_id != action.tenant_id or dep.created_by is None:
        return None
    owner = await session.get(User, dep.created_by)
    if owner is None or owner.tenant_id != action.tenant_id or not owner.is_active:
        return None
    return owner


def _wants_proposals(prefs: Optional[UserVisionPrefsRecord]) -> bool:
    return prefs is not None and bool(prefs.enabled) and bool(prefs.audit_proposals) and bool(prefs.channel_in_app or prefs.channel_telegram)


async def enqueue_for_proposal(session: AsyncSession, action: AiActionRecord, *, now: Optional[datetime] = None) -> Optional[VisionReportRecord]:
    """One `pending` row for the deployment creator when the flag is on and they want proposals audited. Runs in a
    savepoint and never raises: the proposal it follows is already written and stays exactly as it is."""
    from app.platform.controls import flag_enabled
    try:
        async with session.begin_nested():
            if not await flag_enabled(session, byok.FLAG, action.tenant_id):
                return None
            owner = await _owner(session, action)
            if owner is None or not _wants_proposals(await byok.get_prefs(session, owner)):
                return None
            existing = await session.scalar(select(VisionReportRecord).where(VisionReportRecord.user_id == owner.id,
                                                                            VisionReportRecord.ai_action_id == action.id))
            if existing is not None:
                return existing
            row = VisionReportRecord(tenant_id=action.tenant_id, user_id=owner.id, ai_action_id=action.id, status=STATUS_PENDING,
                                     created_at=now or _utcnow())
            session.add(row)
            await session.flush()
            return row
    except Exception as exc:  # noqa: BLE001 - Vision is advisory; the proposal never depends on it
        logger.warning("Vision audit not queued for proposal %s: %s", getattr(action, "id", "?"), exc.__class__.__name__)
        return None


# --- the audit (worker, background) ------------------------------------------------------------------------------------

def telegram_client() -> httpx.AsyncClient:
    """Tests replace this with a MockTransport client."""
    return httpx.AsyncClient(timeout=TELEGRAM_TIMEOUT)


async def send_photo(bot_token: str, chat_id: str, png: bytes, caption: str, client: Optional[httpx.AsyncClient] = None) -> None:
    """Telegram sendPhoto (multipart). The error raised never contains the bot token."""
    own = client is None
    client = client or telegram_client()
    try:
        try:
            response = await client.post(f"{TELEGRAM_API}/bot{bot_token}/sendPhoto", data={"chat_id": chat_id, "caption": caption[:CAPTION_MAX]},
                                         files={"photo": ("chart.png", png, "image/png")})
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Telegram unreachable: {exc.__class__.__name__}") from None
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400 or not payload.get("ok", False):
            description = str(payload.get("description") or f"HTTP {response.status_code}").replace(bot_token, "[redacted]")
            raise RuntimeError(f"Telegram API {response.status_code}: {description}")
    finally:
        if own:
            await client.aclose()


def caption_for(action: AiActionRecord, ctx: Optional[Context], report_text: str) -> str:
    title = f"AI proposes {action.action.replace('_', ' ').lower()}"
    where = f" - {ctx.symbol} {ctx.timeframe}" if ctx is not None else ""
    caption = f"{title}{where} (proposal #{action.id})\n{report_text}\nDecide under AI Copilot."
    return caption[:CAPTION_MAX]


async def _deliver_telegram(session: AsyncSession, row: VisionReportRecord, owner: User, prefs: Optional[UserVisionPrefsRecord], png: Optional[bytes],
                            caption: str, client: Optional[httpx.AsyncClient]) -> None:
    """Photo + caption to the user's OWN linked private chat - only with the channel toggle on, a linked chat and the
    organisation's Telegram bot enabled. Never to the organisation's group chat (the report is this user's)."""
    from app.alerts.channels import decrypt_config
    from app.alerts.links import linked_chat
    from app.secrets_store.envelope import ensure_tenant_key
    if png is None or prefs is None or not prefs.channel_telegram:
        return
    link = await linked_chat(session, owner.tenant_id, owner.id)
    channel = await session.scalar(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == owner.tenant_id,
                                                                   AlertChannelRecord.channel_type == "TELEGRAM"))
    if link is None or not link.chat_id or channel is None or not channel.enabled:
        return
    token = ""
    try:
        await ensure_tenant_key(session, owner.tenant_id)
        token = str(getattr(decrypt_config(channel), "bot_token", "") or "")
        await send_photo(token, str(link.chat_id), png, caption, client)
        row.telegram_status, row.telegram_error = "sent", None
    except Exception as exc:  # noqa: BLE001 - the report stays in-app; the error is kept, without the bot token or the key
        row.telegram_status = "failed"
        row.telegram_error = byok.sanitize_error(str(exc) if isinstance(exc, RuntimeError) else exc.__class__.__name__, [token])
    finally:
        token = ""


async def _call_provider(session: AsyncSession, row: VisionReportRecord, owner: User, provider: str, model: str, ctx: Context, png: bytes, *,
                         client: Optional[Any], now: datetime) -> Tuple[Optional[str], Optional[str], float]:
    """(answer text, sanitised error, cost) - ONE call with the row owner's own key, metered on that key."""
    from app.secrets_store.envelope import ensure_tenant_key
    record = await byok.get_key(session, owner, provider)          # the owner's row only (user id AND tenant id)
    if record is None:
        return None, byok.REASON_NO_KEY, 0.0
    user_text = ctx.user_text()
    key = ""
    result: Optional[Completion] = None
    text: Optional[str] = None
    error: Optional[str] = None
    called = False
    try:
        await ensure_tenant_key(session, owner.tenant_id)
        key = byok.open_key(record, user=owner)
        llm = ai_providers.build_byok_provider(provider, key, model, client=client)
        called = True
        result = await llm.complete_full(SYSTEM, user_text, max_tokens=MAX_TOKENS, image_png=png)
        text = result.text
    except byok.KeyBindingError as exc:
        error = byok.sanitize_error(str(exc), [key])
    except ProviderError as exc:
        error = byok.sanitize_error(str(exc), [key])
        result = exc.usage
    except Exception as exc:  # noqa: BLE001 - reported by class name only (never its text, which could carry the key)
        logger.warning("Vision audit for user %s (%s) failed: %s", owner.id, provider, exc.__class__.__name__)
        error = byok.sanitize_error(f"Vision call failed: {exc.__class__.__name__}", [key])
    finally:
        key = ""
    cost = 0.0
    if result is not None and (result.input_tokens or result.output_tokens or result.cache_read_tokens):
        cost = pricing.cost_usd(provider, result.model or model, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                                cache_read_tokens=result.cache_read_tokens, cache_write_tokens=result.cache_write_tokens).amount
    if called:
        await log_call(session, tenant_id=owner.tenant_id, user_id=owner.id, feature=FEATURE, provider=provider,
                       model=(result.model if result is not None and result.model else model), prompt_version=PROMPT_VERSION,
                       system=SYSTEM, user=user_text, response=text if error is None else None,
                       status="ok" if error is None else f"error: {error}", result=result, cost_usd=cost, billed_to_user_key=True)
        record.last_used_at = now
    return text, error, cost


def _finish(row: VisionReportRecord, status: str, *, reason: Optional[str] = None, report: Optional[str] = None) -> None:
    row.status = status
    row.reason = byok.sanitize_error(reason) if reason else None
    row.report_text = report if report is not None else (skip_line(row.reason or "") if status != STATUS_OK else None)


async def _audit(session: AsyncSession, row: VisionReportRecord, *, client: Optional[Any], telegram: Optional[httpx.AsyncClient],
                 loader: Optional[CandleLoader], now: datetime) -> None:
    owner = await session.get(User, row.user_id)
    action = await session.get(AiActionRecord, row.ai_action_id)
    if owner is None or action is None or owner.tenant_id != row.tenant_id or action.tenant_id != row.tenant_id:
        _finish(row, STATUS_SKIPPED, reason="proposal not available")
        return
    if action.status != "PROPOSED":
        _finish(row, STATUS_SKIPPED, reason="proposal already decided")
        return
    if now - _utc(row.created_at) > STALE_AFTER:
        _finish(row, STATUS_SKIPPED, reason="proposal too old")
        return
    prefs = await byok.get_prefs(session, owner)
    keys = await byok.list_keys(session, owner)
    provider = byok.active_provider(prefs, keys)
    model = byok.effective_model(next((k for k in keys if k.provider == provider), None), provider) if provider else ""
    row.provider, row.model = provider, (model or None)

    ctx = await build_context(session, action, now=now, loader=loader)
    png: Optional[bytes] = None
    if ctx is not None and ctx.bars:
        png = chart_png.render(chart_png.ChartSpec(ctx.bars, ctx.levels()))
        row.image_sha256 = hashlib.sha256(png).hexdigest()       # what was drawn; the image itself is not kept

    estimate = (pricing.cost_usd(provider, model, input_tokens=EST_INPUT_TOKENS, output_tokens=EST_OUTPUT_TOKENS).amount
                if provider else 0.0)
    allowed, reason = await byok.byok_allowed(session, owner.id, provider, now=now, estimate_usd=estimate)
    if not allowed:
        _finish(row, STATUS_SKIPPED, reason=reason)
    elif ctx is None or png is None or provider is None:
        _finish(row, STATUS_SKIPPED, reason="no chart data")
    else:
        text, error, cost = await _call_provider(session, row, owner, provider, model, ctx, png, client=client, now=now)
        row.cost_usd = float(cost or 0.0)
        parsed = parse_answer(text) if error is None else None
        if error is not None:
            _finish(row, STATUS_FAILED, reason=error)
        elif parsed is None:
            _finish(row, STATUS_FAILED, reason="the provider's answer could not be read")
        else:
            _finish(row, STATUS_OK, report=format_report(parsed, ctx, provider, model))
    if reason == byok.REASON_VISION_OFF:
        return                                                    # the feature is off for this user: nothing is sent
    await _deliver_telegram(session, row, owner, prefs, png, caption_for(action, ctx, row.report_text or ""), telegram)


async def process(session: AsyncSession, report_id: int, *, client: Optional[Any] = None, telegram: Optional[httpx.AsyncClient] = None,
                  loader: Optional[CandleLoader] = None, now: Optional[datetime] = None) -> Optional[VisionReportRecord]:
    """Claims one pending row and audits it. Returns the finished row, or None when another worker had it."""
    now = now or _utcnow()
    claimed = await session.execute(update(VisionReportRecord).where(VisionReportRecord.id == report_id, VisionReportRecord.status == STATUS_PENDING)
                                    .values(status=STATUS_RUNNING, started_at=now).execution_options(synchronize_session=False))
    if getattr(claimed, "rowcount", 0) != 1:
        await session.rollback()
        return None
    await session.commit()
    row = await session.get(VisionReportRecord, report_id)
    if row is None:
        return None
    await session.refresh(row)
    try:
        await _audit(session, row, client=client, telegram=telegram, loader=loader, now=now)
        row.finished_at = now
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - recorded on the row; nothing else is touched
        logger.warning("Vision audit %s failed: %s", report_id, exc.__class__.__name__)
        await session.rollback()
        row = await session.get(VisionReportRecord, report_id)
        if row is None:
            return None
        _finish(row, STATUS_FAILED, reason=f"Vision failed: {exc.__class__.__name__}")
        row.finished_at = now
        await session.commit()
    return row


async def drain(session: AsyncSession, *, limit: int = DRAIN_LIMIT, client: Optional[Any] = None, telegram: Optional[httpx.AsyncClient] = None,
                loader: Optional[CandleLoader] = None, now: Optional[datetime] = None) -> int:
    """Closes rows whose worker died, then audits up to `limit` pending rows (oldest first). Returns how many it audited."""
    now = now or _utcnow()
    await session.execute(update(VisionReportRecord).where(VisionReportRecord.status == STATUS_RUNNING,
                                                           VisionReportRecord.started_at < now - RUNNING_TIMEOUT)
                          .values(status=STATUS_FAILED, reason="interrupted", report_text=skip_line("interrupted"), finished_at=now)
                          .execution_options(synchronize_session=False))
    await session.commit()
    ids = list(await session.scalars(select(VisionReportRecord.id).where(VisionReportRecord.status == STATUS_PENDING)
                                     .order_by(VisionReportRecord.id).limit(limit)))
    done = 0
    for report_id in ids:
        if await process(session, report_id, client=client, telegram=telegram, loader=loader, now=now) is not None:
            done += 1
    return done


# --- the user's own reports (API) --------------------------------------------------------------------------------------

def as_dict(row: VisionReportRecord) -> Dict[str, Any]:
    iso = lambda v: _utc(v).isoformat() if v else None  # noqa: E731
    text = row.report_text if row.status in FINAL else None
    if row.status not in FINAL:
        line: Optional[str] = PENDING_LINE
    elif row.status == STATUS_OK:
        line = None
    else:
        line = (text or skip_line(row.reason or "")).split("\n")[0]
    return {"id": row.id, "ai_action_id": row.ai_action_id, "status": row.status, "provider": row.provider, "model": row.model,
            "reason": row.reason, "report": text, "lines": text.split("\n") if text else [], "line": line,
            "image_sha256": row.image_sha256, "image_stored": False, "cost_usd": round(float(row.cost_usd or 0.0), 8),
            "telegram_status": row.telegram_status, "created_at": iso(row.created_at), "finished_at": iso(row.finished_at)}


async def vision_line(session: AsyncSession, user: User, action: Optional[AiActionRecord]) -> str:
    """What the proposal card says when this user has no report for it."""
    keys = await byok.list_keys(session, user)
    prefs = await byok.get_prefs(session, user)
    if not keys:
        return byok.VISION_OFF_NO_KEY
    if prefs is None or not prefs.enabled:
        return VISION_OFF_SWITCH
    if not prefs.audit_proposals:
        return VISION_OFF_SCOPE
    if action is not None:
        owner = await _owner(session, action)
        if owner is None or owner.id != user.id:
            return NOT_CREATOR
    return PENDING_LINE


async def reports_for(session: AsyncSession, user: User, action_id: Optional[int] = None, *, limit: int = 50) -> Dict[str, Any]:
    """The caller's OWN reports (user id AND tenant id), for one proposal or the latest ones."""
    query = select(VisionReportRecord).where(VisionReportRecord.user_id == user.id, VisionReportRecord.tenant_id == user.tenant_id)
    action: Optional[AiActionRecord] = None
    if action_id is not None:
        query = query.where(VisionReportRecord.ai_action_id == action_id)
        found = await session.get(AiActionRecord, action_id)
        action = found if found is not None and found.tenant_id == user.tenant_id else None
    rows = list(await session.scalars(query.order_by(VisionReportRecord.id.desc()).limit(limit)))
    prefs = await byok.get_prefs(session, user)
    return {"reports": [as_dict(r) for r in rows], "vision_line": await vision_line(session, user, action),
            "in_app": bool(prefs.channel_in_app) if prefs is not None else True}


async def delete_report(session: AsyncSession, user: User, report_id: int) -> bool:
    result = await session.execute(delete(VisionReportRecord).where(VisionReportRecord.id == report_id, VisionReportRecord.user_id == user.id,
                                                                    VisionReportRecord.tenant_id == user.tenant_id)
                                   .execution_options(synchronize_session=False))
    if not getattr(result, "rowcount", 0):
        return False                                    # nothing matched: nothing to undo (and the caller's objects stay loaded)
    await write_audit_log(session, user.tenant_id, user.id, "byok_vision_report_deleted", f"#{report_id}")
    await session.commit()
    return True
