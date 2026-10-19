# Local PC (Windows + Docker Desktop) - the host of the PAPER week

> Written in Marathi first (`LOCAL_PC_MR.md`); translated to English in Oct 2026 - the owner's ruling: ATP is English
> only, docs included. The steps are unchanged.

Decision (2026-10-06): no droplet. ATP already runs **on your Windows PC in Docker Desktop**; the **5 PAPER days** run
there. Broker **Fyers** (your current Fyers app), not Upstox. The order is the one of `docs/GO_LIVE_RUNBOOK.md`; this page
only says "how on the local PC" and "how with Fyers". This page has nothing to do with LIVE.

> **Secrets:** Fyers App ID/secret, auth code, access token, TOTP, AI keys - only through the **Settings** page
> (encrypted). `.env` holds only the operator secrets (`JWT_SECRET_KEY`, `SECRETS_ENCRYPTION_KEY` - **never change the
> 44-character key**, `POSTGRES_PASSWORD`). Never in a chat/issue/PR.

## 1. Bringing the running setup to the latest `main` (PowerShell, in the repo folder)

```powershell
cd C:\Users\abhis\AdvanceTradingPlatform
git status                      # the working tree should be clean; an untracked .env is fine
git pull origin main
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.local.yml ps
```

- `docker-compose.local.yml` (new): Postgres, Redis, the API **and** the UI only on `127.0.0.1` (not visible from the
  LAN/internet; the UI at http://localhost:8080 on this PC); every service `restart: unless-stopped`. Pass both `-f`
  every time - a bare `docker compose up` goes back to the old (0.0.0.0) ports.
- **Migrations**: when the `backend` container starts, `migrate_guard.py` runs `alembic upgrade head` itself - **but
  while NSE is open (09:15-15:30 IST) the backend does not start at all if a migration is pending** (exit 3). So
  `git pull` + `up --build` **after 15:30 or before 09:00**. Check:

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml exec backend alembic current
# expected: d5e7f9a1b3c5 (head) or later (the migrations of P0.2-P0.5: mfa_last_step, audit_anchors, webhook_token_hash, NUMERIC money columns, order_style/mark_price). Without "(head)", while the market is closed:
docker compose -f docker-compose.yml -f docker-compose.local.yml exec backend alembic upgrade head
```

- Health: `curl.exe -s http://localhost:8000/api/system/health` → `{"status":"ok"...}`; UI http://localhost:8080.
- Logs: `docker compose -f docker-compose.yml -f docker-compose.local.yml logs --tail 100 backend worker`. (Since P0.7 each container's logs rotate at 5 × 20 MB; the disk does not fill up.)
- An old checkout and CRLF: if the `backup` container keeps restarting with exit 2, see the README note "On Windows"
  (`git ls-files --eol scripts` → `w/lf`).

## 2. After the PC sleeps / restarts

| What happened | What to do |
|---|---|
| PC sleep/hibernate (at night) | Nothing. When Docker Desktop wakes up the containers are still running; the worker gives a heartbeat on its next cycle. In the morning check Dashboard → Worker: Running; if stale, `... restart worker`. |
| PC restart / Docker Desktop update | Keep Docker Desktop on **auto-start** (Settings → General → Start Docker Desktop when you sign in). With `unless-stopped` everything comes back; confirm with `... ps`. |
| You ran `docker compose stop` | `... up -d` (no build). |
| Windows Update will restart at night | Windows → Settings → Update → Active hours 08:00-17:00; no restart during market hours. |
| It must not sleep (market hours) | Power options → Sleep: Never (plugged in) - keep the PC awake 09:00-15:40. |

After a restart the Fyers token is still alive (it expires daily at 06:00 IST), but **if the worker restarts, that day
breaks the "unbroken heartbeat" criterion** - do not count that day (GO_LIVE_RUNBOOK §5).

## 3. Fyers - the daily login (paste the auth code), what you will see

**Once (day 0):**
1. myapi.fyers.in → your app → **App ID** (e.g. `ABCD1234-100`), **Secret**, and the **Redirect URL** registered in the app
   (whatever it is; do not change it - it may serve your Trade bots too; ATP only opens it, it does not listen on it).
2. ATP → Settings → "Add / update broker credentials" → Broker **fyers** → API Key = App ID, API Secret = Secret,
   **Redirect URI = that Redirect URL** → **Store credentials** → "stored (encrypted at rest)".
3. The "Broker session health" card shows a `fyers` row: badge **UNKNOWN**, the button **Open fyers login**, and below it
   the "Paste today's auth_code ..." box.

**Daily (after 06:00, before 09:00):**
1. Settings → Broker session health → the `fyers` row: badge **EXPIRED** (it expired at 06:00) → **Open fyers login**.
2. A new tab: the Fyers login (client id → OTP/TOTP → PIN). Then the browser goes to your Redirect URL; the address bar
   shows `...?s=ok&code=200&auth_code=eyJ...&state=atp` (fine even if the page is blank/404 - the address is what counts).
3. Copy the whole address bar (Ctrl+L, Ctrl+C) → the ATP tab → paste into the "Paste today's auth_code" box →
   **Login with code**.
4. Expected: badge **VALID**, "session valid until <tomorrow 06:00>", a green line "Logged in - session token stored
   (encrypted)". The code is single-use and expires within minutes - if you were late, Open login again.

| You see | Meaning / fix |
|---|---|
| "Store the Redirect URI exactly as registered..." | step 2 of day 0 (the Redirect URI) was skipped |
| "Broker login failed: Invalid auth code" / badge EXPIRED | the code was used/old - Open login again → a new code |
| "Could not authenticate the user (-16)" in the smoke test | the token expired - step 1 again |
| "invalid redirect_uri" on the Fyers page | the Redirect URI in Settings does not match the app's letter for letter |

The old way (when there is no button): paste the `auth_code` into the Request Token field → Store credentials →
**Authenticate**. Both ways drop yesterday's access token (it used to stay, and the new code showed as "fail" - fixed in
PR BG).

## 4. The Fyers read-only check (no order at all)

1. Settings → Broker accounts → `fyers / primary` → **Read-only check**. 9 steps: profile, funds, instruments, quote
   (NIFTY 50), derivatives (the nearest expiry + one CE), contract_quote (that contract's premium, from Fyers' own
   spelling), **option_chain** (the NIFTY chain - new), positions, orders. Expected **"9 ok, 0 failed, 0 skipped"**.
2. `derivatives`/`contract_quote` red = the columns of the Fyers symbol master (the adapter now reads strike/CE/PE from the
   ticker, the columns only confirm) - report the contract name in the message. `profile` red = the token.
3. The Fyers adapter had only been tested **against a mock**; your first green read-only check = the first real
   confirmation (note it in `docs/MASTER_PROMPT_GAP_ANALYSIS.md`).

## 5. The first PAPER day check (local)

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml exec backend `
  python scripts/first_paper_day_check.py --tenant <your-email> --send-test-alert
```

Output/exit codes as in GO_LIVE_RUNBOOK §3; the line "Broker read-only smoke test - fyers (primary): 9 ok" must be there.
Deployment: Autopilot → New deployment → **mode PAPER** → Start (GO_LIVE_RUNBOOK §2).

## 6. Telegram

- **Outbound alerts work** (Settings → Alerts → Telegram → bot token + chat id → Send test; EOD summary, CRITICAL).
- **Two-way Telegram (GO_LIVE_RUNBOOK 1.9) is not available locally**: the webhook needs a public HTTPS URL; the PC is on
  127.0.0.1. Keep the `telegram_inbound` flag off. (`news_feed`, `market_thesis` by your decision in Admin → Controls.)

## 7. Backups (local)

The `backup` service keeps a daily dump in a compose volume; no off-site copy. Once a week:
`docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm --entrypoint sh backup /scripts/verify_backup.sh latest`.
Keep a copy of `.env` in a password manager (a lost SECRETS_ENCRYPTION_KEY = every broker key is useless).
