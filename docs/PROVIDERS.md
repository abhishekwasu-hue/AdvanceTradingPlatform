# Providers (ADR-0016)

Every external capability sits behind a seam: a typed interface, a registry keyed by provider id, a capabilities
declaration, and a contract test every provider must pass. Adding a provider = a module + a registry entry + a green
contract suite; callers do not change. Status: **exists** (interface and providers in the code), **planned** (part that
delivers it).

| Seam | Interface | Capabilities (examples) | Providers | Contract test | Status |
|---|---|---|---|---|---|
| Execution venue (orders, positions, fills) | `app/brokers/base.py::BrokerInterface` (ADR-0002) | native stop / OCO, max tag length, order types, fractional qty | Upstox, Zerodha, Shoonya, Fyers, Angel One, Dhan; next: CoinDCX (v1.3), Binance, Kraken, IBKR | broker adapter tests -> `tests/contracts/test_venue_contract.py` (part E) | exists; contract suite planned |
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
