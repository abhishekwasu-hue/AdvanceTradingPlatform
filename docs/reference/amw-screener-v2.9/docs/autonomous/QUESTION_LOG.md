# Question log (owner input)

### Q-001 — Production market-data provider
Area: data · Status: PARTLY DECIDED (v2.4.0) · Priority: P1 · Owner decision needed: YES (for a shared feed)
Question: Which licensed/authorised NSE data source should production use?
Decision so far: owner chose **FYERS per user** (option A). Implemented in v2.4.0: each user connects their own FYERS app;
data is collected for and shown only to that user (FYERS API terms prohibit redistribution).
Still open: a single operator-paid feed for all users. Research (Oct 2026, docs/LIVE_DATA_FYERS.md): within ₹5–10k/month
only personal-use plans were found — Accelpix Pix Connect from ₹1,599/month + GST per segment; Global Datafeeds offers a
Realtime Exchange Snapshot API (1/2/5/10/15/30 min) but its terms limit data to a single subscriber's use; NSE requires
an agreement for redistribution. Owner action: request a display/redistribution licence quote from an authorised vendor.
Risk if incorrect: licence breach if one user's broker data were shown to others (prevented in code: per-user scoping).
Reversible: yes (provider interface; `src/feed/`).

### Q-002 — Plan limits and prices
Area: SaaS · Status: ASSUMPTION APPLIED · Priority: P2 · Owner decision needed: YES
Question: What limits and prices should Free/Pro/Premium/Enterprise have?
Assumption used: engineering defaults in config/settings.yaml `plans`; no prices anywhere in code.
Risk if incorrect: limits too tight/loose. Reversible: edit config. Files: config/settings.yaml, src/saas/entitlements.py.

### Q-003 — Official AMW Telegram bot
Area: notifications · Status: OWNER INPUT REQUIRED · Priority: P2 · Owner decision needed: YES
Question: Will AMW operate an official bot (token from @BotFather) and on which server?
Assumption used: optional; when `AMW_TELEGRAM_BOT_TOKEN` is unset only user-owned bots are offered.
Risk: none functional; users must create their own bot. Reversible: set the env var.

### Q-004 — Default desktop plan
Area: SaaS · Status: ASSUMPTION APPLIED · Priority: P3 · Owner decision needed: NO
Question: Which plan should the single local desktop user have?
Assumption used: existing behaviour kept (`pro`). Reversible: `UPDATE users SET plan='enterprise' WHERE username='local'`.

### Q-005 — Report retention
Area: storage · Status: ASSUMPTION APPLIED · Priority: P3 · Owner decision needed: YES
Question: How long should generated report files be kept?
Assumption used (v2.5.0): per-plan `report_retention_days` free 7 / pro 90 / premium 365 / enterprise unlimited and
`report_storage_mb` 50 / 1000 / 5000 / unlimited (config/settings.yaml). Reversible: edit config.

### Q-006 — Live broker execution
Area: trading · Status: OPEN · Priority: P3 · Owner decision needed: YES
Question: Will any live order integration ever be authorised?
Assumption used: none exists; analysis + paper only. No change made.

### Q-007 — FYERS live verification
Area: data · Status: OWNER ACTION REQUIRED · Priority: P1 · Owner decision needed: NO
Question: Can the owner run one live FYERS login and one full trading day before release?
Why: the integration was verified against a mocked FYERS API only (no account in the build environment). Confirm
response fields, token expiry time, index symbol names, rate limits and refresh-token availability.
Files: src/feed/fyers.py (facts listed in the module docstring).

### Q-008..Q-013 — decisions proposed in the gap analysis (assumptions applied in v2.6.0, all reversible)
Status: ASSUMPTION APPLIED · Owner decision needed: YES (confirm or change)
* Q-008 Unified spec — **extend the Strategy Lab IR** (no second engine). Files: src/scanner/spec.py, src/lab/ir.py.
* Q-009 Built-in strategies — **logic unchanged**; verified by an identical scan fingerprint.
* Q-010 Elliott Wave / Harmonic — **not implemented** (subjective; would need candidate-only labelling). Requests for
  them are reported as UNSUPPORTED by the NL builder.
* Q-011 NL builder without AI — **yes**, deterministic parser; AI drafting remains optional.
* Q-012 Sharing scope — **not implemented**; duplicate within the same user only. Needs owner choice (workspace vs public).
* Q-013 Options backtests — **not implemented** until an historical option-price source is chosen.
* RSI divergence default: RSI(14), confirmed pivots k=3, last two pivots, latest within 15 bars (shown to the user).

### Q-014 — API worker threads (P-002)
Area: performance · Status: OPEN · Priority: P2 · Owner decision needed: YES
Measured: 40 threads 15 req/s, 4 threads 27, 1 thread 72 (signal list, 20 concurrent clients). Fewer threads let one
slow synchronous request block others. Options: (A) keep 40 (current), (B) 4 threads, (C) move slow endpoints to
background jobs and then use 1–2 threads, (D) multiple processes (needs a scheduler lease first). Recommended: C, then
re-measure. Files: api_server.py (`AMW_API_THREADS`).

### Q-012 update (v2.7.0)
Assumption applied: user-to-user COPY of a scanner's rules (no signals, data, versions or credentials; recipient
publishes; audited). Workspace/team sharing and a public template marketplace remain owner decisions.

### Q-014 update (v2.8.0)
Option D is now available and measured (`AMW_API_WORKERS=4`: signal list 13.9 → 196.6 req/s, 0 errors) and step 1
of option C (opt-in background variants of slow endpoints) is implemented. Remaining owner decision: the production
default for workers, and whether background mode should become the default (that would change API responses).

### Resolved by the owner (2026-10-19)
A-005, Q-014, Q-010, Q-012, Q-002 (structure; values stay provisional), Q-005 (provisional defaults) — implemented in
v2.9.0 (DECISION_LOG D-032..D-037).

### Still open (business / external)
* Q-002b — final prices and approved plan limits (no billing is active).
* Q-001 — licensed shared data feed; enables production failover.
* Q-007 — FYERS live trading-day verification; AI and Telegram live checks.
* Q-013 — historical option data source for options backtests.
* Q-015 — legal retention obligations (which reports must be held, for how long) - legal hold mechanism exists.

