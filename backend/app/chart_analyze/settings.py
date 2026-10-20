"""Analyze a chart: the admin settings (SUPER_ADMIN, `GET/PUT /api/admin/chart-analyze/settings`).

Every number the feature uses - sizes, retention, the link fetch's limits, the rate limit, the plan limits and the AI
cost cap - lives here, in `DEFAULTS`, and nowhere in the logic. The stored values are a platform control row
(`platform_controls`, key `chart_analyze_settings`, the same store as the marketplace terms and the risk ceilings);
a missing or malformed stored value reads as its default, never as a crash.

Plan tiers: `free`, `pro`, `business` (the plan ids) and `trial` (an organisation whose subscription is TRIALING). Each
has a daily and a monthly number of analyses and a monthly AI cost cap in USD for analyses on the platform's AI (the
organisation's provider). 0 analyses means "not included in the plan". BYOK analyses (the user's own key) count against
the analysis numbers and against the user's own BYOK caps, not against the cost cap.
"""
import copy
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.db.models import User

KEY = "chart_analyze_settings"
TIERS = ("free", "trial", "pro", "business")

DEFAULTS: Dict[str, Any] = {
    # Retention of images and results (owner brief: propose 30 days). Deleted rows keep their counters a little longer
    # (`counter_keep_days`) so a month's limit cannot be reset by deleting.
    "retention_days": 30,
    "counter_keep_days": 32,
    # Uploads and links
    "max_image_bytes": 5 * 1024 * 1024,
    "max_image_width": 4096,
    "max_image_height": 4096,
    "max_image_pixels": 4096 * 4096,       # the decompression-bomb limit, checked from the header before decoding
    "link_connect_timeout_seconds": 5,
    "link_read_timeout_seconds": 10,
    "link_total_timeout_seconds": 20,
    "link_max_redirects": 3,
    # Requests per user per minute on upload, link and analyze together
    "rate_limit_per_minute": 6,
    # The AI call
    "max_output_tokens": 3000,
    "estimate_input_tokens": 3000,          # the pre-call estimate used against the caps (the real cost is metered)
    "estimate_output_tokens": 2500,
    # Plan limits (analyses a day, analyses a month, the monthly AI cost cap in USD on the platform's AI)
    "limits": {
        "free": {"daily": 0, "monthly": 0, "cost_cap_usd": 0.0},
        "trial": {"daily": 2, "monthly": 10, "cost_cap_usd": 1.0},
        "pro": {"daily": 10, "monthly": 150, "cost_cap_usd": 10.0},
        "business": {"daily": 50, "monthly": 1000, "cost_cap_usd": 60.0},
    },
}

# Lower and upper bounds an admin may set (a typo can neither switch off a safety check nor open it wide).
BOUNDS: Dict[str, tuple] = {
    "retention_days": (1, 3650),
    "counter_keep_days": (32, 3650),
    "max_image_bytes": (64 * 1024, 8 * 1024 * 1024),      # at most the API's request body limit (MAX_REQUEST_BODY_BYTES)
    "max_image_width": (256, 16384),
    "max_image_height": (256, 16384),
    "max_image_pixels": (256 * 256, 16384 * 16384),
    "link_connect_timeout_seconds": (1, 30),
    "link_read_timeout_seconds": (1, 60),
    "link_total_timeout_seconds": (2, 120),
    "link_max_redirects": (0, 5),
    "rate_limit_per_minute": (1, 600),
    "max_output_tokens": (256, 16000),
    "estimate_input_tokens": (0, 200000),
    "estimate_output_tokens": (0, 200000),
}
LIMIT_BOUNDS: Dict[str, tuple] = {"daily": (0, 100000), "monthly": (0, 1000000), "cost_cap_usd": (0.0, 100000.0)}


class SettingsError(ValueError):
    """A refused admin change; the message says which value and the allowed range."""


def _number(value: Any, default: Any) -> Any:
    try:
        return type(default)(value)
    except (TypeError, ValueError):
        return default


def merge(stored: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The effective settings: the defaults, overlaid with every stored value that is well formed and in bounds."""
    out = copy.deepcopy(DEFAULTS)
    stored = stored if isinstance(stored, dict) else {}
    for key, (low, high) in BOUNDS.items():
        if key in stored:
            value = _number(stored[key], DEFAULTS[key])
            if low <= value <= high:
                out[key] = value
    raw_limits = stored.get("limits")
    limits: Dict[str, Any] = raw_limits if isinstance(raw_limits, dict) else {}
    for tier in TIERS:
        raw_row = limits.get(tier)
        row: Dict[str, Any] = raw_row if isinstance(raw_row, dict) else {}
        for field, (low, high) in LIMIT_BOUNDS.items():
            if field in row:
                value = _number(row[field], DEFAULTS["limits"][tier][field])
                if low <= value <= high:
                    out["limits"][tier][field] = value
    return out


async def load(session: AsyncSession) -> Dict[str, Any]:
    from app.platform.controls import _get
    return merge(await _get(session, KEY))


def validate_update(current: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, Any]:
    """The new settings after `values` (a partial update), or SettingsError naming the first bad value."""
    out = copy.deepcopy(current)
    for key, value in (values or {}).items():
        if key == "limits":
            if not isinstance(value, dict):
                raise SettingsError("limits must be an object of plan tiers")
            for tier, row in value.items():
                if tier not in TIERS:
                    raise SettingsError(f"Unknown plan tier '{tier}' - known: {', '.join(TIERS)}")
                if not isinstance(row, dict):
                    raise SettingsError(f"limits.{tier} must be an object")
                for field, raw in row.items():
                    if field not in LIMIT_BOUNDS:
                        raise SettingsError(f"Unknown limit '{field}' - known: {', '.join(LIMIT_BOUNDS)}")
                    low, high = LIMIT_BOUNDS[field]
                    try:
                        number = type(DEFAULTS["limits"][tier][field])(raw)
                    except (TypeError, ValueError):
                        raise SettingsError(f"limits.{tier}.{field} must be a number") from None
                    if not low <= number <= high:
                        raise SettingsError(f"limits.{tier}.{field} must be between {low:g} and {high:g}")
                    out["limits"][tier][field] = number
            continue
        if key not in BOUNDS:
            raise SettingsError(f"Unknown setting '{key}' - known: {', '.join(sorted([*BOUNDS, 'limits']))}")
        low, high = BOUNDS[key]
        try:
            number = type(DEFAULTS[key])(value)
        except (TypeError, ValueError):
            raise SettingsError(f"{key} must be a number") from None
        if not low <= number <= high:
            raise SettingsError(f"{key} must be between {low:g} and {high:g}")
        out[key] = number
    if out["link_total_timeout_seconds"] < out["link_read_timeout_seconds"]:
        raise SettingsError("link_total_timeout_seconds cannot be below link_read_timeout_seconds")
    for tier in TIERS:
        if out["limits"][tier]["daily"] > out["limits"][tier]["monthly"]:
            raise SettingsError(f"limits.{tier}: the daily number cannot be above the monthly number")
    return out


async def save(session: AsyncSession, user: User, values: Dict[str, Any]) -> Dict[str, Any]:
    from app.platform.controls import _set
    new = validate_update(await load(session), values)
    await _set(session, KEY, new, user)
    await write_audit_log(session, None, user.id, "chart_analyze_settings_set", ", ".join(sorted(values or {})) or "-")
    await session.commit()
    return new
