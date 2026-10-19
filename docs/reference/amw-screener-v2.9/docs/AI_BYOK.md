# AI features — Bring Your Own Key (BYOK)

## Business rule
Every user pays their own AI API usage directly to the provider they choose (OpenAI and/or Anthropic).
AMW **does not** pay for, subsidise, reimburse, bundle or pool AI API usage, and there is **no company API key** in the
request path: the code never reads `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`, and a test fails the build if it ever does.
Any AMW subscription covers platform features only.

Notice shown in the product:
> AI API usage is billed separately by your selected AI provider. You must supply your own API key and maintain your
> provider account's billing and usage limits. AI API charges are not included in your AMW subscription. Using multiple
> providers may generate separate charges.

All AI features are optional. Screening, analysis, strategies, the Strategy Lab and backtesting work with no key.

## User setup
1. **OpenAI** — create an API account on the [OpenAI Platform](https://platform.openai.com/), set up API billing there,
   create an API key. A ChatGPT subscription (e.g. Plus) is separate and does not include API credits.
2. **Anthropic Claude** — create an account in the [Anthropic Console](https://console.anthropic.com/), set up API
   billing, create an API key. A Claude chat subscription (Pro/Max) is separate and does not include API credits.
3. In AMW open **AI Provider Setup & Usage → Provider keys**, paste the key (masked input), optionally choose a model,
   choose *encrypted storage* or *session only*, and click **Test & save**. AMW calls the provider's **model-list
   endpoint** (no token charges) and saves the key only if the provider accepts it and the chosen model is available.
4. Choose a provider-selection mode, fallback behaviour and safeguards, and enter your model prices if you want cost
   estimates and cost limits (AMW ships no price lists; copy prices from your provider's pricing page).

## Provider selection
| Mode | Behaviour |
|---|---|
| Default provider | OpenAI only / Claude only (one key) or your default provider when both are configured |
| Manual | You choose the provider for every request |
| Task-based routing | e.g. chart explanations → OpenAI, strategy drafting → Claude |
| Dual Review | Same evidence to both providers using **your two keys**; must be enabled in settings and confirmed every time, with a warning that both providers bill you |
| Fallback | `none` (stop), `ask` (stop and offer your other provider), `auto` (use your OTHER configured provider). Never an AMW key. |

## Secure key storage
* Keys are sent once to the backend over the API/UI, tested, then either **encrypted at rest** or held **in backend
  memory only** (session-only mode; forgotten on restart). They are never returned to the browser, put in URLs, local
  storage, logs or source code; the UI shows only a mask (`sk-…abcd`).
* Encryption: AES-256-GCM, fresh 96-bit nonce per credential, associated data `amw-ai|<user_id>|<provider>|<master key id>`
  — a ciphertext copied to another user or provider fails authentication.
* Master key: `AMW_SECRET_KEY` (32 bytes, urlsafe base64) from your secret manager in production. On a desktop without it,
  AMW creates `data/.amw_master.key` (mode 0600 inside the 0700 `data/` directory; git-ignored). This protects database
  copies and backups; it does not protect against someone who controls the host account. Changing the master key makes
  stored keys unusable (fail-closed: users re-enter them).
* Keys are decrypted only inside the AI gateway for the requesting user's own request, and are sent only to the matching
  provider (format checks prevent sending an Anthropic key to OpenAI and vice versa). Provider error messages are
  redacted; a root logging filter scrubs key patterns from every log record.
* Delete removes the stored ciphertext and any session copy; later requests fail with `missing_key`.

## Usage transparency
*AI Provider Setup & Usage → Usage dashboard* (and `GET /api/v1/ai/usage`) lists every attempt: provider, model, task
type, time, status (`ok`, `error`, `blocked` = stopped by AMW before calling, `test` = key test), attempt number,
fallback source, **provider-returned** input/output/cached token counts, and **AMW's estimated cost** from your prices,
with daily/monthly/30-day totals. The estimate is not an invoice; **your provider's billing dashboard is authoritative**.
AMW does not read provider invoices.

## Spending safeguards (AMW-side, not provider billing limits)
Checked before every provider call: max calls per task, max retries, max output tokens, max estimated cost per task
(requires a price for the model; otherwise the call is refused), daily/monthly request and estimated-cost thresholds,
confirmation for tasks that may exceed N calls, Dual Review confirmation, cancellation, agent recursion depth and
repair-attempt limits. Your provider account's own usage limits remain the authoritative control.

## AI strategy drafting (agent)
1 generation call + at most `max_repair_attempts` repair calls, each only after the generated code fails host
validation; capped by `max_calls_per_task`; requires authorization for the call count; stops on any threshold or
provider error. Output is offered as a **draft** only — never run on market data, published or scanned automatically.

## Errors
Missing key, invalid/revoked key, permission, insufficient credit/billing, rate limit, unknown model, bad request,
timeout, network error and provider outage are classified and explained without credentials. The user can retry
(within limits), use their other configured provider, or continue with deterministic analysis.

## Provider-specific notes
* OpenAI: Chat Completions API; `max_completion_tokens`; cached tokens from `prompt_tokens_details.cached_tokens`.
* Anthropic: Messages API (`anthropic-version: 2023-06-01`); cache read/write tokens from the usage block.
* Model names are whatever your key can access (from the provider's model list); AMW does not validate pricing.
* Live calls to OpenAI/Anthropic were **not** made during development (no keys); behaviour is verified against mocked
  provider responses that follow the documented API formats.
