"""Analyze a chart: the share link (PR-A4; design PR-D, CA-5) and the structure-only shared copy.

Who may open a link (CA-5, provisional default): only a LOGGED-IN member of the owner's own organisation, while both
`chart_analyze` and `chart_analyze_share` are on for it. Never public: a public analysis would be a publication (CA-2).

What a link shows (CLAUDE.md "Signal language", SEBI): a shared copy is someone else reading the analysis, so it is
treated as a public / education surface even inside the organisation. It carries:
- NO security name: the symbol and the exchange read from the picture are removed, and so is every text that names them;
- NO price and NO other number: every price field is removed, the one-click alert spec is removed, and every text that
  contains a digit is left out (replaced by a neutral line);
- NO date: the visible range (first and last bar) and the higher timeframes' labels are removed;
- NOT the chart picture (it shows the name and the price axis): the drawing is shown over a plain background instead.
What is left is the structure: the story in words, the trend side, the phase, the setup's status and conditions in
words, the evidence list, the scenarios, the areas' sides and states, and the drawing.

The link: `<payload>.<mac>`, the payload naming the item, its owner, the organisation, the link's version and its expiry,
signed with a key derived from the server's secret (HMAC-SHA256). The link's state lives with the analysis it belongs
to - the `_share` key of the stored result (no new table, no migration): `version` (raised by every new link and by
"Stop sharing", so an older link stops working), `active` and `expires_at`. Deleting the analysis (or retention)
removes the stored result and so every link to it. One live link per analysis.
"""
import base64
import copy
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional, Tuple

from app.core import config as app_config

STATE_KEY = "_share"
TOKEN_MAX = 200
NOT_SHARED = "(Left out of the shared copy: it named a price, a date or the chart.)"
SHARED_NOTE = ("Shared by a member of your organisation. The chart's name, its prices, its dates and the picture are left "
               "out: this copy shows the structure only.")
# Keys whose string value is a fixed word from the contract (an enum) or the fixed disclaimer / label: never rewritten.
_ENUM_KEYS = frozenset({"side", "state", "status", "kind", "label", "phase", "divergence", "trend", "mode", "coordinate_space", "id",
                        "disclaimer"})
# Keys that carry a price: removed from the shared copy.
_PRICE_KEYS = frozenset({"price", "price_low", "price_high", "low", "high"})
_DIGIT = re.compile(r"\d")


def _key() -> bytes:
    return hashlib.sha256(b"atp-chart-analyze-share|" + (app_config.JWT_SECRET_KEY or "atp").encode()).digest()


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _mac(payload: str) -> str:
    return hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def make_token(item_id: int, user_id: int, tenant_id: int, version: int, expires_at: datetime) -> str:
    payload = _b64(f"{item_id}:{user_id}:{tenant_id}:{version}:{int(expires_at.timestamp())}")
    return f"{payload}.{_mac(payload)}"


def read_token(token: str) -> Optional[Tuple[int, int, int, int, datetime]]:
    """(item id, owner user id, tenant id, version, expires at) of a link this server signed, else None. Expiry is
    checked by the caller (an expired link says so)."""
    try:
        if not token or len(token) > TOKEN_MAX:
            return None
        payload, mac = token.split(".", 1)
        if not hmac.compare_digest(_mac(payload), mac):
            return None
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
        item, user, tenant, version, until = (int(part) for part in raw.split(":"))
        return item, user, tenant, version, datetime.fromtimestamp(until, tz=timezone.utc)
    except (ValueError, UnicodeDecodeError):
        return None


# --- the state stored with the analysis ------------------------------------------------------------------------------------

def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def split(result_json: Optional[str]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """The stored result without the link state, and the link state ({} when never shared)."""
    if not result_json:
        return None, {}
    try:
        data = json.loads(result_json)
    except ValueError:
        return None, {}
    if not isinstance(data, dict):
        return None, {}
    state = data.pop(STATE_KEY, None)
    data.pop("_pick", None)                       # a hybrid item's pick before its analysis (hybrid.PICK_KEY), not a result
    return data or None, state if isinstance(state, dict) else {}


def _expires(state: Dict[str, Any]) -> Optional[datetime]:
    try:
        return _aware(datetime.fromisoformat(str(state["expires_at"])))
    except (KeyError, TypeError, ValueError):
        return None


def live(state: Dict[str, Any], now: datetime) -> bool:
    until = _expires(state)
    return bool(state.get("active")) and until is not None and until > now


def owner_view(item_id: int, user_id: int, tenant_id: int, state: Dict[str, Any], now: datetime) -> Optional[Dict[str, Any]]:
    """What the owner sees about the link: its address token and expiry while it is live, else None."""
    until = _expires(state)
    if until is None or not live(state, now):
        return None
    return {"token": make_token(item_id, user_id, tenant_id, int(state.get("version") or 0), until), "expires_at": until.isoformat()}


def with_state(result_json: str, state: Dict[str, Any]) -> str:
    result, _ = split(result_json)
    if result is None:
        raise ValueError("no stored result")
    return json.dumps({**result, STATE_KEY: state}, separators=(",", ":"))


def new_state(old: Dict[str, Any], now: datetime, days: int) -> Dict[str, Any]:
    return {"version": int(old.get("version") or 0) + 1, "active": True, "expires_at": (now + timedelta(days=days)).isoformat()}


def stopped_state(old: Dict[str, Any]) -> Dict[str, Any]:
    return {"version": int(old.get("version") or 0) + 1, "active": False, "expires_at": None}


def opens(state: Dict[str, Any], version: int, expires_at: datetime, now: datetime) -> bool:
    """The link with this version and expiry is the analysis's live link (an older or a stopped link is not)."""
    until = _expires(state)
    return (live(state, now) and until is not None and int(state.get("version") or 0) == version
            and int(until.timestamp()) == int(expires_at.timestamp()))


# --- the structure-only copy ------------------------------------------------------------------------------------------------

def _names(result: Dict[str, Any]) -> Tuple[str, ...]:
    raw = result.get("recognised")
    rec: Dict[str, Any] = raw if isinstance(raw, dict) else {}
    return tuple(str(v) for v in (rec.get("symbol"), rec.get("exchange")) if isinstance(v, str) and v.strip())


def _names_in(text: str, names: Iterable[str]) -> bool:
    return any(re.search(rf"(?<![A-Za-z0-9]){re.escape(n.strip())}(?![A-Za-z0-9])", text, re.IGNORECASE) for n in names)


def clean_text(text: str, names: Iterable[str]) -> str:
    """The text itself, or the neutral line when it carries a digit (a price, a date, any number) or names the chart."""
    if not text:
        return text
    return NOT_SHARED if _DIGIT.search(text) or _names_in(text, names) else text


def _walk(value: Any, names: Tuple[str, ...], key: str = "") -> Any:
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            if k in _PRICE_KEYS:
                out[k] = None
            elif k == "alert":
                out[k] = None
            else:
                out[k] = _walk(v, names, k)
        return out
    if isinstance(value, list):
        return [_walk(v, names, key) for v in value]
    if isinstance(value, str):
        return value if key in _ENUM_KEYS and not _names_in(value, names) or key == "disclaimer" else clean_text(value, names)
    return value


def redact(result: Dict[str, Any]) -> Dict[str, Any]:
    """The shared copy of a validated result: structure only (no name, no price, no date, no number in any text).
    The drawing's pixel coordinates stay (they draw the structure over a plain background, they are not shown)."""
    names = _names(result)
    body = copy.deepcopy({k: v for k, v in result.items() if k != STATE_KEY})
    body["recognised"] = {"symbol": None, "exchange": None, "timeframe": None, "first_bar": None, "last_bar": None}
    htf = body.get("htf_context")
    if isinstance(htf, list):
        body["htf_context"] = [{**h, "timeframe": None} for h in htf if isinstance(h, dict)]
    out = _walk(body, names)
    out["approximate_prices"] = False            # nothing priced is left to be approximate
    out["shared"] = True
    out["shared_note"] = SHARED_NOTE
    return out
