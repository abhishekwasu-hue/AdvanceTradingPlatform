# Open questions

Only the OPEN items that are current (token budget, owner 24 Oct: this file stays under 30 KB). Each row has a provisional
decision taken so work continues. When answered: mark it, then move the row to docs/archive/OPEN_QUESTIONS_CLOSED.md.
Older open rows whose provisional default is already in effect: docs/archive/OPEN_QUESTIONS_PROVISIONAL.md. Answered /
closed rows: docs/archive/OPEN_QUESTIONS_CLOSED.md. Both are moved, never deleted - grep them by ID.

Older rows: docs/archive/OPEN_QUESTIONS_CLOSED.md and OPEN_QUESTIONS_PROVISIONAL.md
Answered / closed rows moved on 2 Nov 2026 (and later moves): docs/archive/OPEN_QUESTIONS_ARCHIVE.md
Moved 15 Nov 2026 (the file was ~148 KB, above its 30 KB rule): answered / closed rows to OPEN_QUESTIONS_ARCHIVE.md; every
open row of the sections before the 6 Nov roadmap audit (its default in effect) to OPEN_QUESTIONS_PROVISIONAL.md - still
open, grep by ID. Of those, these wait for the owner: SIG1-1, OKT-9, KN-1, GW-1, P12B-4, CXQ-1..CXQ-10, SXQ-1..SXQ-4, AUD-T8-1, AUD-T6-1, M55Q-1, REVQ-4, M62Q-1, STRATX-Q1, M79Q-1, M68Q-3, M71Q-1, M71Q-3, M71Q-4, M71Q-5, M71Q-2, M72Q-8, M72Q-9, M72Q-10, M72Q-11, M72Q-12, M75Q-1, M75Q-2, M75Q-3, M75Q-4, M75Q-5, M75Q-6, M75Q-7, M75Q-8, M75Q-9, M76Q-1, M76Q-2, M76Q-3, M76Q-4, M76Q-5, M76Q-6, M76Q-7, M76Q-8, M76Q-9, M76Q-10, M77Q-1, M77Q-2, M77Q-3, M77Q-4, M77Q-5, M77Q-6.

## AG-1 multiple agents (owner 3 Nov)
- AG-Q1: how many agents at once? Default: up to 4 in parallel, only for independent work, each in its own worktree;
  the main session reviews and integrates every result.
## Roadmap truth audit (6 Nov 2026)

| # | Question | Provisional default | Where | Status |
|---|---|---|---|---|
| ID-Q1 | The owner's 8 Nov orders "M85" and "M86" reuse IDs that the roadmap already has | They are tracked as M85-J and M86-NB; the owner's words stay verbatim in INBOX | docs/ROADMAP.md | open (default) |
| M86-Q1 | M86 D4: the exit-price fallback can read other orgs' streamed ticks - `market_data/exit_prices.StoredTickPrices` (an organisation with no usable broker session, flag byob_data_tier off) and the worker's own `MarketDataService.get_ltp` read the platform-wide `md:tick:*` keys that every organisation's Phase S stream writes. Key them per credential (an exit then prices only on its own session's last tick or its own broker)? | Unchanged until the owner decides: an exit must always get a price (ADR-0004; owner M87: exits and PAPER safety untouched) | backend/app/market_data/exit_prices.py, backend/app/workers/trading_worker.py | open (default) |
| M86-Q2 | M86 D4: organisation-level stores fed from the organisation's chosen broker credential and shown to every member of that organisation (OI banner collector, market memory / sentiment, option-chain recording, the India VIX gate reading, the data hub's organisation stream for the worker, the per-organisation OI baselines) are kept per TENANT, not per user. Turn them off for organisations with more than one member? | Per tenant (unchanged): one-member organisations are the normal case; with byob_data_tier on they already use each member's own session or nothing | backend/app/workers/trading_worker.py | open (default) |
| M86-Q3 | M86 D4 supersedes LD-10's cross-user candle copy: candles are now one copy per broker SESSION (one user's chart, screener, Copilot share a fetch; another user fetches through his own session). More broker calls per user than LD-10's load test assumed. Accept? | Yes (M86 is the newer owner decision; each user's calls stay inside his own API budget) | backend/app/market_data/shared_candles.py | open (default) |
| M88-Q1 | M88 import: a true old binary .xls (Excel 97-2003) needs a new dependency (xlrd). .csv, .xlsx and the HTML / text ".xls" files broker back-offices save are read without one. Add xlrd? | No new dependency: an old binary .xls gets the sentence "save it as .xlsx or .csv, and import that file" | backend/app/journal/importer.py | open (default) |
| M88-Q2 | M88: imported (and typed) outside-ATP trades now count in the weekly review's totals, setups and history (as they already did in Stats); the discipline score stays ATP-only because no behaviour detector can read an outside trade (no signal, stop moves or fills recorded by ATP). Keep the score ATP-only? | Yes: outside trades in every total, never in the discipline score | backend/app/journal/service.py (manual_review_trades) | open (default) |
| M88-Q3 | M88 "Show on chart": an outside trade is drawn on Daily candles from the shared EOD exchange files (labelled "Daily candles"), because no intraday bar of his own is stored for a trade done elsewhere and M86 forbids another user's broker data. A file row with a date and no time is stored at the start of that day (India time); pairing within that day follows the file's row order. | Daily only; date-only rows as stated | backend/app/journal/trade_chart.py, importer.py | open (default) |
| ID-Q2 | The owner's 8 Nov "M88" (shared daily AI report) reuses the id already used for the 8 Nov Journal import | Tracked as M88-R (report) and M88-J (Journal import); the owner's words stay verbatim in INBOX | docs/owner-orders/INBOX.md | open (default stated) |
| M91-Q1 | M91 W4: when he turns the alert on (or chooses a new screen), are the stocks already matching alerted? | No: the first check is the baseline; only stocks that newly enter the matches after it are alerted (once, while they stay matched) | backend/app/screener/watch_screen_routes.py `_alert_new` | open (default) |
| M91-Q2 | M91 W3 / W4: a list is checked (and so alerted) only while it is on screen - should a closed list also be checked on bar close for its alert? | No (the order: "Lists not on screen are not run"); the alert box says "Checked while this list is open, when a new bar closes" | frontend/src/chart/watchScreen.ts `ScreenChecker` | open (default) |
| M91-Q3 | M91: a screen that ranks symbols against each other (Rank / PercentileRank) has no one-stock answer for the chart (C2 could not hold) | Listed in the dropdown, not choosable: "(not for lists)" with the reason; every other own screen works | watch_screen_routes.py `_usable` | open (default) |
| M91-Q4 | M91 W2: while a screen orders the rows, dragging a row to reorder is off (the display order is the screen's); the Terminal's docked watchlist shows the Screen row but its rows open the Terminal's own chart without the overlay | Drag off with the hint "Choose Screen: None to reorder the list"; Terminal unchanged | frontend/src/chart/WatchlistPanel.tsx | open (default) |
| M93-Q1 | Screen audit S1: a weekly (on 1h screens: daily) context bar counts as closed only at the next Monday 00:00 IST (`runtime.bar_close` / `_DURATION`), so on the week's last session and all weekend the screens read the previous week's trend; 6 Nov results change (HDFCLIFE, NHPC, HINDALCO shown; AXISBANK missed) | Default: fix - a week (and a day) is closed at its last session's close (15:30 IST); one shared change with a truncation test; needs the owner's yes (trading-logic change) |
| M93-Q2 | Screen audit: VCP pivot - spec B3 says the base high, the code (vcp.py) uses the last contraction's high (Minervini's own pivot); 37 of 45 ATP breakouts closed inside the base | Default: follow the spec (base high), as the spec is the owner-approved text; alternative: keep the code and change the card / spec wording |
| M93-Q3 | Screen audit S2 / S3: index volume - daily index volume is 0 (Darvas / Livermore / Weinstein pass "0 >= 0"; the other breakouts never match an index, silently) and intraday zero-volume bars (no future bar) are averaged as traded volume | Default: a 0 / missing volume counts as "no volume data": the volume rule is not checked and the result says "volume not available for this index" (never a silent pass or a silent no-match); the fundamentals screens likewise say "no figures stored" |
| M93-Q3a | M93-Q3 build: a screen that reads volume (the `volume` field, VWAP, OBV, MFI, RelVolume, RelVolumeTOD, SessionVWAP, WaveVolumeLower) on a symbol whose volume part has no value on the last bar is "Not checked: volume not available" as a whole - also when the volume part sits in one OR branch and another branch would match | Default: as built (never a silent pass; ScreenQL has no "skip one condition" rule) |
| M93-Q3b | M93-Q3 build: which conditions are "volume conditions" in the breakout engine. Not applied without volume data, with the note "Volume not available: the volume condition was not applied.": `vol_mult`, `tod_vol_mult`, the intraday VWAP agreement (`vwap_agree`) and the VCP dry-up (`vcp_vol_dryup`). B14 Volume breakout (volume IS the screen) is "not checked" instead | Default: as built |
| M93-Q3c | M93-Q3 build: scope is the screener (screen runs, library, history, alerts, watch lists, breakout page). Strategy templates keep the old rule (a missing volume fails the confirmation) and charts keep the index's 0 - a change there is trading logic. The shared `runtime.resample` now keeps a bucket with no volume data missing (was 0) for every caller | Default: strategy templates follow the screener only on the owner's yes |
| M93-Q3d | M93-Q3 build: an index is recognised by name (the F&O index names, SENSEX / BANKEX, and the platform's stored index names such as INDIA VIX); a sector index outside that list keeps its 0. The result row's note is shown to every user (it is the decision's "says so", not a new feature behind `ui_v2`) | Default: as built |
| SCR-FIX-I1 | M93 audit spec gap: the 15m NR7 / 15m expansion / 5m volume screens have no session clock, so the 09:15 bar can break the previous session's last bar (an overnight gap). A session clock would be a new rule on a structure screen (the chart is read as one continuous unit) | Default: keep as built (pinned by tests/test_scr_fix_intraday_breakouts.py); a session filter only on the owner's yes |
| M94-Q13 | M94 R3 profit give-back: the day peak is per organisation (like the daily loss P&L it reads), not split by PAPER / LIVE | Default: per organisation, as the daily loss limit |
| M94-Q14 | M94 R3: raising the give-back % later in a day where it was already reached does not lift that day's refusal (switching the check off does) | Default: the refusal stays for the day |
| M94-Q15 | M94 S4 / S5 Top / Bottom Reversal: the move from the open is measured in ATRs ($move_atr), not percent (a percent has no exact mirror, so Bottom Reversal could not equal the mirror of Top Reversal) | Default: ATR, Not set |
| M94-Q16 | M94 S5: hammer and shooting star made a mirror pair (exact reflections, checked bar by bar; no existing screen uses them) | Default: as built |
| M94-Q17 | M94 S12 Key Levels: "the session before the previous" read with Lag(high/low, 1)@1d equals the previous session on a session's last bar (the pinned @1d rule); PDC / PDH / PDL stay on the previous session on every bar | Default: as built |
| M94-Q18 | M94 templates: the optional intraday time window is the user's existing Trade-settings window (New entries from / until, Not set), not a window inside each template (that needs TimeOfDay and its flag) | Default: the Trade-settings window |
| M94-Q19 | M94 templates: values the study gives no number for (H8 stretch, H9 / H10 buffers, H10 touch zone) are 0 = "no condition"; a true "ask before running" needs a template-settings editor that does not exist yet | Default: 0 = no condition, shown under the reference-values label |
| M94-Q20 | M94 templates: defaults with no source in the spec - H8 / H10 "within" 12 bars (C6's default), H10 touch look-back 12, H6 exit look-back 1 bar | Default: as built, labelled reference values |
| M94-Q21 | M94 H2 / H3 (ABCD): the trigger is "close above the recent high" (the spec's cross happened before C was confirmed and never fired); once per setup | Default: as built |
| M94-Q22 | M94 H2 / H3: C must come after B (swing direction up and the swing high at or above the session high) - else a straight run whose higher low came before B fired | Default: as built |
| M94-Q23 | M94 H1 (Fallen angel): the base is read on the bars before the signal bar and inside the session (a break no longer widens its own base) | Default: as built |
| M94-Q24 | M94 H1-H5 defaults with no number in the spec: base_n 5, base_atr 1.0 (a tightness limit has no "0 = none"), expiry 12, turn_n 1, exit_n 1 (labelled reference values) | Default: as built |
| M94-Q25 | M94 not built in H1-H5: T1's optional support evidence (would show developer text) and the flags' "new 1-minute and new 5-minute high together" (needs two timeframes) | Default: left out of M94 |
| M99-P2-Q1 | M99 P2 trade-management options (trail by swing / ATR / average, no new extreme, bars in the trade) run on a template bot that trades the symbol it reads; a future (its levels are transplanted) and an option (its levels are on the underlying) are refused in a plain sentence for now | Default: the symbol itself only; futures / options in a follow-up PR on the same `management_step` - built in the follow-up (a future and a single option; a structure stays refused) |
| M99-P2-Q2 | M99 P2: no second partial exit for the H templates - a part is booked through the ExitRules scale-out (M99 P7, #1023); a break-even "after the partial" is not built (the existing break-even at an R multiple stays) | Default: as built |

## M94: Intraday pack spec review (10 Nov, docs/design/M94_INTRADAY_PACK.md)

| # | Question | Provisional default | Where | Status |
|---|---|---|---|---|
| M94-Q1 | Where is the order's "Intraday" group? The library has method x setup axes; templates are families A-G (schema pins `^[A-G]`) | Library: new source `intraday` + an "Intraday" filter chip, screens filed on their real method / setup; templates: new family H "Intraday"; both behind flag `intraday_pack` | app/screener/taxonomy.py, app/strategy_templates/schema.py, routes.py | open (default stated) |
| M94-Q2 | Study values in another market's units (share counts, a currency amount, float) | Not set; the run asks; the help shows only the unit-free hint | docs/design/M94_INTRADAY_PACK.md §3-§4 | open (default stated) |
| M94-Q3 | A parameter the study gives no number for | The existing ATP function default where one exists (e.g. `tod_vol_mult` 1.5), editable; else Not set | app/screener/breakouts/params.py | open (default stated) |
| M94-Q4 | Pre-open bars (09:00-09:08) are not shown to exist in any broker feed by the code | Gap from the session's opening price vs the previous close; pre-open volume / high / low only where the data has pre-open bars, else "not available" | app/screener/breakouts/session.py | open (default stated) |
| M94-Q5 | Circuit Watch: which brokers' quote band fields first | Upstox and Zerodha first; others "not available from your broker" | app/brokers/models.py `Quote` | open (default stated) |
| M94-Q6 | Level exits (VWAP, session high / low, previous close) and partial exits | Build target rule TEMPLATE_LEVEL (one level per deployment, migration) in PR-6; partial exits on deployments not built in M94 | app/strategy_templates/exits.py | open (default stated) |
| M94-Q7 | Intraday time windows | An optional "only between" window per template, Not set (whole session); the study's segment as a hint | app/screener/registry.py `TimeOfDay` | open (default stated) |
| M94-Q8 | Profit give-back: how the day's peak is measured | Peak of closed + open P&L as ATP sees it on each worker cycle and entry check; no minimum peak; resets next trading day; entries only | app/risk_engine/risk_limits.py | open (default stated) |
| M94-Q9 | Position-size calculator reference value | Not pre-filled; "reference 1 % (study: at most 2 %) - edit before use" as a hint; nothing saved without Save | app/execution/position_size.py | open (default stated) |
| M94-Q10 | Footer wording differs from the existing info line | The order's exact sentence on M94 surfaces only; the existing line stays elsewhere | app/study/snapshot.py `INFO_LINE` | open (default stated) |
| M94-Q11 | 5-level market depth has no consumer among the 25 items | Not in M94; a separate roadmap item if wanted | app/brokers/*.py `get_quote` | open (default stated) |
| M94-Q12 | Short counterparts (New Day Low, Bottom Reversal, Turbo Breakdown) | Own ids so the list shows them by name; text generated by the mirror rule and tested equal | app/screener/library.py `mirror_of` | open (default stated) |

## JV2: Journal v2 (P2-8, docs/plans/journal-v2-PLAN.md)
| ID | Question | Default (provisional, built) | Status |
|---|---|---|---|
| JV2-Q1 | When are broker-app trades synced (Upstox gives only today's fills)? | Daily after the close (15:45 IST) + a "Sync from broker" button; read-only | open |
| JV2-Q2 | Where do synced trades live? | Outside-ATP entries "From your broker"; never an order / position row | open |
| JV2-Q3 | When is the chart picture stored? | On its first draw; served from the store after that | open |
| JV2-Q4 | Check-in prompt: where? | Journal card now; Telegram / e-mail in PR 3 | open |
| JV2-Q5 | Feelings list | calm, confident, unsure, anxious, excited, fearful, FOMO; editable | open |
| JV2-Q6 | First formal review | After 20 closed trades (setting); fewer: counts only | open |
| JV2-Q7 | Monthly export | A print-friendly page | open |
| JV2-Q8 | First-20 rule: Stats tab too? | Reviews only; Stats keeps its numbers | open |

## LIVE-1: one live switch (owner order 11 Nov, BINDING; PR `claude/live-1-one-switch`)
| ID | Question | Default (provisional, until Abhi answers) |
|---|---|---|
| LIVE-1-Q1 | Two-factor step-up before creating / resuming a LIVE bot (only when the team owner set `require_mfa_for_live`, always for a platform admin) - a LIVE gate or account security? | Kept as account security (it also guards broker logins); not a LIVE switch |
| LIVE-1-Q2 | Verified email for LIVE (operator setting EMAIL_VERIFICATION_REQUIRED) and the SEBI algo id (ALGO_ID_REQUIRED_FOR_LIVE), both off by default | Kept: identity / regulatory facts, like "his own signed-in broker"; not LIVE switches |
| LIVE-1-Q3 | A team owner can still deny one member `trading:live` (Team page scopes) | Kept as a team permission; every USER now has it by default |
| LIVE-1-Q4 | Screener "then" action Auto LIVE (N1 execution core) has no LIVE order path at all | Stays refused (no path is built; not one of the named order paths); the reason now says "live trading is switched off" only when it is |
| LIVE-1-Q5 | F&O ticket, basket and Reverse LIVE | The ticket's LIVE path is #1002 (it reads `live_owner.org_live_open`, which is now the one switch); the basket and Reverse follow once #1002 merges (LIVE-2) |
| LIVE-1-Q6 | The stored organisation list of `live_trading` (former "LIVE for organisations") | Kept stored, owner-only route kept; it decides nothing and the admin list no longer shows it |
## M96-Q1 (changed): the F&O ticket's LIVE path (PR `claude/m96-ticket-live`)
| ID | Question | Default (provisional, built) | Where | Status |
|---|---|---|---|---|
| M96-L1 | The Phase 0 static guard (tests/test_z10c_order_paths.py rule 2, "no API route reaches a broker ENTRY") vs the order to build the ticket's LIVE path now | One named exception, `POST /api/paper/fno/order` (ROUTES_REACHING_BROKER_ENTRY), with this order as its reason; `fno_ticket.place` is GATED (no other caller can obtain a broker); runtime: live_trading OFF / the lock ON refuse | tests/test_z10c_order_paths.py | open - owner's ✅ before merge |
| M96-L2 | Limit / SL / SL-M and "wait for my price" on LIVE (the shared LIVE router sends MARKET / a protected limit only) | A LIVE ticket order is a Market order; the others are refused in a plain sentence | app/paper/fno_ticket.py `live_ask_errors` | open (default stated) |
| M96-L3 | Which broker when he has several signed in (the ticket has no account picker) | Exactly one signed-in own session; more than one is refused in a plain sentence | app/paper/fno_ticket.py `_own_session` | open (default stated) |
| M96-L4 | "LIVE never pre-selected unless he saved it as his own default" - no such saved setting exists | PAPER always pre-selected; no new setting added | frontend/src/fno/ticketModel.ts `newDraft` | open (default stated) |
| M96-L5 | A bought option with no stop of his own: the pipeline sizes against a one-tick stop, which LIVE would send as a broker stop | No broker stop unless he entered one (`protective_stop`) | app/execution/signal_execution.py | open (default stated) |

## SPOT-13N: the Terminal's one spot and the days left (owner bug 13 Nov, `claude/fix-terminal-spot-source`)

| # | Question | Provisional default | Where | Status |
|---|---|---|---|---|
| SPOT-1 | Days left: the chain's OI banner counts calendar days (Trade compute_dte, M96 "AS IS"); the context bar and the "Days to expiry" card count trading days (B3, DECISIONS 24 Oct). | The banner stays Trade's, unchanged (coordinator, M96 binding); ATP's own labels name their unit: "Expiry in 2 trading days", card "2 trading days". | `optionChain/model.ts` `tradingDaysText` | open (default stated) |
| SPOT-2 | When a live tick stops counting, and when the spot says "as of". | A tick this tab has not heard for 30 s goes out (the LTP read returns); the bar's spot says "as of <time>" once older than 60 s or while the market is closed. | `terminal/spot.ts` `TICK_FRESH_MS`, `SPOT_AS_OF_AFTER_MS` | open (default stated) |
| SPOT-3 | The chart's last-price tag vs the spot. | No separate "last candle" label: every timeframe's forming bar now takes the one spot whenever it is newer than the bar's start, so the tag is the spot; only a 1-minute candle newer than the spot shows its own close. | `chart/timeframes.ts` `foldLive` (unchanged) | open (default stated) |

## FN6-R1: Route 1 user runner (14 Nov, docs/design/USER_RUNNER.md, PR `claude/design-user-runner`)

| ID | Question | Default (provisional, until answered) | Status |
|---|---|---|---|
| RUN-Q1 | Lawyer: does Route 1 fit the tech-savvy client exception (NSE/INVG/67858 I(h), NSE/INVG/69255 §14) when ATP supplies the runner software and the strategy DSL? Does ATP need RA registration / exchange empanelment for its templates? | Route 1 for users; owner LIVE only on his words | open (lawyer) |
| RUN-Q2 | Pilot machine for the runner | a separate small Linux VPS with its own static IP; PAPER only | open (owner) |
| RUN-Q3 | First broker in the runner | Upstox only (the user's own Upstox app and key, never ATP's app) | open (owner) |
| RUN-Q4 | Where a user's PAPER runs | on ATP's server as today; the runner's PAPER mode for the pilot and a pre-LIVE dry run | open (owner) |
| RUN-Q5 | New entries while the runner cannot reach ATP | paused after a grace period (runner setting); exits, stops and square-offs always continue (ADR-0004; same principle as trading_worker.py:24-25) | open (default stated) |
## BIZ-1: Route 3 audit (owner order 14 Nov, docs/design/ROUTE3_AUDIT.md; PR `claude/route3-audit`)

| # | Question | Provisional default | Where | Status |
|---|---|---|---|---|
| BIZ1-Q1 | LIVE-1 (11 Nov): `live_trading` ON = every user may go LIVE through ATP's server; Route 3 (14 Nov): no other user's LIVE through the server. | Route 3 narrows LIVE-1: with the switch ON, server LIVE only for the owner identity; every other user PAPER + "Trade in your broker app". Order-path change - not built before the owner's ✅. | ROUTE3_AUDIT §2, step B1 | open |
| BIZ1-Q2 | "Free or Basic plan". | One free plan shown as "Basic"; the stored id `free` stays (no data change). | ROUTE3_AUDIT §3, step B6 | open |
| BIZ1-Q3 | Brokers with no verified pre-filled order link (all but Zerodha today). | The button opens the broker's own site / app with the order facts shown in ATP to copy; a URL is never guessed. | ROUTE3_AUDIT §1c, steps B2-B4 | open |
RUN-Q2, RUN-Q3, RUN-Q4 answered by the owner on 14 Nov: moved to docs/archive/OPEN_QUESTIONS_ARCHIVE.md.

| ID | Question | Default (provisional, until answered) | Status |
|---|---|---|---|
| RUN-Q1 | Lawyer: does Route 1 fit the tech-savvy client exception (NSE/INVG/67858 I(h), NSE/INVG/69255 §14) when ATP supplies the runner software and the strategy DSL? Does ATP need RA registration / exchange empanelment for its templates? | Route 1 for users; owner LIVE only on his words | open (lawyer) |
| RUN-Q5 | New entries while the runner cannot reach ATP | paused after a grace period (runner setting); exits, stops and square-offs always continue (ADR-0004; same principle as trading_worker.py:24-25) | open (default stated) |
| RUN-Q6 | Angel One data terms in the runner: in ATP they are "pending" (docs/BROKER_DATA_TERMS.md:22, :56). In the runner the user's bars / LTPs come from his own SmartAPI session onto his own machine for his own use - is that within Angel One's API terms? | the pilot (owner's own account) goes ahead; confirm with Angel One in writing / the lawyer (with RUN-Q1) before any other user's runner | open (verify) |
| RUN-S8-1 | Index strategies on the runner read the index's own volume; ATP's worker replaces it with the near-month future's (docs/design/RUNNER_PARITY.md D3). | add the future-volume step to the runner feed in a later step; until then it matters only for an index strategy that reads volume | open (default stated) |
| RUN-S8-2 | The VIX gate's readings are captured by ATP's worker only; on the runner a user who set his own VIX numbers gets the gate's fail-safe (bullish index entries paused) (RUNNER_PARITY.md D4). | capture the reading on the runner from his own broker in a later step | open (default stated) |
| BIZ1-Q4 | Angel One Publisher / Fyers API Connect buttons: formats seen only in search extracts (broker hosts blocked for the research session, 15 Nov). | Not built; copy sheet. Re-check on the official pages (browser or allowed host), then add the builder + tests. | BROKER_DATA_TERMS "Other brokers: pre-filled order research" | open |

## SRV2: 15M SRv2 port (18 Nov, DECISIONS 18 Nov SRV2)
| ID | Question | Default | Where | Status |
|---|---|---|---|---|
| SRV2-Q1 | Where do the bot's PAPER positions and exits run? A = Trade's own PAPER engine (open_multi_leg_trade, manage_open_trades, trade_monitor, its own trades store) inside ATP's worker, shown read-only in ATP; B = ATP's trades table with Trade's exit rules rewritten for it | A (AS IS: the exits stay Trade's code; B rewrites them) | atp-private docs/SRV2_PORT_DESIGN.md | open (default; build continues on A) |
| CA-CHAT-Q1 | Analyze a chart: setup dropdown + AI chat (owner 18 Nov). Shape? | One "Setup" dropdown on the page (My method for the owner only, the book strategies with their rules, "My own conditions"); below the result a chat box on the SAME chart: the trader writes his conditions in plain words, the AI answers each one as met / not met / unclear with the evidence and marks it on the chart; the user's own AI key; cost per message shown; answers pass the signal-language guard (no buy / sell / target words); one thread per analysis, kept in My analyses. Built after the strategy-flow and journal fixes. | INBOX 18 Nov | decided 18 Nov "default yes" (DECISIONS) |
| SRV2-Q5 | Automatic with Vision on, but Vision does not answer (no key, timeout, error): enter or skip? | Skip: "Vision agrees" cannot be read from a Vision that did not answer (atp-private sets `vision_fail_action = skip` for auto; Trade's own default is `ignore`) | atp-private srv2/modes.py, PR #21 | open (default) |
| SRV2-Q6 | Automatic with a gray Vision verdict (neither agree nor disagree) | Trade's own `half` rule AS IS: advice only, the entry is at his full dashboard lots | atp-private (Trade vision/gate.py) | open (default) |
| SRV2-Q7 | The Telegram card's chart photo needs plotly + kaleido + a Chrome in the backend image (Trade: plotly~=7.0, kaleido 1.x); without them the chart is reported unavailable | Yes, in its own small image PR after P3 (the image grows by the Chrome download; checked against the image size budget) | backend/Dockerfile, requirements | open (default) |
| UPX-TB-Q1 | Upstox TradeButton (pre-filled order in Upstox; owner "Default yes" 19 Nov, after checking Upstox's docs). Checked 19 Nov: TradeButton is only in an old Upstox blog post (upstox.com/market-talk/integrate-app-using-tradebutton/ - a JS button with an `apiKey`, login by UCC + trading password + year of birth, i.e. the old API); the current API documentation (all 260 pages of its sitemap) has no TradeButton, publisher or pre-filled-order page. Building on it would be a guess. | Not built. The "Trade in your broker app" copy sheet stays for Upstox (with the cash-product fix, PR claude/cash-product); orders through ATP keep using his own Upstox API app (the LIVE path, his switch). Build only if Upstox confirms in writing that TradeButton works on the current API (owner or Upstox support). | upstox.com docs | open (default stated) |
| CP-CHAT-Q1 | With the AI key, an answer that ATP's rules built and the AI worded (no planner) still carried its rule sections (a grid of cards) under the AI's own words. Keep them on screen or fold them? | Folded under "Details" (the AI words already say them; the code-drawn tables and a strategy draft card stay on screen). Rule answers without a key unchanged. | INBOX 20 Nov (CP-CHAT) | decided 21 Nov "Default yes" (DECISIONS) |
| CP-CHAT-Q2 | The "Market analyst" answer style (Settings > AI) wrote fixed "## Summary ..." sections. Keep the old sectioned style as a third choice? | No: "Market analyst" now replies as a chat (same market words); "Simple words" unchanged. The section index stays only on a long answer (1,500+ characters). | INBOX 20 Nov (CP-CHAT) | decided 21 Nov "Default yes" (DECISIONS) |

## TERM-CON: Terminal "Conclusion" panel (20 Nov, docs/plans/terminal-conclusion-PLAN.md; plan only, waits for the owner)
| ID | Question | Default | Where | Status |
|---|---|---|---|---|
| TC-Q1 | Show how many readings agree / conflict? | Yes, as the confluence meter (counts with glyphs); no score, no weighting | PLAN 3.3, 6A | decided 20 Nov "Default yes" (DECISIONS) |
| TC-Q2 | Which chart timeframes does the panel read? | The Terminal chart's own TF plus every TF his own saved strategies on this symbol use; each shown separately, never merged | PLAN 3.2 | decided 20 Nov "Default yes" (DECISIONS) |
| TC-Q3 | AI summary: on tap or automatic at every refresh? | On tap ("Summarise", cost shown); never automatic | PLAN 3.4 | decided 20 Nov "Default yes" (DECISIONS) |
| TC-Q4 | Where does the panel sit? | In the "Option seller" and "Quick trade" layouts under the chain (desktop) and a "Conclusion" phone tab; other layouts add it from Layout tools | PLAN 6 | decided 20 Nov "Default yes" (DECISIONS) |
| TC-Q5 | SEBI: how strong may the one lean cue (▲ / ▼ / ◆) look? | Chip-sized, always with "Lean" and its count ("3 of 4"), majority of readings only, tie = sideways; never a hero arrow or "signal" word; legal check before LIVE | PLAN 6A | decided 20 Nov "Default yes" (DECISIONS) |
| NEXT-Q1 | "pudhil kaam kr" (21 Nov): every queued item is built and waits for the owner (deploy / flags / ✅; list in docs/design/ROADMAP_TRUTH_21NOV.md). Which build next? | P2-8 Journal v2, the parts not on main (J2 20-second check-in, J3 reviews, J4 My proof, J5 if-then plans + process goals, J6 delivery), audit against app/journal first; draft PRs, no merge before the owner's ✅ (order file rule). Phase 4 items wait for phases 2-3 (R5). | INBOX 21 Nov | decided 21 Nov "Default yes" (DECISIONS) |
