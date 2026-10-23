# The day-one trader test (owner order 23 Oct, README row 19)

Owner order (DECISIONS 2026-10-23, trader review): "a day-one trader test with screenshots (Today → watchlists → a scan →
a Copilot question → a strategy from an idea → backtest → adopt → its signals) before README row 19 is done".

The test is `frontend/e2e/day-one-trader-v2.e2e.ts` (Playwright, project `phone`). It walks ONE new PAPER user (role
USER, no trades yet, his own AI key connected, his trading default "each entry waits for my Approve") through the whole
flow in one test, at 390 and 1440, light and dark. This PR changes no product code; the findings below are for later
PRs.

## How it is built

- **Flags**: every flag of the trader pack, read from `backend/app/platform/trader_pack.py` at run time (the two never
  drift), plus `backtest_stored_history`. That one is an owner decision flag (pending): the test turns it on **only in
  its mocked `/api/system/features`**. Nothing changes on any server.
- **Data**: every reply is made up and written in the spec (no new fixture file). Public lists are names only (NIFTY 50
  from `src/screener/universe.ts`); the backtest bars are a generated walk, never read from a file; signal text uses
  structure words (the spec checks the watch has no buy / sell / target / stop-loss words). Existing fixtures reused:
  `setup_v2.json` (strategies), `copilot_tools_v2.json` (the backend's rule answer), `copilot_chat_v2.json`,
  `ideas_v2.json`, `src/rhythm/fixtures.ts` (the morning brief, his own sections emptied with the server's sentences).
- **Broker**: logged in for steps 1-5 and 8; "the next morning, before his broker login" for steps 6-7 (token EXPIRED,
  market-data sources not usable), which is when the stored-history backtest applies.
- **Baselines**: none committed, as the other recent walk-through specs (`signals-day-one-v2`, `copilot-tools-v2`). Run
  with `D1T_SHOTS=<folder>` to save each step as `<step>-<width>-<theme>.png` and the developer words seen on each page
  as `words-<width>-<theme>.json`. The `settle()` font override (DejaVu Sans, no caret) is the same as the other specs.
- Calls the spec does not mock answer 404, so an empty panel caused only by the mocks is not listed as a finding (the
  watchlist quotes not matching the chart's index level are the mock's numbers, for example).

## The steps and what is asserted

| Step | Screenshot | Asserted |
|---|---|---|
| 1 Today | `01-today` | the v2 dashboard opens |
| 2 Charts watchlist | `02a-charts-watchlist`, `02b-charts-list-groups` | the read-only Indices list (5); the list browser groups Indices / Index stocks / Sectors / F&O / My lists; NIFTY 50 stocks under Index stocks (phone: the Watchlist sheet) |
| 3 Find setups | `03a-find-setups`, `03b-screen-gallery`, `03c-scan-matches` | NIFTY 50 chosen on open ("50 stocks · NIFTY 50"); "Try as a scan" in the gallery runs at once on the 50 names; a match row shows |
| 4 Copilot | `04a-copilot`, `04b-copilot-answer` | "Find stocks in a daily uptrend near a demand zone" answers; "Tools used: Screen builder, Your watchlist, Screener" |
| 5 Ideas | `05a-idea`, `05b-idea-adopt-form` | Breakout and retest has "Try as a scan" and "Adopt as a paper strategy"; the link opens the Paper bot form with the template and "Starts with the settings from the idea."; nothing starts |
| 6 Backtest, no broker login | `06a-find-setups-before-login`, `06b-test-a-strategy`, `06c-backtest-stored-history` | Find setups shows the login gate; Test a strategy shows the stored-history bar; a Daily run sends `data_source: lake:daily` and shows "Prices from ATP's stored end-of-day history (NSE's daily files), 23 Oct 2025 to 22 Oct 2026." |
| 7 Adopt on paper | `07a-adopt-on-paper`, `07b-bot-started` | the idea's template, Daily, RELIANCE; "each entry waits for your Approve"; the POST is `mode: PAPER`, `entry_mode: APPROVAL`, a `template:A5:` strategy |
| 8 Signals | `08a-signals-day-one`, `08b-signals-settings`, `08c-signals-watch` | the day-one card (NIFTY 50 by default), every setting before adopting, the adopt call carries only the template and the list, his watch: "1 new setup, 1 waiting for its trigger, 1 formed recently", 46 symbols watching in structure words |

## Findings (where a day-one trader is confused or stuck), ranked by user impact

1. **The idea's strategy cannot be backtested** (`05b`, `06b`, `06c`). Strategies › Test lists only the built-in and
   custom strategies (`GET /api/strategies`), never a template, so "a strategy from the idea → backtest it" is a dead
   end: the test had to run a different built-in strategy. The Paper bot form opened from the idea has no "Test it
   first". The idea's own timeframe is 15-minute, which stored history cannot test (Daily only) and which needs a broker
   login. Suggest: a "Test it" on the idea / the Paper bot form that backtests the adopted template on Daily stored
   history.
2. **Before the day's broker login nearly every page is a wall** (`06a`). Find setups, Charts, Signals and the Copilot
   show only "Log in to Upstox"; only Test a strategy works on stored history. A trader who has not connected a broker
   on day one sees almost nothing. Suggest: let the Daily-only parts (scans on closed Daily bars, the Signals watch,
   the Copilot's rule answers) run on the stored history too, with the same plain source line.
3. **The stored-history bar is wrong and broken on the phone** (`06b-test-a-strategy-390-*`). It says "No broker
   connected" while his broker is connected (only not logged in today); at 390 the sentence wraps one word per line and
   runs under "History 30 days". The default History is 30 days, so a Daily backtest gets about 20 bars.
4. **Test a strategy is the old developer page** (`06b`, `06c`). "Event-driven simulation with position sizing...",
   "Dynamic exits (same rules the live monitor applies)", "Trade as: Underlying (cash / index level)", a JSON grid for
   "Parameter optimisation (ranked in-sample, validated out-of-sample)", "Run Monte Carlo + walk-forward", trade reasons
   `TARGET1` / `STOP`, a free-text Symbol box, the default strategy "VWAP + Supertrend" (intraday: fails without a
   broker), and a Daily chart whose time axis reads 09:15 on every label.
5. **Adopting an idea quietly picks an option** (`05b`). The form opens on NIFTY 50, so the summary reads "Trades a NIFTY
   50 option at the nearest expiry, 1 lot" for a price-action idea; the idea's settings are not shown on the form (only
   "Starts with the settings from the idea."). Two solid "Start" buttons on the page.
6. **Signals does not know the bot he just started** (`08a`). After starting the A5 bot on RELIANCE, Signals still asks
   "Pick a ready strategy and a list to watch" and shows nothing for the bot ("Waiting · 0"); the day-one watch is a
   second, separate adopt. One idea has two names: "Breakout and retest" (Ideas) and "Structure flip retest"
   (Paper bot, Signals).
7. **The Copilot answer shows code** (`04b`). "Each stage: Trend() == "UPTREND": 3 left / NearZone("demand"): 2 left",
   "within 0.5% of the nearest live zone (the function's default)"; the link says "Open the Screener" (the page is Find
   setups); the market line says "Dow structure daily uptrend, weekly range" and an ISO date "(2026-10-20)"; "More
   Copilot tools" lists seven more names (Watchtower, News Radar...) with no explanation.
8. **Find setups speaks builder language** (`03a`, `03b`, `03c`). "Scan timeframe 1d", conditions "RSI (14) 1d above
   60" / "close above EMA(close, 20)", a "Pine Script" button, "Live counts while editing", gallery cards "RSI(14) at or
   below 30", "Method × setup matrix", a lone "|" left of "Not run yet" (the empty screen name), the title cut to
   "Moment" for Momentum. The results list all 50 rows with 44 "Did not match" (the 6 matches do not stand out) and an
   empty "Stages –" column. On open the page is empty ("Run the scan to see which symbols pass"): no ready-made dish.
9. **Charts: shorthand and an odd default** (`02a`, `02b`). Timeframes "1m 3m 5m 15m 30m 1H D", "Indicators · 2" with an
   unexplained RSI 14 pane, "Volume · NIFTY OCT FUT"; the list shown first is Indices, not a list of stocks; on the
   phone the group "Indices" holds one list also called "Indices".
10. **Today has no next step to the ready watch** (`01-today`). Get started offers "Start your paper bot" and Telegram,
    "Active signals" offers "Set up a scan" (the empty builder); nothing points to the one-tap Signals watch that gives
    a day-one trader something at once. The sidebar puts "Broker" above Today.
11. **Ideas: off scans and jargon** (`05a`). Five of eight ideas say "The scans for this idea are not switched on for
    your account yet" (the fixture is the server payload captured with market zones off; check it with the trader pack
    on before acting); "a lower-timeframe change of character" (CHoCH in words, still unexplained); "The scan in full:
    RetestEntry("up", $retest_of, $retest_max_bars, TRUE)" is white box but reads as code to a beginner.
12. **Signals wording** (`08a`, `08c`). The page intro "Trades your paper bots, scans and Copilot suggest" reads close to
    advice; the watch's "Long" / "Short" chips are the only direction words and are not explained.
13. **Phone Copilot** (`04a-copilot-390-*`): in the full-page capture the sticky question box sits over the "Try one of
    these" chips; check on a device (it may be the capture only).

What worked well: NIFTY 50 is chosen on open and "Try as a scan" runs in one tap; the Copilot names its tools in plain
words; the stored-history "Prices from ..." line is clear; the Paper bot states "each entry waits for your Approve" and
starts PAPER only; the Signals watch reads every symbol in structure words with the new setups first.

## Running it

```
cd frontend && npm run build
PW_CHROMIUM=$(ls -d /opt/pw-browsers/chromium_headless_shell-*/chrome-linux*/headless_shell | head -1) \
  D1T_SHOTS=/tmp/day-one npx playwright test --project=phone e2e/day-one-trader-v2.e2e.ts
```
