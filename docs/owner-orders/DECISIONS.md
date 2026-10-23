# Owner decisions (Abhi) — append only

| Date | Decision |
|---|---|
| 2026-10-21 | Production domain: https://aialgodesk.tech (www redirects to the apex). |
| 2026-10-21 | The broker connect is simple: Broker is the first sidebar tab; the Upstox Analytics Token is the default; no OAuth by default. |
| 2026-10-21 | Exactly two groups: USER and ADMIN. No Simple / Pro split among traders; beginners learn through the page tours. |
| 2026-10-21 | Flags turned OFF by the owner: self_signup, scan_auto_paper, marketplace, public_api. ui_v2 ON. |
| 2026-10-21 | Copilot = a professional trader's brain (8 jobs); the indicator regime becomes an admin diagnostic only. |
| 2026-10-21 | Default watchlists in three separate groups: Indices / Index stocks / Sectors (overrides UI4C-1). |
| 2026-10-22 | The Strategy Lab threat model + ADR-0027 (AMW-18) approved as the design; G-LAB still gates each code mode. Pine first, Python next; SQL / Lua / JS / TS / R deferred. |
| 2026-10-22 | Idea → code: the AI may write code for every Lab mode, including Python, under the sandbox and approval rules (overrides AMW_PORT C-3). The same applies to idea → scan. |
| 2026-10-22 | The engine only generates signals; execution is the user's dashboard settings. Signal text in structure words; area ranges only on the user's own signals; no names / prices on public surfaces. |
| 2026-10-22 | The marketplace is the last phase (about six months out). |
| 2026-10-22 | The idea library: method templates only, white box, honest provenance, no performance claims. "Opening range" dropped (clock-based); options construction stays in the user's settings. |
| 2026-10-22 | The strategy template library (price action, breakout, breakout + retest, indicators) as specified in ATP_SESSION_STRATEGY_TEMPLATES.md; its section 0 corrections are binding. |
| 2026-10-22 | The session plan (handover items 0–13, phases 0–13) is approved; work starts in the given order, several items in parallel. |
| 2026-10-22 | One `claude/<name>` branch per item, one PR per item. |
| 2026-10-22 | Google sign-in: the button stays hidden until the owner adds the OAuth credentials. |
| 2026-10-22 | Broker logos: initials until official permission. |
| 2026-10-22 | Strategy Lab runner server: sizing and monthly cost go to OPEN_QUESTIONS; Pine continues meanwhile. |
| 2026-10-22 | Migrations are announced with the deploy line; the owner runs them after 15:30 IST. |
| 2026-10-22 | Wave / correction templates (family G, G1–G12) as specified in ATP_SESSION_WAVE_TEMPLATES.md: every style choice a user parameter; generic method only (no private Trade method); an independent spec review first; the templates spec section 0 (R1–R10) wins where they differ. |
| 2026-10-22 | Finished PRs (CI green apart from checks red on main too) are merged without asking again; the owner runs the deploy line (migrations after 15:30 IST). |
| 2026-10-22 | While no trade runs in ATP (Phase 0, PAPER), a deploy with a migration may run during NSE hours with `MIGRATION_FORCE=1`: `su - atp -c "MIGRATION_FORCE=1 /opt/atp/deploy/atp.sh deploy && /opt/atp/deploy/atp.sh health"`. Revisit when PAPER bots or LIVE run during market hours. |
| 2026-10-22 | No change without discussion: a plan first, then work only on the owner's go-ahead; a PR merges only after CI is green and the owner says "Merge" (replaces "merge without asking again"). |
| 2026-10-22 | The plan after the 22 Oct merges, in this order: step 1 owner-orders docs, the LD-9 PR, the UX 1-2 and 4-5 PRs; step 2 the unfinished F&O hub + futures chain, Copilot jobs 3-5, Analyze a chart A3; step 3 MO2 part A, the admin console, the F&O remaining slices, MO4, the Strategy Lab, the idea library, the templates, the wave templates. |
| 2026-10-22 | The Settings redesign (ATP_SESSION_SETTINGS_REDESIGN.md) is approved: Settings as a short index with status, one AI key for every AI feature (the user key, then the tenant default, then the rule-based fallback; this unifies the Copilot and Vision keys, approved under CLAUDE.md §8), My trading defaults as pre-fill only, the Dashboard setup-health strip. No data deletion: both key tables stay. Behind `ui_v2`; an independent spec review first; build order A, B, D, C, one draft PR per part. |
| 2026-10-23 | Deploys without a migration use the plain deploy line (no `MIGRATION_FORCE=1`), run by the owner after 15:30 IST; before a deploy the full backend suite and the CI checks run on main and the result is reported. |
| 2026-10-23 | CP6-15 is a bug: the losses-in-a-row pause must end. A user risk setting "after the losses-in-a-row limit: pause until the next session (default) / until I resume manually", identical in the live RiskManager / worker and both backtest engines, with a parity test. |
| 2026-10-23 | FNO-8B-28: liquidity stays comparative by default (no fixed number); an optional user override (max bid-ask %, min OI), OFF by default. FNO-8B-31: the journal chart stays a redraw on tap (nothing stored), plus a "Download image" button. |
| 2026-10-23 | LAB-18: server-side scan versions (AMW-7 `screen_versions`), an additive migration after 15:30 IST; the device history migrates on the first sync. |
| 2026-10-23 | Exits as settings and View as user are approved (additive migrations, after 15:30 IST). LAB-18, exits as settings and View as user ship as one migration block. |
| 2026-10-23 | FNO-4-10 chart drag of SL / target: approved for PAPER only, behind a flag, with a confirm step, the order trail and the risk checks, never blocking an exit (ADR-0004). |
| 2026-10-23 | The Python runner server is deferred; Pine + ScreenQL first. |
| 2026-10-23 | Prices / trial / referral: everything admin-configurable with no hard-coded amounts; billing stays OFF; the owner sets the prices before launch. |
| 2026-10-23 | TPL-F7: the G12 pack-composition proposal is approved; G12 is built last. TPL-Q18: the old trendline retest stays byte-identical; the projected line is a new option, used by C4. |
| 2026-10-23 | TPL-F6 is a bug: equal high / low handling becomes mirror-symmetric in DowTrend and the reversal check; the count of existing signals that change on a sample is reported before / after. |
| 2026-10-23 | Order of work (23 Oct): the full-suite check, CP6-15, TPL-F6, the migrations batch, FNO-4-10, then the rest. One draft PR each, self-review, tests, screenshots for UI. |
