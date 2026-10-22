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
