# REST API (`/api/v1`)

Generated from the application's OpenAPI schema by `python scripts/gen_api_docs.py`. Interactive docs: `http://127.0.0.1:8000/docs`.

**Auth:** `Authorization: Bearer <token>` (create with `python -m src.api.manage create-user <name> [admin|analyst|viewer]`). `AMW_AUTH_MODE=local` disables auth for single-user desktop use. **Roles:** viewer = read-only, analyst = scans/own data, admin = everything. **Errors:** JSON `{"detail": ...}` (4xx) or `{"error": ..., "request_id": ...}` (429/500); every response carries `X-Request-ID`. **Rate limit:** `AMW_RATE_LIMIT_PER_MIN` per token/IP. There are **no order-placement endpoints**; the `/broker/fyers` routes only connect the user's own FYERS account for market data.

**AI (BYOK):** `/api/v1/ai/*` endpoints use only the caller's own provider keys; keys are accepted on `PUT /ai/keys/{provider}` and never returned (masked only). See docs/AI_BYOK.md.

| Method | Path | Summary | Parameters / body |
|---|---|---|---|
| GET | `/health/live` | Live | - |
| GET | `/health/ready` | Ready | - |
| GET | `/api/v1/meta` | Meta | - |
| GET | `/api/v1/universes/{name}` | Universe | `name` |
| GET | `/api/v1/ohlcv/{ticker}` | Ohlcv | `ticker`, `tf`, `bars` |
| GET | `/api/v1/features/{ticker}` | Features | `ticker`, `tf` |
| POST | `/api/v1/scans` | Start Scan | body `ScanRequest` |
| GET | `/api/v1/jobs` | Jobs | - |
| GET | `/api/v1/jobs/{job_id}` | Job | `job_id` |
| POST | `/api/v1/jobs/{job_id}/cancel` | Cancel | `job_id` |
| GET | `/api/v1/jobs/{job_id}/signals` | Job Signals | `job_id`, `strategy`, `direction`, `min_score`, `include_rejected`, `page`, `page_size` |
| GET | `/api/v1/signals` | Signal List | `ticker`, `limit`, `offset` |
| GET | `/api/v1/signals/{key}` | Signal Detail | `key` |
| POST | `/api/v1/evaluate/{ticker}` | Evaluate | `ticker`, `strategies` |
| POST | `/api/v1/analyst` | Analyst | body `AnalystIn` |
| GET | `/api/v1/ai/providers` | Ai Providers | - |
| PUT | `/api/v1/ai/keys/{provider}` | Ai Key Put | `provider`, body `AIKeyIn` |
| DELETE | `/api/v1/ai/keys/{provider}` | Ai Key Del | `provider` |
| PUT | `/api/v1/ai/keys/{provider}/model` | Ai Key Model | `provider`, body `AIModelIn` |
| PUT | `/api/v1/ai/policy` | Ai Policy | - |
| GET | `/api/v1/ai/usage` | Ai Usage | `limit` |
| POST | `/api/v1/ai/draft-strategy` | Ai Draft | body `AIDraftIn` |
| POST | `/api/v1/ai/dual-review` | Ai Dual | body `AIDualIn` |
| GET | `/api/v1/watchlists` | Wl | - |
| POST | `/api/v1/watchlists` | Wl Save | body `WatchlistIn` |
| DELETE | `/api/v1/watchlists/{name}` | Wl Del | `name` |
| GET | `/api/v1/alerts/rules` | Rules | - |
| POST | `/api/v1/alerts/rules` | Rule Add | body `AlertIn` |
| DELETE | `/api/v1/alerts/rules/{rule_id}` | Rule Del | `rule_id` |
| GET | `/api/v1/alerts/events` | Alert Events | - |
| GET | `/api/v1/journal` | Jl | - |
| POST | `/api/v1/journal` | Jl Add | body `TradeIn` |
| POST | `/api/v1/backtests` | Backtest | `async_job`, body `BacktestIn` |
| GET | `/api/v1/lab/capabilities` | Lab Caps | - |
| GET | `/api/v1/lab/templates` | Lab Templates | - |
| GET | `/api/v1/lab/strategies` | Lab List | - |
| POST | `/api/v1/lab/strategies` | Lab Create | body `LabStrategyIn` |
| GET | `/api/v1/lab/strategies/{sid}` | Lab Get | `sid` |
| PUT | `/api/v1/lab/strategies/{sid}` | Lab Draft | `sid`, body `LabDraftIn` |
| DELETE | `/api/v1/lab/strategies/{sid}` | Lab Del | `sid` |
| POST | `/api/v1/lab/strategies/{sid}/publish` | Lab Publish | `sid`, `note` |
| POST | `/api/v1/lab/validate` | Lab Validate | body `LabValidateIn` |
| POST | `/api/v1/lab/render` | Lab Render | - |
| POST | `/api/v1/lab/export/python` | Lab Export | - |
| POST | `/api/v1/lab/strategies/{sid}/scans` | Lab Scan | `sid`, body `LabScanIn` |
| POST | `/api/v1/lab/strategies/{sid}/backtests` | Lab Bt | `sid`, `async_job`, body `LabBacktestIn` |
| GET | `/api/v1/lab/runs` | Lab Runs | `sid` |
| POST | `/api/v1/reports` | Report Create | `async_job`, body `ReportIn` |
| GET | `/api/v1/reports` | Report List | - |
| GET | `/api/v1/reports/{rid}/download` | Report Download | `rid` |
| GET | `/api/v1/schedules` | Sched List | - |
| POST | `/api/v1/schedules` | Sched Add | body `ScheduleIn` |
| DELETE | `/api/v1/schedules/{sid}` | Sched Del | `sid` |
| GET | `/api/v1/notify` | Notify Get | - |
| PUT | `/api/v1/notify` | Notify Put | body `NotifyIn` |
| POST | `/api/v1/notify/telegram/official/start` | Tg Start | - |
| POST | `/api/v1/notify/telegram/official/verify` | Tg Verify | - |
| PUT | `/api/v1/notify/telegram/own` | Tg Own | body `OwnBotIn` |
| DELETE | `/api/v1/notify/telegram` | Tg Unlink | - |
| PUT | `/api/v1/broker/fyers` | Fy Connect | body `FyersConnectIn` |
| DELETE | `/api/v1/broker/fyers` | Fy Disconnect | - |
| GET | `/api/v1/broker/fyers/login-url` | Fy Login | - |
| POST | `/api/v1/broker/fyers/callback` | Fy Callback | body `FyersCallbackIn` |
| POST | `/api/v1/feed/start` | Feed Start | body `FeedStartIn` |
| POST | `/api/v1/feed/stop` | Feed Stop | - |
| GET | `/api/v1/feed/status` | Feed Status | - |
| GET | `/api/v1/feed/bars` | Feed Bars | `symbol`, `since`, `limit` |
| GET | `/api/v1/scanner/templates` | Scanner Templates | - |
| GET | `/api/v1/scanner/templates/{tid}` | Scanner Template | `tid` |
| POST | `/api/v1/scanner/nl` | Scanner Nl | body `ScannerNLIn` |
| POST | `/api/v1/scanners` | Scanner Save | body `ScannerSaveIn` |
| POST | `/api/v1/scanners/{sid}/duplicate` | Scanner Dup | `sid`, `name` |
| POST | `/api/v1/scanners/{sid}/share` | Scanner Share | `sid`, `to_username` |
| POST | `/api/v1/scanners/{sid}/replay` | Scanner Replay | `sid`, body `ReplayIn` |
| POST | `/api/v1/scanners/{sid}/compare` | Scanner Compare | `sid`, body `CompareIn` |
| GET | `/api/v1/instruments` | Instruments Search | `q`, `limit` |
| GET | `/api/v1/instruments/resolve` | Instruments Resolve | `text` |
| GET | `/api/v1/forward` | Forward List | `status`, `limit` |
| POST | `/api/v1/forward/mark` | Forward Mark | - |
| GET | `/api/v1/data-source` | Data Source Get | - |
| PUT | `/api/v1/data-source` | Data Source | `source` |
| GET | `/api/v1/plan` | Plan | - |
| GET | `/api/v1/admin/health` | Ahealth | - |
| GET | `/api/v1/admin/audit` | Audit | - |
| POST | `/api/v1/billing/webhook` | Billing | - |

## Request schemas

* **AIDraftIn** — `description`*, `language`, `provider`, `authorized`
* **AIDualIn** — `ticker`*, `timeframes`, `question`, `authorized`
* **AIKeyIn** — `api_key`*, `model`, `session_only`
* **AIModelIn** — `model`*
* **AlertIn** — `name`*, `strategies`, `min_score`, `directions`, `timeframes`, `symbols`, `cooldown_minutes`, `channels`
* **AnalystIn** — `ticker`*, `timeframes`, `question`, `use_llm`, `provider`
* **BacktestIn** — `ticker`*, `timeframe`, `strategy`*, `start`, `end`
* **CompareIn** — `version_a`*, `version_b`*, `symbols`*, `timeframe`
* **FeedStartIn** — `symbols`*, `poll_every_min`
* **FyersCallbackIn** — `auth_code`*, `state`*
* **FyersConnectIn** — `app_id`*, `secret`*, `redirect_uri`*
* **LabBacktestIn** — `version`, `ticker`*, `timeframe`, `params`, `start`, `end`
* **LabDraftIn** — `source`, `ir`, `spec`
* **LabScanIn** — `version`, `universe`, `symbols`, `limit`, `params`
* **LabStrategyIn** — `name`*, `language`*, `description`, `source`, `ir`, `spec`
* **LabValidateIn** — `language`*, `source`, `ir`, `spec`
* **NotifyIn** — `email`, `quiet_hours`, `max_per_hour`
* **OwnBotIn** — `bot_token`*, `chat_id`*
* **ReplayIn** — `as_of`*, `symbols`*, `version`
* **ReportIn** — `signal_key`*, `format`, `mode`
* **ScanRequest** — `universe`, `symbols`, `strategies`, `limit`
* **ScannerNLIn** — `text`*
* **ScannerSaveIn** — `name`*, `ir`*, `envelope`, `description`, `scanner_id`, `publish`, `note`
* **ScheduleIn** — `name`*, `kind`, `scanner_id`, `scanner_version`, `universe`, `symbols`, `strategies`, `limit`, `top_n`, `at_time`, `interval_minutes`, `trading_days_only`
* **TradeIn** — `symbol`*, `strategy`, `direction`*, `entry_price`*, `qty`*, `stop`*, `target`, `entry_time`*, `notes`, `signal_key`
* **WatchlistIn** — `name`*, `symbols`*

`*` = required.

## WebSocket

`WS /api/v1/stream/bars` — live stream of the caller's **own** completed 1-minute FYERS bars (not in the OpenAPI schema). First message within 10 s: `{"token": "<api token>", "symbols": ["RELIANCE.NS"]}` (token omitted in `AMW_AUTH_MODE=local`). Messages: `subscribed`, `bar`, `revision` (upsert by `symbol`+`ts`), `status`, `error` (close code 4401). Details: docs/LIVE_DATA_FYERS.md.
