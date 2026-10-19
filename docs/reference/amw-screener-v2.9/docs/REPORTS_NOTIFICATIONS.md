# Reports, charts, notifications, schedules and plans

## Canonical signal record (`amw.signal.v1`)
Every qualified/candidate signal from a scan is stored once per owner (`signals` table) with its full payload:
strategy + version, setup, timeframe, direction, signal bar, data bar, condition-by-condition results (passed / failed /
not evaluable, mandatory flags, actual values and thresholds in the detail text), score components, entry zone, stop,
target, invalidation, expiry, warnings, **provider, data kind, data snapshot fingerprint** (SHA-256 of the OHLCV window
the signal used) and evaluation time. Lifecycle transitions are separate events; the record itself is never overwritten.
Dashboard, reports, chart PNGs, Telegram and email all render from this stored record.

## Reports (`src/reports/`)
* Formats: **PDF** (reportlab; quick / standard / detailed), **XLSX** (Signal, Conditions, Score, Bars, Disclaimer
  sheets), **CSV**, **JSON**, **PNG** (annotated chart).
* The chart is recomputed from validated data **up to the signal's data bar** (no later candles). The report states
  whether that data still matches the stored snapshot; if the provider revised history it says DIFFERENT.
* Owner-only (generation, listing, download); files stored in `data/reports/<user_id>/` with generated safe names;
  every generation is logged with status, size and snapshot match. Plan limit: `reports_per_day`.
* UI: **Report Center** page. API: `POST /api/v1/reports`, `GET /api/v1/reports`, `GET /api/v1/reports/{id}/download`.

## Static chart renderer (`src/reports/chart_png.py`)
Headless matplotlib. Annotations come only from computed evidence: confirmed swings (with their confirmation time),
BOS/CHoCH at the candle whose close broke the level, S/R clusters, zones, EMAs, and the record's stop/target/signal bar.
`render_chart` returns the PNG and an annotation manifest; tests check every marker against the underlying candles.

## Telegram (`src/notify/telegram.py`, `src/services/alerts.py`)
* **Official AMW bot** — operator sets `AMW_TELEGRAM_BOT_TOKEN`. A user clicks *Get link code*, sends `/start <code>` to
  the bot, clicks *Verify*; AMW finds the code via `getUpdates` and stores only the chat id.
  (If the bot is configured with a webhook, `getUpdates` is unavailable — use a dedicated bot for linking.)
* **User-owned bot** — token from @BotFather + chat id; verified with `getMe` and a test message, then the token is
  encrypted (same AES-GCM scheme as AI keys, bound to user + "telegram") and shown only masked.
* AMW never asks for Telegram passwords, login codes or session strings.
* Delivery: per-user only (no shared chat), duplicate prevention per rule/signal/channel, rule cooldown, quiet hours
  (exchange time), per-user hourly cap, bounded retries (2) for 429/5xx/network, delivery log with status and error.
  Delivery status reflects the Bot API response; it does not prove the user read the message.
* Email: operator SMTP server, each user's own address.

## Schedules (`src/services/scheduler.py`)
Owner-scoped schedules: `scan` (alerts fire normally) or `scan_report` (scan, then PDF reports for the top-N qualified
signals sent to the user's linked Telegram). Daily at HH:MM or every N minutes within market hours, trading days only
(weekends and configured holidays skipped). A schedule never runs twice for the same slot or while its previous run
is active. Runs in the API process (`api_server.py` sets `AMW_SCHEDULER=1`) or the desktop app (`start_screener.bat`).
In-process scheduling does not survive restarts mid-run; missed slots are not back-filled.

## Plans and entitlements (`src/saas/entitlements.py`, `config/settings.yaml → plans`)
Limits per plan (free / pro / premium / enterprise): symbols per scan, concurrent jobs, reports per day, Lab
strategies, alert rules, schedules, minimum schedule interval. Enforced in the backend (services, API, and UI paths);
API returns 403 `plan_limit`. The values are **engineering defaults, not commercial decisions** (QUESTION_LOG Q-002).
AI usage is BYOK and not part of plans. `GET /api/v1/plan` shows limits and current usage.
