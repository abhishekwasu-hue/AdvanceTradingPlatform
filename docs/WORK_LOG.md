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
| S10 | money columns are `Float`; currency assumed INR | P0.4 (Numeric(18,2) migration with drift check; off-hours) |
| S11 | unset `SECRETS_ENCRYPTION_KEY` -> fixed dev key (refused in production only); Fernet, no AAD; passphrase via plain SHA-256 | P0.4 (fail-closed in hardened envs already; AES-GCM with AAD = tenant_id + row id **dual-read**, re-encrypt on next write; KDF scrypt for passphrases) |
| S12 | `create_all` at every startup next to Alembic | **P0.1 (done)** |
| S13 | metering INSERT + SUM per API call; key prefix is 4 random bytes (no uniqueness guarantee); TradingView token stored plaintext on tenants | P0.3 |
| S14 | HS256 JWT with `email` claim, no iss/aud/kid | P0.3 (iss/aud/kid + rotation, drop email claim; **dual-verify** old tokens until they expire) |
| T1 | partial fills handled, unfilled remainder of a MARKET order not cancelled; unknown fill -> recorded as requested | P0.5 |
| T2 | protective stop is always SL-M; adapters whose broker refuses SL-M on options are not asked | P0.5 (capability matrix per adapter, SL-L with trigger band) |
| T3 | multi-leg: shorts sent before wing fills are confirmed; failed wing fill recorded 0.0 | P0.5 |
| T4 | emergency exit cancels orders in the DB only (`transition_order`), not at the broker; shorts/longs order not enforced | P0.5 |
| T5 | every order `MARKET`, no market-protection % / marketable-limit option | P0.5 (order_style per deployment, default broker-neutral protection) |
| T6 | daily loss on realised P&L only; "day" is UTC | P0.5 |
| T7 | `time_to_expiry_years` in whole days | P0.6 |
| B1-B5 | daily counters, STT on short options, optimizer winner on IS, gap fills, dated lot sizes | P0.6 |
| infra | deploy.sh rollback re-tags the image but does not restart (verified: tags only); no log rotation; CI without ruff/mypy/bandit/gitleaks; actions not SHA-pinned; `deploy-staging.yml` interpolates inputs into the remote shell; OpenAPI title/version placeholder | **P0.1** (OpenAPI, staging workflow), P0.7 (the rest) |

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
