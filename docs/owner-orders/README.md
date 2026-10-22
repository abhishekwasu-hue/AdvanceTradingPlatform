# Owner orders — index and status

Everything Abhi has ordered for ATP lives in this folder. Read CLAUDE.md first. Update the status column in the same PR
that changes an item. Never delete a row; mark it done / superseded with the PR.

| # | Order | File | Status |
|---|---|---|---|
| 0 | Step 0: read the repo state; CLAUDE.md | ATP_HANDOVER_PACK.md (STEP 0) | done: CLAUDE.md (#331 + the #330 fold-in, #333); repo-state check done 22 Oct (status per item below) |
| 1 | Font size | ATP_HANDOVER_PACK.md §1 | done: #335 merged (rem scale, Text size setting, layout fixes, new baselines) |
| 2 | Option chain: analysis + Sensibull-style charts | ATP_HANDOVER_PACK.md §2 | done: #324 / #326 / #327, 2a wiring #337, 2b + 2c Charts view #339 merged (migration a7c9e1f3b5d8; OPEN_QUESTIONS OC5-1..4) |
| 3 | F&O workspace | ATP_SESSION_FNO_WORKSPACE.md | partial: slice 1 chain trading + order ticket and slice 2 basket merged (#338, #341; OPEN_QUESTIONS FNO-1-1..10, FNO-2-1..10); slice 3 symbol hub + futures chain unfinished (branch `claude/fno3-hub-futures`, not pushed); watchlist contracts, chart trading, positions and 8b not started |
| 4 | Default watchlists (indices / index stocks / sectors) | ATP_HANDOVER_PACK.md §4 | done: #346 merged (Indices / Index stocks / Sectors / F&O system lists, daily refresh, admin card, Copy to my list; migration a4c6e8f0b2d4; owner questions WL1-1..7 open) |
| 5 | AI Copilot: a professional trader's brain | ATP_HANDOVER_PACK.md §5 | partial: slice 1 #342 merged (chat page + jobs 1, 2, 7, 8; regime is a diagnostic only); jobs 3–5 (branch `claude/copilot-v2-jobs345`, not pushed), job 6 and the CI eval / safety sets remain |
| 6 | Master Order 2: AI keys + page tours | ATP_HANDOVER_PACK.md §6 | partial: parts C (page tours) and B (key guides) merged in #345; part A (more AI providers, free-tier tags, privacy notice) not started |
| 7 | Front page P3 | ATP_HANDOVER_PACK.md (priority 7) | done: #329 merged |
| 8 | Master Order 3: full UX audit + additions | ATP_SESSION_UX_AUDIT_MASTER.md + ATP_HANDOVER_PACK.md §8 | partial: audit report #340, 8d login #332, fixes 1–2 (sign-in screen, Coming soon, admin flag list) #349 and fixes 4–5 (Paper bot page, Connect Telegram) #350 merged; admin console and the other fixes not started |
| 9 | Master Order 4: the ready-made dish, the daily rhythm, plans | ATP_SESSION_GROWTH_MASTER.md | pending |
| 10 | Analyze a chart | ATP_HANDOVER_PACK.md §10 | partial: A1 backend + A2 page merged (#304, #305 inside it; migration c4e6a8b0d2f4; flag `chart_analyze` OFF); A3 unfinished (branch `claude/chart-analyze-a3`, not pushed) |
| 11 | Strategy Lab (Pine + Python), idea → code / scan, idea library, compliance, signal language | ATP_HANDOVER_PACK.md §11 (a–l) | partial: 11a AMW port audit + 11b ADR-0027 approval (#334); c1 Pine subset translator #344 merged (flag `strategy_lab_pine` OFF); signal language rule in CLAUDE.md; the rest of 11 (c2 Python runner, e/j, k, l) not started |
| 12 | Idea library templates T1–T8 | ATP_SESSION_IDEA_LIBRARY.md | pending |
| 13 | Strategy template library (independently reviewed; section 0 corrections are binding) | ATP_SESSION_STRATEGY_TEMPLATES.md | partial: step 1 recipe core + indicators (MACD, Stochastic, WMA, HMA, PSAR) + parity harness merged (#343; not wired to deployments yet; TPL-Q1..Q7); exits as settings (needs a migration and owner approval), R2 / R5 / R7 / R10 and the templates themselves pending (docs/design/STRATEGY_TEMPLATES_IMPL.md) |
| 14 | Wave / correction templates (family G, G1–G12) | ATP_SESSION_WAVE_TEMPLATES.md | not started: step 1 (the independent spec review) has no branch yet |
| — | Login error message + `atp.sh reset-password` | ATP_HANDOVER_PACK.md §8d | done: #332 merged |
| — | Google sign-in | ATP_HANDOVER_PACK.md (also open) | done: #336 merged, inert; the button stays hidden until the owner adds the OAuth credentials (OPEN_QUESTIONS GS-1, GS-2) |
| — | Upstox OAuth redirect from PUBLIC_BASE_URL | ATP_HANDOVER_PACK.md (also open) | done: #319 / #321 |
| — | Broker field map, `/api/broker/specs`, Zerodha / Fyers OAuth callbacks (UI2-5) | earlier order (UI2-5) | done: #303 merged (no migration) |
| — | Drop `users.ai_language` (EO-4, English only) | earlier order (EO-4) | done: #277 merged (migration e6b8d0f2a4c7) |
| — | Login under load (CI load-500 login SLO, LD-9) | CI | pushed on `claude/ld9-login-under-load`, no PR yet |

Other files here: INBOX.md (every new instruction, verbatim), DECISIONS.md (owner decisions).
