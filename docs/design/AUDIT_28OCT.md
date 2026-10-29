# AUDIT 28 Oct - verification of the owner's complete audit (ATP_ORDER_AUDIT_28OCT.md)

Checked on origin/main b86710c (read-only, 28 Oct). Verdict per item: CONFIRMED / PARTLY / NOT REPRODUCED, with the file:line
found today (line numbers in the order drifted in places). Web sources the order cites (SEBI study, complaints, competitor
pricing) were not attached to the order message; they are added here when the owner sends them - none is invented.

## V - verify-fixes
| Item | Verdict | Evidence (origin/main) | Note |
|---|---|---|---|
| V1 forming bar | CONFIRMED | workers/trading_worker.py:1676 `_evaluate_deployment`, :1685 `get_frames`, :1732 `strategy.analyze`; market_data/service.py:407-413 (`get_candles` -> `build_frames`, bars as they are); strategy_templates/adapter.py:131-150 reads `df.index[-1]` | no trim of bars with start + TF > now; only `candle_staleness` (:1702-1705). Screener trims: screener/runtime.py:115 `closed_only` (routes.py:694, :749) |
| V1 parity test | PARTLY | strategy_templates/parity.py:117-123; tests/test_tpl1_parity.py:39 | parity exists but calls `analyze()` on fixed closed bars - never through `get_frames`, so it cannot catch V1 |
| V2 after-hours P&L | CONFIRMED | portfolio/routes.py:44 / :65 / :68 (live LTP only, else "entry"); position_monitor.py:1343-1346 `_mark`, :1250-1253 open families only; frontend dashboard/model.ts:177-181 `fullyPriced` needs `price_source === "ltp"`, :186 | `mark_price` never read by the portfolio route |
| V3 VIX bands | PARTLY | dashboard/model.ts:141-148 `vixNote` words; ui/levels.ts:21-26 `VIX_BANDS` [12,16,20] + `vixLevel`, :38-44; LevelChip (rhythm/SectionRowsV2.tsx:166); ai/market_memory.py:228 | same thresholds, different words (duplicate table) |
| V4 discipline score | CONFIRMED | journal/service.py:148-151 (open rows included), :202, :214; journal/score.py:47-49, :56; rhythm/build.py:470-476 | only-open untagged trades score 100 with 0 closed |
| V5 OI hysteresis | CONFIRMED | option_chain/oi_regime.py:216, :244-255, :189-203 (:197 both sides >= 2 %, :199 calls up AND puts down) | flip needs 3 negative diffs AND puts falling >= 2 % twice; else "BULLISH (Weakening)". Doc: docs/design/OI_BANNER.md |
| V6 text budget | CONFIRMED | frontend/e2e/textBudget.ts:17-20 (`p`), :24-28 (`li`), :13 skips td / dd / th / dt | div / span text not counted |
| V7 repo hygiene | CONFIRMED | `ls-remote --heads 'claude/*'` = 620; tags = 0; no CHANGELOG; no workflow deletes branches; repo `delete_branch_on_merge` = false | |

## G1 - before public launch
| Item | Verdict | Evidence | Note |
|---|---|---|---|
| G1.1 DPDP erase / export | CONFIRMED | team/routes.py:256, :273 (OWNER erases a member); retention/service.py:180 `erase_user`; exports/routes.py:59, :69 (organisation datasets) | no /api/me/export or /api/me/erase; help.md:153 mentions "delete account" with no route |
| G1.2 legal placeholders | CONFIRMED | legal/content/privacy.md:14, :86-89; terms.md:10, :56, :60, :68; legal/documents.py:13 | no docs/runbooks/BREACH.md (no runbooks folder) |
| G1.3 /docs exposed | CONFIRMED | main.py:213-219 (no docs_url / redoc_url / openapi_url); deploy/Caddyfile:25-26 blocks /metrics only | Caddyfile.ip to check in the fix |
| G1.4 error tracking / redaction | CONFIRMED / PARTLY | no @sentry in frontend; core/logging_config.py:40-45, :85, :103 redact OAuth callback query + token params | the order's "Telegram only" is not what the code does: it redacts OAuth / token query strings; no email / phone redaction |
| G1.5 Web Vitals | CONFIRMED | no lighthouse / web-vitals anywhere | |
| G1.6 tooling | CONFIRMED | no dependabot / renovate; no ESLint; backend/ruff.toml select E4 E7 E9 F B006 S102 S307 T10 PLE; backend/mypy.ini `files =` allow-list (ci.yml:68-70) | |
| G1.7 SEBI readiness | PARTLY | compliance/static_ip.py, compliance/algo_id.py exist (tests d4 / d1); no Compliance page in routes.ts; RiskManagementPage has no disclaimer (shared `Disclaimer kind="signals"` at components/ui.tsx:121-126 unused there) | pieces exist; page + text missing |

## G2 / T - not verified line by line (proposals; each verified when its row starts)
Already present (do not rebuild): T8 public status page (platform/routes.py:64 `/status`, frontend routes.ts:74, flag
`public_status_page` flag_catalog.py:107; .github/workflows/uptime.yml) and docs/SLO.md exist - T8 extends them (per-session
uptime, degradation banner). T9: plans/registry.py:76 / :86 already give Pro and Business a 14-day trial.

## Summary
CONFIRMED 13, PARTLY 4 (V1 parity, V3, G1.4, G1.7), NOT REPRODUCED 0 of the V / G1 claims; T8 / T9 partly already built.

## G1.6 progress (28 Oct)
- Dependabot: `.github/dependabot.yml` (pip, npm weekly with minor / patch grouped; GitHub Actions monthly).
- ruff widening, measured on main: I (import order) 436 findings, all auto-fixable; UP (pyupgrade) 19,261; ASYNC 10
  (7 x "timeout parameter on an async function", 2 blocking pathlib calls, 2 test sleeps). Plan, one family per PR as
  ruff.toml asks: I next (mechanical, done when few other PRs are open - it touches many files), ASYNC with its 10 fixes,
  UP in per-package slices. ESLint + typescript-eslint and mypy on all of app follow the same pattern.

## G2.2 verification (29 Oct; branch claude/aud-g2-2-live-push)
PARTLY. Already built (TM-12, flag `data_hub`): broker websocket -> data hub -> `GET /api/live/stream` (SSE over fetch)
`ticks` / `hub` events per tab, the user's own connection only, 50 symbols per tab, 15 s heartbeat. The four literal 60 s
pollers (App.tsx:78 system status, MarketPulseCard.tsx:68 candles, OiBanner.tsx:41 and OiBannersCard.tsx:15 OI slots)
render only in today's shell (ui_v2 OFF) - the push needs ui_v2 + data_hub, and only MarketPulseCard reads prices - so
they stay. The v2 price timers now follow the push behind `live_push` (fallback to each timer while the stream is down or
quiet, or a symbol has no fresh tick): Today sparklines (dashboard/indexSparks.ts, 60 s), chart candles
(chart/useChartCandles.ts, 60 s: re-read on bar close), chart last price (components/chartHelpers.ts useLiveLtp 5 s,
charting/ltpHub.ts 5 / 20 s); the chart chip reads the age of the last tick (chart/liveChip.ts tickAgeChipState).
## G2.1 N+1 profile (29 Oct)
SQL statements of one read (auth included), 1 vs 20 rows, counted by tests/sql_counter.py in
backend/tests/test_aud_g2_1_n_plus_one.py (SQLite test engine; the same statements reach PostgreSQL).

| Endpoint | Rows varied | Before (1 / 20) | After (1 / 20) | Finding / fix |
|---|---|---|---|---|
| GET /api/positions | open trades | 3 / 3 | 3 / 3 | none (one list query + one batched account-label read) |
| GET /api/orders | orders | 3 / 3 | 3 / 3 | none |
| GET /api/option-chain/{u}/strikes | stored slots of the day | 11 / 11 | 11 / 11 | none (snapshots, then all strikes in one IN query) |
| GET /api/option-chain/banners | enabled underlyings | 11 / 134 | 9 / 9 | N+1: ~6 queries per underlying (settings, flag, slots, strikes, latest capture, exchange, holidays). Fixed: `snapshots.settings_rows_many` / `day_chains_many` / `latest_captures` (one UNION ALL of per-underlying LIMIT 1 reads) and one market-status read per exchange; `enabled_underlyings` filtered to the caller's organisation in SQL. Same response (test compares each entry with GET /{u}/banner). The own-broker tier (B1) path still reads per underlying. |
| GET /api/screener/evidence/runs/{id} | evidence rows of a run | 7 / 7 | 7 / 7 | none (no list-of-runs endpoint exists; GET /api/screener/runs/{id} is one row) |
| GET /api/journal/week | trades of the week | 11 / 30 | 11 / 11 | one `widen` UPDATE per trade on every read. Fixed: `capture.widen_many` - the same atomic UPDATE as one executemany round trip. |

Tracing (same item): app/observability/tracing.py, OFF unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set; `trace_id` / `span_id`
in every JSON log line written inside a span.
