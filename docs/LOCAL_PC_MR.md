# Local PC (Windows + Docker Desktop) - PAPER आठवड्याचा host - मराठी

निर्णय (2026-10-06): droplet नाही. ATP **तुमच्या Windows PC वर Docker Desktop मध्ये** आधीच चालू आहे; तिथेच
**5 PAPER दिवस**. Broker **Fyers** (तुमचा सध्याचा Fyers app), Upstox नाही. `docs/GO_LIVE_MR.md` चा क्रम तोच;
हे पान फक्त "local वर कसं" आणि "Fyers वर कसं" सांगतं. LIVE शी या पानाचा संबंध नाही.

> **Secrets:** Fyers App ID/secret, auth code, access token, TOTP, AI keys - फक्त **Settings** पानातून (encrypted).
> `.env` मध्ये फक्त operator secrets (`JWT_SECRET_KEY`, `SECRETS_ENCRYPTION_KEY` - **44 अक्षरी key कधीही बदलू नका**,
> `POSTGRES_PASSWORD`). Chat/issue/PR मध्ये कधीही नाही.

## 1. चालू असलेला setup नवीनतम `main` वर आणणं (PowerShell, repo folder मध्ये)

```powershell
cd C:\Users\abhis\AdvanceTradingPlatform
git status                      # working tree clean असावा; .env untracked दिसणं ठीक
git pull origin main
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.local.yml ps
```

- `docker-compose.local.yml` (नवीन): Postgres, Redis, API **आणि** UI फक्त `127.0.0.1` वर (LAN/इंटरनेट वरून
  दिसत नाहीत; UI http://localhost:8080 याच PC वर); सगळे services `restart: unless-stopped`. दर वेळी दोन्ही
  `-f` द्या - एकटा `docker compose up` जुन्या (0.0.0.0) ports वर जातो.
- **Migrations**: `backend` container सुरू होताना `migrate_guard.py` स्वतः `alembic upgrade head` चालवतो - **पण
  NSE चालू असताना (09:15-15:30 IST) pending migration असेल तर backend सुरूच होत नाही** (exit 3). म्हणून
  `git pull` + `up --build` **15:30 नंतर किंवा 09:00 पूर्वी**. तपासणी:

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml exec backend alembic current
# अपेक्षित: e0f2a4b6c8d0 (head) किंवा नंतरचं. "(head)" नसेल तर, market बंद असताना:
docker compose -f docker-compose.yml -f docker-compose.local.yml exec backend alembic upgrade head
```

- Health: `curl.exe -s http://localhost:8000/api/system/health` → `{"status":"ok"...}`; UI http://localhost:8080.
- Logs: `docker compose -f docker-compose.yml -f docker-compose.local.yml logs --tail 100 backend worker`.
- जुनी checkout आणि CRLF: `backup` container exit 2 ने फिरत असेल तर README "On Windows" टीप
  (`git ls-files --eol scripts` → `w/lf`).

## 2. PC sleep / restart नंतर

| काय झालं | काय करायचं |
|---|---|
| PC sleep/hibernate (रात्री) | काही नाही. Docker Desktop जागं झाल्यावर containers चालूच असतात; worker पुढच्या cycle ला heartbeat देतो. सकाळी Dashboard → Worker: Running पाहा; stale असेल तर `... restart worker`. |
| PC restart / Docker Desktop update | Docker Desktop **auto-start** ठेवा (Settings → General → Start Docker Desktop when you sign in). `unless-stopped` मुळे सगळं परत येतं; `... ps` ने खात्री. |
| तुम्ही `docker compose stop` केलं | `... up -d` (build नको). |
| Windows Update रात्री restart करणार | Windows → Settings → Update → Active hours 08:00-17:00 ठेवा; market मध्ये restart नको. |
| Sleep मध्ये जाऊ नये (market hours) | Power options → Sleep: Never (plugged in) - 09:00-15:40 PC जागा ठेवा. |

Restart नंतर Fyers token जिवंत असतो (रोज 06:00 IST ला जातो), पण **worker restart झाला तर त्या दिवसाचा "अखंड
heartbeat" निकष मोडतो** - GO_LIVE_MR §5 प्रमाणे तो दिवस मोजू नका.

## 3. Fyers - रोजचा login (auth code paste), स्क्रीनवर काय दिसेल

**एकदाच (दिवस 0):**
1. myapi.fyers.in → तुमचा app → **App ID** (उदा. `ABCD1234-100`), **Secret**, आणि app मध्ये नोंदवलेला **Redirect URL**
   (जो आहे तोच; बदलू नका - तो तुमच्या Trade bots साठीही असू शकतो, ATP फक्त उघडतो, ऐकत नाही).
2. ATP → Settings → "Add / update broker credentials" → Broker **fyers** → API Key = App ID, API Secret = Secret,
   **Redirect URI = तो Redirect URL** → **Store credentials** → "stored (encrypted at rest)".
3. "Broker session health" card वर `fyers` ओळ दिसते: badge **UNKNOWN**, बटण **Open fyers login**, खाली
   "Paste today's auth_code ..." box.

**रोज (06:00 नंतर, 09:00 पूर्वी):**
1. Settings → Broker session health → `fyers` ओळ: badge **EXPIRED** (06:00 ला गेला) → **Open fyers login**.
2. नवीन tab: Fyers login (client id → OTP/TOTP → PIN). झाल्यावर browser तुमच्या Redirect URL वर जातो; address bar मध्ये
   `...?s=ok&code=200&auth_code=eyJ...&state=atp` दिसतं (पान blank/404 असलं तरी चालतं - address महत्त्वाचा).
3. address bar पूर्ण copy करा (Ctrl+L, Ctrl+C) → ATP tab → "Paste today's auth_code" box मध्ये paste →
   **Login with code**.
4. अपेक्षित: badge **VALID**, "session valid until <उद्या 06:00>", हिरवी ओळ "Logged in - session token stored
   (encrypted)". Code single-use आहे आणि काही मिनिटांत expire होतो - उशीर झाला तर पुन्हा Open login.

| दिसलं | अर्थ / उपाय |
|---|---|
| "Store the Redirect URI exactly as registered..." | दिवस 0 ची पायरी 2 (Redirect URI) राहिली |
| "Broker login failed: Invalid auth code" / badge EXPIRED | code वापरलेला/जुना - पुन्हा Open login → नवा code |
| "Could not authenticate the user (-16)" smoke मध्ये | token गेला - पायरी 1 पुन्हा |
| Fyers पानावर "invalid redirect_uri" | Settings मधला Redirect URI app मधल्याशी अक्षरशः जुळत नाही |

जुना मार्ग (बटण नसेल तर): Request Token field मध्ये `auth_code` paste → Store credentials → **Authenticate**.
दोन्ही मार्ग कालचा access token टाकून देतात (आधी तो राहत होता आणि नवा code "fail" दिसत होता - PR BG मध्ये दुरुस्त).

## 4. Fyers read-only check (कुठलीही order नाही)

1. Settings → Broker accounts → `fyers / primary` → **Read-only check**. 9 पायऱ्या: profile, funds, instruments, quote
   (NIFTY 50), derivatives (जवळची expiry + एक CE), contract_quote (त्या contract चा premium, Fyers च्या स्वतःच्या
   spelling मधून), **option_chain** (NIFTY chain - नवीन), positions, orders. अपेक्षित **"9 ok, 0 failed, 0 skipped"**.
2. `derivatives`/`contract_quote` लाल = Fyers symbol master च्या columns (adapter आता ticker वरून strike/CE/PE वाचतो,
   columns फक्त दुजोरा) - message मधला contract नाव सांगा. `profile` लाल = token.
3. Fyers adapter आजवर **mock वरच** tested होता; तुमचा पहिला हिरवा Read-only check = पहिली खरी खात्री
   (`docs/MASTER_PROMPT_GAP_ANALYSIS.md` मध्ये नोंदवा).

## 5. पहिल्या PAPER दिवसाची तपासणी (local)

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml exec backend `
  python scripts/first_paper_day_check.py --tenant <तुमचा-email> --send-test-alert
```

Output/exit codes GO_LIVE_MR §3 प्रमाणे; "Broker read-only smoke test - fyers (primary): 9 ok" ही ओळ हवी.
Deployment: Autopilot → New deployment → **mode PAPER** → Start (GO_LIVE_MR §2).

## 6. Telegram

- **Outbound alerts चालू** (Settings → Alerts → Telegram → bot token + chat id → Send test; EOD summary, CRITICAL).
- **Two-way Telegram (GO_LIVE_MR 1.9) local वर नाही**: webhook ला public HTTPS URL लागतो; PC 127.0.0.1 वर आहे.
  `telegram_inbound` flag बंदच ठेवा. (`news_feed`, `market_thesis` तुमच्या निर्णयाने Admin → Controls मधून.)

## 7. Backups (local)

`backup` service compose volume मध्ये रोज dump ठेवतो; off-site नाही. आठवड्यातून एकदा:
`docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm backup sh /scripts/verify_backup.sh latest`.
`.env` ची copy password manager मध्ये (SECRETS_ENCRYPTION_KEY हरवला = सगळे broker keys निरुपयोगी).
