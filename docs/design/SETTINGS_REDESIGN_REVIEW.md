# Settings redesign: the independent review (22 Oct)

The order ATP_SESSION_SETTINGS_REDESIGN.md, reviewed against main c02173eb (#352) by a separate agent that only read
the code. This file is the plan of record for building the order. Where it differs from the order, the finding wins
until the owner says otherwise (open questions SR-1..SR-10 in docs/OPEN_QUESTIONS.md, each with a default).

**Migrations: none.** User key providers fit `user_ai_keys.provider` String(20). The consent and the paid-plan
confirmation are `ai_acknowledgements.kind` values (String(24)). Trading defaults are a `user_prefs` namespace
(String(32), validated in code). The plain error is stored in `last_test_error` String(200). Caps stay in USD.
Setup status is computed from existing rows. New flags are registered in code.

## Blocking findings and their resolution

| # | Finding | Resolution |
|---|---|---|
| R1 | A5 "tenant operator default managed by ADMIN" is wrong. Every sign-up owns its own tenant (`auth/routes.py`), so `ai_provider_configs` is that owner's own key. An ADMIN-managed key for other tenants would be a cross-tenant write and a platform-paid key (CA-3 says no platform key). | Tier 2 stays the tenant's own key. `PUT /api/ai/provider` stays `require_owner`. Under `ui_v2` the AiProviderCard leaves the user view and shows on `/admin` only. No cross-tenant admin path. "Use this key for your account too" is offered only to the OWNER who entered that key, and the copy is re-sealed for the user and audited. |
| R2 | "One key" is still tied to the Vision switch: `byok_allowed` needs `prefs.enabled`, and every byok route is 404 while the `vision_byok` flag is off (default off). | "Connected" means a `user_ai_keys` row exists. `prefs.enabled` and the audit scopes keep controlling only the background Vision reviews. The resolver and the connect routes sit behind one new flag `ai_one_key` (OFF; needs `ui_v2`). |
| R3 | User-key calls would go through `MeteredProvider`, which charges the plan budget and logs `billed_to_user_key=False`. | New `app/ai/resolve.py`: `provider_for_user()` and a `UserKeyProvider` (cap check per call, `billed_to_user_key=True`, no plan `UsageRecord`, tier-aware model incl. Gemini tiers, accepted by `spent_by` / `supports_tools`, no tenant `mark_used`). |
| R4 | The MO2 free-tier rule does not exist yet, and chart analyze already sends the private method instructions to any user key, a free Gemini key included. A free and a paid Gemini key look the same. | Each provider is marked "may train on the free tier" (Gemini: yes). Private-method features (chart analyze) refuse such a key until the user ticks "My key is on a paid plan" (an `ai_acknowledgements` kind). The one-line warning shows at connect. Owner decision SR-2. |
| R5 | The backend and frontend key-format tables disagree (the backend takes a `sk-or-` key as OpenAI), and OpenRouter / Groq are not supported by the backend. | One prefix table on the backend (`app/ai/key_formats.py`) plus a shared JSON fixture that a vitest parity test reads. Session / admin prefixes are refused first. `sk-or-` and `gsk_` are refused as OpenAI. Part A takes Anthropic, OpenAI and Gemini; any other key gets a plain "not supported yet" line and a picker of the three. OpenRouter and Groq come with MO2 part A. |
| R6 | `ProviderError` has no kind, so "no credit" cannot be told from "rate limit" (Anthropic 400 credit balance, OpenAI 429 insufficient_quota, Gemini 429 RESOURCE_EXHAUSTED). | `ProviderError.kind` (auth / no_credit / rate / model / unreachable / no_vision / other), set at the raise sites from the error code in the body; one table maps a kind to the plain sentence. |
| R7 | Part C names fields that do not exist: stop source, target rule, carry-forward, "next weekly on expiry day", separate lots for spreads and singles, "Use this template". Risk limits are tenant rows already enforced, not pre-fill. | C1 uses only real fields: `instrument_kind`, `option_position`, `expiry_rule`, `strike_rule` + `strike_offset`, `premium_stop_pct`, `max_lots`, `option_strategy`, `spread_width`, `target_credit_pct` / `stop_credit_pct`, `holding`, `entry_mode`, ExitRules (`trailing_stop_pct`, `break_even_at_r`, `time_exit_minutes`, `time_exit_at`), and the chain ticket's lots / product / order type. Risk limits become a link to the Risk limits page. Template pre-fill waits until templates are wired. Never pre-filled: `mode`, broker routing, `order_style`, any LIVE field. |
| R8 | Part D duplicates the Dashboard's existing Needs attention block (`dashboard/model.ts` `attentionRows`) and the Get started card. Calling the Telegram connect GET would poll Telegram on every Dashboard load. | Extend `attentionRows` and `/api/me/setup` (no second strip): add the AI key row and the "Live prices are delayed" row. Read Telegram via `platform_bot.telegram_channel` only. Cache 30-60 s per (tenant, user). |

## Other findings (folded into the parts)

- A1: one machine-readable refusal `detail.code = "ai_not_connected"` at the user-started AI call sites; the frontend opens the Connect AI panel on it.
- A2: the key test gains one tiny constant image when the provider reads images (today it is one text call).
- A3: per-user consent `byok_consent` (versioned text) before the first save.
- A6: spend and caps are stored in USD; the bar shows rupees via `pricing.usd_inr_rate()`.
- A5 callers moved to the resolver: Copilot chat, the AI routes, the generator, screener NL, the scanner plan, option-chain Ask, journal and support. Background jobs (news classification, research jobs) stay on the tenant key. A user at their own cap gets a plain hard stop, never a silent fallback to the tenant key.
- B1: "Account & plan" reuses `/settings/account`; the Setup wizard link and the `#integrations` section were missing from the list; personal API keys get the `public_api` gate.
- B3: "Prepare for LIVE", the `#go-live` link and the Setup page's prepare-live block hide while `phase0_paper_only` is ON; the admin keeps the platform checklist on `/admin`.
- B4: hashes redirect on the client (the v2 redirect sees no hash); the expired-token deep link goes to `/broker`; the backend "Settings > ..." strings are swept; the four settings tour ids move onto the index rows.
- Safety: no server order path changes; Part C pre-fills forms in the frontend only, PAPER only.

## Build order

A (`claude/settings-a-connect-ai`) → B → D → C, one draft PR each, behind `ui_v2` (and `ai_one_key` for A).
