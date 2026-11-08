# BOT_GUIDE_GAPS - five gaps between the bots guide and the code

Audit of docs/guides/bots-guide.html against the code on origin/main. Read only: nothing here is built. Each fix that
changes behaviour is marked **needs the owner's approval** (CLAUDE.md section 8). Live flag values on production were not
read; where a result depends on a flag, the flag is named and its state is "not known".

## Summary (numbers first)

| # | Question | Answer from the code | Behaviour change in the fix? |
|---|---|---|---|
| 1 | Library stop / targets "Suggested - change in settings": where used, where set? | Used in every signal (stop, targets, position size); **0** places a user can change them for a bot | Yes - needs the owner's approval |
| 2 | User kill switch control? | **8** kill switch endpoints; **1** UI control (the platform switch, admin only); **0** for account or strategy | Yes (new user control) - needs the owner's approval |
| 3 | "Closes before the market does" vs "ATP closes nothing by itself" | **3** UI lines promise a close; the worker closes an intraday position **only** at the user's own Square-off time | Text only: no. Asking for the time: yes |
| 4 | Paper bot on a data-only Upstox connection (Analytics Token)? | **No new entries**: the bot waits "broker login needed"; exits run on stored prices | Yes - needs the owner's approval |
| 5 | Builder test with no broker? | Only Daily / Weekly NSE (flag `backtest_stored_history` on) and 15m / 30m / 1h of a backfilled index; **any 1 / 5 min test is refused** | Text only: no. More data: yes |

## 1. Library stop / targets have no field

Evidence
- The note: `frontend/src/ui/strategyLabels.ts:72-75` (`SUGGESTED_KEYS = ["atr_mult_sl", "target_rr"]`,
  `SUGGESTED_NOTE`), shown by `frontend/src/ui/LibraryChips.tsx:21-22, 86-87`.
- The values are the strategy class's `default_params`, e.g. `backend/app/strategy_engine/indicator_strategies.py:30-31`,
  `combo_strategies.py:81, 121, 161, 204`, `swing_strategies.py:49, 96`; merged once at `base.py:19-20`.
- They are used in every signal: `base.py:61-95` (`build_signal`: stop, target1 / target2 from `target_rr`), then the
  order: `backend/app/execution/router.py:339` (stop and targets stored on the order), `router.py:346-352` (the broker
  stop), and the size: `backend/app/risk_engine/risk_manager.py:148` (`risk_per_unit = entry - stop`).
- A bot never gets other values: `backend/app/workers/trading_worker.py:2157` calls `resolve_strategy`, which returns the
  shared built-in as is (`backend/app/custom_strategies/resolver.py:98`, `registry.get`). `DeploymentCreateRequest`
  (`backend/app/deployments/routes.py:271-308`) has no strategy-settings field; `template_exits` (line 307) is for
  templates only (`PaperBotV2.tsx:596-597`).
- Only the backtest API takes an override: `backend/app/backtest/routes.py:177, 199-200` (`strategy_params`); no frontend
  file sends `strategy_params` (grep of `frontend/src`: 0 hits).
- The owner's decision behind the label: DECISIONS 2026-10-30 STRAT-X (D2: "shown as suggested user settings").

Proposed fix (**needs the owner's approval**: changes order levels and size; migration yes). Add an optional
`strategy_settings` object to `DeploymentCreateRequest`, limited to `SUGGESTED_KEYS`, stored on the bot (one new JSON
column), and applied in `_evaluate_deployment` on a copy of the strategy (`copy.copy` + params merge, the same way
`backtest/routes.py:194-200` does it), never on the shared registry object. Show both values in the bot form under
"Stop and targets (suggested values, yours to change)", pre-filled with the suggestion, and send the same values to the
backtest so test = bot. Until then, the Library note should read "Fixed in this strategy for now" so the page does not
promise a setting that does not exist (text only, no behaviour change).

## 2. Kill switches: server only

Evidence (endpoints, `backend/app/kill_switch/routes.py`, prefix `/api/kill-switch`, line 23)
- `GET /status` (51), `POST /global/engage` (75) and `/global/disengage` (94): super admin only.
- `POST /tenant/engage` (105) and `/tenant/disengage` (117), `POST /strategy/{strategy_id}/engage` (127) and
  `/disengage` (141), `POST /emergency-exit` (203): any trader (`require_trader`); the docstring at 110-111 calls the
  account switch "the 'stop everything for my account' panic button".
- Automatic use: a LIVE loss-limit breach engages the strategy or account switch (`backend/app/risk_engine/hierarchy.py:
  250-260`); a PAPER block never does (`backend/app/risk_engine/paper_nr.py:29-30`).

Evidence (UI)
- The only control: the platform switch in the admin console, `frontend/src/pages/AdminPage.tsx:152-165`, calling
  `frontend/src/api/client.ts:1299-1302` (global only). No frontend file calls `/tenant/`, `/strategy/.../engage` or
  `/emergency-exit` (grep: 0 hits).
- Read-only state: the status bar, `frontend/src/terminal/StatusBarV2.tsx:125-128` ("New orders stopped / allowed").
- A dead end: the go-live check says "A kill switch is on. Turn it off once you know why it was used." and links to
  `/risk-management` (`frontend/src/setup/goLive.ts:45`); that page has no kill switch control
  (`frontend/src/pages/RiskManagementPage.tsx`, `frontend/src/trading/RiskV2.tsx`: 0 hits); `RiskPartsV2.tsx:233` only
  explains that a loss limit turns it on.
- Near alternatives that do exist: PAPER pause / resume (`backend/app/paper/routes.py:51-62`, buttons in
  `frontend/src/components/PaperAccountCard.tsx:88-92`), per-bot Pause / Stop (`deployments/routes.py:974, 992, 1019`),
  and "Exit all" for PAPER positions (`frontend/src/trading/ExitAllV2.tsx:26-78`).

Answer: no user-facing account or strategy kill switch control exists; a switch the risk engine turns on after a LIVE
loss can be turned off only through the API.

Proposed fix (**needs the owner's approval**: a new user control, A.5 / D.1). Add a "Stop new orders" card on the Risk
page: one account switch (engage with a reason, disengage) and a list of strategy switches from `GET /status`, each with
a ConfirmDialog and the line "Exits still run" (ADR-0004). Keep `/emergency-exit` out of the UI until LIVE (it needs
prices per symbol, routes.py:151-156). No backend change, no migration. Until then, change the go-live sentence
(goLive.ts:45) so it does not send the user to a page without the control.

## 3. "Closes before the market does" is not what the code does

Evidence (texts)
- `frontend/src/paperbot/model.ts:23` (ORB: "... Closes before the market does.") and `model.ts:24` (VWAP + Supertrend:
  "... and closes before the market does.").
- `frontend/src/setup/model.ts:304` (Intraday suggestion: "Trades NIFTY 50 during the day and closes before the market
  does.").
- `backend/app/me/trade_settings.py:263-265`: Square-off time, "Intraday positions are closed at this time. Not set: ATP
  closes nothing by itself." (owner decision D1).

Evidence (behaviour)
- The worker squares off an INTRADAY position only at its owner's own Square-off time:
  `backend/app/workers/trading_worker.py:102-107, 1608-1613, 2810-2840` (`cut is None` -> `continue`, line 2838);
  `backend/app/execution/exit_settings.py:8-10, 74-76`. SWING positions are never squared off (lines 2830-2834).
- The two strategies have no close of their own: ORB has only an entry window (`last_entry` "14:30",
  `backend/app/strategy_engine/combo_strategies.py:203, 221-223`); VWAP + Supertrend has none (`combo_strategies.py:160-162`).
- The broker's own MIS close applies to LIVE intraday orders only (a broker fact, `exit_settings.py:8-9`); a PAPER
  position with no Square-off time stays open until its stop, a target, a time exit or Exit now.

Proposed fix (text: no behaviour change). Replace the three lines with, e.g., "... then follows the move. Closes at your
own Square-off time (Settings > Trade settings > Exits); not set, it stays open until its stop or target." Optional
second step (**needs the owner's approval**: changes what a bot does): when an INTRADAY bot is started and Square-off time
is Not set, the form asks for it, the same "Needs your setting" pattern used for order style and lots.

## 4. A paper bot on a data-only Upstox connection takes no entries

Evidence
- The worker's broker sessions come from `_usable_adapter`, which needs the daily login (`token_is_usable`):
  `backend/app/workers/trading_worker.py:1465-1474, 2676-2695`; `backend/app/brokers/token_lifecycle.py:128-132`.
  The Analytics Token counts only in `data_is_usable` (`token_lifecycle.py:139-145`).
- No such session -> `no_session` (line 1507), `prepare = False` (1509): every bot gets "No usable broker session ..."
  and `WAIT_BROKER_LOGIN` = "Waiting: broker login needed" (lines 116, 1510-1515); no entry is evaluated, PAPER or LIVE.
- Prices still flow in one case only: flag `data_hub` on and `byob_data_tier` off, through `_data_only_adapter`
  (lines 1494-1503, 3049-3062); its comment says "Entries still need the daily login (no_session)" (line 1499).
  Production flag states: not known.
- Exits keep running on the newest stored price (`StoredTickPrices`, line 1530; ADR-0004).
- Other jobs already use the token: the market read (lines 1217-1221, DATA-FIX), charts / scanner / option chain
  (`backend/app/brokers/upstox_analytics.py:1-14`), backtests (`backend/app/backtest/bar_source.py:4`).

Answer: no. On a data-only connection a paper bot keeps its exits but takes no new entries until the daily login.

Proposed fix (**needs the owner's approval**: changes when PAPER entries run). In `_process_tenant`, when there is no
daily session but `_data_only_adapter` returns one, build `market_data` from it and evaluate PAPER bots only; LIVE bots
and every order call stay gated on the daily login. Tests: PAPER entry on a data-only row, LIVE stays waiting, parity of
bars with the daily-login path. No migration. Until then, the bots guide and the bot card should say "A paper bot needs
the daily broker login; the Analytics Token alone keeps charts and exits only."

## 5. The builder's test without a broker

Evidence
- The builder's "save and test": `frontend/src/strategyFlow/StrategyFlowV2.tsx:502, 517-538`, through
  `useCandleSource(30, { storedHistory: true })`.
- Client side: with no usable source and the flag `backtest_stored_history` on, the bars come from
  `POST /api/backtest/bars` (`frontend/src/components/DataSource.tsx:103, 131-150`); with the flag off it stops before any
  call with "Connect your broker on the Broker page to run this on real prices." (`DataSource.tsx:48, 155`).
  An Analytics Token row counts as a usable source (`backend/app/market_data/candles_routes.py:134`, `data_is_usable`).
- Server side: `backend/app/backtest/bar_source.py:286-338` (`resolve`): broker first; else (flag on) stored Daily /
  Weekly for NSE only (`lake_frame`, lines 333-338; other exchanges: `STORED_ONLY_NSE`, line 57); 15m / 30m / 1h only for
  a backfilled index (`_STORED_TF`, line 99; lines 323-331); every other intraday timeframe gets 409
  "Intraday backtests need a connected broker. Connect it on the Broker page, or test the strategy on the Daily chart."
  (`INTRADAY_NEEDS_BROKER`, lines 55-56, 331). The run itself re-reads the bars on the server
  (`backend/app/backtest/console_routes.py:52-69`, `server_candles.fill`).

Answer: partly. With no broker the test runs only on Daily / Weekly NSE symbols (flag on; production state not known)
and on 15m / 30m / 1h of a backfilled index; a 1 or 5 minute strategy, a non-index intraday test and any non-NSE symbol
are refused with a plain message.

Proposed fix (text / UI hint: no behaviour change). On the builder's timeframe step and Result step, when no broker is
usable, say before the run which timeframes can be tested ("Without a broker: Daily and Weekly, and 15m / 30m / 1h on
indices") and offer "Test on the Daily chart" in place of a refusal after the click. Widening stored intraday data to
stocks or 5 minute bars changes data sources and their licence position (OPEN_QUESTIONS BSH-2): **needs the owner's
approval**.
