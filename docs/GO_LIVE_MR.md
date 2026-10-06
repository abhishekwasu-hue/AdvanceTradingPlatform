# Go-live (PAPER) - मराठी operator checklist

`docs/GO_LIVE.md` चाच क्रम, पण प्रत्येक पायरीला नेमके clicks/commands, अपेक्षित output आणि चूक झाली तर काय
करायचे. लक्ष्य: खऱ्या broker खात्यावर, खऱ्या data वर **पहिला पूर्ण PAPER दिवस**, आणि मग सलग **5 PAPER दिवस**.
LIVE या checklist च्या बाहेर आहे (GO_LIVE.md §3).

> **निर्णय 2026-10-06 - host आणि broker बदलले:** PAPER आठवडा **तुमच्या Windows PC वर Docker Desktop मध्ये**,
> broker **Fyers** (सध्याचा app). Local पायऱ्या (`git pull`, compose overlay, migrations, sleep/restart, Fyers रोजचा
> auth-code login, read-only check, तपासणी script चा local command) **`docs/LOCAL_PC_MR.md`** मध्ये. खालचा §0
> (droplet/Caddy/Spaces/Upstox app) **नंतरसाठी** ठेवला आहे; §1-§6 दोन्ही host ला लागू, Upstox-विशिष्ट ओळींना Fyers
> पर्याय दिला आहे. Two-way Telegram (1.9) local वर नाही (public HTTPS लागतो); outbound alerts चालू.

> **Secrets चा नियम:** broker key/secret, access token, TOTP secret, AI provider key, `JWT_SECRET_KEY`,
> `SECRETS_ENCRYPTION_KEY`, Razorpay keys - हे कधीही chat मध्ये, GitHub issue/PR मध्ये किंवा repo मधल्या फाइलमध्ये
> नाहीत. Operator secrets फक्त host वरच्या `.env` मध्ये; broker/AI keys फक्त **Settings** पानातून (encrypted साठवले
> जातात). `.env` कधीही commit होत नाही (`.gitignore`).

## 0. Host तयार करणे (platform operator) - एकदाच

निर्णय ADR-0011 मध्ये: DigitalOcean Basic **2 vCPU / 4 GB** (≈ $24/मह), compose मधलाच Postgres, backups ची off-site
copy Spaces वर (≈ $5/मह), Caddy ने HTTPS. Trade repo चे bots वेगळ्या ($6) droplet वर तसेच राहतात.

| # | काय | कसे | अपेक्षित | चूक झाली तर |
|---|---|---|---|---|
| 0.1 | Droplet | DO console → Create → Droplet → Ubuntu 24.04, Basic, Regular 2 vCPU/4 GB, region BLR1, SSH key | `ssh root@<ip>` चालते | password login बंद ठेवा; फक्त SSH key |
| 0.2 | Domain + DNS | registrar मध्ये `A` record `atp.<तुमचा-domain>` → droplet IP | `dig +short atp.<domain>` IP दाखवतो | DNS 5-30 मिनिटे लागू शकतात |
| 0.3 | Docker | `curl -fsSL https://get.docker.com \| sh` | `docker compose version` ≥ 2.24 | जुना compose: `apt install docker-compose-plugin` |
| 0.4 | Repo | `git clone https://github.com/abhishekwasu-hue/AdvanceTradingPlatform.git /opt/atp && cd /opt/atp` | `main` branch | - |
| 0.5 | `.env` | `cp .env.example .env && nano .env` - भरायचे: `POSTGRES_PASSWORD`, `JWT_SECRET_KEY` (`python3 -c "import secrets;print(secrets.token_urlsafe(48))"`), `SECRETS_ENCRYPTION_KEY` (**तुमच्या PC वरचा 44-अक्षरी key, बदलू नका**), `DOMAIN`, `ACME_EMAIL`, `OFFSITE_S3_*`, `METRICS_TOKEN`, `ENVIRONMENT=production` | `chmod 600 .env` | key विसरला = सगळे broker keys निरुपयोगी; `.env` चा backup password manager मध्ये |
| 0.6 | Spaces | DO → Spaces → bucket `atp-backups` (private) → API keys → `.env` मध्ये | - | endpoint region नुसार (`blr1.digitaloceanspaces.com`) |
| 0.7 | Upstox app | developer.upstox.com → **नवा app फक्त ATP साठी** → Redirect URL `https://<DOMAIN>/api/broker/upstox/oauth/callback` | key/secret फक्त Settings मध्ये (पायरी 1.3) | Trade bots चा app वापरला तर दोघे एकमेकांना logout करतात |
| 0.8 | Build + up | `scripts/deploy.sh production` | शेवटी `deploy[production]: healthy`; `curl -s https://<DOMAIN>/api/system/health` → `{"status":"ok"...}` | guard ने नाकारले (market open) → 15:30 नंतर; `docker compose logs backend` |
| 0.9 | Migrations | deploy.sh स्वतः `migrate_guard.py` चालवतो (BB-BD2 च्या नव्या tables: news feed columns, `telegram_callbacks`, `thesis_records`, `news_feedback`) | `docker compose exec backend alembic current` = head (`b3c5d7e9f1a3` किंवा नंतरचे) | drift: `docker compose exec backend alembic upgrade head` (market बंद असताना) |
| 0.10 | Holidays | पहिला owner बनल्यावर (१.१) Admin console → Exchange holidays → NSE यादी paste | पायरी 3 च्या check मध्ये ✅ holidays | - |
| 0.11 | Backup drill | `docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm backup sh /scripts/verify_backup.sh latest` आणि `... run --rm offsite sh /scripts/offsite_sync.sh --once` | `verified` / `synced to spaces:atp-backups` | OPERATIONS 1.2 |

## 1. पहिली organisation (owner)

| # | काय | कसे | अपेक्षित |
|---|---|---|---|
| 1.1 | नोंदणी | `https://<DOMAIN>` → Register → email verify (SMTP नसेल तर Admin ने verify) → **Account → MFA चालू** (backup codes जपून ठेवा) | Account पानावर MFA: enabled |
| 1.2 | Admin | DB मध्ये पहिल्या user ला `SUPER_ADMIN` (OPERATIONS 1.6) → Admin console उघडते | Admin → Readiness सगळे ok/warn |
| 1.3 | Broker key | Settings → Brokers → Upstox → API key + secret (पायरी 0.7 चा app) → Save. **Fyers (local):** broker `fyers` → API Key = App ID, API Secret, **Redirect URI = app मधला Redirect URL** → Save (LOCAL_PC_MR §3) | "stored, encrypted" |
| 1.4 | Login | Upstox: त्याच card वर **Login to Upstox** → Upstox पान → परवानगी → परत. **Fyers:** **Open fyers login** → Fyers पान → redirect झाल्यावर address bar copy → "Paste today's auth_code" → **Login with code** | banner `VALID`, expiry 03:30 IST (Upstox) / 06:00 IST (Fyers) |
| 1.5 | Read-only check | card वर **Read-only check** | **9/9** हिरवे (profile, funds, instruments, quote, derivatives, contract_quote, option_chain, positions, orders) - कुठलीही order नाही |
| 1.6 | Alerts | Settings → Alerts → Telegram (bot token + chat id) → Save → **Send test** | फोनवर message |
| 1.7 | Risk | Risk पान: capital, 0.5% प्रति trade, daily loss cap, max positions | Risk limits set ✅ |
| 1.8 | Feature flags (PAPER मध्ये, तुमच्या निर्णयाने) | Admin console → Controls → `news_feed`, `market_thesis`, `telegram_inbound` on (default off). कुठलाही flag order/sizing बदलत नाही: feed = unverified बातम्या + alerts, thesis = वाचन + shadow multiplier (कधीही लागू नाही), telegram_inbound = commands + PAPER approve/reject | Copilot "आजचा market" वर sentiment gauge, thesis card; News पानावर feed rows |
| 1.9 | Two-way Telegram (ऐच्छिक, 1.8 नंतर; **local PC वर नाही** - public HTTPS लागतो) | Settings → Alerts → Telegram → "Two-way Telegram" on → allowed chat ids → **Register webhook** (HTTPS domain लागतो) → फोनवरून `/help` | bot उत्तर देतो; `/brief`, `/positions`, `/thesis NIFTY 50` चालतात. Buttons फक्त PAPER pause/reduce/review; exits/LIVE web + authenticator |

## 2. PAPER deployment

1. Strategy: built-in (उदा. `ema_rsi_scalper_1m`) किंवा Builder/Copilot strategist (adopt → PAPER). AI चे प्रस्ताव फक्त
   प्रस्ताव; deploy तुम्ही करता (ADR-0006).
2. Autopilot → New deployment → symbol (NIFTY 50 / BANKNIFTY / RELIANCE), timeframe, **mode PAPER**, contract rules
   (underlying किंवा option नियम) → Preview → Start.
3. Dashboard → Go-live checklist (PAPER) सगळे ✅ किंवा जाणीवपूर्वक ⚠️.

## 3. पहिल्या PAPER दिवसाची सकाळ (08:30-09:10 IST)

1. **Broker login** (token रोज expire होतो - Upstox 03:30, Fyers 06:00): Settings → Brokers → Login to Upstox /
   Fyers: Open login → code paste → banner `VALID`.
2. **Read-only check** (पायरी 1.5) एकदा.
3. **तपासणी script** (read-only; काहीही बदलत नाही):

```
cd /opt/atp
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backend \
  python scripts/first_paper_day_check.py --tenant <owner-email> --send-test-alert
```

अपेक्षित output (नमुना):

```
पहिला PAPER दिवस - तपासणी · <org> (tenant 1) · 2026-10-06 08:52 IST
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
निकाल: 13 ठीक · 0 इशारे · 0 अडथळे · 0 वगळले
✅ आज PAPER दिवस सुरू करू शकता. काहीही बदललेले नाही.
```

Exit code `0` = सुरू करा; `1` = ❌ आहे; `2` = organisation निवडता आली नाही (`--tenant` द्या).

Local PC वर (PowerShell): `docker compose -f docker-compose.yml -f docker-compose.local.yml exec backend python scripts/first_paper_day_check.py --tenant <email> --send-test-alert` (LOCAL_PC_MR §5); smoke ओळ `fyers (primary): 9 ok`.

4. **Flags on असतील तर** (1.8): News पान → feed status `last_run` ताजा (≤15 मिनिटे) आणि items येत आहेत; Copilot → आजचा
   market → sentiment gauge "not read yet" नसावा (broker login नंतर पहिल्या 15 मिनिटांत भरतो); thesis card वर watchlist
   चा symbol दिसतो. काहीही नसेल तर `docker compose logs --tail 200 worker | grep -i "news\|sentiment\|thesis"`.

| ❌ दिसले | करायचे |
|---|---|
| Broker session token valid today | पायरी 3.1 पुन्हा; Upstox app चा redirect URL तपासा; Fyers: code जुना/वापरलेला → नवा code |
| Broker read-only smoke test: profile fail | token चुकीचा/expired → पुन्हा login; `derivatives`/`contract_quote` fail → Instruments → Sync (Fyers: symbol master - LOCAL_PC_MR §4) |
| Trading worker running: never_seen / stale | `docker compose ... up -d worker`; `docker compose logs --tail 100 worker` |
| Migrations at head: todo | market बंद असताना `alembic upgrade head` |
| An ACTIVE PAPER deployment | Autopilot → Start (PAPER) |
| Alert channel test message | Settings → Alerts → Send test; Telegram chat id/bot token तपासा |
| NSE session today: ⚠️ holiday/weekend | आज बाजार बंद; पुढच्या trading दिवशी पुन्हा |
| Deployment ... evaluated recently (फक्त 09:15 नंतर) | worker heartbeat, token, candle staleness (G1) → `docker compose logs worker` |

## 4. दिवसभर काय पाहायचे

| वेळ (IST) | कुठे | अपेक्षित |
|---|---|---|
| 09:15-09:20 | Autopilot | Worker: Running · Market: Open; deployment चे `last evaluated` 1-2 मिनिटांत बदलते |
| 09:30 | Positions/Signals | candles येत आहेत (Pro Chart live badge, stale नाही) |
| कधीही | Notifications | ENTRY/EXIT INFO; CRITICAL आले तर फोनवरही आले का ते पाहा |
| 15:00 | Autopilot | नव्या entries बंद (cutoff) |
| 15:15 | Positions | सगळ्या intraday positions square-off; open = 0 |
| 15:35 | Notifications/Telegram | **EOD summary** notification (signals, entries, exits, net P&L, open after square-off, reconciliation, worker errors) |
| 15:40 | Positions → Reconcile | 0 mismatches |
| 15:40 (शुक्रवार, `market_thesis` on) | Notifications/Telegram | **Thesis scoreboard** आठवड्याचा (hit rate, shadow multiplier फक्त नोंद) |
| संध्याकाळ | Trade journal, Coach | दिवसाची नोंद; `docker compose logs --since 8h worker \| grep -i error` रिकामे |

**Worker मध्येच थांबला (heartbeat stale) तर:** OPERATIONS 1.7 - प्रथम `docker compose restart worker`; PAPER मध्ये
पैशांचा धोका नाही, पण तो दिवस "अखंड heartbeat" निकषात मोजू नका.

## 5. Part A पूर्ण होण्याचा निकष (5 सलग PAPER दिवस)

प्रत्येक दिवसासाठी हे पाचही ✅ असतील तरच तो दिवस मोजायचा:

| निकष | पुरावा |
|---|---|
| 09:15-15:30 worker चालू, heartbeat मध्ये खंड नाही | Dashboard heartbeat; worker logs मध्ये restart नाही |
| ≥1 PAPER deployment चे signals → orders → exits ledger मध्ये | EOD summary: signals ≥ 1, entries ≥ 1, exits = entries |
| Reconciliation clean | EOD summary "Reconciliation: clean today" किंवा Positions → Reconcile 0 mismatches |
| Telegram alerts पोहोचले | पहिल्या दिवशी test message + EOD summary फोनवर |
| EOD summary 15:35 ला आला | Notifications मध्ये `EOD_SUMMARY` |

नोंद वही (एक ओळ प्रति दिवस): `दिनांक · signals · entries · exits · net P&L · open after 15:15 · reconcile · worker restarts · टीप`.
5 दिवस झाल्यावर `docs/GO_LIVE.md` §3 (LIVE) वेगळ्या निर्णयाने.

Flags (1.8) चे features या निकषांत **नाहीत**: feed/sentiment/thesis/Telegram बंद पडले तरी PAPER दिवस मोजता येतो; ते
background वाचन आहे, trading नाही. Trade repo चं level engine (AY/AZ/BA) port **थांबवलं** आहे - validation मध्ये
random पेक्षा edge दिसला नाही; strategies built-in / Builder / strategist वरच्याच.

## 6. रोजचे (OPERATIONS 1.5)

03:30 (Upstox) / 06:00 (Fyers) नंतर token expired → 09:00 पूर्वी login; worker heartbeat; Risk limits; alert channel; 15:15 square-off; backups
verified (`docker compose ... run --rm backup sh /scripts/verify_backup.sh latest` आठवड्यातून एकदा).
