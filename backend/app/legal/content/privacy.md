---
slug: privacy
title: Privacy notice
version: 2026-10-11
---
> **Draft — to be reviewed by a lawyer before launch.** Text in [SQUARE BRACKETS] is a placeholder that the company fills in once it is registered.

# Privacy notice

This notice explains what personal data the Advance Trading Platform ("the platform") holds about you, why it holds it, how long it keeps it and what rights you have. It is written for the Digital Personal Data Protection Act, 2023 (the "DPDP Act").

## Who is responsible for your data

The data fiduciary is [COMPANY NAME], [REGISTERED ADDRESS]. Questions about this notice go to the grievance officer named at the end of this page.

## Why we process your data (purpose)

We process personal data only to:

- create and secure your account (sign-in, two-factor authentication, sessions);
- run the platform's features you use: paper trading (simulated trades), strategies, backtests, scans, alerts, the journal and the AI features you turn on;
- send the alerts and notifications you set up (in the app, by email, Telegram or push);
- keep the records that law and regulation require, and investigate security incidents;
- answer your support requests and feedback.

We do not sell personal data, we do not show advertising, and we do not use your data to build marketing profiles.

## What data we hold

- **Account:** your email address, a salted hash of your password (never the password itself), your two-factor secret if you turn it on, your role and organisation.
- **Security records:** sign-in attempts and sessions, with the IP address and browser (user agent) they came from.
- **Broker connections (only if you link a broker):** the credentials and session tokens your broker issues, encrypted with your organisation's own key.
- **Trading and research records:** paper orders and trades, strategies, deployments, backtests, alerts, screens, journal entries and risk settings.
- **AI features (only if you use them):** the questions you ask and the answers given. The stored copy has email addresses, mobile numbers, PAN, Aadhaar and account numbers masked. If you add your own AI provider key, it is stored encrypted and never shown again.
- **Notification channels (only if you set them up):** your Telegram chat id, push subscription or alert email.
- **Feedback:** the message you send, the app version, the screen you sent it from and your browser's user agent.

## Who receives your data

- **Your organisation:** members of your organisation see its shared records (positions, deployments, alerts) according to their role.
- **Service providers who run the platform for us:** hosting, email delivery and similar providers, bound to use the data only for that service.
- **The AI provider your organisation or you configured:** only after the organisation owner accepts the data-sharing consent shown in Settings, and never your broker credentials, API keys or passwords. The provider may process data outside India under its own terms.
- **Your broker:** only the requests needed to read data through the broker session you linked yourself.
- **Authorities:** when the law requires it.

Data from your broker session is never pooled or shown to another user.

## How long we keep it (retention)

- Sign-in attempts (IP address, browser): {{retention.login_events_days}} days.
- Sessions: {{retention.sessions_days}} days after they expire or are ended.
- In-app notifications: {{retention.notifications_days}} days.
- Delivered alert records: {{retention.alert_deliveries_days}} days.
- Password-reset links: {{retention.password_resets_days}} days after use or expiry; team invitations: {{retention.invites_days}} days.
- Orders, trades, signals, strategy versions, risk events, the audit trail, AI call records and your acceptances of these documents: kept for at least five years, as securities regulation requires. These records stay attributed to an internal account id even after your personal data is erased.
- Security logs are kept for at least {{compliance.log_retention_days}} days.

## Your rights

Under the DPDP Act you may:

- ask for a summary of the personal data we hold about you and who it was shared with;
- ask us to correct, complete or update it;
- ask us to erase it. Erasure replaces your email, password and two-factor data with inert values and ends every session; trading records the law requires us to keep stay under an anonymous id;
- withdraw a consent you gave (for example the AI data-sharing consent) at any time, without affecting what was done before;
- nominate another person to exercise these rights if you die or become unable to;
- raise a grievance with our grievance officer, and, if you are not satisfied with the answer, complain to the Data Protection Board of India.

To use any of these rights, write to the grievance officer below. We may ask you to confirm your identity first.

## Security

Passwords are stored only as salted hashes. Broker credentials and AI keys are encrypted with a key that belongs to your organisation. Two-factor authentication is available for every account, and every sign-in from a new device is notified.

## If a data breach happens

If a breach affects your personal data, we will tell you and the Data Protection Board of India what happened, what data was involved, what we are doing about it and what you can do to protect yourself. Our breach runbook sets a target of {{compliance.breach_notify_hours}} hours from the time we become aware of a breach to notify.

## Children

The platform is not meant for anyone under 18. Do not create an account if you are under 18.

## Grievance officer

- Name: [GRIEVANCE OFFICER NAME]
- Email: [GRIEVANCE OFFICER EMAIL]
- Address: [REGISTERED ADDRESS]
- We answer a grievance within [GRIEVANCE RESPONSE TIME].

## Changes to this notice

Each version of this notice has a date. When it changes, the platform asks you to read and accept the new version the next time you sign in. Your open positions and exits are never held back while you decide.
