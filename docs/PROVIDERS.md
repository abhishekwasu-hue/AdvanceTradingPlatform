# Providers (ADR-0016)

Every external capability sits behind a seam: a typed interface, a registry keyed by provider id, a capabilities
declaration, and a contract test every provider must pass. Adding a provider = a module + a registry entry + a green
contract suite; callers do not change. Status: **exists** (interface and providers in the code), **planned** (part that
delivers it).

| Seam | Interface | Capabilities (examples) | Providers | Contract test | Status |
|---|---|---|---|---|---|
| Execution venue (orders, positions, fills) | `app/brokers/base.py::BrokerInterface` (ADR-0002) | native stop / OCO, max tag length, order types, fractional qty | Upstox, Zerodha, Shoonya, Fyers, Angel One, Dhan, CoinDCX; next (v1.3 §3): Binance spot, Bybit / KuCoin / OKX, CoinSwitch PRO / ZebPay, Kraken later; IBKR (ADR-0018, TWS then Web API) | broker adapter tests -> `tests/contracts/test_venue_contract.py` (part E) | exists; contract suite planned |
| Market data (history, ticks, chains) | `app/lake/provider.py` (part B) | history depth, tick depth (L1/L5), option chain, as-of | broker-backed; next: one vendor (TrueData / GlobalDataFeed) | `tests/contracts/test_market_data_contract.py` | planned (B2) |
| Alt data (news, macro, sentiment) | `app/altdata/provider.py` | latency, coverage, licence | news feed (ADR-0012); next: macro calendar | `tests/contracts/test_altdata_contract.py` | partial (news feed exists) |
| ML pipeline (offline features -> model -> predictions) | `app/ml/pipeline.py` | inputs, horizon, retrain cadence | none yet | `tests/contracts/test_ml_contract.py` | planned (part H) |
| Notification channel | `app/alerts/channels.py` (today: channel types in `app/alerts/dispatcher.py`) | rich text, buttons, delivery receipt | Telegram, email, webhook (HMAC), push, SMS; next: WhatsApp, voice | `tests/contracts/test_notification_contract.py` | exists; contract suite planned (E) |
| Billing + metering | `app/billing/service.py::BillingProvider` (ADR-0007) | subscriptions, webhooks, refunds | manual, Razorpay; next: Stripe | billing tests | exists |
| LLM | `app/ai/providers.py::LLMProvider` (ADR-0006) | thinking, max tokens, caching | Anthropic, OpenAI, rule-based | provider tests | exists |
| Fundamentals | `app/fundamentals/providers/base.py::FundamentalDataProvider` | statements, ratios | NSE | fundamentals tests | exists |
| Cost model (per venue) | `CostModel` registry (ADR-0017; C2 BrokerageModel) | fees, taxes, funding | India (`india_costs.py`); next: crypto maker/taker, IBKR tiered | `tests/contracts/test_cost_contract.py` | planned |
| Compliance rule-set (per jurisdiction) | `app/compliance/rules.py` (part D) | rules, params, flags | IN-SEBI; next: crypto-IN, US | `tests/test_compliance_rules.py` | in part D PR #87 |

## Adding a provider
1. Implement the interface in `app/<area>/providers/<id>.py`; declare capabilities.
2. Register it (`REGISTRY["<id>"] = ...`); settings or tenant configuration selects it.
3. Run the seam's contract suite with the provider added to its parameter list; record any capability it lacks.
4. Add the row above and, for a venue, the rule-set and cost-model rows it needs (ADR-0017).

## Venue order and registry (v1.3 §3, ADR-0017, ADR-0018)
- **Crypto venues, in order:**
  1. CoinDCX spot (exists).
  2. Binance spot.
  3. Bybit / KuCoin / OKX. Perpetuals and options only for jurisdictions whose rule-set allows them.
  4. CoinSwitch PRO / ZebPay.
  5. Kraken, later.
- **Exchange registry.** One data row per exchange with these fields:
  - `fiu_registered: true/false`, plus `fiu_source` and `fiu_checked_on`;
  - the products offered (spot, perpetual, option);
  - the quote currencies.
- **India resident rule-set.** It allows only `fiu_registered: true` exchanges and only spot. A block names the rule.
  A registry row unchecked for longer than the rule-set's freshness window shows a warning (CR-1).
- **Tax.** A seam for the 30 % + 1 % TDS report (P2), on the `TaxModel` (ADR-0017). It produces reports only.
- **IBKR.**
  - `IBKRBroker` with an `IBKRTransport` seam: the TWS API through IB Gateway (paper, operator account) first, and
    the Web API (OAuth, per tenant) after vendor registration.
  - Design and runbook: [ADR-0018](adr/0018-ibkr-transports.md), [BROKERS_IBKR.md](BROKERS_IBKR.md).
