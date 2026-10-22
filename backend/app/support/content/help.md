# Help pages

How-to answers the support bot searches (together with the legal pages). One entry per `## ` heading: the heading is
the question, `keywords:` lists other words people use for it, `page:` is where in the app it happens, and
`requires: legal_pages` marks an entry that refers to the legal pages (left out while that flag is off). Keep every
answer about how to use the platform - never about what to trade.

## What is paper trading, and does the platform place real orders?
keywords: paper, simulated, simulation, real money, real orders, live trading, demo, virtual
page: /positions

During this phase every order and trade in the platform is paper (simulated): no real money moves and the platform sends no order to any broker or exchange. Simulated fills can differ from real ones. Where a proposal shows "Trade in your broker app", the link opens your own broker's app, where you review and confirm any order yourself.

## How do I create an account?
keywords: sign up, signup, register, registration, new account, join, invite code, beta
page: /account

Open the Account page and choose "Need an account? Register". Enter your email and a password of at least 10 characters, tick the box to accept the terms when the form shows one, and choose "Create account". During the closed beta you may also need an invite code ("Have a beta invite code?").

## How do I join my team's organisation?
keywords: invite, invitation, teammate, team member, organisation, organization, join team
page: /team

An owner of the organisation invites you from the Team page. Open the link in the invitation, choose a password and select "Join team". You then share the organisation's broker connections, deployments, positions and alerts according to the role the owner gave you.

## I forgot my password. How do I reset it?
keywords: forgot password, reset password, lost password, cannot log in, can't sign in, locked out
page: /account

On the Account page choose "Forgot password?", enter your email and select "Send reset link". If your organisation has email set up you receive a link; otherwise ask your organisation owner to send you one. A reset link works once and ends every other session.

## How do I change my password?
keywords: change password, new password, update password
page: /account

Open the Account page and use the "Change password" card: enter your current password and the new one. Every other session of your account is ended when the password changes.

## How do I turn on two-factor authentication?
keywords: 2fa, two factor, mfa, authenticator, totp, otp, backup codes, security
page: /account

Open the Account page and find "Two-factor authentication (TOTP)". Scan the code with an authenticator app, enter the 6-digit code to confirm, and keep the backup codes somewhere safe. From then on every sign-in asks for a code.

## How do I sign out of other devices?
keywords: log out, logout, sign out, sessions, other devices, stolen, lost phone
page: /account

The Account page lists your active sessions. End one session, or choose the button that ends every session of your account on every device. Change your password too if you think someone else used your account.

## How do I verify my email address?
keywords: verify email, verification, confirm email, verification link
page: /account

When you sign up, the platform sends a verification link to your email. Open it to verify the address. If it did not arrive, use "Email verification" on the Account page to send it again.

## How do I connect my broker?
keywords: broker, connect broker, link broker, zerodha, upstox, dhan, angel, credentials, api key, login to broker, session
page: /broker

Open the Broker page and use "Connect your broker": pick your broker, store the API credentials it issued and log in. "Your brokers" shows whether each session is working, and "Connect again" appears there when the broker asks for a daily login. The credentials are encrypted with your organisation's own key.

## Why does my broker need a login every day?
keywords: daily login, token expired, session expired, broker logged out, reconnect
page: /broker

Indian brokers end API sessions every day, so the broker session has to be renewed once a day. The Broker page shows when a session has expired; use "Connect again" there. The platform reminds you before the market opens when your alert channel is set up.

## How do I get alerts on Telegram, email or push?
keywords: alerts, notifications, telegram, email alerts, push, sms, webhook, alert channel, bot token, chat id
page: /settings/alerts

Open Settings > Alerts. "Alerts on your phone" links your Telegram in a few taps; "Send a test" checks it. "More ways to get alerts" adds email, app notifications, SMS and others. Each channel has a severity floor: only messages at or above it are sent there.

## Where do I see my notifications?
keywords: notifications, bell, messages, alerts list, inbox
page: /notifications

Select the bell in the top bar to open Notifications. It lists entries, exits, rejections, broker disconnects, risk-limit breaches and system messages for your organisation.

## How do I see and close my paper positions?
keywords: positions, open positions, close position, exit position, square off, exit now, sell position
page: /positions

Open Positions to see your open paper positions with their stop and targets. Close one from its row there. Exits are never blocked: the platform's stop-loss, time exit and end-of-day square-off also keep running on their own.

## What is a proposal, and how do I approve one?
keywords: proposal, approve, reject, snooze, approval, watchtower, pending, entry proposal
page: /copilot/watchtower

When approval-only entries are on, an entry signal becomes a proposal instead of an automatic paper order. You see it in the AI Copilot page (Watchtower tab), and on Telegram or push if you set those up. Approve places a paper order at a fresh price; Reject drops it; Snooze hides it for a while. A proposal expires if nobody decides in time.

## How do I set my risk limits?
keywords: risk, risk limits, daily loss, position size, lot size, max loss, risk per trade, stop trading
page: /risk-management

Open Risk Management to set risk per trade, the daily loss limit, the number of trades per day and the consecutive-loss guard. The risk engine checks them before every paper entry. Organisation limits and platform ceilings also apply; the strictest one wins.

## How do I run a backtest?
keywords: backtest, backtesting, test strategy, historical, simulate strategy, results
page: /backtest

Open Backtesting, choose a strategy, a symbol, a timeframe and the period, and run it. The report shows trades, equity curve and charges. A backtest shows how rules behaved on past data only; it does not show what will happen next.

## How do I build my own strategy?
keywords: strategy builder, create strategy, custom strategy, rules, indicators, no code
page: /strategy-builder

Open Strategy Builder and compose entry rules from indicators, with stop and target settings, without writing code. Save it, then test it in Backtesting before running it in paper.

## How do I run a strategy automatically in paper?
keywords: autopilot, deploy, deployment, run strategy, automatic, worker
page: /deployments

Open Autopilot and create a deployment of a saved strategy in paper mode. The background worker reads closed bars, applies the same risk engine, monitors exits and squares everything off before the market closes. You can pause or stop a deployment at any time.

## How do I use the screener?
keywords: screener, scan, scanner, filter stocks, screens, gallery, watchlist
page: /screener

Open Screener to filter symbols by rules on closed bars, or Market Scanner to filter a watchlist by indicator conditions. Results list symbols that match the rules; they are research, not trade suggestions.

## How do I use my own AI key?
keywords: ai key, api key, own key, byok, openai, anthropic, gemini, vision, ai provider, spend cap
page: /settings/ai

Open Settings > AI. The organisation owner sets the organisation's AI provider there (it needs the data-sharing consent first). Where your organisation has it enabled, you can also store your own provider API key there, test it and set daily and monthly spend caps. A key is used only on your own or your organisation's requests, never for another user, and it is never shown again after you save it.

## How do I install the app on my phone or computer?
keywords: install, app, mobile, android, iphone, ipad, home screen, pwa, desktop app
page: /

On Android, or in Chrome or Edge on a computer, use the "Install app" button when the platform offers it, or the browser menu. On iPhone or iPad, open the platform in Safari, tap Share, then "Add to Home Screen".

## How do I switch between dark and light mode?
keywords: theme, dark mode, light mode, appearance, colour, color blind
page: /settings/appearance

Use the theme button in the top bar, or Settings > Appearance, which also has a colour-blind friendly option.

## How do I send feedback or report a problem?
keywords: feedback, bug, report problem, contact support, human, help, complaint, issue
page: /

Select the speech-bubble button ("Send feedback") in the top bar, describe what happened and send it. The app version and the screen you were on are attached, and your account and browser type (user agent) are recorded with it so the team can follow up. A person on the team reads every message.

## How do I download my organisation's records?
keywords: export, download data, csv, json, audit log, records, my data
page: /system-logs

The organisation owner can download orders, trades, audit logs and login events as CSV or JSON from the export card on System Logs. Each file comes with a SHA-256 checksum.

## How do I ask for my personal data to be deleted?
keywords: delete account, erase, erasure, remove my data, privacy request, dpdp, data rights, grievance
page: /legal/privacy
requires: legal_pages

Write to the grievance officer named in the Privacy notice. For a teammate who has left, the organisation owner can also use "Erase personal data (irreversible)" on the Team page. Erasure replaces the email, password and two-factor data with inert values; trading records the law requires stay under an anonymous id.

## Does the platform give investment advice or tips?
keywords: advice, tips, recommendations, adviser, advisor, research analyst, sebi registered, calls
page: /

No. The platform is a software tool, not a SEBI-registered investment adviser or research analyst. Signals, screens, backtests and AI answers describe rules and data; the decision to trade is always yours.
