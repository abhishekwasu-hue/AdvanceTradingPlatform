"""P0.8-C / C5: what a provider call costs, in USD per million tokens, so every call can be metered in money.

The table holds the published list prices of the models the platform may run (input, output, cache read); a cache
write is billed at 1.25x input. `AI_MODEL_PRICES_JSON` (`{"model-prefix": [input, output, cache_read]}`) adds or
overrides rows without a release - prices move and new models appear. A model the table does not know is priced at
the most expensive row of its provider and flagged `estimated`, so an unknown model never under-reports spend.
`AI_USD_INR_RATE` converts to rupees for the plan budget and the Settings card.
"""
import json
import logging
import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

Price = Tuple[float, float, float]      # USD per million tokens: input, output, cache read
CACHE_WRITE_MULTIPLIER = 1.25

_LIST_PRICES: Dict[str, Price] = {
    # Anthropic (first-party API list prices)
    "claude-fable-5-1": (10.0, 50.0, 0.25), "claude-fable-5": (10.0, 50.0, 1.0), "claude-mythos-5-1": (10.0, 50.0, 0.25),
    "claude-opus-5-5": (4.0, 20.0, 0.20), "claude-opus-5": (5.0, 25.0, 0.50), "claude-opus-4-8": (5.0, 25.0, 0.50),
    "claude-opus-4-7": (5.0, 25.0, 0.50), "claude-opus-4-6": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20), "claude-sonnet-5": (2.0, 10.0, 0.20), "claude-sonnet-4-6": (3.0, 15.0, 0.30),
    "claude-haiku-5-5": (1.0, 5.0, 0.10), "claude-haiku-4-5": (1.0, 5.0, 0.10),
    # OpenAI
    "gpt-4.1-mini": (0.4, 1.6, 0.1), "gpt-4.1-nano": (0.1, 0.4, 0.025), "gpt-4.1": (2.0, 8.0, 0.5),
    "gpt-4o-mini": (0.15, 0.6, 0.075), "gpt-4o": (2.5, 10.0, 1.25),
    "o4-mini": (1.1, 4.4, 0.275), "o3-mini": (1.1, 4.4, 0.55), "o3-pro": (20.0, 80.0, 20.0), "o3": (2.0, 8.0, 0.5), "o1": (15.0, 60.0, 7.5),
    "gpt-5-mini": (0.25, 2.0, 0.025), "gpt-5-nano": (0.05, 0.4, 0.005), "gpt-5-pro": (15.0, 120.0, 15.0), "gpt-5": (1.25, 10.0, 0.125),
}
_PROVIDER_PREFIX = {"anthropic": "claude-", "openai": ("gpt-", "o1", "o3", "o4")}
_prices: Dict[str, Price] = {}


def reload() -> None:
    """Rebuild the table from the list prices plus `AI_MODEL_PRICES_JSON`."""
    table = dict(_LIST_PRICES)
    raw = os.environ.get("AI_MODEL_PRICES_JSON", "").strip()
    if raw:
        try:
            for model, row in json.loads(raw).items():
                table[str(model)] = (float(row[0]), float(row[1]), float(row[2]) if len(row) > 2 else float(row[0]) / 10.0)
        except (ValueError, TypeError, IndexError, AttributeError) as exc:
            logger.warning("AI_MODEL_PRICES_JSON ignored: %s", exc)
    _prices.clear()
    _prices.update(table)


reload()


@dataclass(frozen=True)
class Cost:
    amount: float          # USD
    estimated: bool        # True when the model was not in the table
    price: Price


def price_for(provider: str, model: str) -> Tuple[Optional[Price], bool]:
    """(price, estimated): the longest table prefix the model id starts with (`claude-opus-5-5-20260401` matches
    `claude-opus-5-5`), else the provider's most expensive row."""
    model = (model or "").lower()
    if provider == "rule_based" or not model:
        return (0.0, 0.0, 0.0), False
    best = max((name for name in _prices if model.startswith(name.lower())), key=len, default=None)
    if best is not None:
        return _prices[best], False
    prefixes = _PROVIDER_PREFIX.get(provider, ())
    rows = [p for name, p in _prices.items() if name.startswith(prefixes)] if prefixes else list(_prices.values())
    if not rows:
        rows = list(_prices.values())
    return max(rows, key=lambda p: p[0] + p[1]), True


def cost_usd(provider: str, model: str, *, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> Cost:
    price, estimated = price_for(provider, model)
    assert price is not None
    inp, out, cached = price
    amount = (max(0, input_tokens) * inp + max(0, output_tokens) * out + max(0, cache_read_tokens) * cached
              + max(0, cache_write_tokens) * inp * CACHE_WRITE_MULTIPLIER) / 1_000_000.0
    return Cost(amount=round(amount, 8), estimated=estimated, price=price)


def usd_inr_rate() -> float:
    try:
        return max(1.0, float(os.environ.get("AI_USD_INR_RATE", "84")))
    except ValueError:
        return 84.0


def usd_to_inr(usd: float) -> float:
    return round(float(usd) * usd_inr_rate(), 4)


def inr_to_usd(inr: float) -> float:
    return round(float(inr) / usd_inr_rate(), 8)
