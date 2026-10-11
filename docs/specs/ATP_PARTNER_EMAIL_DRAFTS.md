# ATP partner email drafts — send AFTER the company is registered

Fill in before sending: [Company legal name], [CIN/LLPIN], [registered address], [website], [phone], [email]. Keep one copy of each sent email and every reply in docs/BROKER_DATA_TERMS.md (ATP repo).

## Recipients
Brokers (use draft A): Upstox (developer partnerships / API support), Dhan (partner form at dhanhq.co → Algo Platforms; apihelp@dhan.co), Angel One (SmartAPI support), Fyers (API support — written consent needed because their API terms restrict building "charting/technical tools" on exchange data), Groww (trade API support). Later: Kotak Neo, ICICI Breeze, 5paisa, Shoonya/Finvasia, Alice Blue. Zerodha: execution-only (terms forbid display on other platforms) — no email needed now.
Data vendors (use draft B): TrueData (partnering email on their site footer; +91 7304-22-44-66), Global Datafeeds (sales/support email on their site). Later: Accelpix.

---

## Draft A — to a broker

Subject: Partner platform enquiry — per-user API access for our trading-discipline platform

Dear [Broker] API / Partnerships team,

I am writing on behalf of [Company legal name] ([CIN/LLPIN]), the company behind Advance Trading Platform (ATP), a web and mobile platform that helps retail traders trade with discipline: paper trading, trade journal and analytics, screeners, option-chain analysis and an educational AI assistant. We would like to offer [Broker] clients the ability to connect their own [Broker] account to ATP.

How the integration works
- Each user links their own [Broker] account through your OAuth flow and their own API app/key. ATP never holds shared credentials.
- Market data is requested only under that user's own access token, and is shown only to that same user, inside their login. We do not pool, cache across users, redistribute, or display one client's data to anyone else.
- In our beta phase ATP places no orders through the API. Trade ideas are sent to the user, who places orders in your app. In a later phase we intend to support approval-only order placement (the user approves each entry) in line with the SEBI retail-algo framework: registered static IP, algo tagging, daily 2FA login, order-rate limits and limit orders only.

We would be grateful if you could confirm, in writing:
1. That displaying a client's own market data (quotes, candles, option chain) inside ATP, under that client's own token and only to that client, is permitted under your API terms.
2. That the same data may be used for that client's paper (simulated) trading and alerts within ATP.
3. Whether a partner/vendor registration or empanelment is required for a platform serving multiple clients, and the steps and any fees.
4. The per-connection limits (instruments per WebSocket, connections per user, request rates) we should design to.
5. The contact for the algo-provider empanelment process under the April 2026 framework, for our later phase.

About us: [Company legal name], [registered address], [website]. We are happy to sign your partner agreement, provide technical details and complete any due diligence.

Thank you for your time.

Kind regards,
Abhishek Wasu
Founder, Advance Trading Platform — [Company legal name]
[phone] · [email] · [website]

---

## Draft B — to a data vendor

Subject: Partner enquiry — per-subscriber live data inside our platform (vendor-billed)

Dear [Vendor] Partnerships team,

I am writing on behalf of [Company legal name] ([CIN/LLPIN]), the company behind Advance Trading Platform (ATP), a web and mobile platform for retail traders: charts, screeners, option-chain analysis, paper trading, a trade journal and an educational AI assistant. We would like to offer our users the option to subscribe to [Vendor] real-time data and use it inside ATP, in the same way your broker and platform partners do today.

Proposed model
- The user subscribes with and pays [Vendor] directly, at your prices, under your subscriber agreement. ATP does not bill for data and adds no mark-up; exchange per-user fees remain within your subscription.
- [Vendor] issues a per-subscriber credential. ATP receives the feed only for that subscriber's session and displays it only to that subscriber, inside their own login. No pooling, no cross-user caching, no redistribution, no display to non-subscribers.
- ATP is an intermediary platform only. We do not hold an exchange data licence and do not intend to resell data.

We would like to understand:
1. Whether your partner programme covers an online platform like ours, and the terms of the partner agreement.
2. Written confirmation that a subscriber may view their [Vendor] data inside ATP (charts, screeners, option chain, alerts) under this model.
3. Whether use of the subscriber's data for their own paper (simulated) trading within ATP is permitted under your agreement, or can be included in the partner terms, given the exchange's restriction on simulation use.
4. Technical integration: per-subscriber authentication, WebSocket/REST endpoints, symbol limits per subscriber, historical data access, and any sandbox for integration testing.
5. Partner pricing or revenue arrangement, if any, and the onboarding steps.

About us: [Company legal name], [registered address], [website]. We are happy to sign your partner agreement and complete any due diligence.

Thank you for your time.

Kind regards,
Abhishek Wasu
Founder, Advance Trading Platform — [Company legal name]
[phone] · [email] · [website]

---

## When replies arrive
- Record each reply (date, sender, exact wording) in docs/BROKER_DATA_TERMS.md with status: confirmed / conditional / declined.
- A broker or vendor becomes visible to ATP users only when its status is "confirmed" for both display and paper-trading use.
- Any condition they add (e.g. attribution text, symbol caps, no caching) becomes a test in the adapter.
