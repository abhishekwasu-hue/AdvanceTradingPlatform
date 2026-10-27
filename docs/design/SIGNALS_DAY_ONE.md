# Signals day one

Owner order 23 Oct ("Signals not empty on day one", README row 19). Questions SIG1-1..6 in `docs/OPEN_QUESTIONS.md`.
Behind `ui_v2` + `strategy_templates` (404 while either is off). PAPER only; no migration.

## What the user sees

1. First visit on Signals: a card "Pick a ready strategy and a list to watch" - two or three ready strategies (white box:
   the idea and every default setting, the rules) and a list (NIFTY 50 by default; other index / sector lists, his own
   lists). "See its settings" shows every default before he confirms; "Adopt on paper and read my list" adopts it.
2. His watch: a headline ("1 new setup, 1 waiting for its trigger on your list of 50."), the symbols that stand out
   (a new setup, one waiting for its trigger, one formed recently) with Long / Short, the state in structure words and
   the area as a price range; the rest folded; plain empty states when there is no data. Refresh, change, stop, and
   "Start a paper bot with it" (the existing form, the template picked).

## How it works

- `app/signals_feed/day_one.py`: offers, lists, the stored adoption (internal `user_prefs` row `signals_day_one`, not a
  prefs route namespace), the daily-lake read (`load_daily`), the evaluation (`read_symbol` / `evaluate`).
- `read_symbol` runs `engine.compute` - the series the scan, both backtests and the worker read - on the worker's own
  window (`history_bars`, R6) of closed Daily bars, and reads the last bar: a signal there ("new"), an instance still
  armed after the last bar (`SignalSeries.armed_end`, additive: it never changes a signal) ("armed"), a signal within the
  display window ("recent"), the warm-up, or "watching".
- Text: the template's own signal text, or the fixed state lines; every line checked by `app/signals/language.check`
  on `signal_own` and withheld on a problem (backend and again in the frontend).
- Routes (`app/signals_feed/day_one_routes.py`): `GET /api/signals/day-one`, `POST .../adopt`, `POST .../refresh`,
  `DELETE /api/signals/day-one`. Writes need a trading role; everything is the caller's own (tenant and user).
- Frontend: `src/signals/DayOneV2.tsx` (container + `SetupView` + `WatchView`), `src/signals/dayOne.ts` (API, helpers),
  on `SignalsV2` under the PageDish.

## Tests

`backend/tests/test_signals_day_one.py` (flags, card, adopt PAPER / approval-only, nothing created, viewer, states,
parity with the scan and worker paths, loader closed shared bars only, empty states, refresh / stale / outdated / stop,
tenancy, language), `tests/test_signal_language_guard.py` (registered producer), `frontend/src/signals/DayOneV2.test.tsx`,
`frontend/e2e/signals-day-one-v2.e2e.ts` (390 and 1440, light and dark).
