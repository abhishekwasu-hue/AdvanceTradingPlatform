# Rollback rehearsal (Phase 0 exit criterion "rollback rehearsed")

**Result: PASS, 12 of 12 checks**, CI run
[38291357196](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38291357196) (job `rollback`,
2026-10-12, GitHub-hosted runner, 4 vCPU). It runs again on every push to main (`.github/workflows/staging-stack.yml`).

## What runs

`deploy/ci/rollback_rehearsal.sh` drives the owner's own command, `deploy/atp.sh`, on a throwaway clone of the commit
under test: the production compose files (`docker-compose.yml` + `prod` + `hostinger` + `deploy/compose.release.yml`),
images built from each commit and tagged with it, a pre-migration backup before every migration, the migration guard,
the rollback safety check and the health checks. Nothing is mocked. The clone's `.env` is generated with random values.

Three releases:

| Release | What it adds |
|---|---|
| N | the commit under test |
| N+1 | N plus a migration declared `ROLLBACK_SAFE = True` (a new table) |
| N+2 | N+1 plus a migration declared `ROLLBACK_SAFE = False` (a NOT NULL column with a default) |

N+1 and N+2 are local commits in the clone, never pushed, so the run sets `ATP_ALLOW_UNMERGED=1`. Also set for the
rehearsal only: `ATP_ALLOW_SESSION_RESTART=1` and `MIGRATION_FORCE=1` (CI can run during market hours on an empty
database), and `ATP_CONFIRM_DB` (the confirmation `--restore-db` asks for at the keyboard).

## Result (run 38291357196)

| Step | Result | Detail |
|---|---|---|
| deploy N | PASS | df1f92e52e67 running, schema a7d3e9c1b5f2, deep health ok (100 s, image builds included) |
| data under N | PASS | user before@example.com signed up through the API |
| deploy N+1 | PASS | 376dcbedc908 running, schema ci0rehearsal1, pre-migration backup `pre-migration/atp_advance_trading_platform_20261012T183416Z.dump.enc` (57 s) |
| data under N+1 | PASS | a `ci_rehearsal_marker` row + user during-n1@example.com |
| rollback, code-only | PASS | N running on schema ci0rehearsal1 (ahead of N, allowed because the migration is rollback-safe), deep health "degraded: behind" as designed; both users log in; the marker row is there (47 s) |
| audit chain after rollback | PASS | intact |
| deploy N+2 | PASS | 7a4083a93be0 running, schema ci0rehearsal2 (58 s) |
| data under N+2 | PASS | a marker row + user during-n2@example.com |
| code-only rollback past an unsafe migration | PASS | refused, exit 1: `rollback safety: {"db": "ci0rehearsal2", "target_head": "a7d3e9c1b5f2", "unsafe": ["ci0rehearsal2"]}`; N+2 still running, deep health ok, checkout put back |
| rollback --restore-db | PASS | N running; the database is back at ci0rehearsal1 (the backup taken just before N+2 migrated); the rows of N and N+1 are present, N+2's are only in the kept copy `advance_trading_platform_pre_rollback_20261012183703` (54 s) |
| audit chain after restore | PASS | intact |
| atp.sh health | PASS | `health: OK - 4 warning(s)` (R2 not configured, database ahead of the code after the rollback, no backup age yet; public TCP ports 22 80 443 only) |

`atp.sh releases` at the end:

```
time (UTC)           kind     release      previous     pre-migration backup                                   schema
2026-10-12T18:33:58Z deploy   df1f92e52e67 -            pre-migration/atp_advance_trading_platform_20261012T183336Z.dump.enc - -> a7d3e9c1b5f2
2026-10-12T18:34:55Z deploy   376dcbedc908 df1f92e52e67 pre-migration/atp_advance_trading_platform_20261012T183416Z.dump.enc a7d3e9c1b5f2 -> ci0rehearsal1
2026-10-12T18:35:43Z rollback df1f92e52e67 376dcbedc908 -                                                      ci0rehearsal1 -> ci0rehearsal1
2026-10-12T18:36:44Z deploy   7a4083a93be0 df1f92e52e67 pre-migration/atp_advance_trading_platform_20261012T183603Z.dump.enc ci0rehearsal1 -> ci0rehearsal2
2026-10-12T18:37:42Z rollback df1f92e52e67 7a4083a93be0 -                                                      ci0rehearsal2 -> ci0rehearsal1
running: df1f92e52e67
```

## What the Hostinger run adds

The same commands on the real host: its disk and Docker version, the R2 copy of the pre-migration backup (here R2 is
not configured: the backups stay on the runner and `atp.sh` warns), the Caddyfile for the real domain or IP, and the
owner's own `.env`. The rehearsal script is not meant for the production host (it builds throwaway releases); there the
owner rehearses with real releases: `atp.sh deploy <newer>`, then `atp.sh rollback` (runbook section 9).
