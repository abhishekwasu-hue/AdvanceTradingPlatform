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
