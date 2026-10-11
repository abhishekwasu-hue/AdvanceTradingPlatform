# Architecture Decision Records

Short, dated records of the decisions that shape the platform (master prompt section 55). One
file per decision; a superseded record stays in place with a pointer to its replacement.

Format: **Context** (what forced a choice), **Decision** (what we do), **Consequences** (what it
costs and what it buys), **Status**.

| # | Decision | Status |
|---|----------|--------|
| [0001](0001-row-level-multi-tenancy.md) | Row-level multi-tenancy with a server-derived `tenant_id` | accepted |
| [0002](0002-broker-abstraction.md) | One `BrokerInterface`, adapters per broker, credentials never leave the tenant | accepted |
| [0003](0003-risk-hierarchy-strictest-wins.md) | Eight-scope risk hierarchy, strictest limit wins, append-only risk events | accepted |
| [0004](0004-safety-rules-exits-never-blocked.md) | Entry refusals are cheap and many; exits are never blocked | accepted |
| [0005](0005-declarative-strategy-dsl.md) | One declarative strategy DSL for builder, backtest, paper and live | accepted |
| [0006](0006-ai-holds-no-credentials-acts-only-on-approval.md) | AI providers hold no credentials and never act without approval | accepted |
| [0007](0007-billing-provider-seam.md) | Billing behind a provider seam; the gateway's secrets are the operator's | accepted |
| [0008](0008-envelope-encryption-per-tenant-keys.md) | Envelope encryption: one wrapped data key per tenant | accepted |
| [0009](0009-scopes-over-roles.md) | Fine-grained scopes layered over roles, not replacing them | accepted |
| [0010](0010-worker-single-loop-with-lock.md) | One trading worker loop per replica set, Redis lock, market-calendar gated | accepted |
| [0011](0011-production-hosting-first-paper-days.md) | One 2 vCPU / 4 GB droplet with Caddy and an off-site backup copy for the first PAPER days; a separate broker app | accepted |
| [0012](0012-news-feed-shared-ingest-tenant-classification.md) | News feed: shared ingest of official feeds, classification with the organisation's own key, corroborated proposals | accepted |
| [0025](0025-options-strategy-builder.md) | The Options Strategy Builder: the Trade repo's logic ported as pure functions with golden parity; instrument numbers as parameters; hedge first; never places an order | proposed |
| [0019](0019-agent-tools-and-loop.md) | Copilot agent: typed read-only tools, a bounded loop, an answer contract, proposals only | provisional |
| [0020](0020-ai-evals-and-governance.md) | AI evals in CI and AI governance: golden sets per prompt version, a model registry, a kill switch | provisional |
| [0013](0013-market-data-lake.md) | Market data lake: TimescaleDB hot tier + Parquet/DuckDB cold tier, one as-of query interface | provisional |
| 0014, 0015 | reserved: event bus / queue (part E), Trade engine port interface (part G) | - |
| [0016](0016-provider-seams.md) | Provider seams: typed interfaces, registries, capabilities and contract tests for every external capability | provisional |
| [0017](0017-global-first.md) | Global-first instrument, time, money, cost and rules model; India is the first configuration | provisional |
| [0018](0018-ibkr-transports.md) | IBKR behind `BrokerInterface` with a transport seam: TWS API via IB Gateway (paper, operator account) now, Web API (OAuth, per tenant) after vendor registration | provisional |
| [0021](0021-screener-engine.md) | Screener engine: one typed AST (ScreenQL) over Factor/Filter/Classifier primitives, server data only | provisional |
| [0022](0022-notification-service.md) | Notification Service: grow the existing alert outbox, do not build a second one | provisional |
