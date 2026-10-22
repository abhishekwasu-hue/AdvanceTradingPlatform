From Abhi — SETTINGS REDESIGN + "connect AI in one paste" + trading defaults + setup health (English spec)

Owner approval: Abhi approved this proposal ("Ok"). That covers the behaviour change of unifying the AI keys (CLAUDE.md
§8). All user-facing work goes behind `ui_v2`.

Builds on:
- MO2 (ATP_HANDOVER_PACK.md §6: provider choice with free options, the key guides, page tours; KeyGuidesV2 exists);
- MO3 (the UX audit; USER / ADMIN only).
It changes nothing in the trading logic or the order paths.

STEP 1
- Record this order in docs/owner-orders: INBOX verbatim, a DECISIONS line, README row 15.
- Then an independent review of this spec against the real code by a separate agent (CLAUDE.md §6). Fix the findings,
  then build.

WHY (what Abhi saw)
- The Settings page is long and crowded. One page holds Appearance, Broker link, Billing, Telegram + alert channels,
  Webhook, API keys, key guides, the AI provider card, the Vision key card, Help and Advanced.
- AI needs TWO separate keys in two cards:
  - `AiProviderCard`: the tenant-level `ai_provider_configs`, for the Copilot;
  - `VisionByokCard`: the per-user `user_ai_keys`, for Vision.
- The AI provider card shows developer material: a long paragraph, a task / tier / model / token-price table, a provider
  dropdown, a model box, an "enabled" checkbox, a consent checkbox and a Save button.
- "Prepare for LIVE" sits in the header during the PAPER phase.

PRINCIPLE: a setting lives where it is used. Settings is only a short index with the status of each thing.

PART A — CONNECT AI IN ONE PASTE (one key for every AI feature)
A1 Where:
- a "Connect AI" panel on the AI Copilot page itself (an empty state when not connected; a small status chip when
  connected);
- the same panel opens in place the first time the user uses ANY AI feature (Analyze a chart, Vision review, idea →
  code / scan, Copilot);
- Settings → AI shows the same panel.
A2 Paste flow:
- An `onPaste` / input that trims the key.
- It **detects the provider from the key format**, for example: `sk-ant-` → Anthropic; `sk-or-` → OpenRouter;
  `gsk_` → Groq; `AIza` → Google Gemini; `sk-` / `sk-proj-` → OpenAI.
  - Keep the patterns in one table with tests.
  - If the format is unknown, show a small provider picker.
  - Refuse session cookies / OAuth tokens / JWTs, as byok.py already does.
- **Save, encrypted, then test immediately**: one cheap text call, plus one tiny image when the provider supports vision.
  Reuse the existing byok save / test.
- Then "✅ Connected · Claude · key …a9F2", with a "Disconnect" button.
- No Save button, no model box, no provider dropdown, no "enabled" checkbox in the user view.
A3 Consent:
- one plain line plus a checkbox, shown before the first save: "Your questions and chart images are sent to <provider>
  to answer you. Never your broker login or passwords."
- The MO2 free-tier privacy notice applies (free tiers that may train on data get a one-line warning).
- ATP's private method prompts are never sent to such a tier (existing rule).
A4 Errors in plain words. Examples:
- "This key did not work. Check that you copied all of it.";
- "This key has no credit left.";
- "Too many requests. Try again in a minute.";
- "This provider cannot read charts. Vision needs Gemini, Claude or OpenAI."
Never raw provider errors. Use the sanitised text that byok.py already produces.
A5 One key, all features (backend). For a user, the AI resolution order is:
1. the user's own key (`user_ai_keys`);
2. the tenant's operator default (`ai_provider_configs`, now managed only by ADMIN in the admin console);
3. the rule-based fallback.
The Copilot, chart analysis and idea → code read the same resolution. Vision keeps its vision-capable check. The model
per task stays automatic (the existing task tiers); users never choose models.
- No data deletion: both tables stay.
- Migration: none, unless the review finds one needed. If needed: additive only, run after 15:30 IST; say so.
- A one-time, non-destructive copy: if a tenant owner has a tenant key and no user key, offer "Use this key for your
  account too" (one tap). Never automatic.
- Tests:
  - the resolution order;
  - one key used by Copilot + Vision + chart analysis;
  - another user's key is never used (existing isolation);
  - the spend logging is unchanged (`billed_to_user_key`).
A6 Usage in plain words: one bar, "AI this month: ₹X of your ₹Y limit". A limit control (daily / monthly cap, existing
byok caps) sits under "Limits". The per-task / tier / model / token / price table moves to the ADMIN console (Users &
business → AI cost).
A7 "Get a free key" links to the existing KeyGuidesV2 in a drawer, with "Start with free Google Gemini" first (MO2).

PART B — SETTINGS = A SHORT INDEX WITH STATUS (like phone settings)
B1 The Settings page becomes a list of rows. Each row has an icon, a name, a live status chip and a chevron, and opens
its own small page (route `/settings/<section>`):
- Account & plan — plan, renewal, billing (BillingCard moves here); profile.
- Broker — "✅ Connected (Upstox) · token valid until …" / "⚠️ Renew today" → the existing Broker page.
- AI — "✅ Connected (Claude)" / "Not connected" → Part A panel.
- Alerts — "✅ Telegram connected" / "Not connected". The one-tap Telegram connect (TelegramConnectV2) first. The alert
  channel options below it. The "own bot" token / chat id under Advanced.
- My trading defaults — Part C.
- Appearance — text size, theme, colour-blind P&L, reduce motion (AppearanceCard). The theme is also on the top-bar moon
  icon (existing).
- Help & tips — show Get started again, page tips again / off (the existing Help card). Also reachable from a "?" in
  the header.
- Advanced (collapsed) — Webhook (TradingView), personal API keys (shown ONLY when `public_api` is ON; it is OFF today),
  own Telegram bot.
B2 Search box at the top of Settings. It matches row names and the settings inside them ("telegram", "ai key", "text
size"), and jumps there.
B3 Remove from the user view:
- "Prepare for LIVE" while `phase0_paper_only` is ON. The Go-live checklist moves to the ADMIN console (System →
  go-live), per MO3 §8a.
- Every developer / internal word outside Advanced (CLAUDE.md §2).
B4 Old links keep working: `/settings#alerts`, `#ai`, `#go-live`, `#advanced` redirect to the new section pages. Page
tours are updated (the stable element ids); the CI step-target check passes.
B5 Phone first: rows ≥ 44 px, the status chip wraps under the name on narrow screens; light / dark.

PART C — MY TRADING DEFAULTS (a pre-fill, never a hidden rule)
C1 A page with the user's own defaults. They PRE-FILL every new deployment / "Use this template" / trade-from-chain form,
and the user can change them per deployment.
- **Instrument choices:** instrument kind (option / future / underlying), option strategy (e.g. credit spread / naked),
  spread width, strike rule + offset, expiry rule (incl. "next weekly on expiry day").
- **Size:** default lots (spread / naked separately).
- **Exits:** the exit presets (stop source, target rule, trailing / break-even, time exit, carry-forward), from the
  existing ExitRules fields.
- **Entry mode:** AUTO / APPROVAL.
- **Account-level risk limits (if the existing RiskManager / account settings already have them):** max open
  positions, daily loss limit.
C2 Rules:
- Only existing StrategyDeploymentRecord / ExitRules / RiskManager fields. NO new trading rule, and nothing applied
  silently. The form shows "from your defaults" next to each pre-filled value.
- Empty defaults ⇒ today's behaviour, byte-identical (test).
C3 Inspiration (research Sensibull / Kite / Dhan / Tradetron presets; record the sources): order presets, default
product, default quantity. Keep only what maps to existing fields.

PART D — SETUP HEALTH (the "ready-made dish" for connections)
D1 A slim strip at the top of the Dashboard, shown only when something needs action:
- the broker token expired / expiring today → "Renew";
- the AI key failing or out of credit → "Fix";
- Telegram not connected → "Connect";
- the paper bot paused;
- the data feed stale (user wording, e.g. "Live prices are delayed").
Each item has one button that opens the exact fix. Dismissible per item for the day. Nothing shows when all is fine.
D2 It uses the same status sources as the Settings chips (one backend endpoint, `/me/setup-status`, cached). Tests per
state.

PART E — TESTS, SCREENSHOTS, ORDER
- **Tests:**
  - the paste detection table;
  - auto save + test;
  - the error mapping;
  - the resolution order (A5);
  - the redirects (B4);
  - the search;
  - the defaults pre-fill + the empty-defaults identity;
  - the setup-status states;
  - the English-only and public-repo guards.
- **Screenshots before / after:** Settings, Copilot (not connected / connected), each section page, Dashboard with the
  health strip, all at 360 / 390 / desktop, light / dark.
- **Order:**
  1. The review.
  2. Part A (the biggest user pain).
  3. Part B.
  4. Part D.
  5. Part C.
  One PR per part (`claude/<name>`), drafts, merge on Abhi's word. Each PR comes with the deploy line and whether a
  migration is needed.
