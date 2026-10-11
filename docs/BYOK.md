# BYOK - Vision with your own AI key

Status: **BYOK-V1** (settings, key test, caps). Behind the operator flag `vision_byok`, **off by default**.
V2 and V3 add the audits themselves (see the end of this page).

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

## Next

- **BYOK-V2** - Vision audit on trade proposals: the chart image and minimal context go to the user's chosen provider
  (after `byok_allowed`), and the comment is reported by Telegram and/or in-app per the user's channels. Comment only.
- **BYOK-V3** - the same audit for every scan alert and a daily review, per the user's audit scope.
