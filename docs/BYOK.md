# BYOK - Vision with your own AI key

Status: **BYOK-V1** (settings, key test, caps), **BYOK-V2** (Vision audit on proposals, report in-app and on
Telegram) and **BYOK-V3** (the same audit for scan alerts and a daily after-close review). Behind the operator flag
`vision_byok`, **off by default**; V3 also needs `vision_byok_v3`, **off by default**.

## Principle

Each user can turn on "Vision chart review" from Settings with their **own** AI provider API key:

- The user pays the provider directly. The platform stores the key encrypted, never bills for it, and never uses one
  user's key for another user (or for the organisation's own AI features).
- Providers: Anthropic (Claude), OpenAI and Google Gemini, through the existing provider seam (`app/ai/providers.py`:
  `AnthropicProvider`, `OpenAIProvider`, and the new `GeminiProvider`; `build_byok_provider` builds one for a user's key).
- **API keys only.** Signing in with a consumer chat subscription (Claude Pro/Max or similar) is not supported:
  Anthropic's terms do not allow subscription authentication in third-party apps. The Settings card says so and links to
  each provider's console:
  - Anthropic: https://console.anthropic.com/settings/keys
  - OpenAI: https://platform.openai.com/api-keys
  - Google Gemini: https://aistudio.google.com/app/apikey
- Vision only comments. It never places, blocks or changes an order (ADR-0006).
- Without a key everything else works; the Vision line reads "Vision off - add your API key in Settings".

## What is stored

| Table | Row | Contents |
|---|---|---|
| `user_ai_keys` | one per (user, provider) | `tenant_id`, `user_id`, `provider`, `encrypted_key`, `key_hint` (first 4 + last 4 characters), `model` ("" = default), `created_at`, `updated_at`, `last_used_at`, `last_tested_at`, `last_test_ok`, `last_test_error` (sanitised, max 200 characters) |
| `user_vision_prefs` | one per user | `enabled`, `provider` (where charts go; empty = first provider with a key), `daily_cap_usd`, `monthly_cap_usd`, audit scope (`audit_proposals`, `audit_scan_alerts`, `audit_daily_review`), channels (`channel_telegram`, `channel_in_app`) |
| `llm_calls.billed_to_user_key` | per call | `true` for a call that ran on the user's own key |

The key itself is never returned by any API, never logged and never written to the audit log. Responses carry the hint
only. The audit log records `byok_key_saved`, `byok_key_tested`, `byok_key_removed` and `byok_prefs_saved` with the
provider and the outcome, never key material (not even the hint).

When a member's personal data is erased (`erase_user`), their keys and Vision settings are deleted. A deactivated user's
key is never used (`byok_allowed` answers "vision off").

## How the key is encrypted

The key reuses the platform's envelope encryption (ADR-0008, `app/secrets_store/`):

1. The plaintext is a small JSON document that names its owner: `{"v": 1, "t": tenant_id, "u": user_id, "p": provider, "k": key}`.
2. It is encrypted with the organisation's data key under the new purpose `byok_ai_key` (`t1` Fernet or `t2` AES-256-GCM
   with the tenant and purpose as associated data, per `SECRETS_WRITE_FORMAT`). The data key is wrapped by the master key.
3. Opening (`byok.open_key`) refuses a row that is not the caller's, a token minted for another organisation, and a
   plaintext whose tenant, user or provider does not match the row. A ciphertext copied into another user's row (same or
   another organisation) or another provider's row therefore fails to open and is never sent anywhere.

`scripts/reencrypt_secrets.py` and the encryption status report cover `user_ai_keys` like every other secret column.

## Key validation

`PUT /api/ai/byok/key` refuses, before anything is stored:

- session cookies, OAuth / subscription tokens and JWTs (`sk-ant-sid...`, `sk-ant-oat...`, `sk-ant-ort...`, `ya29.`,
  `sess-...`, `eyJ...`, `Bearer ...`, anything with `=` or `;`, two or more dots, or words such as "session" or "cookie");
- organisation admin keys (`sk-ant-admin...`, `sk-admin-...`);
- the wrong provider's key (Anthropic keys start with `sk-ant-`, OpenAI keys with `sk-`, a Gemini key is not an `sk-` key);
- anything shorter than 20 or longer than 256 characters, or with characters other than letters, digits, `-` and `_`.

Error messages never contain the key, and 422 bodies never echo the input (`app/core/validation_errors.py`).

## Key test

`POST /api/ai/byok/test/{provider}` makes one tiny call ("Reply with OK", 16 output tokens plus the provider's thinking
headroom) through the provider seam with the caller's own key and model. The outcome is stored on the key row
(`last_test_ok`, `last_test_error`, `last_tested_at`, `last_used_at`). Error text is sanitised: the key, and anything that
looks like a key or token, is removed before it is stored or returned. The Gemini key travels in the `x-goog-api-key`
header, never in the URL.

## Models

Per provider, a cost-efficient vision-capable model is the default; the user may pick another from a short list. Both
are configuration (`app/core/config.py`), overridable per deployment:

| Provider | Default (env override) | Choices (env override, comma list) |
|---|---|---|
| Anthropic | `BYOK_ANTHROPIC_VISION_MODEL` | `BYOK_ANTHROPIC_VISION_MODELS` |
| OpenAI | `BYOK_OPENAI_VISION_MODEL` | `BYOK_OPENAI_VISION_MODELS` |
| Gemini | `BYOK_GEMINI_VISION_MODEL` | `BYOK_GEMINI_VISION_MODELS` |

The UI shows the configured model id, not a marketing name. Prices for the cost estimate come from `app/ai/pricing.py`
(Gemini rows added; an unknown model is priced at its provider's most expensive row, so spend is never under-reported).

## Spend and caps

- Every call on a user's key is written to `llm_calls` with `billed_to_user_key = true`. It is **not** written to the
  plan's usage rows, so it never counts against the organisation's AI budget and is never billed by the platform.
- `byok_spend(session, user_id, provider=None, since=None)` sums the user's own-key spend (USD).
- Caps are per user, in USD, set by the user (defaults `BYOK_DEFAULT_DAILY_CAP_USD` = 1 and
  `BYOK_DEFAULT_MONTHLY_CAP_USD` = 10; at most `BYOK_MAX_CAP_USD` = 500; the daily cap cannot exceed the monthly cap).
- `byok_allowed(session, user_id, provider=None, estimate_usd=0)` is the gate V2/V3 call before every audit. It answers
  `(True, "ok")` or `(False, reason)` with reason `"vision off"` (flag off, user switch off, inactive account),
  `"no key"`, `"daily cap reached"` or `"monthly cap reached"`. It is a hard stop: at the cap (or when the next call's
  estimate would cross it) nothing runs until the day resets at 00:00 UTC or the month on the 1st (UTC). The key test
  obeys the same caps (409 with a notice).
- The month-to-date figures on the card are the platform's own estimate; the provider's bill is the authority.

## Privacy (AI-usage disclosure)

When Vision reviews a chart (V2/V3), the chart image and the minimal context needed (symbol, timeframe, the proposal
being reviewed) are sent **only** to the provider the user chose, under the user's own key. Nothing is sent with another
user's key or the organisation's key. The V1 key test sends a fixed one-line prompt and nothing else. This text is shown
on the Settings card and belongs in the AI-usage disclosure.

## API (`/api/ai/byok`, logged-in user, own rows only)

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/status` | - | providers (hint, model, last used, last test, month-to-date), caps, spend, prefs, Vision line |
| PUT | `/key` | `{provider, api_key, model?}` | validates the shape; stores encrypted; returns the status |
| DELETE | `/key/{provider}` | - | 404 when the caller has no key for it |
| POST | `/test/{provider}` | - | one tiny call; 404 without a key, 409 at a cap |
| PUT | `/prefs` | `{enabled?, provider?, daily_cap_usd?, monthly_cap_usd?, audit?, channels?, models?}` | `provider: "auto"` clears the choice |

No route takes a user id; request bodies refuse unknown fields. With `vision_byok` off every route answers 404
(header `X-Feature-Disabled: vision_byok`) and the Settings card says the feature is not enabled.

## BYOK-V2 - Vision audit on proposals

### Who gets an audit, with whose key

One rule, so a key is never used for somebody else's content: **a proposal is audited only for the user who created
its deployment** (`strategy_deployments.created_by`, same organisation, active), **only with that user's own key**, and
the report is **shown only to that user**. Proposals without a deployment, or on a deployment whose creator is gone, are
not audited. Other members of the organisation see the proposal exactly as before, without anybody's Vision report.

### Flow

1. **Queue (no network).** `monitor.raise_proposals` writes the proposal and its normal `AI_PROPOSAL` notification
   exactly as before, then calls `vision_audit.enqueue_for_proposal`. With the flag on, and the creator's Vision
   switched on with proposals in the audit scope and at least one channel (in-app or Telegram), one `vision_reports`
   row is written as `pending` - inside a savepoint that never raises, so the proposal cannot fail or slow down because
   of Vision. With the flag off nothing is written and nothing is called.
2. **Audit (worker, background).** The trading worker checks for pending rows after the alert drain and, when there
   are some, starts `vision_audit.drain` as its own background task with its own database session - never inside the
   cycle, never in the approve / reject path. A row is claimed with a conditional update (`pending` -> `running`), so
   two replicas never audit it twice; a row whose worker died is closed as failed ("interrupted") after 15 minutes. A
   proposal that was already decided, or is older than 2 hours, is skipped without a call.
3. **Chart.** `app/ai/chart_png.py` renders a deterministic PNG (800 x 450, standard library only - no new dependency,
   no network): the last 120 bars of the deployment's symbol and timeframe from the platform's own market-data lake
   (`md_candles`, `<EXCHANGE>:<SYMBOL>`; one-minute bars when the deployment's timeframe has none), with the open
   position's ENTRY / SL / TGT lines and the window's HIGH / LOW. No bars: skipped ("no chart data"), no call.
4. **Gate.** `byok_allowed(user, provider, estimate)` before every call (flag, the user's switch, key, daily and
   monthly cap including the estimate of this call).
5. **Call.** One request to the provider the user chose, with the chart and a minimal context: symbol, exchange,
   timeframe, the proposed action and rule, the open position's direction, which lines are drawn. No account data, no
   prices in the text, no reason text. The key is opened with `byok.open_key` for the row's own user only. The call is
   metered in `llm_calls` (`feature = vision_audit`, `billed_to_user_key = true`) and the key's `last_used_at` is set.
6. **Report.** The answer is parsed for the four judgements only - JSON first, then "Heading: ✅ note" lines; every
   other field the model sends (a `status`, a `decision`, ...) is ignored. Each note is cleaned: digits removed, only
   letters and simple punctuation kept, at most 90 characters, and a note that tries to instruct (approve, reject,
   snooze, place, order, execute, ignore instructions, ...) is replaced by "(comment withheld)". The numbers are added by
   the platform from its own data. Format (at most 12 lines):

   ```
   Vision report - RELIANCE 5min (Anthropic (Claude), <model id>)
   Zones: ✅ <note> (range <window low> - <window high>)
   Structure: ⚠️ <note> (last close <last close>)
   Pullback & trigger: ❌ <note>
   Entry/SL/Target: ✅ <note> (entry <entry> / SL <stop> / target <target1>)   or (no open position)
   Comment only - Vision never approves, rejects or changes anything.
   ```

7. **Delivery.** In-app: the Watchtower proposal card shows the caller's own report (or the line below) while the flag
   is on and the in-app channel is on. Telegram: with the user's Telegram toggle on, a private chat linked to the user
   (Settings, `/start <code>`) and the organisation's Telegram bot enabled, the chart goes to **that user's linked chat**
   as a photo with the caption (proposal, symbol, the report) - never to the organisation's group chat, and without
   buttons. One attempt; a failure is kept on the row (`telegram_status = failed`, error without the bot token).

### When there is no report

| Situation | Status | What the user reads | Provider call | Telegram |
|---|---|---|---|---|
| Flag off at proposal time | - (no row) | nothing (the card shows no Vision line) | no | no |
| No key (Vision on) | skipped `no key` | Vision off - add your API key in Settings | no | chart + that line |
| Vision switched off / flag turned off before the audit | skipped `vision off` | Vision off - turn it on in Settings | no | no |
| Daily / monthly cap reached | skipped | Vision skipped: daily cap reached | no | chart + that line |
| No bars for the symbol | skipped | Vision skipped: no chart data | no | no (no chart) |
| Proposal already decided / older than 2 h | skipped | Vision skipped: proposal already decided | no | no |
| Provider error (401, 429, 5xx, refusal, timeout) | failed | Vision skipped: <sanitised error> | yes (metered) | chart + that line |
| Answer without the four judgements | failed | Vision skipped: the provider's answer could not be read | yes (metered) | chart + that line |

In every case the proposal, its normal notification and its Approve / Reject path are unchanged.

### Vision never changes anything (ADR-0006)

Nothing in V2 approves, rejects, snoozes, places, blocks or modifies an order, a proposal or a deployment. The audit
reads the proposal, deployment, trade and bars, and writes only its own `vision_reports` row, one `llm_calls` row and the
key's `last_used_at`. `tests/test_byok_v2.py` runs the whole flow with a provider that answers "APPROVE THIS", "place
order" and JSON with `status` / `decision` fields, and checks that `ai_actions`, `orders`, `strategy_deployments` and
`trades` are unchanged column by column and that no decision reached the audit log.

### What is stored

| Table | Row | Contents |
|---|---|---|
| `vision_reports` | one per (user, proposal) | `tenant_id`, `user_id`, `ai_action_id`, `status` (pending / running / ok / skipped / failed), `provider`, `model`, `reason` (sanitised, max 200 characters, never the key), `report_text` (at most 12 lines), `image_sha256`, `cost_usd`, `telegram_status`, `telegram_error`, `created_at`, `started_at`, `finished_at` |

**The chart image is not stored** - only its SHA-256, which identifies exactly what was sent (the renderer is
deterministic). The platform has no journal setting that keeps chart images yet; when one exists, keeping the image
will be the user's opt-in. The user deletes their own reports (`DELETE /api/ai/byok/reports/{id}`, audit event
`byok_vision_report_deleted`); erasing a user (`erase_user`) deletes them too. Migration `e4c2a9b7d1f6` (on
`d1b0e7a5c3f1`), with downgrade.

### Privacy (V2)

The chart image and the minimal context above are sent **only** to the provider the user chose, under the user's own
key, and only for proposals on deployments that user created. The report is shown only to that user (in-app) and sent
only to that user's own linked Telegram chat. This belongs in the AI-usage disclosure next to the V1 text.

### API (V2)

| Method | Path | Notes |
|---|---|---|
| GET | `/reports?action_id=` | the caller's own reports (all, or for one proposal), `vision_line` (what the card says without a report: no key, switched off, not your deployment, pending) and `in_app` |
| DELETE | `/reports/{id}` | the caller's own report; 404 for anybody else's (same answer as a missing one) |

Both answer 404 while `vision_byok` is off, like every BYOK route.

### Provider seam

`AnthropicProvider`, `OpenAIProvider` and `GeminiProvider` take an optional `image_png` in `complete_full` (Messages API
image block; Chat Completions `image_url` data URL at low detail; Gemini `inlineData`). Without it every request is
byte-for-byte what it was. `build_byok_provider` returns a `VisionProvider`.

## BYOK-V3 - scan alerts and the daily review

Flag **`vision_byok_v3`** (off by default, in `DEFAULT_OFF_FLAGS`) on top of `vision_byok`: with either off nothing is
queued and nothing is called (proposal audits follow V2 as before). The user opts in per subject in the Settings card
("Every scan alert", "Daily review" - the V1 audit scope, greyed out while V3 is off for the organisation).

### Whose key, whose content

The one-key-one-user rule of V2 holds for both subjects:

- **Scan alert**: queued for the **rule's creator** (`alert_rules.created_by`, active, same organisation) - the user the
  SCREEN_ALERT notification is addressed to - and audited only with that user's key; the report is shown only to that
  user. A teammate's key is never used, even when the teammate has one.
- **Daily review**: one row per symbol of the user's **own** list - the deployments they created (not stopped), then
  their own instrument alert rules (active), newest first, at most `BYOK_DAILY_REVIEW_MAX_SYMBOLS` (3; at most 10).
  Never another member's symbols.

### Flow

1. **Scan alert, queue.** The alert engine's `_send` writes the notification and its deliveries exactly as before, then
   calls `vision_audit.enqueue_for_scan_alert` - a savepoint that never raises, no network. One `vision_reports` row:
   `subject_kind = scan_alert`, `subject_key = scan:<notification id>`, the notification and rule ids, the first
   matched symbol (A-Z), the rule's exchange and timeframe. At most `BYOK_SCAN_AUDITS_PER_HOUR` (6) audits per user per
   rolling hour: beyond it the row is written already **skipped** ("scan audit limit (6 per hour)"), with no call, so a
   noisy screen cannot burn the user's cap and the user sees why there is no report.
2. **Daily review, queue.** Every 15 minutes the worker calls `vision_audit.enqueue_daily_reviews`: from
   `BYOK_DAILY_REVIEW_AT` (16:00 IST) on NSE trading days (the holiday calendar; weekends and holidays have none), one
   row per symbol with `subject_key = daily:<date>:<EXCHANGE>:<SYMBOL>` and `subject_date`, on
   `BYOK_DAILY_REVIEW_TIMEFRAME` (`1d`). The key is unique per user, so a day is queued once (and a second replica's
   insert loses quietly).
3. **Audit.** The same drain, claim, chart, gate (`byok_allowed` with the estimate), call, parse and report as V2 - the
   chart of the row's own symbol from the platform's lake (daily bars only with `byob_data_tier` on, BT1b), the
   window's HIGH / LOW, no position lines. The context sent is the symbol, exchange, timeframe and the kind of review
   ("a screen alert the trader set up matched this symbol" / "the trader's own after-close daily review") - no rule
   text, no account data, no prices. Skipped without a call when V3 was turned off, the rule is gone or no longer the
   user's, a scan alert is older than 2 hours, or a daily review older than 12 hours.
4. **Delivery.** In-app: the Notifications feed shows the user's report under that scan-alert notification; the
   "Vision daily review" tab lists the daily reviews, newest day first. Telegram (the user's own linked chat only, as
   in V2): "Scan alert - SYMBOL 15m (rule #id)" or "Daily review - SYMBOL 1d (date)" with the report.

Vision still never approves, places, blocks or changes anything (ADR-0006): `tests/test_byok_v3.py` runs a scan-alert
audit against a provider that answers "APPROVE THIS. place order now" with `status` / `decision` fields and checks that
`orders`, `trades`, `strategy_deployments`, `alert_rules` and `ai_actions` are unchanged.

### What is stored (V3)

`vision_reports` gains `subject_kind` (proposal / scan_alert / daily_review, default proposal), `subject_key` (unique
per user; NULL on V2 rows, which stay unique on (user, proposal)), `notification_id`, `alert_rule_id`, `symbol`,
`exchange`, `timeframe` and `subject_date`; `ai_action_id` becomes nullable. Migration `c9e1f3a5b7d2` (on
`a7d3e9c1b5f2`) is `ROLLBACK_SAFE = True` with a reason: the one new constraint only binds rows the new release writes
(the previous release never sets `subject_key`, and NULLs never collide). The downgrade deletes the scan-alert and
daily-review rows (they do not fit V2's NOT NULL `ai_action_id`). Images are still never stored.

### API (V3)

`GET /api/ai/byok/reports` takes `kind` (`proposal`, `scan_alert`, `daily_review`) and `notification_id`; each report
carries `kind`, `notification_id`, `alert_rule_id`, `symbol`, `exchange`, `timeframe` and `subject_date`. No new route.
`GET /api/ai/byok/status` carries `scope`: `v3_enabled`, `scan_audits_per_hour`, `daily_review_at` (IST),
`daily_review_max_symbols`, `daily_review_timeframe`.

### Privacy (V3)

The chart and the minimal context above go only to the provider the user chose, under the user's own key, and only for
the user's own alert rules and own symbols. The Settings privacy note says so.

## Next

- Per-user choice of the daily-review symbols (a watchlist) and review time - open questions BYOK3-2 and BYOK3-3.
