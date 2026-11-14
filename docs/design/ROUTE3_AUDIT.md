# Route 3 audit: signals for all users, orders on the broker's own page (BIZ-1)

Owner order 14 Nov (INBOX 14 Nov, DECISIONS 14 Nov). Route 3 now, for every user (Free / Basic plan): Paper, signals,
scans, Copilot and "Trade in your broker app" - the user places a real order himself on the broker's own page; ATP's
server never sends another user's LIVE order to a broker API. Route 1 (Pro) = a runner on the user's own machine
(separate design on `claude/design-user-runner`, not repeated here). Paid "we set up your VPS" service. The owner's own
LIVE keeps running through the ATP server. Route 2 (exchange empanelment as algo provider) is deferred (BIZ-3).
Audit of `origin/main` at ec7d0141. No application code is changed by this PR.

## 1. "Trade in your broker app" today

### 1a. Code
| What | Evidence |
|---|---|
| Only Zerodha builds a pre-filled link; every other broker returns null ("not verified", a URL is never guessed) | `frontend/src/proposals/brokerLinks.ts:8-10`, `:55-66` |
| Kite link = HTML form POST of `api_key` + JSON `data` to `https://kite.zerodha.com/connect/basket` in a new tab | `brokerLinks.ts:13`, `:89-106` |
| Pre-filled today: `variety=regular`, symbol, exchange, side, `order_type=MARKET`, whole quantity, `readonly=false` (user can edit in Kite). No price, product, trigger / SL | `brokerLinks.ts:20-29`, `:47` |
| Equity: NSE / BSE cash only (`instrument_kind == UNDERLYING`); F&O: NFO / BFO with Kite's own contract symbols from the instrument master | `brokerLinks.ts:15-18`, `:69-87`; `backend/app/paper/fno_basket.py:530-555` |
| Needs `KITE_PUBLISHER_API_KEY` (env, empty by default) | `backend/app/core/config.py:794`; `proposal_card.py:103` |
| Flag `broker_app_links`: OFF by default, catalog status "decision", not in the trader pack | `backend/app/platform/flag_catalog.py:90`; `controls.py:698-714`; `trader_pack.py:7-9` |
| Broker chosen = the organisation's single broker (default account, else its only credential) | `backend/app/ai/proposal_card.py:50-67` |

### 1b. Where the button appears
| Surface | Today | Evidence |
|---|---|---|
| Copilot proposal card | Yes - **only EXIT_POSITION** proposals (closing an open position). ENTRY proposals get no ticket | `proposal_card.py:38`, `:121-122`; `frontend/src/copilot/tabs/ProposalCard.tsx:49` |
| F&O basket (Option chain / Terminal basket) | Yes - "Trade in your broker app" tool | `frontend/src/fno/BasketPanelV2.tsx:382-393`, `:737` |
| Signals page cards (bot / scan / alert / Copilot entries) | No | `frontend/src/signals/SignalsV2.tsx`, `SignalPartsV2.tsx` (no import of brokerLinks) |
| Scan matches (Find setups results) | No | `frontend/src/screener/ResultBoard.tsx`, `ResultViews.tsx` |
| Paper bot signals | No | `frontend/src/paperbot/PaperBotV2.tsx` |
| F&O order ticket (single leg, Terminal / chain) | No - a LIVE mode choice instead (see §2) | `frontend/src/fno/OrderTicketV2.tsx:483`, `:489-492`, `:620` |
| Telegram / push alerts | No - links only to an ATP app path (by design: a payload never sends the user to another site) | `backend/app/alerts/deeplink.py:1-9`, `:64-99` |
| Position actions (Reverse) | No | `frontend/src/fno/PositionActionsV2.tsx:53` |

### 1c. Broker link schemes (web research 14 Nov; the session's network blocked kite.trade, upstox.com, fyers and angelone pages, so only search-result text from the official domains was read)
| Broker | Public order link / button | Can pre-fill | Status |
|---|---|---|---|
| Zerodha | Kite Publisher "Offsite order execution": POST basket to `kite.zerodha.com/connect/basket` with `api_key` ([kite.trade/docs/connect/v3/basket](https://kite.trade/docs/connect/v3/basket)) | Official table (snippet): variety, tradingsymbol, exchange, transaction_type, order_type; forum list adds quantity, product, price, trigger_price, readonly, disclosed_quantity, validity, tag. No TTL validity; no BO / CO (forum) | **Verified** (format already in `docs/BROKER_DATA_TERMS.md` §Kite Publisher); price / trigger / product fields **to re-check** on the live page before use |
| Fyers | API Connect JS: branded Buy / Sell buttons and baskets (up to 10) ([api-connect-docs.fyers.in/docs/order](https://api-connect-docs.fyers.in/docs/order)) | symbol, quantity, price, order type, side (search snippet) | **Unverified** - page not fetched |
| Angel One | SmartAPI "Publisher" trade buttons / baskets (Angel knowledge centre + community post) | not read | **Unverified** |
| Upstox | "TradeButton" JS (Upstox market-talk article); no current developer doc found; a community question on pre-fill deep links has no answer read | not read | **Unverified** - may be retired |
| Dhan | No basket API; "Sharable basket" link inside the Dhan app (Dhan announcement) - created in Dhan, not by a third party | none | **No public pre-fill link found** |
| Groww, Kotak Neo | Nothing found | none | **No public pre-fill link found** |

Without a verified scheme the button can still **open the broker's own app / site** (no pre-fill) with the order facts
shown in ATP for the user to copy - that needs only the broker's public web address (not researched per broker yet).

### 1d. What may be pre-filled (§3 of CLAUDE.md)
- Only the user's OWN settings: the side and contract of his own signal, quantity = his lots x lot size, product and
  order type = his Trade settings (`ASK_SETTINGS`, `backend/app/paper/fno_ticket.py`), price only when his own order
  type is LIMIT and he typed / set it. SL / target only if his own settings hold them - never an engine default.
- The ATP button text is "Trade in your broker app" (no buy / sell wording); the BUY / SELL appears only on the
  broker's own page, which is the user's action. `readonly=false` stays (he can change it there).

## 2. LIVE through ATP's server: who can, and what gates it

LIVE-1 (DECISIONS 11 Nov, binding) made `live_trading` the ONE switch: ON = **every user** may pick LIVE on every
order path; the per-organisation grant is stored but no longer read.
| Fact | Evidence |
|---|---|
| `org_live_open` reads only the global switch (tenant ignored) | `backend/app/platform/live_owner.py:95-118`; `live_switch.py` (`live_allowed`, "the switch is the same for everyone") |
| Only the owner (2FA + typed "ENABLE LIVE") can turn `live_trading` on; default OFF | `live_owner.py:1-11`, `:63-92`; `controls.py:837-848` |
| OFF = `phase0_paper_only` derived ON; entry refused in `entry_refusals`, at the broker chokepoint and in the worker | `phase0_lock.py:1-22`; `execution/signal_execution.py:71-73`; `workers/trading_worker.py:1750-1760` |

| LIVE path | Gate today | With `live_trading` OFF | With ON, non-owner |
|---|---|---|---|
| F&O ticket (Terminal / chain) | `fno_ticket._own_session` -> `org_live_open`, then his own broker | PAPER only | **LIVE allowed** |
| Basket, Reverse | `fno_ticket.live_route` -> `live_switch.live_refusal` | PAPER only | **LIVE allowed** |
| Bots / deployments | `deployments/routes.py:628-640` `_live_gates` (+ scope `trading:live`, every USER has it) ; worker `:1756` | PAPER only | **LIVE allowed** |
| Multi-leg / incomplete structures | `execution/multileg.py:142`, `incomplete_structures.py:396` -> `entry_refusals` | PAPER only | **LIVE allowed** |
| Public API `/signals` | `public_api/routes.py:175-176` refuses LIVE always; flag `public_api` | PAPER | PAPER |
| Webhooks, strategy console | hard-coded `mode="PAPER"` (`webhooks/routes.py:156`, `strategy_engine/console_routes.py:228`) | PAPER | PAPER |
| Copilot ENTRY approve | always PAPER (`proposal_card.py:118-119`); the AI never places (ADR-0006) | PAPER | PAPER |

Findings:
- **No leak with the switch OFF**: every path above stays PAPER (production default OFF; the real production value
  is not read here - OWNER_RULES A.2).
- **Gap G1 (Route 3 conflict)**: the day the owner turns `live_trading` ON for his own LIVE, every user can send LIVE
  orders through ATP's server (its IP) - what Route 3 forbids. A per-user gate is needed: LIVE through the server only
  for the owner identity (`live_owner.is_owner` / an owner-held allow-list), everyone else PAPER + broker-app link.
  This narrows LIVE-1 (an order-path change: owner's ✅ needed, BIZ1-Q1).
- **Gap G2 (dead Live)**: the F&O ticket always shows the PAPER / LIVE choice; when LIVE is refused it shows a sentence
  and disables Review (`OrderTicketV2.tsx:483`, `:492`, `:620`) - no "Trade in your broker app" there.

## 3. Plans
| Fact | Evidence |
|---|---|
| Three plans in code: `free`, `pro` (Rs 2,999 / month), `business`; unknown id -> `free`; `live_trading=False` on all | `backend/app/plans/registry.py:1-5`, `:62-92` |
| Free: `ai_features=False`, `option_features=False`, 2 bots, 1 alert channel; Pro: options, AI, 10 bots | `registry.py:63-80` |
| Enforced: bot count (always), `ai_features` only for an external AI provider, marketplace, public API; AMW-1 limits only with `plan_limits_v2` (OFF, "decision") | `plans/limits.py:129`, `:200-205`; `ai/routes.py:119`; `flag_catalog.py:213` |
| `option_features` is shown on the plan page, not enforced on an order path | `growth/plan.py:54-55`; no use in `paper/`, `execution/` |
| Plan page = usage meter (flag `growth_v2`, OFF, "plans come with payments P5-2"); no price shown, no upgrade | `growth/plan.py:1-14`; `flag_catalog.py:206`, `:449` |
| No "Basic" plan, no runner / VPS-service entitlement field | `registry.py:12-52` |

So Free / Pro can be expressed today (two ids exist); "Basic" needs a decision (rename Free or add a plan id).
Pro would gate: the runner pairing (BIZ-2), and the VPS set-up service as a paid add-on (no code: a request form +
admin list). Payments are P5-2 (Razorpay, not built).

## 4. Signals, scans, Copilot, alerts for normal users
| Feature | Flag(s) | Default | Trader pack |
|---|---|---|---|
| Copilot | `ai_copilot` (+ `ai_agent`, `ai_one_key`) | ON (`controls.py:699`) | yes |
| Copilot planner / ticket / watch | `copilot_agent`, `copilot_ticket`, `copilot_watch` | OFF | no (`copilot_agent`, `copilot_watch` not built yet: `flag_catalog.py:430-433`) |
| Pages they live on | `ui_v2` | OFF | yes |
| Scans | `screener_v2` + 20 screener flags | OFF | yes (`trader_pack.py:65-69`) |
| Paper account, risk tools | `paper_account`, `risk_by_default`, ... | OFF | yes |
| Strategy templates (paper bots) | `strategy_templates` | OFF | yes |
| Signal / alert wording | `signal_language_v2` (structure words, no buy / sell / prices) | OFF | yes |
| Telegram approve buttons | `telegram_inbound` | OFF | yes |
| Scan -> automatic PAPER | `scan_auto_paper` | OFF | never (FORBIDDEN) |
| Owner's signal engine | `trade_engine` | OFF | never (owner only) |
| Broker-app link | `broker_app_links` | OFF | no ("decision") |

All are global flags (with tenant allow-lists), not plan-based: once the admin runs "Enable trader features" every user
gets them. Free-plan Copilot answers run on the user's own AI key or ATP's rules unless the operator sets
`AI_PLATFORM_BUDGET_INR_FREE` (`plans/registry.py:95-109`).

## 5. Gaps -> PR-sized build steps
| Step | What | Flag | Migration | Tests | Screens | Tokens |
|---|---|---|---|---|---|---|
| B1 | Per-user LIVE gate: server LIVE only for the owner (or an owner-held allow-list reusing the stored `live_trading` tenant list / a user list in controls); one check in `entry_refusals` + `fno_ticket._own_session` + `live_route` + `deployments._live_gates` + worker; exits never blocked | none (safety; behaviour only when `live_trading` ON) - owner ✅ first (BIZ1-Q1) | no (controls JSON) | non-owner LIVE refused on each path, owner allowed, exits pass | - | ~60k |
| B2 | Ticket / basket / Reverse: when LIVE is not open for him, the LIVE option becomes "Trade in your broker app" (pre-filled where verified, else open the broker + the order facts to copy) | `ui_v2` + `broker_app_links` | no | ticket shows the link, never a dead Live | ticket desktop + phone, before / after | ~45k |
| B3 | Entry-side ticket: `ticket_for` for ENTRY proposals and signal-feed items from the user's own settings only (lots, product, order type); the Signals card, scan match row and paper-bot signal get the button | `broker_app_links` | no | no engine default leaks into a field; no buy / sell text in ATP | Signals, scan, bot | ~55k |
| B4 | Broker registry: verify Fyers / Angel / Upstox schemes on the live docs (needs those hosts allowed), add each verified one behind its own test; others "open the app" | `broker_app_links` | no | one test per broker format | - | ~35k |
| B5 | Alerts: Telegram / push text adds "Open in ATP to trade in your broker app" (ATP path only, per `deeplink.py`) | `signal_language_v2` | no | text has no buy / sell / price | Telegram sample | ~15k |
| B6 | Plans: decide Free vs Basic naming; Pro entitlement field `runner` (+ `vps_setup_service` add-on), shown on the plan page; no payment | `growth_v2` (OFF) | no (plans in code) | plan matrix test | plan page | ~25k |
| B7 | "We set up your VPS" request card (Pro) -> admin list; nothing runs on any server | new flag `vps_setup_service` OFF | yes (one request table) | request + admin list | card + admin | ~30k |

Order: B1 (safety) -> B2 -> B3 -> B5 -> B4 -> B6 -> B7. B1-B3 are the minimum for Route 3.

## 6. Owner decisions (defaults; also in docs/OPEN_QUESTIONS.md "BIZ-1")
- BIZ1-Q1: LIVE-1 said ON = every user LIVE; Route 3 says no other user's LIVE through ATP's server. Default: Route 3
  narrows LIVE-1 - with `live_trading` ON, server LIVE only for the owner; everyone else PAPER + broker-app link.
- BIZ1-Q2: "Free or Basic". Default: one free plan named "Basic" in the UI, id `free` kept (no data change).
- BIZ1-Q3: brokers without a verified pre-fill link. Default: the button opens the broker's own site / app with the
  order facts shown in ATP to copy; never a guessed URL.
