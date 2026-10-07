# WORK_LOG - autonomous overnight run (2026-10-05/06 IST)

Decisions taken without the operator (asleep), with the reason. Rule applied throughout: when in
doubt choose the safe default (feature off, PAPER only, no LIVE change), write it here.

## Context
- Instruction received 22:40 IST: complete BC -> BE -> BD-lite -> "strategist language" PR autonomously
  per `ATP_FULL_EXECUTION_PROMPT`; each PR with tests + green CI + a self-review subagent, then merge
  and move on; stop at "G1" and leave a morning report; nothing LIVE-related.
- **`ATP_FULL_EXECUTION_PROMPT` was not found** in the session uploads or the repository (only the
  master prompt PDFs). The phases are therefore executed from the approved plan of 2026-10-05
  (`docs/MASTER_PROMPT_GAP_ANALYSIS.md`, the scratchpad plan) and the names in the instruction:
  * **BC** = deterministic market sentiment (approved plan).
  * **BE** = Telegram inbound with the approved restrictions (whitelist, LIVE web + TOTP only, PAPER and
    reduce/pause only over Telegram).
  * **BD-lite** = the per-symbol market thesis (regime/bias, agreement matrix, bull/base/bear
    scenarios, numbers-check on any LLM narrative) with the reduce-only overlay in **shadow only**
    (recorded, never applied to sizing) and next-day thesis scoring; no deployment setting that can
    change sizing is introduced.
  * **"strategist language" PR** = the Copilot strategist answering fully in the trader's language
    (Marathi/English) for rules-in-words, verdicts, plan lines and the study, plus Marathi/English
    free-text parsing of the strategist request (symbol, style, direction). Interpreted narrowly;
    nothing about order placement.
  * **G1** is not defined in anything available here; the run stops after the four PRs above and this
    log carries the morning report.

## Entries

### BC (PR #51) - self-review findings fixed before merge
- Review found the breadth component dead on Upstox/Zerodha: the bulk `get_quote(list)` wants broker-native
  keys and 4xx'd on plain symbols, silently leaving breadth "missing". Switched to one resolved
  `get_quote_for_symbol` per heavyweight; the test fake now rejects plain-symbol bulk quotes. Also: chain analysis
  inside the try, non-object `SENTIMENT_WEIGHTS` falls back to defaults, worker/route roll back after a failed
  sentiment commit, symbol-filtered memory view keeps the market SENTIMENT row.

### BE - decisions
- Telegram approvals limited to PAPER deployments and PAUSE_DEPLOYMENT / REDUCE_RISK / REVIEW_STRATEGY (the
  approved plan); EXIT_POSITION and anything LIVE get no buttons, and `telegram_allowed()` is re-checked when the
  button is pressed, not only when it is sent.
- The webhook is unauthenticated by nature; it is bound to the tenant's existing TradingView `webhook_token` plus
  Telegram's secret header, with a chat whitelist and a per-chat rate limit, all audited. Flag default off.
- No Telegram command can place, modify or exit an order; free text goes through the read-only Copilot router.

### BE (PR #52) - self-review findings fixed before merge
- BLOCKING: the generic `PUT /api/alert-channels/telegram` accepted `inbound_enabled` / `allowed_chat_ids` /
  `inbound_secret` verbatim, so any trader could switch inbound on with a chosen whitelist and secret, bypassing
  the owner-only endpoint and the flag. The PUT now drops those keys and carries the stored ones over; the webhook
  itself also checks the `telegram_inbound` flag (403 when off). Test added.
- The operator's `ai_copilot` kill flag now binds Telegram `/brief`, `/risk` and free text (it bound only the web).
- Button presses claim the nonce with a conditional UPDATE (and retire the sibling button) so two deliveries cannot
  both decide; the buttons hook can never break the alert drain (own try/except + rollback, nonce rows dropped on a
  failed send); strangers are rate-limited before the audit log and audited at most 5 times per chat per hour;
  command replies are plain text (no HTML double-escaping); transport errors to Telegram become `ok: false`.

### BD-lite - decisions
- Built as "lite" per the night instruction: the thesis, agreement matrix, scenarios, numbers-check and scoring are
  real; the reduce-only overlay is computed and stored as `shadow` only. No deployment setting (`thesis_overlay_mode`
  from the full plan) was introduced - a setting that can change sizing is exactly what "safe default" rules out
  while nobody is watching. A source-scan test keeps execution/risk/guardian code from importing the thesis.
- Scoring uses the symbol's last market-memory read of the next session (0.3% threshold) because that is data the
  platform already has for every tenant; a candle-based scoring can replace it later without a schema change.
- Flag `market_thesis` default off; the worker builds and scores only for tenants with the flag on.

### BD-lite (PR #53) - self-review findings fixed before merge
- Numbers check: a number glued to a prefix (`Rs.26000`, `x26000`) escaped the regex; the lookbehind now only
  avoids re-matching the tail of a number. Factor weights/strengths are no longer "allowed numbers".
- Worker: `news_items` is bound per tenant before the sentiment block (a failure there could have handed the
  previous tenant's classified items to this tenant's thesis); the thesis log line named the wrong job.
- Scenarios fall back to the ATR stand-in when a zone sits on the wrong side of the price (inverted/stale read);
  `capture_daily` ignores reads from an earlier day; the scoreboard counts every scored thesis, not the page;
  stored theses are picked per language; Telegram trailer tells "no read yet" apart from "flag off".
### Strategist language PR (Phase BF) - decisions
- Interpreted narrowly as planned: the strategist's output fully in the trader's language (rules in words,
  direction/timeframe words, summary; verdicts/notes/study were already bilingual) and a deterministic Marathi/English
  request parser (symbol, style, direction, language). No model call is involved in parsing, so a mis-heard request
  can only change which study runs, never an order. Unmatched words fall back to the form's values and the UI shows
  exactly what was understood before anything runs.

### BF - self-review findings fixed before merge
- Ticker fallback took only the first all-caps word and gave up on grammar words, so `SHORT ONLY ON RELIANCE` lost the
  symbol and `INTRADAY BOTH` made INTRADAY the symbol; now the first all-caps word that is not request grammar wins.
- A Latin-only request no longer flips a Marathi UI to English; the build runs from the form (a hand correction
  after the parse wins) and only the detected script travels along; Marathi index variants (`निफ्टी बँक`, `नीफ्टी`,
  bare `तेजी`/`मंदी`) recognised; debounce race guarded; timeframe grammar in rule words fixed.

## सकाळचा अहवाल (Morning report) - 2026-10-06 IST

**रात्रीचं काम पूर्ण: BC → BE → BD-lite → BF (strategist भाषा) - चारही PR tests + CI हिरवा + self-review नंतर merge.**
LIVE शी संबंधित काहीही बदललेलं नाही; नवीन सगळी features default-off flags मागे किंवा फक्त PAPER.

### Merge झालेले PR (main वर)
| PR | Phase | मुख्य | Flag |
|----|-------|-------|------|
| #51 | BC - market sentiment | PCR/OI, VIX, heavyweight breadth, global mood, FII/DII seam → -100..+100 score; memory card gauge + brief chip. Review fix: breadth quotes resolved per symbol (bulk quote Upstox/Zerodha वर silently fail होत होता). | नाही (background read) |
| #52 | BE - Telegram inbound | Commands (/brief /positions /risk /news /levels /thesis /why + free text), Approve/Reject buttons फक्त PAPER + pause/reduce/review; HMAC single-use nonces press-time re-check. Review fix: alert-channel PUT ने inbound bypass (BLOCKING) बंद; webhook वर flag; ai_copilot kill flag Telegram ला लागू; atomic nonce claim. | `telegram_inbound` (off) |
| #53 | BD-lite - market thesis | Per-symbol thesis, agreement matrix, bull/base/bear scenarios, numbers-checked narrative, next-session scoring. **Shadow multiplier फक्त नोंद - कुठेही लागू नाही** (source-scan test). Review fix: numbers regex bypass, worker मध्ये per-tenant news_items. | `market_thesis` (off) |
| #54 | BF - strategist भाषा | नियम शब्दांत (mr/en), direction/timeframe/summary; मराठी/इंग्रजी request parsing (`बँक निफ्टी फक्त long scalping`) + `/strategist/parse`. Review fix: all-caps ticker fallback, Latin request ने भाषा बदलत नाही, hand correction जिंकते. | नाही (ai_copilot) |

### आकडे
- Backend suite (merged BF tree): 1055+ passed, 4 skipped (प्रत्येक PR आधी पूर्ण suite स्थानिक + CI वर Postgres `alembic upgrade head` + `alembic check`).
- नवीन migrations: `c8d0e2f4a6b8` (telegram_callbacks, notifications.ai_action_id), `d9e1f3a5b7c9` (thesis_records). Deploy करताना `scripts/deploy.sh` migrations लावेल (market hours guard आहे).
- Frontend `tsc` + `vite build` प्रत्येक PR वर clean.

### न सापडलेलं / interpretations
- `ATP_FULL_EXECUTION_PROMPT` कुठेही सापडला नाही (uploads मध्ये फक्त master prompt PDFs). वरचे चारही phases approved plan + रात्रीच्या सूचनेतल्या नावांवरून केले (वर "Context" पाहा).
- **G1** कुठेही define केलेलं नाही → चार PR नंतर थांबलो; हा अहवाल तेच.
- BD "lite": overlay चं deployment setting (`thesis_overlay_mode`) मुद्दाम आणलं नाही - झोपेत sizing बदलू शकणारं काहीही नको.

### सकाळी तुम्ही ठरवायचं / करायचं
1. Flags (Admin > Controls): `telegram_inbound`, `market_thesis` on करायचे का (default off). `news_feed` BB पासून off आहे.
2. Telegram two-way हवं असेल तर: Settings > Alert delivery > Telegram > "Two-way Telegram" on → allowed chats → Register webhook (HTTPS domain लागतो; prod overlay चा Caddy देतो). फक्त PAPER + pause/reduce/review; exits/LIVE web + authenticator.
3. Thesis scoreboard काही आठवडे पाहा; overlay active करण्याचा निर्णय scoreboard वर, नंतर, वेगळ्या PR मध्ये.
4. उरलेलं (plan मधलं, न केलेलं): AY/AZ/BA price-action port (Trade repo rewrite ची वाट), weekly thesis report notification, news feedback table, FII/DII source (terms), managed Postgres (LIVE आधी).
5. Known limits: Telegram rate-limit per-process (multi-worker API वर 20×N); sentiment/news feeds sandbox मधून live तपासलेले नाहीत (outbound नाही) - पहिल्या दिवशी logs पाहा.

### Branches
- `main` = सगळं merged; `claude/gracious-rubin-cjrkhg` main शी sync. Feature branches: `claude/bc-sentiment`, `claude/be-telegram-inbound`, `claude/bd-lite-thesis`, `claude/bf-strategist-language` (merged; हटवता येतील).

## Continuation - 2026-10-06 (after the morning report)

### BD-2 - decisions
- "Continue" without a new brief: picked the two Phase BD items still open that need no operator decision and change no
  sizing - the weekly thesis scoreboard notification and the news feedback table. The overlay stays shadow; the
  report is what the operator will judge it on.
- News trust is deterministic (useful share over 90 days, floor 0.25, neutral until 10 verdicts) and tenant-scoped;
  it only scales the thesis news factor's strength, so the worst a wrong verdict can do is change a reading.
- The weekly report is idempotent per ISO week and flag-gated like the thesis itself.

### BD-2 (PR #55) - self-review findings fixed before merge
- "My verdicts" lookup moved from a 2000-char GET query to a chunked POST (<=200 ids per call): with months of feed rows
  the GET would have 422'd and blanked every pressed thumb on load. Unicode-digit and oversized ids no longer reach the DB.
- The report's flag-off test was vacuous (no scores in that week); it now proves silence with scores present, plus a
  503 on the preview with the flag off. Concurrent double vote updates instead of 500; note length is a clean 422.
- News page skips the feedback calls when the feed is off; THESIS_REPORT added to the UI notification type; Marathi
  label for the trust suffix; worker import hoisted.

### 2026-10-06 - operator decisions and CI hygiene
- Operator: AY/AZ/BA (Trade price-action engine port) **stopped** - the level engine showed no edge over random in
  Trade's validation. Flags `news_feed`, `market_thesis`, `telegram_inbound` will be switched on by the operator in
  PAPER after the droplet deploy. No new phase until then; only CI/flaky-test hygiene and keeping GO_LIVE_MR.md current.
- CI: one run per commit (the pull_request event duplicated every job for in-repo branches and produced the
  cancelled/skipped check runs seen on PR #55); job timeouts; superseded feature-branch runs cancelled, main never.
- Tests: feature flags and platform-wide market events are reset after every test module (conftest), so a module
  that turns a flag on or seeds a global event can no longer change what a later module sees (the class of failure
  fixed by hand twice this week). Unused imports and an ambiguous name cleaned (ruff).
- GO_LIVE_MR.md: flags step (1.8), two-way Telegram step (1.9), morning check for feed/sentiment/thesis, Friday thesis
  scoreboard in the day table, the stopped engine port and the "flags are not in the PAPER criteria" note.


### 2026-10-06 - Phase BG: local PC host, Fyers daily login, read-only check end to end
- Operator decision: no droplet; the PAPER week runs on the Windows PC (Docker Desktop) that already hosts ATP,
  broker Fyers (existing app), not Upstox. `docker-compose.local.yml` binds Postgres/Redis/API/UI to 127.0.0.1
  and restarts everything with Docker Desktop; `docs/LOCAL_PC_MR.md` (Marathi) covers pull/build/migrations,
  sleep/restart, the daily Fyers auth-code login with what the screen shows, the read-only check, the first-day
  script on local, and that two-way Telegram (1.9) is not available locally (outbound alerts are).
- Bugs found by writing the end-to-end test over the real seams (stored encrypted credential -> build_adapter ->
  smoke -> first-day report), all fixed with tests:
  1. Daily re-login by pasting the auth code could not work: the credential merge kept yesterday's access token
     and the auth-code adapters (Fyers, Kite) skip the exchange while a token is on file, so every pasted code
     "failed" with the stale token. A fresh request_token now drops the stored access token.
  2. Fyers F&O positions came back with exchange `NSE` (Fyers uses the `NSE:` prefix for derivatives too), so
     the contract-symbol translator never restored them to the platform spelling - the monitor and reconciliation
     would not have matched an option position. Positions/holdings now carry NFO/BFO from the segment code or
     the ticker shape.
  3. The Fyers symbol-master parser read strike and option type from fixed columns whose documented order has
     moved; with the current layout every NIFTY option would have had strike 26000 (the index's scrip code) and
     no CE/PE. The ticker is parsed first, columns confirm; both layouts are under test.
- New: `GET /api/broker/{name}/login-url` + `POST /api/broker/{name}/login-code` (Fyers, Kite), the banner's
  "Open login" + paste box, a smoke `option_chain` step (optional), broker-neutral fix text in the first-day check.
- Self-review findings fixed before merge: the compose overlay had *added* loopback bindings next to the base
  file's 0.0.0.0 ones (Compose concatenates `ports`) - `ports: !override` like the prod overlay, with a test that
  every published port in the overlay is `127.0.0.1:`; `_code_from_paste` no longer 500s on a stray `[` and reads a
  code carried in the URL fragment; `login-code` has the same verified-email check, failure notification and
  "mark EXPIRED only when the broker answered" rule as `/authenticate`; the banner opens the tab inside the click
  (popup blockers) and always shows the link; digit-leading underlyings (360ONE, NIFTYNXT50) parse from the ticker;
  NSE commodity segment no longer labelled NCDEX; a CE/PE cell is never taken as the underlying.
- Operator's new upload (ATP_PRO_GRADE_UPGRADE_PLAN.pdf: P0 bugs -> P1 frontend -> P2 options-seller core ->
  P3 backtesting -> P4 DSL v2 -> P5 scale) read; its method says "plan each phase in WORK_LOG, then PRs, stop
  only at the G-* gates". P0 planning starts after this PR merges (next entry).

### 2026-10-06 - P0 plan (ATP_PRO_GRADE_UPGRADE_PLAN, "P0: serious bugs before real users")
Method per the plan's §0: plan the phase here, then one PR per goal with tests, migrations + drift check, frontend
build, docs, WORK_LOG; full suite + self-review subagent before merge; ADR-0004/0006, PAPER default, flags for new
behaviour, no LIVE, secrets only in env/Settings; stop only at the G-* gates. **PAPER-week rule added by us:** the
operator's local PC is running the PAPER week on `main`, so nothing in P0 may invalidate stored secrets, log the
operator out, or need a manual step on `git pull` + `up --build` - secret/auth format changes land dual-read
(old format still accepted) and re-encrypt/re-issue lazily.

Every P0 item was checked against the code before planning (the plan's file names were partly guesses):

| # | Verified in code | PR |
|---|---|---|
| S1 | uvicorn behind Caddy without `--proxy-headers`; lockout per account/IP in `auth/lockout.py` (15 min window), no platform-wide rate limit, no progressive delay | P0.2 |
| S2 | `cache_acquire_lock` fail-open on Redis errors; release = GET then DEL (not atomic); no renewal | **P0.1 (done)** |
| S3 | `/api/backtest` optional user, `/api/price-action/*`, `/api/support-resistance/zones`, `/api/option-chain/analyze|greeks`, `/api/scanner/run` anonymous; pandas work on the event loop; no body cap | **P0.1 (done)** |
| S4 | staging overlay added ports next to the base file's 0.0.0.0 ones; production checks skipped for staging | **P0.1 (done)** |
| S5 | webhook/SMS channel URL check is scheme + hostname only, no private-range resolution; SMS response echoed | P0.2 |
| S6 | access + refresh token in localStorage; no CSP | P0.3 (dual: cookie refresh + memory access, old clients keep working until they re-login) |
| S7 | audit chain reads last row without a lock; `atp_app` has DELETE on all tables; audit FK SET NULL | P0.3 (advisory lock per tenant, revoke DELETE on audit/orders/trades, range verification) |
| S8 | `verify_totp` accepts a code twice inside its window (no last-used step) | P0.2 |
| S9 | refresh rotation without `SELECT ... FOR UPDATE`; two tabs can revoke each other | P0.2 |
| S10 | money columns are `Float`; currency assumed INR | **P0.4 (done)** - Numeric(18,2) amounts / Numeric(18,4) prices, migration c4d6e8f0a2b4, drift check green |
| S11 | unset `SECRETS_ENCRYPTION_KEY` -> fixed dev key (refused in production only); Fernet, no AAD; passphrase via plain SHA-256 | **P0.4 (done)** - AES-256-GCM, AAD = tenant_id + column purpose, dual-read, `SECRETS_WRITE_FORMAT` (default fernet this release), scrypt for passphrases |
| S12 | `create_all` at every startup next to Alembic | **P0.1 (done)** |
| S13 | metering INSERT + SUM per API call; key prefix is 4 random bytes (no uniqueness guarantee); TradingView token stored plaintext on tenants | P0.3 |
| S14 | HS256 JWT with `email` claim, no iss/aud/kid | P0.3 (iss/aud/kid + rotation, drop email claim; **dual-verify** old tokens until they expire) |
| T1 | partial fills handled, unfilled remainder of a MARKET order not cancelled; unknown fill -> recorded as requested | **P0.5 (done)** - remainder cancelled, unfilled order cancelled (no phantom position), unconfirmable -> broker-uncertain |
| T2 | protective stop is always SL-M; adapters whose broker refuses SL-M on options are not asked | **P0.5 (done)** - `BrokerCapabilities` per adapter, SL with a 1% limit band where SL-M is refused |
| T3 | multi-leg: shorts sent before wing fills are confirmed; failed wing fill recorded 0.0 | **P0.5 (done)** |
| T4 | emergency exit cancels orders in the DB only (`transition_order`), not at the broker; shorts/longs order not enforced | **P0.5 (done)** |
| T5 | every order `MARKET`, no market-protection % / marketable-limit option | **P0.5 (done)** - `order_style` per deployment (MARKET default, PROTECTED_LIMIT opt-in) |
| T6 | daily loss on realised P&L only; "day" is UTC | **P0.5 (done)** - IST trading day, open positions marked by the monitor count |
| T7 | `time_to_expiry_years` in whole days | **P0.6 (done)** - seconds to the 15:30 IST close |
| B1-B5 | daily counters, STT on short options, optimizer winner on IS, gap fills, dated lot sizes | **P0.6 (done)** |
| infra | deploy.sh rollback re-tags the image but does not restart (verified: it did not even tag - `images --format '{{.ID}}'` is not a compose form, so the previous id was always empty); no log rotation; CI without ruff/mypy/bandit/gitleaks; actions not SHA-pinned; `deploy-staging.yml` interpolates inputs into the remote shell; OpenAPI title/version placeholder | **P0.1** (OpenAPI, staging workflow), **P0.7 (done)** - rollback restarts the previous image, log rotation, lint job (ruff/mypy/bandit/gitleaks), SHA-pinned actions |

**P0.8 (added 2026-10-06 from `ATP_AI_COPILOT_FIX_PROMPT`, before P1):** the AI Copilot review's must-fixes, every
item re-read in code before planning - all seven safety findings are real:

| # | Verified in code | PR |
|---|---|---|
| A1 | `ai/monitor.py` execute: `close_position` returning `closed=False` still ended EXECUTED, and the rule could not fire again that day | **P0.8-A (done)** |
| A2 | `ai/routes.py` approve_action: no TOTP step-up for a LIVE deployment/position (Telegram text promised "web + authenticator") | **P0.8-A (done)** |
| A3 | `strategist_adopt` saved whatever config the browser sent with `origin="ai-strategist"`, no compliance check, no stored backtest; interview "Deploy in PAPER" posted straight to /deployments | **P0.8-A (done)** |
| A4 | `custom_{id}` returned by AI approve / marketplace, resolver knows `custom:` only | **P0.8-A (done)** |
| A5 | `decide` checked status in memory; no database guard against two open proposals for one rule | **P0.8-A (done)** |
| A6 | Telegram checked the chat id only; any group member could press Approve and the owner was recorded as decider; rate limits in-process | **P0.8-A (done)** |
| A7 | empty news scope matched everything in corroboration; bare "circuit" = severity 5; PAUSE landed on `active[0]` when no symbol matched | **P0.8-A (done)** |
| B1-B3 | thesis headlines not wrapped, headline digits counted as allowed numbers, abs() compare; no numbers-check on copilot/knowledge/Telegram; flaky bd2 test | **P0.8-B (done)** |
| C1-C5 | thinking + small max_tokens, no `stop_reason` handling, hard-coded models, OpenAI reasoning params, no client reuse/caching, no cost metering | P0.8-C |
| D1-D5 | interview "Recommended"/match %/allocation advice, strategist "Best", thesis targets, no first-use acknowledgement, no LLM audit table, DPDP text wrong, AI marketplace listings | P0.8-D |

Migrations: P0.4 (Numeric money, encryption format columns) and P0.3 (audit anchors, key hashing) only, all
batch-safe, off-hours per the guard. Risks: S6/S14 affect every logged-in client -> dual-read for one release;
S11 re-encryption must never run before the key ring is warm; T5 changes order types at the broker -> PAPER
default and G-LIVE gate before any LIVE wiring.

### 2026-10-06 - P0.1: hardening PR 1 (S2, S3, S4, S12, OpenAPI, deploy workflow)
- S3: backtest, price action, S/R zones, option-chain analyze/greeks and the scanner need a logged-in caller; the
  backtest is metered for everyone; pandas work runs in the threadpool; bodies over `MAX_REQUEST_BODY_BYTES`
  (8 MB default) are refused from Content-Length with a 413 (chunked bodies stay with the proxy's limit).
- S2: `cache_try_lock` reports Redis reachability; release and renew are compare-and-act Lua; the worker renews the
  lock after the evaluation phase and, in production/staging (or when constructed with `require_lock_for_live`),
  pauses LIVE *entries* for the cycle while Redis is unreachable (exits, PAPER and housekeeping continue).
- S4: staging overlay `ports: !override` on loopback; staging boots with the production configuration checks;
  METRICS_TOKEN now required there too. Deploy workflow passes inputs through the environment, never into the
  script text, and refuses a ref with unexpected characters.
- S12: `create_all` only outside production/staging (`tables_created_at_startup`). OpenAPI title/version from
  `APP_VERSION`.
- Self-review findings fixed before merge: the body limiter now sits inside CORS/observability (a 413 carries CORS
  headers and a request id); Monte Carlo, walk-forward and optimizer closed to anonymous callers and moved to the
  threadpool too; the replica lock is renewed per tenant (the evaluation phase is the long one) and the LIVE gates
  sit below the venue check; compose forwards `MAX_REQUEST_BODY_BYTES`/`APP_VERSION`; Caddy caps chunked bodies;
  the staging deploy's ref check accepts `@^~+` and refuses a leading `-`; `deploy.sh` fetches then checks the ref
  out detached (`git fetch origin origin/main` never worked); the UI asks anonymous users to sign in on the pages
  that call the closed endpoints instead of surfacing a raw 401, and the Signals page no longer fails entirely when
  the S/R zones call is refused.

### 2026-10-06 - P0.2: login protection, egress, TOTP replay, refresh race (S1, S5, S8, S9)
- S1: uvicorn runs with `--proxy-headers --forwarded-allow-ips "$FORWARDED_ALLOW_IPS"` (default 127.0.0.1; the prod
  overlay sets `*` because only Caddy reaches backend:8000), so the client IP the limiter and login protection see
  is real and unspoofable. The request limiter counts in Redis (`INCR`/`EXPIRE`, shared across replicas) in
  production/staging or with `RATE_LIMIT_BACKEND=redis`, falls back to the per-process window when Redis is down
  (never fail-open), and gained a per-user flavour applied to the backtest and scanner endpoints. The per-email
  *hard lock* is gone: after 3 failures the next attempt must wait 1, 2, 4 ... 60 s since the last failure (429 +
  Retry-After, from any IP), so a brute force crawls while the owner can never be locked out by someone spamming
  their address; the per-IP cap (50 / 15 min, 423) stays. CAPTCHA hook: with `CAPTCHA_PROVIDER` (turnstile |
  hcaptcha), `CAPTCHA_SECRET` and `LOGIN_CAPTCHA_AFTER_FAILURES` set, the login demands `captcha_token` (403
  `captcha_required`, header `X-Captcha`) and verifies it with the provider; off by default (no widget in the UI yet).
- S5: `app/core/egress.py` - tenant-supplied URLs (alert webhooks, SMS gateways, push endpoints) are checked
  literally at save time (https, no loopback/private/link-local literal, optional `EGRESS_ALLOWED_HOSTS`) and
  resolved right before every request in production/staging (any non-public address, IPv4 or IPv6-mapped,
  refuses the send - DNS rebinding included). Dev/test keep localhost webhooks. The SMS gateway's response body is
  no longer echoed into the error (it can carry the gateway's own secrets); the push service's neither.
- S8: `users.mfa_last_step` (migration f1a3b5c7d9e1, nullable) - a TOTP code is accepted once; the same or an
  older step is a replay. Confirm, verify, step-up, backup-code regeneration and disable all go through
  `accept_totp`. Existing users: column NULL -> first code accepted as before.
- S9: refresh rotation locks the session row (`FOR UPDATE`, no-op on SQLite) and honours a token presented again
  within `REFRESH_REUSE_GRACE_SECONDS` (30) of its rotation - two tabs refreshing at once no longer log each other
  out; the token issued in between is retired, and reuse after the window still revokes the session.
- Tests: `tests/test_phase_p0_2_auth_egress.py`; login-protection, MFA, sessions and rate-limit tests adapted to
  the new semantics (the MFA tests now ask the authenticator for the *next* code when they use it twice).
- Self-review fixes before merge: attempts the platform refused (delayed / locked / captcha) are recorded but no
  longer counted as failures, so hammering an address cannot extend the owner's wait with requests that were never
  evaluated (the remaining trade-off: a persistent attacker who keeps *failing* real passwords, at most one per
  minute, can still inconvenience the owner - the CAPTCHA hook is the answer for that); the Redis limiter's INCR
  and EXPIRE run as one script so a crash between them can never leave a counter that refuses forever.

### 2026-10-06 - P0.3: tokens, audit chain, API keys / webhook token (S6, S7, S13, S14)
- S14: access tokens carry `iss`/`aud` and a `kid` header, no e-mail claim; `JWT_PREVIOUS_SECRET_KEYS` keeps
  tokens verifiable through a secret rotation; `JWT_ACCEPT_LEGACY` (default on) accepts pre-P0.3 tokens until they
  expire, so the deploy logs nobody out. Kept HS256 rather than the plan's asymmetric keys: no third party
  verifies these tokens (one API, one UI), so a public key would add a key file and a JWKS endpoint and no
  security; revisit when a second service needs to verify them. Passwords are capped at 72 bytes (bcrypt
  silently truncates beyond) at the policy and at hashing.
- S6: the refresh token travels in an HttpOnly, SameSite=Strict cookie scoped to `/api` (Secure in
  production/staging); `/auth/refresh` takes the cookie or the body; logout clears it. The UI keeps the access
  token in memory only, refreshes from the cookie on reload, and uses a token an older build left in
  localStorage once, then removes it. `REFRESH_TOKEN_IN_BODY` (default on this release) keeps the JSON field for
  older clients. Content-Security-Policy and Permissions-Policy on the frontend image and at the Caddy edge
  (scripts/connect self only; inline style attributes allowed because React components set them).
- S7: audit appends take a Postgres advisory lock (no forks under concurrency); `audit_anchors` pins the chain
  head once a day from the worker; `verify_audit_chain(since_anchor=True)` recomputes from the anchored row;
  `audit_logs` foreign keys are RESTRICT (migration a2b4c6d8e0f2 - SET NULL would rewrite hashed fields); the
  application role loses DELETE on audit_logs/audit_anchors/orders/order_events/trades (db_roles.sql).
- S13: API keys are resolved by their unique hash (the 4-byte display prefix is not unique); the day's usage
  total is kept in Redis (atomic INCRBYFLOAT, SUM fallback) so the per-request allowance check is O(1); the
  TradingView URL token is stored hashed (`tenants.webhook_token_hash`, migration b3c5d7e9f1a3, backfilled on
  Postgres; a legacy plaintext still matches once and is hashed) - a new organisation sees its URL only when the
  **owner** rotates (shown once); rotation is audited.
- Tests: `tests/test_phase_p0_3_tokens_audit.py`; the TradingView tests rotate to obtain a URL.
- Self-review findings fixed before merge: Telegram inbound looked the organisation up by the plaintext token
  (broken for every new organisation) - one shared `resolve_tenant_by_webhook_token` for both webhooks, the
  Telegram path now carries the stored hash as identifier (secret header authenticates; re-register after a
  rotation); `verify_password` truncates at 72 bytes like bcrypt did when old hashes were made (refusing would
  have locked out long-password accounts - PAPER-week rule); the Redis day counter is seeded from the SUM when
  its key is recreated (a Redis restart mid-day no longer under-counts the plan cap); the owner-only rotation test
  really exercised (team invite route); CSP allows the Google Fonts the UI loads; an invalid refresh clears the
  cookie (explicit 401 response); refresh rate limit 60/min per IP because every reload refreshes; the DELETE
  revoke in db_roles.sql is guarded for a fresh database; a 429 on refresh is treated as transient by the UI.

### 2026-10-06 - P0.4: exact money, AES-GCM secrets (S10, S11)
- S10: `Money = Numeric(18, 2)` for amounts (P&L, charges, balances, fees, invoices, payouts) and
  `Price = Numeric(18, 4)` for traded prices and levels; both `asdecimal=False`, so every engine still computes on
  floats and no call site changed. Migration `c4d6e8f0a2b4` alters 28 columns in 9 tables (batch mode; Postgres
  casts FLOAT -> NUMERIC in place, rounding to the scale - verified on a scratch Postgres 16 with seeded rows,
  `alembic check` clean after upgrade and after downgrade + upgrade). Quantities, percentages, fundamentals and
  FX rates stay Float on purpose. Not done: a currency column per trade (Phase P3 already reports portfolio
  figures in the organisation's base currency; a per-row currency belongs with multi-currency trading, not P0).
- S11: `t2:<tenant>:<purpose>:<nonce||ct>` - AES-256-GCM under an HKDF key derived from the tenant data key,
  associated data `tenant_id|purpose`; every encrypt/decrypt call names its column (`envelope.PURPOSE_*`), so a
  ciphertext copied into another organisation's row or another column refuses to open. Deviation from the plan's
  "AAD = tenant_id + row id": a row id does not exist before the first flush and the same helper serves inserts
  and updates, so the binding is tenant + column; swapping two rows of the *same* column within one organisation
  stays possible (and is the least useful attack: both are that organisation's own secrets). Dual-read of the
  Phase N `t1:` and the pre-Phase-N master formats; `SECRETS_WRITE_FORMAT` defaults to `fernet` this release
  (PAPER-week rule: an image rollback must still read every secret) and is flipped to `aesgcm` after the week,
  then `reencrypt_secrets.py reencrypt` converts rows (idempotent; `status` lists formats and `pending_rewrite`).
  Passphrase masters get a scrypt-stretched key (n=2^14, fixed domain salt) next to the SHA-256 derivation via
  `MultiFernet`; the scrypt key *encrypts* only once `SECRETS_WRITE_FORMAT=aesgcm` (self-review caught that
  scrypt-first by default would have wrapped a new organisation's data key in a form the previous image cannot
  open - a boot failure after rollback), SHA-256 leads until then and both always decrypt. A proper Fernet key
  (the operator's) is used unchanged. `decrypt_text` also takes the owning row's tenant id and refuses a token
  minted for another organisation even when copied verbatim (the `t2` header alone could not catch that);
  `warm_all` logs and skips one unopenable tenant key instead of stopping the API.
- Tests: `tests/test_phase_p0_4_money_crypto.py` (Numeric types + float round trip; migration covers every
  Numeric column; default format; AAD/tenant/relabel/move refusals; older formats; re-encrypt both modes via the
  credentials API; scrypt + legacy passphrase).

### 2026-10-06 - P0.5: trading safety (T1-T6)
- T1: after a LIVE entry the router reads the book; a partial fill cancels the working remainder at the broker;
  an order still unfilled after the poll window is cancelled (a fill that lands during the cancel is honoured)
  and *no position is booked* - previously it was recorded at the signal price as if filled. A book the broker
  cannot show, or a cancel that fails, records the order as requested and flags the organisation broker-uncertain
  (LIVE entries pause until reconciliation). REJECTED/CANCELLED in the book is a business rejection. Day counters
  move only after a confirmed fill.
- T2: `BrokerCapabilities` on every adapter (`stop_market`, `stop_market_on_options`, ...); Zerodha declares no
  SL-M on options (Kite's rule); CoinDCX keeps the defaults because its adapter already turns SL-M into its own
  stop-limit. `stop_order_params` picks SL-M or SL with the limit one
  band (1%) past the trigger, rounded to the tick; the router, the stop guard re-arm and the trailing-stop modify all
  go through it, and `looks_like_option` recognises every broker spelling (RELIANCE is not an option).
- T3: `_place_live_legs` confirms each wing's fill in the book before any short is sent, cancels a leg that does
  not fill, unwinds only confirmed legs and never records a 0.0 fill.
- T4: the emergency exit cancels LIVE orders at the broker first (every usable session is tried; failures are
  listed in the response), then closes shorts before longs.
- T5: `order_style` per deployment - MARKET (default, unchanged) or PROTECTED_LIMIT, a marketable limit
  `market_protection_pct` (0.5% default) past the signal price on the 0.05 tick; with T1 an unfilled limit is
  cancelled instead of chased. Deployment form has the selector. Multi-leg entries stay MARKET (sequencing is the
  protection there). PAPER is unaffected.
- T6: `trading_day_start()` (00:00 IST as UTC) bounds "today" for the daily loss, strategy loss and trade counts
  (was UTC midnight = 05:30 IST, so a pre-05:30 loss fell out of the day); the position monitor writes
  `trades.mark_price/mark_time` each sweep and the daily / strategy loss limits add the marked-to-market P&L of
  open positions to realised P&L (a position without a mark contributes nothing - never guessed). Stricter, so
  on by default. Migration `d5e7f9a1b3c5` (verified on Postgres: upgrade, check, downgrade, upgrade).
- Tests: `tests/test_phase_p0_5_trading_safety.py` (incl. the signal-execution path flagging the organisation and
  the stacked worker wrappers); `test_live_execution` "never fills" case now asserts the cancel; the worker's fake
  broker lists its orders as a real book does.
- Self-review findings fixed before merge: `ContractSymbolBroker` (every non-Upstox adapter runs inside it) did not
  accept the option flag nor expose the inner matrix - an F&O LIVE entry would have lost its exchange-side stop;
  a cancel *request* the adapter accepted was treated as proof (every adapter answers "CANCELLED"; Kite/Upstox cancel
  asynchronously) - now only the book showing the order terminal with nothing filled counts, anything else is
  recorded and flags the organisation; CoinDCX's `active_orders` drops filled orders, so the adapter now looks up
  the ids it placed by `orders/status`; a structure leg that fills during its cancel joins the unwind (filled
  quantity, shorts first) instead of being orphaned; CoinDCX refuses to edit a stop's trigger (it can only move the
  limit); the marked-to-market term counts only positions opened and marked today (a carried swing gain must not
  hide today's losses); a remainder the exchange already cancelled is not cancelled again; the emergency exit asks
  the broker whose book lists the order first.

### 2026-10-06 - P0.6: Greeks clock and backtest honesty (T7, B1-B5)
- T7: `time_to_expiry_years` measures seconds to the contract's 15:30 IST close; an aware datetime is exact, today's
  date (the exchange's date, `today_ist`) means "now", an older date keeps whole days. Whole days made every expiry-day Greek the one-hour floor
  and every 1-DTE theta a day too large (strike selection, leg Greeks and chain analysis all go through it).
- B1: the plain backtest engine never reset `trades_today` / `daily_pnl`, so `max_trades_per_day` and the daily
  loss limit capped the *whole run* after the first day. Counters now reset per Indian trading day, as the option
  engine already did and the worker does. `ENGINE_VERSION` 3: stored runs from version 2 are not comparable.
- B2: the cost model assumed entry = buy, exit = sell; a written option's sell-side STT therefore landed on its
  exit premium instead of its entry premium (`sold_first` for short positions, in the option engine, the position
  monitor and the paper broker). A bought leg settled in the money at expiry is exercised: STT 0.125% of the
  intrinsic value replaces the sell-side premium STT; a settled leg pays one brokerage and no stamp duty on a
  buy-back that never happened; a calendar spread's far leg is closed at market as before. Option engine version
  `4-options`: stored option runs from `3-options` are not comparable.
- B3: the optimizer ranked candidates by the out-of-sample metric, which makes the held-out part in-sample. It now
  ranks in-sample and reports each candidate's out-of-sample figure as `validation`;
  `best_confirmed_out_of_sample` says whether the winner held up (the Backtest page shows it as a badge). A caller
  that read `score` as the out-of-sample number must read `validation`.
- B4: a bar that opens beyond a stop or target fills at the open, not at the level (`determine_exit_price` takes
  the bar's open; the live path passes none and is unchanged).
- B5: option backtests size with the lot the exchange applied on the entry day (`lot_size_on`, dated table with the
  20 Nov 2024 index revision; earlier history uses the pre-revision lot - an approximation stated in the code),
  unless the run pins `lot_size`.
- Tests: `tests/test_phase_p0_6_backtest_fixes.py`; the `test_phase_m_closure` optimizer test is named for the new
  ranking (its assertions already held).
- Self-review findings fixed before merge: option engine version bumped (every option run's numbers changed); the
  exercise treatment applied to a calendar spread's far long leg, which is sold at market, not settled; settled legs
  were charged an exit order's brokerage and stamp duty; `date.today()` callers (chain analysis, leg Greeks, position
  Greeks) now use the IST date so the 00:00-05:30 IST window does not read yesterday's Greeks; the run summary's
  `lot_size` lists every lot used when a run spans the revision; the lot table's comment states its approximations
  (no intermediate-revision row, keyed by entry date) instead of implying full history.

### 2026-10-06 - P0.7: infra (deploy rollback, log rotation, static gates, pinned actions)
- `scripts/deploy.sh`: the previous image id is read with `images -q` (the old `--format '{{.ID}}'` is not a compose
  form, failed silently and left nothing to roll back to); the worker image is built with the others (it was never
  rebuilt, so the worker kept the first image forever); the rollback restarts the API from the previous image (tagged `:previous` before the build,
  retagged `:latest` on failure, `up -d --no-deps --no-build backend`, health wait); before it only re-tagged and
  the broken container stayed up. Worker/frontend are untouched by a rollback (they restart after health only);
  the schema is not rolled back (additive migrations, previous image reads them).
- Log rotation: `x-logging` anchor (json-file, 20m x 5, compressed) on every compose service incl. Caddy/offsite.
- CI `lint` job: ruff (`backend/ruff.toml`: E4/E7/E9/F/B006/S102/S307/T10/PLE, E741 ignored, tests keep E402/E702/E731),
  mypy (`backend/mypy.ini`: the 15 packages that are clean today, `follow_imports = silent`; 390 findings remain in
  the others - widen package by package), bandit `-ll -ii` on `app/` (B314 fixed with defusedxml), gitleaks on the
  pushed commits (`.gitleaks.toml`: the two documented false positives allowlisted; the full history scanned clean
  locally). 133 unused imports / variables removed. Every action SHA-pinned (checkout, setup-python, setup-node,
  trivy, ssh-action, gitleaks) with the tag in a comment.
- Tests: `tests/test_phase_p0_7_infra.py`. No migration, no runtime behaviour change beyond log rotation and the
  XML parser.
- Self-review findings fixed before merge: the dead `images --format` line (above); the worker build; gitleaks binary
  version pinned and PR comments off (the job has read permission only); pin comments name the exact tag
  (v4.4.0, v5.6.0, v2.3.9); `ET.Element` does not exist on defusedxml (annotation now the stdlib `Element`); the
  OPERATIONS roll-out note says the first `up -d` recreates every container, Postgres and Redis included.

### 2026-10-07 - P0.8-A: AI Copilot safety (A1-A7 of ATP_AI_COPILOT_FIX_PROMPT)
- A1: an approved EXIT whose `close_position` did not close is **FAILED** with the reason (never EXECUTED), and a FAILED
  row no longer blocks the rule for the day; a LIVE position without a broker session is refused, and the web approval
  passes the trade's broker adapter (`position_monitor.broker_for_trade` - the same helper as the Positions page,
  the kill switch and the worker: broker account, else the deployment's broker, else the tenant's only broker).
- A2: `approve_action` runs `ensure_live_step_up` when the proposal's deployment or position is LIVE (same rule as
  creating/resuming a LIVE deployment: 403 with the MFA code until the session passed TOTP).
- A3: `ai_candidates` table (migration `e6f8a0b2c4d6`): `/strategist/build` and `/interview/plan` persist the server's
  validated candidates (config, simulation/evidence, deployment, risk; 7-day TTL). `/strategist/adopt` takes
  `candidate_id` + `accept_risk` (a browser config is a 422), requires a simulation with trades, runs the compliance
  checklist (`evaluate_config`, 400 on failures) and the risk acceptance before stamping `origin=ai-strategist`.
  New `/interview/deploy` (candidate_id + accept_risk) requires backtest evidence with trades and forces PAPER; the
  Strategy Interview and the Strategist cards show the risk checkbox. Candidates are per tenant (404 otherwise).
- A4: `custom:` everywhere (`/drafts/{id}/approve`, `generator.as_dict`, marketplace); `resolver.normalize_strategy_id`
  accepts the legacy `custom_<id>` and the migration rewrites stored deployments; deployment creation normalises.
- A5: `decide` is a conditional UPDATE (`WHERE status='PROPOSED'`; the loser gets "decided concurrently"); partial
  unique index `uq_ai_actions_open_rule` (tenant, deployment, rule where status in PROPOSED/APPROVED) with
  `raise_proposals` inserting each row in a savepoint and skipping the IntegrityError (earlier rows and the caller's
  objects survive); the migration expires pre-existing duplicate open rows before building the index. Self-review
  fixes: canonical `position_monitor.broker_for_trade`, no rollback in `decide`, interview-deploy happy path
  tested on a cash deployment, `ai_candidates` retention knob, Telegram tests pinned off the shared limiter.
- A6: `TelegramConfig.approvers` (Telegram user id -> team member, set by the owner under Settings by e-mail); a
  button press or command is attributed to the sender's `from.id`: with approvers configured only a listed sender acts,
  as that member (recorded in `decided_by` and the audit log); without approvers only a *private* chat with a
  whitelisted id acts (legacy PC setup unchanged), a group member is refused and audited. The per-chat rate limit is a
  Redis fixed window (`cache_incr_window`) with the in-process window as fallback.
- A7: corroboration needs an overlapping scope (empty scope confirms nothing); "upper/lower circuit" is a stock
  CORPORATE item (severity 3), only trading halt / market-wide circuit breaker / outage is LIQUIDITY 5; a severity-5
  PAUSE names a deployment the news is about (symbol, or an index deployment for INDEX scope) and otherwise nothing is
  paused (the alert still goes out); severity-4 REDUCE_RISK is unchanged.
- Tests: `tests/test_phase_p0_8a_copilot_safety.py` (8); strategist adopt / Telegram / AI draft tests follow the new
  contracts. Migration verified on Postgres (upgrade, check, downgrade, upgrade). Frontend: adopt + interview deploy
  with the risk checkbox, approvers textarea on the Telegram card.

### 2026-10-07 - P0.8-B: prompt injection and the numbers-check everywhere (B1-B3)
- `app/ai/grounding.py` is the one place for the checks: `numbers_in_values` (numeric leaves only - digits inside
  strings such as headlines are not evidence; the sign is kept, no `abs`), `numbers_in_text` (the model's shorthand
  `25k`, `1.2 लाख`, `2 cr`, `25,200.00`, `73%` expanded), `check_numbers`, `tickers_in`/`check_tickers` (symbols must
  appear in the facts or the question; indicator and platform acronyms are not symbols), `wrap_untrusted`.
- B1 thesis: the facts JSON no longer carries the headlines; they follow the JSON in an `<untrusted_data>` block with
  the closing tag escaped and the prompt says they are text to summarise, never instructions. `numbers_check` keeps
  the sign (a +0.4% day written as -0.4% is refused) and allows only numeric fields plus the symbol's own digits
  (`NIFTY 50`). `narrate` also checks tickers; one retry names the offending numbers or symbols, then the rule text.
- B2 `copilot.narrate` returns `(text, why)`: numbers and symbols only from the facts lines and the question, one
  retry, else the rule-based answer with the note (`"AI answer not used (numbers not in the facts: 26000)"`). The
  Telegram free text goes through the same `copilot_answer`. `knowledge.ai_answer` applies the same check against the
  market memory (numeric values), the concept notes and the question, else the library answer with the note.
- B3 tests `tests/test_phase_p0_8b_grounding.py` (5): injection through a headline (tag escaped, digits refused,
  no leak into the JSON), sign flip, headline digit, Copilot hallucination (API falls back to the rules), knowledge
  guide, cross-tenant approve/reject/list (404 / empty). The order-dependent
  `test_weekly_thesis_report_is_flag_gated_idempotent_and_read_only` asserts this organisation's notifications
  (the sender is platform-wide: other tests' organisations are due too). Self-review fixes: Unicode minus / en dash
  read as a minus sign; `NIFTY` = `NIFTY 50`, `BANKNIFTY` = `NIFTY BANK`, `FINNIFTY` = `NIFTY FIN SERVICE` and every word
  of an allowed name counts; a level >= 100 may be rounded to the rupee; more prose acronyms; every closing-tag
  variant escaped; the Telegram reply carries the "AI answer not used" note; the thesis reason names numbers or
  symbols. The `max_tokens` truncation test lands with
  the provider work in P0.8-C (providers only return text today).
