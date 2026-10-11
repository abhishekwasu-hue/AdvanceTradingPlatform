# ADR-0016: Provider seams - every external capability behind a typed interface with contract tests

**Date:** 2026-10-10 · **Status:** provisional · **Parts:** B, E, H (MASTER SPEC v1.1)

## Context
The platform is for any trader's method; the Trade engine is one plugin among many (v1.1). Today the seams exist but
are uneven: `BrokerInterface` (ADR-0002) is a full interface with per-broker adapters; `LLMProvider` and
`BillingProvider` are protocols; fundamentals have `FundamentalDataProvider`; market data has no interface (the broker
is the market data source); notifications are channel types inside one module. v1.1 asks for pluggable
MarketDataProvider, AltDataProvider, MLPipeline, ExecutionVenue (IBKR / Alpaca / crypto, FIX, low-latency),
Notification (voice / WhatsApp) and billing metering + plan flags - each swappable without touching callers.

## Decision (provisional)
1. **One pattern for every seam**: a `typing.Protocol` (or ABC where shared behaviour exists) in `app/<area>/provider.py`,
   a registry keyed by provider id, provider selection from settings (platform) or tenant configuration (per tenant),
   and a **capabilities** object the provider declares (e.g. `{"ticks": True, "depth": 5, "history_days": 2000}`) so
   callers ask what is supported instead of catching NotImplementedError.
2. **Contract tests**: each seam has `tests/contracts/test_<seam>_contract.py`, a parametrised suite every registered
   provider must pass against a recorded or fake transport (the broker adapter tests become the first instance).
3. **Seams** (and their first two providers):
   | Seam | Interface | Providers now / next |
   |---|---|---|
   | MarketDataProvider (history, ticks, chains) | `app/lake/provider.py` | broker-backed (existing adapters) / one vendor (TrueData or GlobalDataFeed) |
   | AltDataProvider (news, macro, sentiment) | `app/altdata/provider.py` | the news feed (ADR-0012) / macro calendar |
   | MLPipeline (features -> model -> predictions, offline) | `app/ml/pipeline.py` | none yet (part H) |
   | ExecutionVenue (orders, positions, fills) | `BrokerInterface` renamed in docs, unchanged in code | Indian brokers / CoinDCX (v1.3), then Binance, Kraken, IBKR |
   | Notification channel | `app/alerts/channels.py` | Telegram, email, webhook, push, SMS / WhatsApp, voice |
   | Billing provider + metering | `BillingProvider` (ADR-0007) | manual, Razorpay / Stripe |
   | LLM provider | `LLMProvider` (ADR-0006) | Anthropic, OpenAI, rule-based |
4. `docs/PROVIDERS.md` lists every seam, its interface, capabilities, registered providers, how to add one, and the
   contract test to pass.

## Consequences
- A new provider is a new module + a registry line + a green contract suite; callers do not change.
- Some existing modules move behind an interface (market data, notifications); done inside the part that needs them
  (B for market data, E for venues/notifications), never as a big-bang refactor.
- Capabilities make degraded modes explicit (a venue without native stop orders gets the platform's stop guard).
