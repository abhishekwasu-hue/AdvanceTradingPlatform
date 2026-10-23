# Google Play listing prep (Android) - prepared, not published

Readiness B8, `docs/design/MOBILE_APP_PLAN.md` §3.6. Nothing here is uploaded until Abhi says so. Waits for the
company registration, the compliance pack (BT7), the Play Console account (MA-4) and the lawyer's review of the legal
pages (A5, RDY-4). iOS (App Store) comes later, from the same project (plan §12).

## 1. Store text (plain English, no performance claims)

**App name:** AiAlgoDesk

**Short description (80 characters at most):**

> Practise trading with paper money, follow your own strategies and get alerts.

**Full description:**

> AiAlgoDesk is a trading workspace for people in India who are learning to trade or want to trade with a plan.
>
> - Paper trading: practise with simulated money, with real market prices from your own broker login.
> - Your own strategies: pick or build a strategy, choose the symbols, and AiAlgoDesk tells you when its rules are met.
>   You decide every trade yourself.
> - Signals that wait for you: a signal from your strategy waits in Signals until you approve or skip it.
> - Positions and orders: see what is open, what was closed and why, on one screen.
> - Notifications: a short notification on your phone for your signals, decisions waiting for you, closed positions
>   and broker login reminders. Tapping one opens the right screen. Telegram alerts work too.
> - Learn as you go: every page has a short tour.
>
> What AiAlgoDesk does not do:
> - It does not give investment advice or tell you what to buy or sell.
> - It does not promise or show expected returns.
> - The AI assistant explains and reviews; it never places, blocks or changes an order.
> - Trading is simulated (paper) for now; real-money trading is not available in the app.
>
> Trading in securities and derivatives carries risk. Read the Risk disclosure before you start.

Words never used anywhere in the listing: profit, returns, accuracy, win rate, guaranteed, "best", tips, calls,
buy / sell recommendations, target, stop-loss figures, any number about performance, any security's name or price.

**Category:** Finance. **Contact e-mail:** [COMPANY SUPPORT E-MAIL]. **Website:** https://aialgodesk.tech.
**Privacy policy URL:** https://aialgodesk.tech/legal/privacy (live once the `legal_pages` flag is on after the
lawyer's review, RDY-4).

## 2. Data safety form (from A5: `backend/app/legal/content/privacy.md`, `docs/READINESS_STATUS.md` A5)

Answers to prepare; the lawyer confirms them before submission (MA-12).

| Play data type | Collected | Shared (Play's meaning: to a third party, not a service provider) | Why | Optional |
|---|---|---|---|---|
| Personal info → Email address | yes | no | Account, sign-in, security notices | required |
| Personal info → Name | no | - | - | - |
| Financial info → Other financial info (paper orders, trades, strategies, risk settings) | yes | no | App functionality; records regulation requires | required to use trading features |
| Financial info → Purchase history (plan payments through Razorpay) | yes, on a paid plan | no (Razorpay is the payment processor) | Billing | only on a paid plan |
| Messages → Other in-app messages (questions to the AI assistant, feedback) | yes | to the AI provider the organisation configured, after its owner's consent | App functionality | optional |
| App activity → Other user-generated content (journal, alerts, screens, watchlists) | yes | no | App functionality | optional |
| App info and performance → Diagnostics (feedback with app version) | yes, when sent | no | Support | optional |
| Device or other IDs (the phone's notification token) | yes, when notifications are on | no (Google delivers the notification as a service provider) | Notifications | optional |
| Location | no | - | - | - |
| Contacts, photos, files, audio, calendar, health, web history | no | - | - | - |

Also: security records (sign-ins with IP address and browser) are kept for account security; broker credentials are
encrypted with the organisation's own key and never shared.

- **Encrypted in transit:** yes (https only; the app refuses plain http).
- **Deletion:** users can ask for their data to be erased by writing to the grievance officer (privacy notice "Your
  rights"); a self-service "Delete my account" is planned (A5.3). Trading records regulation requires stay under an
  anonymous id.
- **Children:** not for children (18+ at sign-up).

## 3. Screenshots list (phone, from the Playwright fixtures; never real accounts, symbols or prices from a live session)

Play asks for at least two phone screenshots; check the current size rules before capture (verify). Shot from the
same fixtures as the v2 phone baselines (`frontend/e2e/`), light theme, 390 x 844 scaled to Play's size:

1. Today (Dashboard) - the morning view with the ready-made cards.
2. Signals - "Waiting for you" with one signal opened (fixture data, structure words only).
3. Positions - paper positions as cards with "Exit now".
4. Charts - a chart with the bottom toolbar (anonymised fixture data older than three months).
5. Settings → Alerts - "Notifications on this phone" and Telegram.
6. Broker - the connect / renew step.

Each screenshot is checked for: no real person's data, no live prices, no performance figure, no buy / sell wording.

## 4. Content rating questionnaire (prepare)

- Violence, sexual content, profanity, drugs, gambling (real-money gambling or simulated gambling): no.
- User-generated content shared with other users: no (organisation members only; the marketplace is off).
- The app handles financial information: yes (paper trading records; payments through Razorpay).

## 5. Policy checks before any submission (verify each at that time)

- **Financial features declaration:** ATP is a trading tool, not a broker; it places no real-money orders in Phase 0
  and gives no investment advice (SEBI; compliance pack BT7 decides the final wording).
- **Apps that mainly show web content:** the app has native notifications, App Links into its screens, an offline
  screen and (B3) a biometric lock; record how each meets the rule at submission time.
- **Notification permission (Android 13+):** asked only after the explain-first screen, never in a loop.
- **Target API level:** the current Play requirement (verify before B4).
- **Account deletion:** Play requires an in-app path and a web link to request deletion - needs A5.3 or a documented
  request route before submission.
