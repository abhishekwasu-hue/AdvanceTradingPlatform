# FN0 — trader journeys J1–J7 on production (owner PR-2, 25 Oct)

Docs only. Built from `FN0_FUNCTIONAL_AUDIT.md` (feature rows #1–#37) and the deploy of `eb11fb0a` (25 Oct, 06:53 UTC).
Production cannot be reached from the build container, so "on production" means: deployed, and the check in the last
column is the owner's proof (screenshot). Status: **W** works end to end in code · **P** partial · **B** broken ·
**H** hidden / unfinished. Journeys from `docs/ROADMAP.md` ("The goal of ATP v1").

| Journey | Steps | Status | What blocks it today (evidence) | Fix item | Owner check on production |
|---|---|---|---|---|---|
| J1 Start | sign in → connect Upstox → Today | P | Sign in, broker connect, Today: W (rows 2, 4). Today's market card and live prices are pack flags, OFF until "Enable trader features" (rows 2, 7) | P1-3, P1-5 | after the button: Today shows your broker's index prices, not "–" |
| J2 Read the market | charts, option chain + charts, watchlists | P | Charts W (row 5); chain P: spot = chart since #537 (deployed); watchlists P: no own page / phone entry, lists empty until universe sync (row 8); 1H / 15M index history MISSING (health 25 Oct) | DATA-H, P1-8, universe sync | NIFTY 15m chart has days of history; watchlist panel lists NIFTY 50 |
| J3 Get signals | template → signals (screen + Telegram) → approve → paper trade → positions → exit → journal → performance | P | Signals + approve W (row 24); paper account and journal are pack flags (rows 25, 27); templates + idea library H (row 29); Telegram needs the bot (owner) | P1-3, P2-3, P2-8, alerts | adopt a template → a signal appears → Approve → paper position → Exit → journal entry |
| J4 Trade by hand | Terminal: chart / chain / ticket, exits, group exits, Exit all | P | Terminal ON since eb11fb0a (row 12); two empty panels hidden in #539 (next deploy); spot / premium exit triggers = T3; combined exit has the API only, the screen is CX PR 3 (row 13) | T2, T3, CX PR 3, SX | Terminal opens (desktop + phone); a paper order from the ticket; Exit all |
| J5 Ask the Copilot | key pasted once → answers about own positions and risk | P | Copilot W (row 30); one-key connect and agent are pack flags; AI call log lacks inputs / outputs (FN5) | P1-3, FN5, CP-V | paste a key → "Test" OK → ask "what is my risk now" |
| J6 Stay safe | own risk limits (OFF until set), kill switch, exits never blocked | W | risk tools behind pack flags (row 25); kill switch and never-blocked exits W (ADR-0004 tests) | P1-3 | set a daily loss limit → a new paper entry is held, an exit still goes |
| J7 Get help | page tours, help centre, plain words | P | tours + help W in code; developer pages visible to traders: Factor Lab, Contract specs, Activity log (row 34) | FN1 (move to Advanced) | first visit of Charts shows the tour; "?" replays it |

## What must happen first (PR-3 order)
1. Data: DATA-H backfill (owner's org confirmed as "My Organisation"), `data_hub` ON, universe sync ON, sector / watchlist jobs.
2. Day-one trader test on production: J1 + J3 end to end, with screenshots.
3. Terminal T2 / T3 + CX PR 3 + SX. 4. FN2 → FN3 → FN4. 5. FN5 + CP-V. 6. Telegram / WhatsApp alerts.
