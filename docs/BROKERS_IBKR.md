# Interactive Brokers (IBKR): setup and operations

Design: [ADR-0018](adr/0018-ibkr-transports.md).

Status: **design only**. Code lands in PRs I2–I7, after the Indian PAPER go-live and crypto Phase 1. Everything below is
**paper first**. Live trading through IBKR is a separate owner decision, off by default.

## 1. Paper account
1. **Open an IBKR account.** IBKR provides a paper-trading account linked to it, with its own username. Use the
   **paper username** for everything below.
2. **Market data.** Subscriptions are bought on the live account and shared with the paper account through Account
   Settings → "Share real-time market data subscriptions with paper trading account".
   - US data has monthly fees, so it is an owner decision (OPEN_QUESTIONS IB-3).
   - Without data, quotes are delayed. Delayed data is enough for the read-only smoke test, and the smoke test says
     when data is delayed.
3. **Commission plan.** IBKR offers fixed and tiered pricing. Note which plan the account uses, because the platform's
   cost model must match it per account (IB-5).

## 2. IB Gateway in Docker (TWS transport, PR I3)
- **The service.** It is `ib-gateway` under compose profile `ibkr`, so `docker compose up` does not start it. To start
  it:
  ```
  docker compose --profile ibkr up -d ib-gateway
  ```
- **The host `.env` holds the operator's paper login.**
  - Variable names: `IBKR_GATEWAY_USER`, `IBKR_GATEWAY_PASSWORD`, `IBKR_TRADING_MODE=paper`.
  - Set them **on the host only**: never in chat, never in the database, never in git.
  - Changing `IBKR_TRADING_MODE` to `live` is an owner-only step and is not part of any PR.
- **Ports.** The Gateway API port is reachable only from the backend and worker on the internal network. Nothing is
  published to the internet. The VNC port (used for the second-factor prompt) is bound to 127.0.0.1; use an SSH
  tunnel.
- **Image.** Pinned by digest after review (IB-2). Upgrades are a reviewed PR.

## 3. Daily and weekly login
- **Daily restart.**
  - IBKR restarts the Gateway once a day. The time is set in the Gateway and kept outside the US session.
  - The platform marks the account broker-uncertain while it is disconnected.
  - **New entries are blocked with a reason.** **Exits are queued and retried, with an EMERGENCY alert** (ADR-0004).
  - After a reconnect, reconciliation (open orders, executions, positions) runs before the account is clear again.
- **Weekly second factor.** IBKR asks for a second-factor confirmation on a regular cycle. Health shows "waiting for
  login" and the operator gets an alert. The runbook:
  1. Open the VNC tunnel.
  2. Approve the login in IBKR Mobile.
  3. Confirm the health check shows "connected" and the reconciliation report is clean.
- **Missed login.** If the login is missed, the account stays uncertain. Nothing new opens, and exits keep retrying.

## 4. Read-only smoke test (Phase AJ steps, PR I3)
Run by hand against the paper account. It is never in CI and never places an order.
1. **authenticate**: the profile shows the paper account id.
2. **positions and account values**: they load, with currency on every amount.
3. **quote** for a liquid US ETF (chosen by the operator at run time, not hardcoded), with delayed or real-time
   stated.
4. **history**: daily and intraday bars, with session boundaries in UTC matching the venue calendar.
5. **open orders and executions**: they load. An empty result is fine.

The script prints PASS or FAIL per step and stops at the first FAIL.

## 5. Web API (OAuth) – vendor onboarding checklist (PR I7)
The Web API lets each tenant connect their own IBKR account, with tokens per tenant encrypted under ADR-0008. It needs
IBKR to register the platform as a third-party vendor. Owner items (OPEN_QUESTIONS IB-1):
- [ ] The legal entity that registers, and its compliance contact.
- [ ] IBKR's third-party / OAuth application: company details, product description, data use, security answers.
- [ ] Consumer key and signing keys. These are generated and stored as operator secrets on the host only.
- [ ] Redirect URL on the production domain (`PUBLIC_BASE_URL`).
- [ ] Market-data redistribution terms (whether tenant screens may show IBKR data).
- [ ] Test account(s) from IBKR for certification, and any certification run IBKR requires.

Until this list is done, `WebApiTransport` is a stub that refuses with "IBKR Web API needs vendor registration". The UI
tells tenants that IBKR is operator-only for now.

## 6. What the platform enforces (all venues, restated for IBKR)
- **No MARKET orders by default.** LIMIT, STOP and STOP-LIMIT, plus combo orders for spreads. A refused combo is not
  legged.
- **Jurisdiction rules run before sizing**, and every block names its rule.
  - India resident: foreign cash equity and ETFs are allowed with an LRS note; foreign derivatives and margin are
    blocked (IB-4).
  - US resident: the pattern-day-trader check.
- **Exits are never blocked.**
- **The AI never places, modifies or cancels orders** (ADR-0006).
