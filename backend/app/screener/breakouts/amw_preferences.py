"""Validated owner-scoped preferences for informational AMW breakout scans."""

from dataclasses import asdict, fields
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import InitErrorDetails, PydanticCustomError

from app.price_action.zones.store import normalise_key
from app.screener.breakouts.amw_breakout import Settings

NAMESPACE = "amw_breakout"
_RULE_FIELDS = frozenset(field.name for field in fields(Settings))


def _error(path: tuple[str | int, ...], message: str) -> InitErrorDetails:
    return {"loc": path, "type": PydanticCustomError("breakout_preference", message), "input": None}


def _ordered_errors(rule: dict[str, Any], index: int) -> list[InitErrorDetails]:
    out = []
    for high, low, message in (
        ("zone_pass", "zone_block", "The pass distance must exceed the block distance."),
        ("max_extension", "buffer", "Maximum extension must exceed the breakout buffer."),
    ):
        upper, lower = (rule.get(key, getattr(Settings, key)) for key in (high, low))
        if type(upper) in (int, float) and type(lower) in (int, float) and upper <= lower:
            out.append(_error(("rules", index, high), message))
    return out


def _rule_error(rule: dict[str, Any], index: int, exc: Exception) -> list[InitErrorDetails]:
    prefix = ("rules", index)
    message = str(exc)
    numeric = message.removeprefix("Invalid ")
    if numeric in _RULE_FIELDS:
        return [_error((*prefix, numeric), "Choose a value in this field's allowed range.")]
    if message == "Invalid ordered zone or extension thresholds":
        return _ordered_errors(rule, index) or [_error(prefix, "Check the zone and extension thresholds.")]
    if not set(rule) <= _RULE_FIELDS:
        return [_error(prefix, "Remove unsupported profile fields.")]
    field = next((key for key in _RULE_FIELDS if message.startswith(f"{key} ")), None)
    known = {
        "Unsupported breakout profile or direction": "direction",
        "Unsupported acceptance mode. AUTO_SELECT is disabled.": "acceptance",
        "Unsupported divergence mode": "divergence_mode",
        "Unsupported divergence types": "divergence_types",
        "Unsupported candle quality mode": "candle_mode",
        "BO-02 requires compatible divergence": "divergence_mode",
        "BO-03 requires retest confirmation": "acceptance",
        "BO-04 requires opposite divergence blocking": "divergence_mode",
        "Unsupported timeframe or independent confirmation clock": "breakout_tf",
        "Unsupported breakout source": "sources",
        "Previous-session levels require an intraday breakout timeframe": "breakout_tf",
        "Follow-through exceeds setup validity": "follow_bars",
        "Retest requires a subsequent candle": "max_age",
        "Unsupported B1 range mode": "range_mode",
    }
    if message == "Required sources must have at least one timeframe":
        required = ["demand_tfs", "supply_tfs"]
        if rule.get("divergence_mode") != "OFF" and (rule.get("profile") != "BO-01" or rule.get("divergence_mode")):
            required.append("rsi_tfs")
        return [_error((*prefix, key), "Choose at least one timeframe.") for key in required if rule.get(key) == []] or [
            _error(prefix, "Choose the required timeframes."),
        ]
    if field or message in known:
        return [_error((*prefix, field or known[message]), f"{message.rstrip('.')}.")]
    return [_error(prefix, "Check the values for this breakout profile.")]


class BreakoutPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool = False
    instruments: list[str] = Field(default_factory=list, max_length=10)
    rules: list[dict[str, Any]] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_setup(self):
        errors = []
        keys = []
        for index, value in enumerate(self.instruments):
            try:
                key = normalise_key(value)
            except ValueError:
                key = ""
            if not key.startswith(("NSE:", "NSE_INDEX:")):
                errors.append(_error(("instruments", index), "Choose an NSE share or index."))
            elif key in keys:
                errors.append(_error(("instruments", index), "This instrument is already in the scan list."))
            else:
                keys.append(key)
        configs = []
        for index, rule in enumerate(self.rules):
            try:
                config = Settings(**rule)
            except (AttributeError, TypeError, ValueError) as exc:
                errors.extend(_rule_error(rule, index, exc))
            else:
                if any(item.profile == config.profile for item in configs):
                    errors.append(_error(("rules", index, "profile"), "A profile can appear only once."))
                else:
                    configs.append(config)
        if self.enabled and not self.instruments:
            errors.append(_error(("instruments",), "Choose at least one instrument to enable scans."))
        if self.enabled and not self.rules:
            errors.append(_error(("rules",), "Choose at least one breakout profile to enable scans."))
        if errors:
            raise ValidationError.from_exception_data(type(self).__name__, errors)
        self.instruments = keys
        self.rules = [asdict(cfg) for cfg in configs]
        return self
