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
| C1-C5 | thinking + small max_tokens, no `stop_reason` handling, hard-coded models, OpenAI reasoning params, no client reuse/caching, no cost metering | **P0.8-C (done)** |
| D1-D5 | interview "Recommended"/match %/allocation advice, strategist "Best", thesis targets, no first-use acknowledgement, no LLM audit table, DPDP text wrong, AI marketplace listings | **P0.8-D (done)** |

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

### 2026-10-10 - ATP review (PR #74, #75, #77, #81) - 13 fixes, draft PR, merge only on "Merge"
- Stop path: accept-then-reject re-arm loop capped (STOP_REARM_MAX_REJECTS, then LIVE_EXIT_IF_NO_STOP exit or one
  CRITICAL); stop triggers on the tick away from the market (place, re-arm, trailing modify); the guard's per-trade
  memory dies with the position; an exit after a cancel no longer waits on the stop - a late stop fill is netted and
  handed to reconciliation. All LIVE switches stay default off.
- Expiry data: a day > coverage_end + 10 raises ExpiryDataStale (backtest skips + counts); a 403 refuses the build and
  is never cached; check_against compares from the run's --start (older rows kept); the workflow starts CI before the
  pull request, warns instead of failing when Actions may not open one, and caches the bhavcopies.
- Copilot: AICore3D has its own error boundary (SVG core on failure); banned words checked on the server on every
  model text (English + खात्रीशीर, हमखास ...; negated disclaimers pass); Marathi line only under the interview
  questions; Ask Copilot sends language "en"; Playwright: Watchtower approve / reject / LIVE step-up, risk checkbox
  gate, apply-risk confirm, 3D chunk failure, strict ai-core-3d; mockApi answers /ai/actions/* by method and state.
- Two thesis tests seeded market reads at a fixed date and broke once the wall clock passed it (date bomb) - they now
  seed a read as fresh as today's for the wall-clock paths.
- data/nse-expiries is not merged; a fresh refresh pull request (with CI) comes from the fixed workflow after merge.

### 2026-10-10 21:40 IST - Part B (backtest realism) started - branch claude/backtest-realism
- Status table given (HTF lookahead: no; models: partial; speed: no; reproducibility: partial; trial ledger: partial;
  report: partial). Part A (PR #82) waits on CI + self-review.
- B1 done: `app/backtest/windows.py` WindowCursor - a timeframe shows only bars whose end <= the decision time (the
  primary bar's close), one binary search per timeframe instead of a boolean mask per bar. Both engines use it;
  ENGINE_VERSION 4 / 7-options. Truncation test over every multi-TF strategy (fails on the old slicing: 4 of 4).
- Next: B3 speed benchmark + guard, then B4 reproducibility, B2 models, B5 trial ledger, B6 report.

### 2026-10-08 - Copilot UI redesign (7 tabs, i18n, 3D AI Core, compliance lint) -> G-UI
- Seven tabs at `/copilot/<tab>` (Market Pulse, Strategy Lab, Idea Builder, Ask Copilot, Watchtower, News Radar,
  Coach & Scorecard); the old `/ai-copilot/<slug>` addresses and `?page=ai-copilot` forward to the tab that now
  holds that content. The manual regime classifier of the old "Drafts & agent" tab is dropped - the regime dial
  (briefing) and the per-timeframe study cover it.
- 3D "AI Core": **plain three.js, not react-three-fiber + drei**. Measured: r3f + three = ~235 KB gzip (r3f imports
  the whole three namespace), over the 180 KB budget for the 3D chunk; three.js with named imports = ~129 KB. Lazy
  chunk, loaded on browser idle; SVG core when motion is reduced (system or the new "Reduce motion" setting) or
  WebGL is missing; 30 fps and fewer particles on phones; the loop stops off screen / in a hidden tab. Two designs
  (orb, particle sphere) - `localStorage.atp_copilot_core = "particles"` switches until the operator picks one.
- Budget: the brief's "Copilot route initial JS <= 120 KB gzip" is measured as the Copilot's own JS (page chunk,
  its static imports and the default Market Pulse tab) = 72 KB; with the shared app shell (81.6 KB, already under
  its own 300 KB budget) a first visit downloads 154 KB. Both numbers are printed by `check:bundle` in CI.
- i18n: react-i18next on a Copilot-only i18next instance (no cost outside the Copilot chunk); `copilot` namespace
  (English) and `interview` namespace (Marathi second lines, Hindi = one more bundle). Server-sent text (interview
  questions, AI answers, the acknowledgement's Marathi translation) is data, not interface strings.
- "Streaming" in Ask Copilot is a progressive reveal of the server's whole answer (the router answers in one
  response); Cancel aborts the request (AbortController), timeouts stop it with a friendly message. Token streaming
  from the provider would need an SSE endpoint - not in this PR.
- Cost chips: Ask Copilot shows the **metered** tokens and rupees of each AI answer (new `usage` field on
  `/api/ai/copilot`, from the metered provider of that request); Strategy Lab / Idea Builder / drafts show the
  per-task **estimate** from the provider card (marked "est."). Nothing is shown when the rules answer (no cost).
- `<main>` no longer scrolls on its own (`overflow-x-clip` instead of `overflow-y-auto`; the window was always the
  scroller) so the Copilot tab bar can be sticky.
- Compliance lint: a Vitest test parses every Copilot source with the TypeScript compiler and fails on
  "recommended / best / for you / match % / guaranteed / risk-free ..." in any string or JSX text; a negated
  disclaimer ("not a recommendation") is allowed. TradeCoach's "best day" became "top day".
- Stop point G-UI: no merge until the operator approves the screenshots (all 7 tabs, desktop + mobile, dark +
  light) and the 3D hero recording.

### 2026-10-08 - P1.3 batch 4: Settings, Account, Team, System Logs, Notifications, Admin, Coach & Guide + their cards
- Pages and the components they render (alert channels, AI provider, billing, MFA, broker accounts, API keys,
  holidays, export, contract notes, go-live checklist, market pulse, position chart, chart strategies, ProChart
  toolbar, trade coach, guide chat, sidebar, error boundary) on semantic tokens; destructive admin actions (global
  kill switch) stay solid `down`, maintenance on is solid `warn`; channel headings are neutral; coach P&L bars are
  visible again and zero is neutral. The AI Copilot page and its own panels are left for the Copilot redesign.
### 2026-10-08 - P1.3 batch 3: Strategy Builder, Fundamentals, Instruments, News & Events, Factor Lab, Marketplace
- Same rules as batches 1-2: semantic tokens, colour only for meaning, `PageHeader` on every page (Marketplace had only
  a screen-reader title), signed figures through `signClass` / `signTone`. Factor Lab's run button no longer wraps.
- Checked in dark and light at 1440 px and 390 px.

### 2026-10-08 - P1.3 batch 2: Dashboard, Positions, Backtesting, Strategy Library, Analytics, Scanner
- Same rules as batch 1: hue classes -> semantic tokens, colour only for meaning, `PageHeader` on every page.
- Signed figures use `signClass` / `signTone` (`components/ui.tsx`): up / down by sign, neutral when the figure shows as
  zero (a 0.00 average win or an empty gross loss is no longer green / red). `brand-dim` -> `brand-strong`.
- Checked in dark and light at 1440 px and 390 px with data on each page (paper trades, a run backtest, a run scan).

### 2026-10-08 - P1.3 batch 1: six pages on the design system (G-DESIGN approved)
- Pages: Signals, Autopilot (Deployments), Orders, Portfolio, Option Chain, Risk Management, plus the components they
  use (RiskLimitsCard, SignalCard, BrokerTokenBanner, BrokerUncertainBanner, StepUpDialog, DataSource).
- Hue classes -> semantic tokens (surface / fg / border / brand / up / down / warn / info); colour only for meaning:
  P&L and direction keep up/down, everything decorative is neutral, a zero is neutral (`signTone`), destructive
  actions stay `down`. Page titles use `PageHeader`; Orders and the Portfolio exposure table use `Table` with
  `Badge` / `EmptyState`; the Signals form uses `Select` / `Input` / `Button` (no more horizontal overflow).
- Checked in dark and light at 1440 px and 390 px with real data on each page (signal + chart, an analysed chain,
  paper orders, open positions). Initial JS 80.3 KB gzip.
### 2026-10-08 - G-LIVE order fixes behind switches (nothing LIVE turned on)
- `LIVE_MARKET_PROTECTION`: Zerodha and Upstox MARKET / SL-M orders carry `market_protection` (`-1` automatic band, or
  `ORDER_MARKET_PROTECTION_PCT`). Kite rejects an API market order without a non-zero value (exchange rule for algo
  orders, per Kite's forum); ATP's Zerodha adapter sent none.
- `LIVE_UPSTOX_OPTION_STOP_LIMIT`: Upstox option stops as SL with the limit `STOP_LIMIT_BAND_PCT` past the trigger
  (the exchanges discontinued SL-M on index options); a triggered-but-unfilled stop is closed by the software stop.
- `LIVE_EXIT_IF_NO_STOP`: the broker clearly rejected the stop at entry or on re-arm -> immediate market exit + one
  CRITICAL alert; never after a timeout / 5xx (the stop may stand), while broker-uncertain or with the market shut;
  at most 3 tries. An exit skips cancelling an already rejected / cancelled / missing stop (found: today that cancel
  fails and blocks the exit), and nets off a stop-limit's partial fill.
- `STOP_LIMIT_BAND_PCT`: empty = today's 1%; set = rounded outward, at least one tick past the trigger.
- Self-review fixes before merge: clear-rejection rule, uncertain / market-shut / retry guards, partial-fill netting,
  outward band rounding, dead-status set (EXPIRED, LAPSED), modify keeps a standing SL-M, one alert at entry.
- Second review round: a caller's "stop is dead" is trusted only when a fresh book read agrees (a stop still working
  is cancelled first); CANCEL PENDING is not dead; the stop's fill is polled until terminal before netting, and an
  unreadable fill or an exit that errors without a clear answer flags the tenant broker-uncertain (no more tries);
  the immediate exit can never break the entry alert or the guard pass (try/except).
- Tests: `tests/test_glive_order_flags.py` (mock brokers: payloads, band rounding, blocked vs. completed exit, closing
  an unprotected position, the skip cases, retry limit, partial-fill netting, entry-path rejection flag, the four
  strict wing-fill outcomes, defaults off).
### 2026-10-08 - NIFTY expiries from NSE data too (no weekday rule); refresh PR opens itself
- `expiry_data.DATA_DRIVEN` = NIFTY and BANKNIFTY. NIFTY's Thursday/Tuesday rule is gone from
  `expiry_calendar`; backtests read the dates NSE printed in its F&O bhavcopies (same file, same causal rules).
- Verified from the data: first weekly 2019-02-14; Thursday weeklies up to 2025-08-28 (holiday moves to the
  Wednesday before, e.g. 2018-03-28 monthly, 2021-11-03 Diwali week); Tuesday from 2025-09-02; holiday moves after
  that (2026-10-19 Monday). Tests: the backtest calendar serves exactly the file's NIFTY expiries for every month
  since 2016 (causal first_seen), and the dates above are pinned against the exchange's history.
- Builder fix found on NIFTY: a re-dated or long-dated contract (quarterly / half-yearly listings) merges into the
  contract whose future expired that day, not the nearest weekly (Sep 2025, Dec 2025, Mar / Jun 2026 were wrong).
- Review fixes: NIFTY is STRICT in the builder too (an unexplained NIFTY expiry refuses the build); only a monthly or a
  far-listed contract that the exchange re-dated merges into the day of a future, a re-dated weekly goes to the nearest
  day; a far contract missing from the newest file (old Thursday long-dated NIFTY dates re-dated to Tuesday) is no
  longer listed (`meta.delisted`). Workflow: no token in the checkout, PR opened before CI is started.
- Options engine version 6 (`6-options`), shown on every options backtest report and run row.
- `nse-expiries.yml` now starts CI on `data/nse-expiries` and opens the refresh pull request itself (needs the
  repository setting that lets Actions create pull requests); the operator only reviews and merges.

### 2026-10-08 - BANKNIFTY expiries from NSE data (no weekday rule)
- **Source**: NSE's own F&O bhavcopies - `EXPIRY_DT` (legacy file, to 5 Jul 2024) and `XpryDt` (UDiFF, from 8 Jul 2024).
  `backend/app/instruments/nse_expiries.py` (stdlib only) reads one file per week from 1 Jan 2016, confirms every
  expiry with the file of its own day, finds a contract the exchange moved (late holiday, change of weekday) in the
  files six days either side, and calls an expiry "monthly" when a future expired that day. Result:
  `backend/app/instruments/data/nse_index_expiries.csv` (+ `.meta.json`: coverage, drops, moves) for NIFTY,
  BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50. NSE's archive is not reachable from the build sandbox, so the
  GitHub workflow "NSE expiry data" builds it (monthly schedule + manual run; result on `data/nse-expiries` for a PR).
- **Use**: `app.instruments.expiry_data` - BANKNIFTY's backtest calendar (`ExpiryCalendar`, `ExpiryBook`) reads only
  this file; the weekday rule for BANKNIFTY is gone from `expiry_calendar.UNDERLYINGS` and the rule helpers refuse it.
  A contract counts from the day it was first seen (causal). After the file's last day a bar sees only what was
  listed by then (no invented dates, "no expiry" when none is left); before 2016 is an error. Default: every listed
  expiry (weeklies while they existed; `ENGINE_VERSION` 5 - old BANKNIFTY runs re-run with weeklies before Nov 2024);
  `weekly_expiry=false`: monthlies only; the MONTHLY rule always takes the monthly. Live trading is unchanged (it
  uses the broker instrument master).
- **Self-review fixes**: the builder also reads the last file on or before `end`; a 403 is retried; the build refuses
  a week with no file, an unexplained BANKNIFTY drop, or a result that knows less than the committed file; weekly
  schedule; the far leg is chosen as listed on the bar day; "NIFTY BANK" / "Bank Nifty" reach the data. Known limit:
  a contract re-dated by the exchange shows its final date from its first listing (DTE off by a few days before the
  announcement).
- **What the data shows for BANKNIFTY** (checked by `tests/test_nse_expiry_data.py`): weeklies from 2 Jun 2016 to
  13 Nov 2024 - Thursdays until Aug 2023, Wednesdays from 6 Sep 2023 (2024: Wednesdays, Tuesday when Wednesday was a
  holiday); monthlies only after Nov 2024. Monthly day: last Thursday to Feb 2024, last Wednesday Mar-Dec 2024 (24 Dec
  2024 for the Christmas holiday), last Thursday Jan-Aug 2025, last Tuesday from 30 Sep 2025. Holiday moves found in
  the data, e.g. 29 Mar 2023 (Ram Navami), 28 Jun 2023 (Bakri Id, announced late), 30 Mar 2026, 23 Nov 2026.
- Tests: every BANKNIFTY expiry in the coverage is the calendar's next expiry after the previous one, every month's
  monthly matches, the 2024 changes, no rule left for BANKNIFTY, out-of-coverage errors; the builder on hand-made
  legacy / UDiFF files (moved and re-dated contracts, far monthlies). NIFTY still uses the dated rule (Trade port);
  it can move to the same data in a follow-up.

### 2026-10-08 - P1.2: design system (tokens, themes, primitives, Storybook) -> G-DESIGN
- **Tokens** (`src/styles/tokens.css`): surface / surface-1..3 / fg / fg-muted / border / brand / up / down / warn / info,
  as RGB CSS variables. Dark (default) and light themes; a colour-blind option makes profit blue and loss orange in
  both. Colour means something or is not there: up / down for P&L and direction, warn for attention, brand for actions.
- **Every Tailwind colour is a variable** (`tailwind.config.js`; `src/styles/palette.css` generated by
  `scripts/gen-palette.mjs`): the old names (`bg`, `panel`, `accent`, `danger`, `muted`...) alias the tokens, and text /
  border shades of the hues the pages use mirror in the light theme, so every page follows the theme before it is
  migrated (P1.3). Charts read the tokens and repaint on a theme change.
- **Appearance**: Settings > Appearance (dark / light / follow the device, colour-blind P&L), a sun/moon switch in the
  top bar; stored per browser (`atp_appearance`).
- **Primitives** (`src/components/primitives`): Button, Input, Select (Radix), Dialog / Sheet (Radix), Tabs (Radix),
  Table (dense, tabular numbers), Skeleton, EmptyState, Badge, Signed (P&L with sign and colour), PageHeader; Toasts
  restyled on tokens. Typography: Inter with tabular figures for numbers and tables.
- **Storybook** (`npm run storybook`; CI builds it): tokens, every primitive, both themes and the colour-blind option
  from the toolbar, a11y addon.
- **Applied to the five G-DESIGN pages**: Dashboard (gradient hero, glows and rainbow tiles removed; PageHeader with
  status badges; KPI and engine tiles neutral except P&L / health), AI Copilot, Positions, Backtesting, Settings
  (PageHeader; rainbow headings removed). The other pages keep their layout and follow the theme; they move to the
  primitives in P1.3 after approval.
- **Review fixes**: chart overlays (entry / stop / target lines, S/R zones, trade markers, Supertrend, ±DI, volume)
  name a meaning (`up` / `down` / `fg`, `theme.ts: resolveChartColor`) and redraw on a theme or colour-blind change;
  the colour-blind profit blue (Okabe-Ito sky blue / blue) is kept apart from the brand blue and `info` turns neutral
  there; `public/theme-init.js` applies the saved theme before the first paint (no dark flash; a same-origin script
  because the CSP forbids inline ones); light-theme contrast fixes (chat bubbles, briefing chips, entry line); theme
  buttons use `aria-pressed` in a labelled group; Select links its hint / error; toast close icon stays neutral;
  Marketplace and the chart window get a page heading; other open tabs follow a theme change. Tests: `theme.test.ts`.

### 2026-10-08 - P1.1: router, lazy pages, error handling, bundle budget
- **URLs**: every page has its own path (`react-router` 7; `/` = dashboard, `/<page>` otherwise, Copilot tabs at
  `/ai-copilot/<tab>`, the last tab remembered for a bare `/ai-copilot`); deep links, reload and back/forward work; the
  sidebar uses real links (`aria-current`) and preloads a page's chunk on hover; unknown paths show a not-found card;
  the old `?verify=`, `?broker=...&connected=1` and `?chart=` links keep working.
- **Lazy loading**: each page is its own chunk (`routes.ts`); the chart engine (`lightweight-charts`) is a separate
  `charts` chunk. The dashboard's market-pulse charts load it lazily after the page renders, and the chart-window
  link helpers moved to `components/chartHelpers.ts` so the Copilot no longer pulls the chart engine. Initial JS:
  76.7 KB gzip; `npm run check:bundle` fails CI above 300 KB.
- **Errors**: `api/errors.ts` normalises every failure into `ApiError` (status, message, request id; network errors
  as status 0 with one message); an error boundary per page (one page failing never blanks the app) and toasts for
  unhandled API errors. nginx: hashed `/assets/*` cached for a year, `index.html` and 404s never cached.
- Tests: Vitest `api/errors.test.ts`, `routes.test.ts` (+ the P0.10 sample-data tests); a Playwright smoke run of
  deep links, back/forward, reload, legacy links, not-found and chunk loading passed (18/18).

### 2026-10-08 - Trade port: price action, NSE contracts, India costs, order safety, validation
Source: `https://github.com/abhishekwasu-hue/Trade` (read-only clone, nothing changed or pushed there) at
**Trade@0df3e09d0aa5942e3352327d5b998c5f6939ac61**. Logic only - no bots, Streamlit pages, Supabase/Upstox-specific
code, golden personal trades, SR V3, Elliott setups/counts/exits or vision. Every ported file starts with
"Ported from Trade@<sha>, <path>"; thresholds come from settings in median-range / ATR multiples.

| ATP file | Trade source |
|---|---|
| `backend/app/price_action/pa_settings.py` | `elliott/settings.py` (candle / break / swing keys), `price_action/candles.py` settings |
| `backend/app/price_action/reversal.py` | `elliott/reversal.py` (E2+C1 composite) + `price_action/candles.py` (#241 0-100 score), one `evaluate_reversal` API, mode `composite` (default) or `score100` |
| `backend/app/price_action/level_strength.py` | `price_action/level_strength.py` (sweep, real break, failed breakout, strength features) |
| `backend/app/price_action/breaks.py` | `elliott/breaks.py` (real vs false break) |
| `backend/app/price_action/causal_swings.py` | `elliott/swings.py` (causal multi-degree swings, confirmed pivots, auto timeframe) |
| `backend/app/price_action/gap_context.py` | placeholder only (TODO: port Trade's unified gap context in its own PR once merged there) |
| `backend/app/instruments/expiry_calendar.py` | `elliott/contracts.py` (expiry calendar, expiry choice, DTE, dated NIFTY lot) |
| `backend/app/execution/india_costs.py` | `elliott/costs.py` (dated STT / exchange / SEBI / stamp / GST), extended to futures and equity |
| `backend/app/execution/order_safety.py` | `order_safety.py` (market protection, full-failure test, exit re-send plan) |
| `backend/app/backtest/validation.py` | `research_stats.py` (DSR, PBO/CSCV, shuffle p) + `research/elliott_candle_merge_report.py` (day-block bootstrap, random-entry baseline, Reality Check) |
| `backend/app/backtest/data_policy.py` | `elliott/data_policy.py` (sealed holdout; boundary is a setting in ATP) |

- **Costs**: `PaperBroker` now prices every leg with the rates of its own day (option STT 0.15% on the sale from
  1 Apr 2026, older rates before; exercise STT 0.125% -> 0.15%; futures 0.05% from 1 Apr 2026). The old equity profile
  charged STT on both legs of an intraday trade ("STT on the wrong side") - now STT sits on the executed sell order only
  (both sides only for delivery / SWING holds). The option backtester and the position monitor pass the trade dates.
- **Contracts**: the option backtester's calendar follows the weekday in force on each date (NIFTY Thursday until
  31 Aug 2025, Tuesday since; weeklies only after the first weekly listing); `lot_size_on("NIFTY", day)` uses Trade's dated
  table (50 from Jul 2021, 25 from Apr 2024, 75 from Nov 2024, 65 from the Jan 2026 series) and `LOT_SIZES["NIFTY"]` is
  65. Other underlyings keep ATP's table.
- **Order safety audit (G-LIVE, nothing enabled)**: router - the protective SL-M is sized to the filled quantity on a
  partial fill (test added); when the fill cannot be confirmed (book unreadable / cancel unconfirmed) the stop is placed
  for the requested quantity - reported, unchanged. Multi-leg - wings go before shorts and a short waits for a confirmed
  wing fill, but a *partly* filled wing still let the short go at full size: fixed behind `LIVE_STRICT_WING_FILL`
  (default off). Kill switch - shorts are bought back before longs are sold; pending orders (including broker stops) are
  cancelled first, so a close that then fails leaves that position without a broker stop - reported, unchanged. Upstox
  `market_protection` on MARKET / SL-M orders behind `ORDER_MARKET_PROTECTION_PCT` (unset = not sent, today's behaviour).
- **Validation**: the optimizer drops bars from the sealed holdout (`BACKTEST_HOLDOUT_START` or a per-run
  `holdout_start`) before the in-sample / out-of-sample split and reports `overfitting.pbo` (CSCV, 8 blocks of in-sample
  days) and the winner's DSR.
- **API**: `POST /api/price-action/reversal`, `POST /api/price-action/reversal-markers` (chart annotations; settings
  validated, unknown keys -> 400). DSL v2 blocks (P4) will call the same functions.
- **Concept library**: candlestick patterns (psychology and evidence), the reversal candle score, false breakout / sweep /
  real break, opening gaps (types, fill statistics, limits), overfitting checks - written in English, with sources and
  limits, from the two research reports; each also has a Marathi body for users whose AI answer language is Marathi.
- **Self-review fixes**: a per-run `holdout_start` can only seal more (the earlier of it and `BACKTEST_HOLDOUT_START`
  wins); in strict wing mode a seen partial fill is unwound even if the book then becomes unreadable; numeric price-action
  settings have ranges (bad values -> 400, not 500) and the reversal endpoints cap candles/levels, the marker scan reads a
  bounded tail (identical results, tested); the option backtester's lot follows the contract's expiry (an entry on
  31 Dec 2025 into the 6 Jan 2026 series uses 65), an explicit `weekly=True` is honoured for BANKNIFTY, and the report's
  calendar label comes from the expiries actually traded; `build_frame` keeps a bar only when its own minutes reach its
  end (as in Trade). Open (verify before relying on it): BANKNIFTY's monthly expiry weekday in 2024 (reported as Wednesday
  for part of that year) is not in the dated table.
- Tests: `test_trade_port_price_action.py` (49, incl. no-lookahead truncation tests for swings, breaks, reversal, markers
  and zone events), `test_trade_port_contracts_costs.py` (15), `test_trade_port_order_safety.py` (9),
  `test_trade_port_validation.py` (11); existing cost / lot tests moved to dated rates.

### 2026-10-08 - P0.10: last fixes from the P0.9 screenshot review
1. **Interview fully bilingual, rest English**: the intro (`intro_mr` from `/ai/interview/start`), questions, options,
   tips, every button (OK, Back, Start / Start over, Choose this, Not this, Show other templates, Apply risk settings,
   Deploy in PAPER, Open the chart, Ask the AI...), the "Why not?" reasons and the template headings show an English line
   with a small muted Marathi line under it. `scripts/check-devanagari.mjs` now allows Devanagari only as a value of an
   `INTERVIEW_MR` key, and only the interview screens may import that namespace.
2. **Today's market**: the briefing carries `market_data` (`fresh` / `stale` / `suspect` / `none`, age, figures shown).
   The banner shows "Market open now · market data read 29 h ago", a bold `STALE (29 H AGO)` / `PLACEHOLDER DATA` /
   `NO MARKET DATA YET` stamp, and hides prices and changes unless the read is current; every symbol with the same change
   is treated as a placeholder. Market memory hides the same figures. The data-source switch is hidden on this tab - it
   never reads sample candles.
3. **Risk settings**: experience no longer changes any risk value (interview `risk_plan`, the three options, exits,
   ranking). The briefing line is "Default risk settings (not set yet): ..." until the trader saves their own.
4. **Experience tip**: "Smaller risk per trade and PAPER first is a common way to start; you set your own risk."
5. **Instrument choice kept**: futures chosen on an index -> a futures plan with an information note (lot value =
   price x lot size, margin about 10-15%, option buying as the alternative); option selling stays a hedged spread with a
   note. Only facts still change a choice (a cash stock has no options; no overnight option writing).
6. **Sample candles**: shaped like NSE sessions (375 one-minute bars 09:15-15:29 IST, weekdays only, day volatility
   0.4-1.1%, U-shaped intraday volatility, small gaps; daily bars for swing reads). Root cause of "R2 +10.98%,
   PDH +7.08%": P0.9's generator ran minute bars round the clock, so one IST "day" held 1,440 bars. Resampling is
   aligned to the session. `backend/tests/sample_market.py` ports the generator for backend tests.
7. **Breakout figures behind the SAMPLE blur** (seed 5, 12 sample sessions, test output):
   `orb_breakout` 16 trades, 62.5% win, +0.793R expectancy (unseen: 6 trades, +0.851R); `pd_breakout` 8 trades, 50.0%
   win, +0.361R (unseen: 3 trades, +0.856R) - different trades, different results, verdict "sample". The Templates tab
   on the screenshot data (seed 7) showed `orb_breakout` 13 trades, 53.8%, +0.497R next to reversion and VWAP templates.
8. **Cost**: a `cheap` tier (`AI_ANTHROPIC_CHEAP_MODEL`, default `claude-haiku-5-5`; `AI_OPENAI_CHEAP_MODEL`,
   `gpt-4.1-nano`) runs news classification and scanner reads; the provider card lists it with its per-call estimate
   (Haiku 5.5 priced like Haiku 4.5 - set `AI_MODEL_PRICES_JSON` if the list price differs).
9. **Coach**: "shows patterns in your own trades (rules followed or broken); decisions are yours".
- Tests: `tests/test_phase_p0_10_final_fixes.py` (13), `frontend/src/utils/sampleData.test.ts` (Vitest, 4); CI runs
  `npm test`.

### 2026-10-08 - P0.9: English-only dashboard, compliance round 2, data fixes (after the P0.8 screenshot review)
- A English UI: the Marathi toggle is gone; the Copilot (all tabs), the Coach & Guide page, the acknowledgement, the
  data consent and Settings are English. The only Devanagari in the frontend is `src/i18n/interviewSecondary.ts`
  (muted lines under the interview's own prompts); interview questions and answer chips show the server's English
  text with a small muted Marathi line. The acknowledgement and the data consent have a closed "Read in Marathi".
  `frontend/scripts/check-devanagari.mjs` runs in CI (`npm run check:devanagari`). AI answers (Copilot chat, guide)
  follow the new per-user `users.ai_language` (migration `a1c3e5f7b9d2`, default `en`; `GET/PUT /api/ai/preferences`;
  Settings > AI provider). UI routes default to English; a Marathi strategist request is understood, answered in English.
- B persona "explains rules and data; decisions are yours". Interview: nothing pre-selected or highlighted - a
  template's details appear only after "Choose this"; no "Built from your own answers", no "Your trading plan ·
  BULLISH", no "Overall bias" line, no "the plan follows the market" warning. A single stock gets no bull/base/bear
  price levels and no score (thesis and strategist study) unless `thesis_stock_targets` is on; an index's score reads
  "model score N/100 - factor agreement, not a forecast". Sample data: the strategist's figures and the interview's
  backtest section are blurred under a "SAMPLE DATA - not real performance" stamp, the price is tagged SAMPLE PRICE;
  "Held up on unseen data" needs real broker candles and 30+ trades on the unseen sessions (`MIN_OOS_TRADES`),
  otherwise "Insufficient sample" (or "Sample data").
- C the two breakout templates' identical 0% / -1.02R came from the frontend sample series (a 40-bar sine of 1.5% plus
  a 25% drift, which also carried NIFTY to 30,713): every breakout reversed on schedule and lost exactly 1R + costs.
  The sample is now a seeded random walk near its start price. Real bug found on the way: a bar opening beyond the
  stop filled at the stop - it now fills at the open (`strategist.simulate`). Market memory older than three worker
  intervals is labelled STALE and dimmed. The AI provider card lists every task with its tier, model (operator env per
  tier, tenant model for the strong tier) and an estimated INR cost per typical call; no hard-coded default model.
- Review follow-ups: a returning user's saved profile no longer brings back a Marathi plan (language forced to English,
  `InterviewAnswers.language` defaults to `en`); a strategist candidate with no trades on the unseen sessions cannot be
  adopted; the market background (interview, briefing, Telegram) states data only - no bias trail, no "trade with it",
  no size advice, no "a beginner should watch"; the worker's daily theses are built in English (no duplicate rows for
  the scoreboard); the AI's thesis narrative is not asked for bull/bear cases on a stock without scenarios; the
  strategist parse summary follows the UI language; the provider card applies the tenant's model only to its own
  provider; a separate note when no judgement was possible (sample / insufficient); the mobile drawer closes on
  Escape and on the account link and leaves the tab order when closed.
- Tests: `tests/test_phase_p0_9_english_ui.py` (9); guide/copilot/strategist/market-memory/compliance tests follow the new rules.

### 2026-10-07 - P0.8-D: compliance (SEBI / DPDP) of the AI Copilot (D1-D5)
- D1 interview: three risk settings on **templates you choose** - no "Recommended/शिफारस", no "Three options for you",
  no match % ("matches you", "Closest to you", match history), no best option; the plan says "Template shown first ...
  you choose; this is a description, not a recommendation", explains how the order came about (regime fit + backtest),
  and names "Other template" instead of "Alternative". The trading capital is the figure the trader entered (no
  allocation by experience, no "keep as reserve", no "raise the allocation"); the R:R line gives the break-even
  arithmetic *before costs* and points to the backtest's net figures; "Consider waiting" removed; the LLM prompt no
  longer asks to "improve on the pick". `/interview/choose` ignores the match field (kept for older clients).
- D2 strategist: no "Best/सर्वोत्तम" badge (`best` is null), no "Market ठरवू दे" - `auto` now means both sides and the
  default is both; no priced "Today's triggers" on templates; the "wait or paper-trade" note is data-only. Thesis: for
  a single stock the confidence % and the next reference levels ("targets") are hidden unless the operator turns on
  `thesis_stock_targets` (indices keep them); the wording is a data read ("keeps the bullish read", "this read is
  invalid"), never "opens room towards"; the narrative prompt forbids buy/sell/hold/wait and says it is not advice.
  Copilot persona: "explains the platform's rules, templates and data", never recommends a strategy or allocation.
- D3 first-use acknowledgement (`ai/compliance_terms.py`, version 2026-10-07, en/mr text): every AI content route
  (drafts, interview, ask, brief, thesis, coach, copilot, strategist, interview deploy, adopt) answers 428
  `ai_acknowledgement_required` until the user accepts; `GET/POST /api/ai/acknowledgement`; the acceptance is an
  `ai_acknowledgements` row (version, text hash, language, IP, user agent) and an audit event. Approve/reject of
  proposals and provider settings never wait for it. The Copilot page shows the text and the checkbox first.
- D4 `llm_calls` (migration `f7a9b1c3d5e7`): every LLM input and output through `MeteredProvider` - system prompt,
  user text, answer or error, SHA-256 of each, prompt version, provider, model, tokens, cost, tenant, user, feature.
  `llm_calls` and `ai_acknowledgements` are in `retention.NEVER_DELETED`.
- D5 DPDP: the AI provider card says what is actually sent (questions, interview answers incl. capital and experience,
  trade facts, market data, headlines; never credentials or keys); saving an external provider needs the owner's
  versioned data-sharing consent (purpose, processing possibly outside India, opt-out = rule-based), recorded and
  audited. Marketplace: a strategy whose origin starts with `ai` cannot be listed or submitted while the new flag
  `marketplace_ai_listings` is off (default off, SEBI RA gating).
- Review follow-ups (independent review of the PR): the interview templates show no score at all (the market-fit %
  became a yes/no "this template's regime filter is open/closed today"); Telegram free text, `/brief` and `/thesis`
  and the two AI scanner routes wait for the acknowledgement too, and so do reading and approving an AI draft; the
  acknowledgement is matched on its text hash as well as its version (an edited text asks again); `provider_for`
  sends nothing to an outside model until the owner holds the current data-sharing consent (also for providers saved
  before the consent existed - they answer from the rules with a note until the owner ticks it once); the consent is
  asked once per version, not on every save; the strategist market study and the thesis API hide a single stock's
  confidence and next reference level like the thesis card; the AI scanner read describes each match in the scanner's
  order without a score unless `thesis_stock_targets` is on; the marketplace also refuses publishing and subscribing
  an AI-originated listing while `marketplace_ai_listings` is off; the shared `AiAcknowledgementGate` covers the Coach
  & Guide page and any 428 brings the screen back; the VIX line in the interview became data-only; "Apply risk
  settings" names the trading capital it will write.
- Tests `tests/test_phase_p0_8d_compliance.py` (12); the suite's `_register` accepts the terms (`ai_terms=False` to skip).

### 2026-10-07 - P0.8-C: the provider layer (C1-C5)
- C1 `providers.AnthropicProvider`: the request `max_tokens` is the caller's text budget plus a thinking headroom per
  effort (`THINKING_HEADROOM`: low 2000, medium 6000); the fast tier runs at `effort=low`. `stop_reason ==
  "max_tokens"` is retried once with twice the budget, then raised as `ProviderError("... cut off ...")` - a partial
  answer is never returned as a complete one. `complete_full` returns a `Completion` (text + token counts); the
  `ProviderError` carries the usage of failed attempts. Models without the effort control (Haiku 4.5 and older) get a
  plain request.
- C2 model names come from the environment per tier: `AI_ANTHROPIC_STRONG_MODEL` / `AI_ANTHROPIC_FAST_MODEL`,
  `AI_OPENAI_STRONG_MODEL` / `AI_OPENAI_FAST_MODEL` (`providers.default_models`; built-in names fill gaps only).
  `TASK_TIERS` maps each AI task: strategy generation, strategist proposals and scanner plans run the strong tier
  (the tenant's Settings model overrides it), narration, Copilot, knowledge, thesis, news classification and scanner
  reads run the fast tier. Every `provider_for` call site names its task.
- C3 `OpenAIProvider` sends `max_completion_tokens` (never `max_tokens`), no `temperature` on reasoning models
  (`is_reasoning_model`: o1/o3/o4/gpt-5), logs the 400 body (the key is only ever in the header) and surfaces its
  message; `finish_reason == "length"` is handled like C1.
- C4 one `AsyncAnthropic` client per API key is cached (`_ANTHROPIC_CLIENTS`; tests with their own http client get
  their own), one shared `httpx.AsyncClient` for OpenAI; the static system prompt goes as a `cache_control: ephemeral`
  block (prompt caching); the request timeout is `AI_PROVIDER_TIMEOUT_SECONDS + AI_TIMEOUT_PER_1K_SECONDS` per 1k
  tokens of budget (a 16k draft gets ~3-4 minutes instead of 45 s).
- C5 `ai/pricing.py` (list prices per model prefix, `AI_MODEL_PRICES_JSON` override, unknown model = the provider's
  most expensive row and `estimated`; `AI_USD_INR_RATE`) and `ai/metering.py`: `MeteredProvider` wraps the tenant's
  provider from `provider_for` and writes `ai_calls`, `ai_tokens_input` (fresh + cached), `ai_tokens_output` and
  `ai_cost_usd` usage rows with `{feature, provider, model, cache_read, estimated}` for every answer and every failed
  attempt that reported tokens; Prometheus `ai_tokens_total`, `ai_cost_usd_total`. `Plan.ai_monthly_budget_inr`
  (Pro 1,500, Business 10,000; `AI_BUDGET_INR_PRO` / `AI_BUDGET_INR_BUSINESS`; 0 = no cap): once this month's spend
  reaches it, `provider_for` returns the rule-based provider with the reason and `GET /api/ai/provider` shows
  `usage` (calls, tokens, USD/INR, by feature and model, budget, exhausted, note) and `models` (strong/fast); the
  Settings card shows the month's spend against the budget and which model does what.
- Tests `tests/test_phase_p0_8c_provider_layer.py` (7): headroom + retry + no partial answer, client reuse + timeout
  scaling + plain request for older models, env models per tier + tenant override, OpenAI parameters + 400 body +
  length retry, pricing table/override/estimate, metering through `provider_for` + usage endpoint + budget fallback +
  recorder failure, truncated Copilot answer metered and falling back to the rules (the test deferred from P0.8-B).
  Test fakes of `provider_for` accept the `task` keyword.
- Self-review fixes: usage rows join the caller's transaction in a savepoint (no commit inside an AI call; the
  thesis route commits the narration's usage); a model equal to the operator's default (or blank) is stored as ""
  so the environment keeps driving it, the card shows the default as the placeholder; OpenAI reasoning models get
  the C1 headroom and `reasoning_effort`; `_supports_effort` parses the model generation (4.6+); clients are cached
  by a key digest with eviction, API keys are out of dataclass reprs; o3-pro / gpt-5-pro / o1 / o3-mini priced;
  the rule-based fallback reason (budget spent) is shown in Copilot and guide notes.

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

### 2026-10-10 22:40 IST - part D (SEBI) design PR
- `docs/design/D_SEBI.md`: D1-D8 against what exists (algo tag, rate budget, market protection, daily-login pieces,
  readiness) and the gaps; rules-engine design shared with v1.2 (SEBI = one rule-set); PR order and test plan.
- `app/compliance/rules.py` + `rulesets/in_sebi.json` (11 rules, parameters as data, status enforced/partial/planned);
  `docs/COMPLIANCE_IN.md` (rule -> code -> test -> flag); `tests/test_compliance_rules.py` keeps file, doc, code and
  named tests in step. No behaviour change. OPEN_QUESTIONS D-1..D-3.
- In parallel: part B ADR drafts (ADR-0013/0016/0017) on claude/data-lake-adr.

### 2026-10-10 22:46 IST - part D2: OPS throttle (exits first), flag off
- `app/execution/ops_throttle.py`: per (tenant, broker account) and exchange, orders/s from the IN-SEBI rule-set; entry lane
  refused immediately (REJECTED, reason ops_throttle, never sent), exit lane (exits, stops, modify, cancel, untagged) waits
  and is never refused; no entry while an exit waits; broker 429 pauses the exchange (back-off from the rule-set), success
  resets. Wired through RateLimitedBroker when OPS_THROTTLE_ENABLED (default off). Metrics + 2 alert rules.
- Rule IN-SEBI.ops.throttle -> enforced; docs/COMPLIANCE_IN.md now generated (scripts/compliance_doc.py, CI checks it).
- Tests: tests/test_d2_ops_throttle.py (6); worker/rate-budget/tagging/G-LIVE suites 64 passed.

### 2026-10-10 22:48 IST - part D3: per-broker order-type policy for algo entries
- `app/compliance/order_policy.py`: rule-set default + per-broker overrides; MARKET allow / map_to_limit (PROTECTED_LIMIT
  price) / refuse; allowed validities. Router applies it to entries only (exits and stops untouched, ADR-0004); a refusal
  is a clean refusal before the broker. Default policy = today's behaviour. Rule IN-SEBI.order_type.policy enforced.
- Tests: tests/test_d3_order_policy.py (6); router/tagging/order-safety/G-LIVE suites green (63).

### 2026-10-10 22:53 IST - part D4 (backend): registered static egress IPs
- Tables egress_ips (PRIMARY/BACKUP per broker) + egress_ip_changes (append-only); migration d4e1f2a3b4c5 verified on a
  local Postgres: upgrade, alembic check (no drift), downgrade, upgrade. Service app/compliance/static_ip.py: public IPs only,
  max_changes_per_week from the rule-set (the first registration is not a change), shared-IP + server-IP warnings.
  API GET/PUT /api/compliance/static-ips (owner writes, audited). Worker: LIVE entries refused when
  STATIC_IP_REQUIRED_FOR_LIVE (off) and SERVER_EGRESS_IP is not registered for the broker; PAPER/exits untouched.
  Readiness item static_ip (LIVE). Rule IN-SEBI.static_ip.registered enforced. Tests: 6. Frontend card next.

### 2026-10-10 22:55 IST - part D4 (UI): Settings > Static IP card
- StaticIpCard on Settings (after broker accounts): this server's egress IP, a table per broker (primary / backup /
  server IP registered), warnings from the API, a form to register an IP (owner; server enforces). Helper staticIpRows
  with vitest (2). tsc, vitest 54, build, devanagari and bundle checks green.

### 2026-10-10 23:02 IST - part D5: daily broker login + pre-open reminder
- app/brokers/login_reminder.py: login method per broker (oauth / login_code / api_key / manual) from token_lifecycle's
  sets; on NSE trading days from `reminder_minutes_before_open` (rule-set data) before the open until the close, one
  WARNING per organisation whose ACTIVE deployment's broker session will not last to the close (once per IST day, also
  across restarts). Worker housekeeping hook + CycleReport.login_reminders. Rule IN-SEBI.login.daily -> enforced;
  COMPLIANCE_IN.md regenerated; OPERATIONS step 1 updated. Tests: 7 (crafted past-weekday clock). Next: D1 format/threshold.

### 2026-10-10 23:07 IST - part D1: algo id format, generic vs registered, audit rows
- app/compliance/algo_id.py: per-broker tag format from the rule-set (`brokers`: length + `alnum`/`alnum_dash`; brokers
  without an entry keep today's tag byte for byte); the registered id always wins, the broker's generic id
  (`generic_ids`, empty until brokers publish theirs - OPEN_QUESTIONS D-2 provisional) only while the D2 OPS throttle is on
  and capped at or below `ops_threshold`. ALGO_ID_REQUIRED_FOR_LIVE (off) refuses a LIVE entry without a usable id.
  Every LIVE entry and exit writes an `algo_order` audit row with the tag; the stop re-arm row carries it too.
  Router, multi-leg, position monitor and stop guard all go through the same resolution. Rule
  IN-SEBI.algo_id.registered_above_ops -> enforced. Tests: 5 new; tagging/worker/monitor/multileg suites green.

### 2026-10-10 23:11 IST - part D6: go-live checklist evidence
- Table compliance_evidence (append-only; migration e6a1b2c3d4f5 verified on local Postgres: upgrade, check, downgrade,
  upgrade). app/compliance/golive.py: the item list is rule-set data (vendor ISO 27001/SOC 2, CERT-In VAPT, incident
  register; strategy white-box/black-box filing; AI disclosure; DPO, breach runbook) with validity periods, plus two
  automatic checks (login history >= log_retention_days; AI trade ideas unpublished unless RA registration on record).
  SUPER_ADMIN API GET /api/compliance/golive, PUT /api/compliance/golive/evidence (audited). Platform readiness shows them
  as LIVE-scope items, `warn` at most (PAPER never blocked). Strategy class kept as evidence (OPEN_QUESTIONS D-4,
  provisional). Rule IN-SEBI.golive.checklist -> enforced. Tests: 8. Next: D7 waits for part B data -> part B build.

### 2026-10-10 23:16 IST - part B1: market data lake schema + as-of reads
- Tables md_candles (bar END, source, version), md_ticks, md_option_chain_snapshots, md_position_limits (MWPL / OI for
  D7), instrument_master_versions (valid_from), md_corporate_actions (the fundamentals `corporate_actions` table is a
  different thing and stays), data_quality_events; every row carries ingested_at. Migration b1c2d3e4f5a6: hypertables
  only when the timescaledb extension is installable (plain Postgres go-live unaffected, B-1 provisional); verified on
  local plain Postgres 16: upgrade, alembic check, downgrade, upgrade. The Timescale branch is not exercised here (no
  extension in this sandbox). app/market_lake/asof.py: candles / instrument terms / position limits as of T (late rows
  and corrections invisible before ingestion). Tests: 4 (crafted instants). Next: B2 ingest (candle builder from ticks,
  vendor seam with a mocked adapter, broker backfill).

### 2026-10-10 23:19 IST - part B2: lake ingest (candle builder, writer, broker backfill, vendor seam, tick writer)
- app/market_lake/ingest.py: CandleBuilder (bars labelled by END, published only after close; late ticks dropped and
  counted), write_candles (idempotent; a changed bar becomes the next version), backfill_from_broker (START labels ->
  END), HistoryVendor seam + MockVendor (no paid vendor until the owner picks one, B-2). app/market_lake/recorder.py:
  stream ticks -> 1-minute bars (source stream:<broker>), bounded backlog, never raises into the stream; worker drains
  it each cycle. Flag LAKE_TICK_WRITER_ENABLED (off). Tests: 9; stream + worker suites green. Next: B3 quality detectors.

### 2026-10-10 23:21 IST - part D7 (first slice): F&O ban period from lake MWPL / OI
- app/compliance/fo_limits.py: open interest at or above ban_threshold_pct (rule-set, 95) of MWPL on the signal's IST
  day -> new F&O entry refused (single contract and multi-leg; PAPER too, so paper never takes trades the market would
  refuse); exits untouched. No lake row -> no refusal, a note on the order instead. Flag FO_BAN_CHECK_ENABLED (off).
  Rule IN-SEBI.risk.futeq_mwpl stays partial: client-level FutEq limit, expiry-day margin multiplier and lot-size as-of
  are next. Tests: 3 (incl. a PAPER option entry refused, then filled on a non-banned day); contract/multileg suites green.

### 2026-10-10 23:23 IST - part B3: lake data-quality detectors
- app/market_lake/quality.py: gap (missing bar ends inside the session, one event per run), spike (close move above
  LAKE_SPIKE_PCT), invalid OHLC, late (ingested more than LAKE_LATE_SECONDS after close), duplicate (same bar twice in a
  batch), cross-source mismatch (above LAKE_MISMATCH_PCT); scan_day reads the latest version per source and records new
  events once. Thresholds are config. Tests: 3 (a clean session raises none; each crafted problem exactly one; re-scan
  writes nothing new). Next: B4 corporate-action adjustment; worker scan job with B6 metrics/alerts.

### 2026-10-10 23:27 IST - part B4: corporate-action adjusted view
- app/market_lake/adjust.py: adjusted candles as a read-time view - splits/bonuses multiply prices before the ex-date
  by ratio_old/ratio_new and divide volume by it (factors compound); dividends only with LAKE_ADJUST_DIVIDENDS, from
  the cum-dividend close, volume untouched; only actions known at as_of apply; derivatives never adjusted; incomplete
  ratios ignored. Stored data never changes. Tests: 5. Next: B5 history API + backtests reading the lake.

### 2026-10-10 23:33 IST - part B5 (API): GET /api/market-data/history
- app/market_lake/routes.py: authenticated point-in-time history (as_of hides later rows and corrections), adjusted
  for splits/bonuses known at as_of (equity only), bar_label "end", count of data-quality events in the window,
  LAKE_HISTORY_MAX_BARS cap (422 beyond). Test: 1 end-to-end (401, as_of, adjusted/raw, FUT, bad window, cap).
  Next: backtests read the lake (data_source=lake), B6 retention/compression/metrics.

### 2026-10-11 00:24 IST - Screener U1-a: securities and symbol history (NSE universe)
- Tables `securities` (ISIN key, series, listing/delisting, SME/ETF flags, last_seen_on, source/fetched_at/checksum) and
  `symbol_history` (half-open ranges), migration c7a1d2e3f4b5 (stacked on the part B chain, down-migration drops both).
- app/universe: `ReferenceSource` seam (NSE archive files from config URLs, polite; static/manual upload), tolerant
  parsers that refuse an unknown layout, idempotent `sync_equity_lists` (row checksums; a short equity list is refused
  with a quality event; a name missing from a file is not delisted; symbol history derived from the cumulative
  symbol-change file and never shrunk; a rename without a record is dated at the run with a RENAME_NOREC event),
  as-of reads `symbol_on`, `isin_for`, `listed_on` (delisted names kept for historical days).
- Worker: once a day after UNIVERSE_SYNC_HOUR_IST when UNIVERSE_SYNC_ENABLED (off; NSE terms to be checked first).
- Tests: tests/test_u1a_universe_securities.py (7) with fixture CSVs.

### 2026-10-11 00:30 IST - Screener U1-b: index catalogue, membership as-of, NSE classification
- Tables indices / index_membership / classifications (migration c7a1d2e3f4b6, down-migration drops them).
- Catalogue as data (app/universe/data/indices.json: 25 broad and sectoral indices, provider file URL, broker symbol,
  derivatives flag; no index sizes stored). Constituent files are diffed against open ranges: entries open a range
  from the run's day, exits close it; a first file marks start_observed (inclusion date unknown; reads before it
  return nothing and coverage_from says from when). Gates: empty file, short file (UNIVERSE_MIN_ROWS_RATIO),
  churn above UNIVERSE_MAX_CHURN_RATIO (0.2) - refused with a CONSTITUENTS event. Unknown ISINs reported.
- The files' Industry column fills the NSE sector level (U1-Q3, provisional); other levels stay empty.
- Reads: members_on, indices_of, classification_on, coverage_from. Worker runs it after the equity lists (flag off).
- Tests: tests/test_u1b_universe_indices.py (6).
### 2026-10-10 21:40 IST - Hostinger KVM 2 production host: deploy preparation (nothing run on a server)
- What: three one-line blocks (bootstrap as root, deploy as `atp`, rollback) + a status block, generated from
  `deploy/hostinger/*.sh` into `docs/DEPLOY_HOSTINGER_ONELINERS.txt`; `docker-compose.hostinger.yml` (memory limit per
  container, Postgres tuned for ~2 GB with WAL archiving kept, Redis 384 MB `volatile-lru`, Caddyfile by `CADDYFILE`,
  off-site target by `OFFSITE_REMOTE`); `deploy/Caddyfile.ip` (no domain yet: the IP with Caddy's internal certificate);
  `scripts/deploy.sh` takes `COMPOSE_OVERLAYS`; Marathi runbook `docs/DEPLOY_HOSTINGER_MR.md` (backups + restore test,
  SEBI static-IP checklist per broker, 8 GB budget, troubleshooting); OPERATIONS §1.2b budget table.
- Secrets: none in the scripts - `.env` is made on the server from `.env.example` with `CHANGE_ME` placeholders (mode
  600) and the deploy stops until the owner fills them. Deploy key read-only. The Trade repo droplet is not touched.
- Tests: `tests/test_hostinger_deploy.py` (10) - bash syntax, one-liners = scripts, merged compose publishes only
  Caddy 80/443 and keeps the limits within 7 GB, no secret literals, the IP Caddyfile routes like the domain one.
- Questions: OPEN_QUESTIONS H-1 (Redis policy), H-2 (local off-site until a provider is chosen).
- Next: owner runs the blocks once the server IP exists; then PR #82 / #83 CI, part C3/C4.
### 2026-10-10 22:30 IST - part C3 + C4 on PR #83 (backtest realism)
- C3: `backend/scripts/bench_backtest.py` (timings, growth, bars/s, indicator-cache counters); CI guard
  `tests/test_realism_benchmark.py` - per run the indicator cache's whole computations and unserved calls must not grow
  with the bars (deterministic; checked to fail on a simulated regression: 12/13 strategies). Table in docs/BENCHMARKS.md.
- C4: `app/backtest/repro.py` - engine/code/data/config/result hashes + seed on every result (`reproducibility`), kept
  in the run record; `tests/test_realism_repro.py` (same bytes in-process and across PYTHONHASHSEED; each hash moves only
  with what it names; API + record). No schema change (stored in the run's metrics JSON). Engine version unchanged
  (results are identical).
- Next: C2 (pluggable models) design note, then C5/C6 per the spec order; ROADMAP_STATUS board.
