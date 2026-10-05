# ADR-0011: Production hosting for the first PAPER days

**Date:** 2026-10 · **Status:** accepted · **Phase:** AX

## Context
The platform has only ever run against mocked brokers. The first full PAPER day on a real Upstox
account needs a host that runs the six compose services (Postgres with WAL archiving, Redis, API,
worker, frontend, backup) through the session without contention, reachable over HTTPS for the
browser and for the broker's OAuth redirect, with a stable public IP, and with backups that leave
the machine. The operator already runs the Trade repository's bots on a 1 GB DigitalOcean droplet.

Measured baseline (`docs/PERFORMANCE.md`): 294 req/s with a worst p95 of 150 ms on a 2 vCPU
sandbox with a single uvicorn worker. Resident memory of the stack at rest is about 1.6 GB
(Postgres, Redis, two Python processes, nginx), before the browser-facing build.

## Decision
1. **One DigitalOcean Basic droplet, 2 vCPU / 4 GB** (about $24/month), dedicated to this
   platform. The existing 1 GB droplet is not enough for the stack and keeps running the Trade
   bots untouched. Reserved/static IPv4 comes with the droplet; register it with the broker under
   the SEBI retail-algo framework (static-IP whitelist) when LIVE is considered.
2. **Postgres stays in compose** for the PAPER days (no managed database yet, re-decided before
   LIVE). Backups: the existing backup service (daily `pg_dump`, WAL archive, base backups) plus
   an hourly **off-site copy to DigitalOcean Spaces** (about $5/month) by the `offsite` service in
   `docker-compose.prod.yml` (`scripts/backup/offsite_sync.sh`, rclone).
3. **Caddy** as the only public edge (`deploy/Caddyfile`): automatic Let's Encrypt certificate
   for `DOMAIN`, `/api/*` to the API, the rest to the frontend, `/metrics` hidden; the API is bound
   to 127.0.0.1 and the frontend is not published. A domain is about Rs 1,000 a year.
4. **A separate Upstox API app for this platform.** Upstox issues one access token per app per
   day and a new login invalidates the previous token of the same app; sharing the app with the
   Trade bots would log each other out during the session.
5. Monthly cost: about $30 (droplet $24, Spaces $5, domain), rising to about $45 if a managed
   Postgres is added before LIVE.

## Consequences
- One host, one database: a host failure is a restore from the off-site copy (RTO per
  `docs/OPERATIONS.md` 1.1), acceptable for PAPER; LIVE revisits a managed database and a second
  host.
- `scripts/deploy.sh production` now includes the production overlay; Compose v2.24+ is required
  for the port overrides.
- The Trade bots and this platform never share a broker app or a host; neither can disturb the
  other's token or memory.
