# Part D - SEBI / NSE retail-algo framework: design note

Spec: MASTER SPEC v1 §5 (D1-D8). Status: **design** (this note is the first PR of part D; code follows in small PRs).
v1.2 asks for a config-driven jurisdiction rules engine in which SEBI is one rule-set: everything here is written as
**rules with parameters in config**, not code paths keyed on "India", so the global rule-sets (v1.2/v1.3) slot in
beside it. Nothing here changes a LIVE default: every new refusal is behind a flag that is off until the owner turns it on.

## What exists already (main, fd217a4)

| Item | Already in the code | Gap |
|---|---|---|
| D1 Algo ID on every order | `app/execution/tagging.py` builds `<algo id>-<strategy>-<leg>` within each broker's tag length; `tenants.algo_id` (Team page); `orders.algo_tag` stored; `ALGO_ID_REQUIRED_FOR_LIVE` refuses LIVE without one; readiness item `algo_id` | per-broker **format** (not only length) in the adapter; the "generic id below the OPS threshold vs registered id above it" rule; the tag in the audit trail row, not only the order row |
| D2 OPS throttle | `app/brokers/rate_budget.py`: per-tenant, per-broker token buckets (per second + per minute) on every call; circuit breaker counts 429s | the regulatory unit is **orders per second per client per exchange/segment** (modify/cancel/SL included), not API calls per key; no 429 back-off with alert; exits are not prioritised over entries when the bucket is short |
| D3 Order types | `order_style` MARKET / PROTECTED_LIMIT per deployment; Zerodha/Upstox `market_protection` (G-LIVE) | a per-broker **order-type policy**: algo MARKET refused or mapped to LIMIT/MPP, IOC/AMO refused where the broker forbids them for algos - config, checked before placement |
| D4 Static IP | runbook checklist per broker (`docs/DEPLOY_HOSTINGER_MR.md` §6) | per-tenant egress IP model (primary + backup, registered-at, change history), weekly-change rule, Settings UI, shared-IP warning, readiness item |
| D5 Daily login | Upstox OAuth; Fyers/Kite login-url + login-code; `TOKEN_DAILY_EXPIRY_IST`; TOKEN_EXPIRED CRITICAL | one generic daily-login flow for every adapter (no refresh-token dependence), a pre-open login reminder, expiry handling per broker in config |
| D6 Go-live checklist | `app/platform/readiness.py` (LIVE readiness items), DPDP consent + provider card (P0.8-D), AI disclaimers | vendor empanelment items (ISO 27001/SOC 2, CERT-In VAPT, incident register), white-box/black-box class per strategy, RA flag for AI trade ideas + AI-usage disclosure + fee-cap note, DPDP 72-h breach / 1-year logs / erasure / DPO |
| D7 Risk engine | risk hierarchy (8 scopes), margin pre-check, lot sizes from the instrument master, `india_costs.py` | FutEq / OI (MWPL) limits, expiry-day extra margin, lot sizes **as-of** (needs part B's instrument_master_versions), charges config versioned |
| D8 Docs | `docs/GO_LIVE_MR.md`, `docs/OPERATIONS.md` | `docs/COMPLIANCE_IN.md`: rule -> code -> test table |

## Design

### The rules engine (shared with v1.2)
`app/compliance/rules.py`: a `RuleSet` (id, jurisdiction, version, effective_from) holding typed rule parameters
loaded from `config/compliance/<id>.yaml`; the active set per tenant comes from its jurisdiction (default `IN-SEBI`).
Every check returns `Allowed | Refused(reason, rule_id)`, is logged with the rule id, and counts in a metric
`atp_compliance_refusals_total{rule}`. SEBI is the first file; crypto/IBKR sets come later with the same shape.

### D1 algo id
- Adapter-level `format_algo_tag(algo_id, strategy, leg)` with the broker's charset + length (config), default = today's.
- Rule `algo_id.required_above_ops`: below the exchange's OPS threshold a generic broker algo id may be used; above it
  the registered id is required. Threshold and generic id are config (no number in code).
- The tag goes into the audit-log entry of every order event.

### D2 OPS throttle (exits first)
- `OrderThrottle` keyed (tenant, broker account, exchange, segment): a token bucket at `ops_per_second` (config,
  default 10) counting place / modify / cancel / SL. Separate from the API rate budget (which stays).
- Two lanes: **exit lane** (exits, stop placement/modify, cancels of protective orders) is served before the
  **entry lane**; an entry waits or is dropped with a reason, an exit only waits (ADR-0004: never refused).
- Broker 429 -> exponential back-off (config) + WARNING; three in a window -> CRITICAL.
- Test: a burst of entries and one exit under a full bucket -> the exit goes first; entries above the rate are
  refused with `ops_throttle`; no exit is ever refused.

### D3 order-type policy
Per-broker config: `market_for_algo: refuse | map_to_limit | map_to_mpp`, `allowed_validity: [DAY]`,
`amo_allowed`, `ioc_allowed`. Checked in the router before placement; a mapped order records the mapping.
Default policy for every broker = today's behaviour (no change) until the owner switches it.

### D4 static IP
Tables `egress_ips` (tenant, broker account, ip, role primary|backup, registered_at, verified_at) and
`egress_ip_changes`; rule `static_ip.max_changes_per_week` (config); readiness item "the server's egress IP is the one
registered with each LIVE broker" (compares `status.sh`'s egress IP source with the table); shared-IP warning when two
tenants register the same IP. Settings UI card. LIVE entry refused only when the flag `STATIC_IP_REQUIRED_FOR_LIVE` is on.

### D5 daily login
`DailyLogin` protocol per adapter: `login_url()`, `complete(code|callback)`, `expires_at()`; reminder notification at a
configured pre-open time when a LIVE deployment's token will not last the session.

### D6 checklist, D7 risk, D8 docs
- D6: readiness items grouped "vendor", "strategy class", "AI", "DPDP", each with evidence fields; strategy class
  (white-box / black-box) stored on the strategy version; AI trade-idea publishing behind an RA flag (off).
- D7: FutEq / MWPL limits as risk-hierarchy rules fed by data (part B supplies OI and MWPL); expiry-day margin
  multiplier in config; lot size as-of once part B lands (until then: today's master).
- D8: `docs/COMPLIANCE_IN.md`, one row per rule: circular reference, rule id, code, test, flag.

## Order of work (each a PR <= ~800 lines)
1. This note + `docs/COMPLIANCE_IN.md` skeleton + rules engine skeleton with the SEBI rule file (no behaviour change).
2. D2 throttle with exit lane + 429 back-off (flag `OPS_THROTTLE_ENABLED`, off).
3. D3 order-type policy (default = current behaviour).
4. D4 static IP model + readiness + UI card.
5. D1 format/threshold + audit; D5 generic daily login + reminder.
6. D6 checklist items; D7 FutEq/MWPL (after part B's OI data).

## Test plan
- Unit: each rule with crafted parameters (allowed/refused, reason, rule id); throttle ordering under a clock fake;
  429 back-off schedule; order-type mapping per broker config; egress IP weekly-change counter.
- Integration: worker cycle with a full OPS bucket - exits placed, entries refused with `ops_throttle`, nothing LIVE
  refused while the flag is off; readiness lists the new items.
- Compliance doc test: every rule id in the SEBI rule file appears in `docs/COMPLIANCE_IN.md` with a test name that exists.

## Status
| # | Item | Status |
|---|---|---|
| D1 | algo id format + threshold + audit | done: `app/compliance/algo_id.py` (per-broker format, generic vs registered id, `algo_order` audit rows) |
| D2 | OPS throttle, exits first, 429 back-off | planned (PR 2) |
| D3 | order-type policy per broker | planned (PR 3) |
| D4 | static IP model + UI + checklist | runbook only; model planned (PR 4) |
| D5 | generic daily login + reminder | done: `app/brokers/login_reminder.py` (login method per broker, pre-open reminder from the worker) |
| D6 | go-live checklist items | partly exists; planned (PR 6) |
| D7 | FutEq / MWPL, expiry-day margin, lot as-of | needs part B data; planned (PR 6) |
| D8 | COMPLIANCE_IN.md + tests | skeleton in PR 1 |

Open questions: OPEN_QUESTIONS D-1 (default OPS when a broker publishes none), D-2 (generic vs registered algo id
threshold source).
