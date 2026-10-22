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
