# Go-live (PAPER) - operator checklist

The same order as `docs/GO_LIVE.md`, but with the exact clicks/commands for each step, the expected output and what to
do when something goes wrong. Goal: **the first full PAPER day** on a real broker account with real data, then **5
PAPER days** in a row. LIVE is outside this checklist (GO_LIVE.md §3).

> Written in Marathi first (Phase AX, `GO_LIVE_MR.md`); translated to English in Oct 2026 - the owner's ruling: ATP is
> English only, docs included. The steps are unchanged.

> **Decision 2026-10-06 - host and broker changed:** the PAPER week runs **on your Windows PC in Docker Desktop**, broker
> **Fyers** (the current app). The local steps (`git pull`, the compose overlay, migrations, sleep/restart, the daily
> Fyers auth-code login, the read-only check, the local command of the check script) are in **`docs/LOCAL_PC.md`**. §0
> below (droplet/Caddy/Spaces/Upstox app) is kept **for later**; §1-§6 apply to both hosts, with a Fyers alternative
> next to the Upstox-specific lines. Two-way Telegram (1.9) is not available locally (it needs public HTTPS); outbound
> alerts work.

> **The secrets rule:** broker key/secret, access token, TOTP secret, AI provider key, `JWT_SECRET_KEY`,
> `SECRETS_ENCRYPTION_KEY`, Razorpay keys - never in a chat, a GitHub issue/PR or a file in the repo. Operator secrets
> only in the host's `.env`; broker/AI keys only through the **Settings** page (stored encrypted). `.env` is never
> committed (`.gitignore`).

> **Production host (2026-10): Hostinger KVM 2** - three one-line blocks (bootstrap, deploy, rollback) and status:
> **`docs/DEPLOY_HOSTINGER.md`**. §0 below (DigitalOcean) is for reference.

## 0. Preparing the host (platform operator) - once

Decided in ADR-0011: DigitalOcean Basic **2 vCPU / 4 GB** (≈ $24/month), Postgres in compose, an off-site copy of the
backups on Spaces (≈ $5/month), HTTPS by Caddy. The Trade repo's bots stay on their separate ($6) droplet.

| # | What | How | Expected | If it goes wrong |
|---|---|---|---|---|
| 0.1 | Droplet | DO console → Create → Droplet → Ubuntu 24.04, Basic, Regular 2 vCPU/4 GB, region BLR1, SSH key | `ssh root@<ip>` works | keep password login off; SSH key only |
| 0.2 | Domain + DNS | at the registrar, an `A` record `atp.<your-domain>` → droplet IP | `dig +short atp.<domain>` shows the IP | DNS can take 5-30 minutes |
| 0.3 | Docker | `curl -fsSL https://get.docker.com \| sh` | `docker compose version` ≥ 2.24 | old compose: `apt install docker-compose-plugin` |
| 0.4 | Repo | `git clone https://github.com/abhishekwasu-hue/AdvanceTradingPlatform.git /opt/atp && cd /opt/atp` | `main` branch | - |
| 0.5 | `.env` | `cp .env.example .env && nano .env` - fill in: `POSTGRES_PASSWORD`, `JWT_SECRET_KEY` (`python3 -c "import secrets;print(secrets.token_urlsafe(48))"`), `SECRETS_ENCRYPTION_KEY` (**the 44-character key from your PC, do not change it**), `DOMAIN`, `ACME_EMAIL`, `OFFSITE_S3_*`, `METRICS_TOKEN`, `ENVIRONMENT=production` | `chmod 600 .env` | a forgotten key = every broker key is useless; keep a backup of `.env` in a password manager |
| 0.6 | Spaces | DO → Spaces → bucket `atp-backups` (private) → API keys → into `.env` | - | the endpoint follows the region (`blr1.digitaloceanspaces.com`) |
| 0.7 | Upstox app | developer.upstox.com → **a new app for ATP only** → Redirect URL `https://<DOMAIN>/api/broker/upstox/oauth/callback` | key/secret only in Settings (step 1.3) | with the Trade bots' app, the two log each other out |
| 0.8 | Build + up | `scripts/deploy.sh production` | ends with `deploy[production]: healthy`; `curl -s https://<DOMAIN>/api/system/health` → `{"status":"ok"...}` | refused by the guard (market open) → after 15:30; `docker compose logs backend` |
| 0.9 | Migrations | deploy.sh runs `migrate_guard.py` itself (the new tables of BB-BD2: news feed columns, `telegram_callbacks`, `thesis_records`, `news_feedback`) | `docker compose exec backend alembic current` = head (`d5e7f9a1b3c5` or later) | drift: `docker compose exec backend alembic upgrade head` (while the market is closed) |
| 0.10 | Holidays | once you are the first owner (1.1): Admin console → Exchange holidays → paste the NSE list | ✅ holidays in the step 3 check | - |
| 0.11 | Backup drill | `docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm --entrypoint sh backup /scripts/verify_backup.sh latest` and `... run --rm offsite --once` | `verified` / `synced to spaces:atp-backups` | OPERATIONS 1.2 |

## 1. The first organisation (owner)

| # | What | How | Expected |
|---|---|---|---|
| 1.1 | Register | `https://<DOMAIN>` → Register → verify the email (without SMTP the Admin verifies it) → **Account → MFA on** (keep the backup codes safe) | Account page shows MFA: enabled |
| 1.2 | Admin | make the first user `SUPER_ADMIN` in the DB (OPERATIONS 1.6) → the Admin console opens | Admin → Readiness all ok/warn |
| 1.3 | Broker key | Settings → Brokers → Upstox → API key + secret (the app of step 0.7) → Save. **Fyers (local):** broker `fyers` → API Key = App ID, API Secret, **Redirect URI = the Redirect URL registered in the app** → Save (LOCAL_PC §3) | "stored, encrypted" |
| 1.4 | Login | Upstox: on the same card **Login to Upstox** → the Upstox page → allow → back. **Fyers:** **Open fyers login** → the Fyers page → after the redirect copy the address bar → "Paste today's auth_code" → **Login with code** | banner `VALID`, expiry 03:30 IST (Upstox) / 06:00 IST (Fyers) |
| 1.5 | Read-only check | **Read-only check** on the card | **9/9** green (profile, funds, instruments, quote, derivatives, contract_quote, option_chain, positions, orders) - no order at all |
| 1.6 | Alerts | Settings → Alerts → Telegram (bot token + chat id) → Save → **Send test** | a message on your phone |
| 1.7 | Risk | Risk page: capital, 0.5% per trade, daily loss cap, max positions | Risk limits set ✅ |
| 1.8 | Feature flags (in PAPER, your decision) | Admin console → Controls → `news_feed`, `market_thesis`, `telegram_inbound` on (default off). No flag changes an order or sizing: feed = unverified news + alerts, thesis = a read + a shadow multiplier (never applied), telegram_inbound = commands + PAPER approve/reject | the sentiment gauge and the thesis card on the Copilot's "today's market"; feed rows on the News page |
| 1.9 | Two-way Telegram (optional, after 1.8; **not on the local PC** - needs public HTTPS) | Settings → Alerts → Telegram → "Two-way Telegram" on → allowed chat ids → **Register webhook** (needs an HTTPS domain) → `/help` from the phone | the bot answers; `/brief`, `/positions`, `/thesis NIFTY 50` work. Buttons only for PAPER pause/reduce/review; exits/LIVE on the web + authenticator |

## 2. PAPER deployment

1. Strategy: a built-in one (e.g. `ema_rsi_scalper_1m`) or the Builder / the Copilot strategist (adopt → PAPER). The AI's
   proposals are only proposals; you deploy (ADR-0006).
2. Autopilot → New deployment → symbol (NIFTY 50 / BANKNIFTY / RELIANCE), timeframe, **mode PAPER**, contract rules
   (the underlying or an option rule) → Preview → Start.
3. Dashboard → Go-live checklist (PAPER) all ✅ or a deliberate ⚠️.

## 3. The morning of the first PAPER day (08:30-09:10 IST)

1. **Broker login** (the token expires daily - Upstox 03:30, Fyers 06:00): Settings → Brokers → Login to Upstox /
   Fyers: Open login → paste the code → banner `VALID`.
2. **Read-only check** (step 1.5) once.
3. **The check script** (read-only; changes nothing):

```
cd /opt/atp
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backend \
  python scripts/first_paper_day_check.py --tenant <owner-email> --send-test-alert
```

Expected output (sample):

```
First PAPER day check · <org> (tenant 1) · 2026-10-06 08:52 IST
Platform
✅ Migrations at head: ok
✅ Redis reachable: ok
✅ Trading worker running: ok, 31s ago
✅ Instrument master synced: upstox/NSE: 1xxxx
✅ Exchange holiday calendar loaded: 14 from 2026
Organisation
✅ Broker API key stored: 1 account(s): upstox
✅ Broker session token valid today: upstox (primary)
✅ A deployment is active: 1 active (PAPER 1, LIVE 0)
✅ Risk limits set: ...
✅ Out-of-app alert channel: TELEGRAM
✅ No kill switch engaged: none engaged
Live probes
✅ NSE session today: trading day; opens 09:15 IST
✅ Broker read-only smoke test - upstox (primary): 9 ok, 0 failed, 0 skipped
✅ An ACTIVE PAPER deployment: ema_rsi_scalper_1m on NIFTY 50 [1min]
✅ Alert channel test message - TELEGRAM: sent
Result: 13 ok · 0 warnings · 0 blockers · 0 skipped
✅ Ready for today's PAPER session. Nothing was changed.
```

Exit code `0` = start; `1` = there is a ❌; `2` = no organisation could be chosen (pass `--tenant`).

On the local PC (PowerShell): `docker compose -f docker-compose.yml -f docker-compose.local.yml exec backend python scripts/first_paper_day_check.py --tenant <email> --send-test-alert` (LOCAL_PC §5); the smoke line reads `fyers (primary): 9 ok`.

4. **When the flags are on** (1.8): News page → the feed status `last_run` is fresh (≤ 15 minutes) and items arrive; Copilot →
   today's market → the sentiment gauge is not "not read yet" (it fills in the first 15 minutes after the broker login); the
   thesis card shows a watchlist symbol. If nothing shows: `docker compose logs --tail 200 worker | grep -i "news\|sentiment\|thesis"`.

| ❌ shown | Do |
|---|---|
| Broker session token valid today | step 3.1 again; check the Upstox app's redirect URL; Fyers: an old/used code → a new code |
| Broker read-only smoke test: profile fail | wrong/expired token → log in again; `derivatives`/`contract_quote` fail → Instruments → Sync (Fyers: symbol master - LOCAL_PC §4) |
| Trading worker running: never_seen / stale | `docker compose ... up -d worker`; `docker compose logs --tail 100 worker` |
| Migrations at head: todo | `alembic upgrade head` while the market is closed |
| An ACTIVE PAPER deployment | Autopilot → Start (PAPER) |
| Alert channel test message | Settings → Alerts → Send test; check the Telegram chat id/bot token |
| NSE session today: ⚠️ holiday/weekend | the market is closed today; again on the next trading day |
| Deployment ... evaluated recently (only after 09:15) | worker heartbeat, token, candle staleness (G1) → `docker compose logs worker` |

## 4. What to watch during the day

| Time (IST) | Where | Expected |
|---|---|---|
| 09:15-09:20 | Autopilot | Worker: Running · Market: Open; the deployment's `last evaluated` changes within 1-2 minutes |
| 09:30 | Positions/Signals | candles arrive (Pro Chart live badge, not stale) |
| any time | Notifications | ENTRY/EXIT INFO; when a CRITICAL comes, check it reached the phone too |
| 15:00 | Autopilot | new entries closed (cutoff) |
| 15:15 | Positions | every intraday position squared off; open = 0 |
| 15:35 | Notifications/Telegram | the **EOD summary** notification (signals, entries, exits, net P&L, open after square-off, reconciliation, worker errors) |
| 15:40 | Positions → Reconcile | 0 mismatches |
| 15:40 (Friday, `market_thesis` on) | Notifications/Telegram | the week's **thesis scoreboard** (hit rate; the shadow multiplier only recorded) |
| evening | Trade journal, Coach | the day's notes; `docker compose logs --since 8h worker \| grep -i error` empty |

**If the worker stops midway (heartbeat stale):** OPERATIONS 1.7 - first `docker compose restart worker`; in PAPER no money
is at risk, but do not count that day under the "unbroken heartbeat" criterion.

## 5. When part A is done (5 PAPER days in a row)

Count a day only when all five are ✅:

| Criterion | Evidence |
|---|---|
| Worker running 09:15-15:30, no break in the heartbeat | Dashboard heartbeat; no restart in the worker logs |
| Signals → orders → exits of ≥ 1 PAPER deployment in the ledger | EOD summary: signals ≥ 1, entries ≥ 1, exits = entries |
| Reconciliation clean | EOD summary "Reconciliation: clean today" or Positions → Reconcile 0 mismatches |
| Telegram alerts arrived | on day one the test message + the EOD summary on the phone |
| The EOD summary came at 15:35 | `EOD_SUMMARY` in Notifications |

The log book (one line per day): `date · signals · entries · exits · net P&L · open after 15:15 · reconcile · worker restarts · note`.
After 5 days, `docs/GO_LIVE.md` §3 (LIVE) by a separate decision.

The features behind the flags (1.8) are **not** in these criteria: if the feed/sentiment/thesis/Telegram stop, the PAPER day
still counts; they are background reads, not trading. The port of the Trade repo's level engine (AY/AZ/BA) is **stopped** -
validation showed no edge over random; strategies stay with the built-in ones / the Builder / the strategist.

## 6. Daily (OPERATIONS 1.5)

After 03:30 (Upstox) / 06:00 (Fyers) the token has expired → log in before 09:00; worker heartbeat; risk limits; alert
channel; 15:15 square-off; backups verified (`docker compose ... run --rm --entrypoint sh backup /scripts/verify_backup.sh latest`
once a week).
