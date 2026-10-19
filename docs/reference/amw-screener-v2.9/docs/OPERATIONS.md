# Operations runbook

## Health
* `GET /health/live` — process alive. `GET /health/ready` — database reachable (503 otherwise).
* `GET /api/v1/admin/health` (admin) — DB ping/schema version, provider requests/last error, cache hits/misses,
  jobs, API request count, error count, p50/p95 latency.
* Dashboard → *Settings & System Health* shows the same plus recent warnings/errors from `amw_screener.log`.

## Backups (PostgreSQL)
```bash
docker compose exec -T db pg_dump -U amw -Fc amw > backup_$(date +%F).dump      # daily, keep 14+
# restore into a fresh database
docker compose exec -T db createdb -U amw amw_restore
docker compose exec -T db pg_restore -U amw -d amw_restore < backup_YYYY-MM-DD.dump
```
SQLite (desktop): stop the app and copy `data/amw.db`.
Generated report files live in the `reports` volume (Docker) or `data/reports/` (desktop); back them up with the
database (`docker run --rm -v <project>_reports:/r -v "$PWD":/b alpine tar czf /b/reports_$(date +%F).tgz -C /r .`).
`AMW_SECRET_KEY` (or desktop `data/.amw_master.key`) is required to decrypt stored AI keys, FYERS credentials and bot
tokens: store it separately from database backups — without it those secrets must be re-entered.
Test a restore at least monthly; the application runs migrations idempotently on start.

## Upgrade / rollback
1. Back up the database. 2. `git pull` (or extract the new release). 3. `docker compose build && docker compose up -d`.
4. Check `/health/ready`, the Strategy Lab capability table and one scan.
Rollback: redeploy the previous image tag; schema changes are additive (new tables/columns only), so the previous
version keeps working. If a release ever needs a destructive migration it will ship a documented down-migration.

## Incidents
| Symptom | Action |
|---|---|
| Many `UNAVAILABLE` / provider errors | Yahoo throttling: reduce batch size, increase pause, wait. Scans report partial status — do not treat as complete. |
| Lab runs FAILED with "sandbox could not start" | Container lost `CAP_SYS_ADMIN` or base image changed: restore compose settings, check the capability table. |
| A job/lab run/scheduled run shows FAILED "interrupted: … restarted" | Expected after a crash/restart: in-process work does not continue across restarts; on start-up AMW marks it FAILED (jobs after 2 min without heartbeat, other runs after 30 min). Run it again. |
| Report shows DELETED | Removed by the plan's retention period or storage quota (reason stored on the row). |
| Alerts not delivered | *Watchlist & Alerts → Delivery history* shows `failed/skipped` with the reason (unconfigured channel, provider error). |
| FYERS feed `TOKEN_EXPIRED` | Normal every morning: the user logs in on *Live Data (FYERS)* (or via the API); the feed resumes by itself. |
| FYERS feed `RATE_LIMIT_GUARD` | FYERS returned HTTP 429 twice today; calls stay paused until the next day. Reduce symbols or use a longer poll interval. |
| "already running in another AMW process" | One feed per account across dashboard and API. Press Stop (any process), then Start where you want it. A crashed holder is replaced after 90 s. |

## Data retention
Signals, lifecycle events, lab runs and audit logs grow over time. Archive or delete rows older than your retention
period (e.g. 12 months) with scheduled SQL; OHLCV is cached on disk only (`.cache/`, safe to delete).
FYERS 1-minute bars (`minute_bars`) are purged automatically after 30 days by the running collector.
User deletion: `DELETE FROM users WHERE id = …` cascades to watchlists, presets, alert rules, journal, lab strategies.

## Provider failover (optional, `src/data/failover.py`)
Only providers with the SAME data kind can be paired (delayed and live/broker data are never mixed). Each symbol's frame
comes from one provider; the canonical record stores the actual source. Run `reconcile()` regularly: a symbol whose
recent closes differ by more than `max_discrepancy_pct` is not failed over (reported unavailable). After
`fail_threshold` consecutive primary errors the primary is skipped for `cooldown_s`. Not enabled by default because no
second provider of the same kind is configured (QUESTION_LOG Q-001).

## Scaling the API
`AMW_API_WORKERS=4 python api_server.py` (or `uvicorn api_server:app --workers 4`) runs several API processes; measured
14x higher throughput for DB-heavy reads. Scheduled slots run once (atomic claim), one FYERS collector per account
(lease), job results are stored in the database. Rate limiting is per process. Long actions can be submitted with
`?async_job=true` and polled at `/api/v1/jobs/{id}`.

## Report retention vs backups (Q-005)
Report FILES are deleted after the plan's retention period (provisional: Free 7, Pro 90, Premium 365, Enterprise 365
days) by the daily maintenance run; the database row stays with status DELETED and the reason. Protected: reports on
legal hold (`PUT /api/v1/admin/reports/{id}/legal-hold`), reports younger than 30 minutes (possible delivery in
progress) and reports not COMPLETED. A failed file deletion leaves the row COMPLETED and is retried next run; each
run is written to the audit log (`report_retention`). Database and volume BACKUPS keep their own retention: a report
deleted by retention can remain in backups until those backups expire - do not promise immediate removal from backups.

