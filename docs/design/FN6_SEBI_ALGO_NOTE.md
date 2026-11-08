# FN6 - Live-automation compliance note (SEBI retail algo framework)

Roadmap item FN6 (docs/ROADMAP.md:121). Scope: what ATP does, what it misses, and the owner's steps with a lawyer
before LIVE. This is a fact sheet, not legal advice: section 4 lists questions for the lawyer and draws no conclusions.
Paths are relative to `backend/app/` unless they start with `docs/` or `CLAUDE.md`. Sources are numbered [S1]..[S9] (section 5).
**Primary** = SEBI or NSE document read in full for this note. **Unconfirmed** = not found in a primary source.

## 0. Timeline (primary)

| Date | Event | Source |
|---|---|---|
| 4 Feb 2025 | SEBI circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 issued; applicable from 1 Aug 2025 (para 7b) | [S1] |
| 5 May 2025 | NSE implementation standards (NSE/INVG/67858) | [S3] |
| 22 Jul 2025 | NSE detailed operational modalities (NSE/INVG/69255, Annexure I) | [S4] |
| 29 Jul 2025 | SEBI moves the start to 1 Oct 2025 (stated in [S2] para 1; the July circular's number was not checked against a primary source, so it is **unconfirmed**) | [S2] |
| 19 Sep 2025 | NSE corrigendum NSE/INVG/70309 (empanelment criteria, ISO 27001:2022, CERT-In VAPT) | [S5] |
| 30 Sep 2025 | SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/132: glide path (31 Oct / 30 Nov 2025 / 3 Jan 2026); brokers who miss it may not onboard new API-algo clients from 5 Jan 2026 | [S2] |
| **1 Apr 2026** | The framework, with the exchange standards and modalities, **applies to all stock brokers** ([S2] para 8) | [S2] |
| 3 Nov 2025 | NSE FAQ on retail algo | [S6] |

No SEBI circular after 1 Apr 2026 that changes these dates was found (**unconfirmed**; a web search found none).
The repo's rule-set still says `effective_from: 2025-08-01` (`compliance/rulesets/in_sebi.json:5`). That is gap G9.

## 1. What the framework requires

| # | Requirement | Who carries it | Source |
|---|---|---|---|
| R1 | For API algo trading the broker is the principal and the algo provider is its agent | broker / provider | [S1] 5.I(a); [S4] 3 |
| R2 | Every algo order carries an exchange-issued unique identifier (algo ID). All API orders count as algo orders, including those below the threshold. Below 10 OPS the broker sends the generic algo ID "99999" and NNF 13th digit "0" | broker (in the exchange message) | [S1] 5.I(b), 5.II(b); [S3] B3, G; [S4] 2.1, 2.8, 8.2.4; [S6] Q8 |
| R3 | Threshold: 10 orders per second per exchange/segment, counted on the broker server's clock. Above it the client's algo must be registered. The broker rejects orders above its limit | broker / client | [S3] B2, B5, F; [S4] 8.2.3 |
| R4 | No open APIs. Access only through a unique client-specific (or vendor-client-specific) API key plus a static IP the broker has whitelisted | broker | [S1] 5.I(d); [S3] I(e); [S4] 10.8, 10.18 |
| R5 | Static IP: one primary plus an optional secondary, changed at most once a calendar week. **One IP maps to one client at a time** (family sharing only). For an empanelled vendor's algo, the IP is the vendor's or the client's. For a vendor platform hosted at the broker, it is the broker server's IP | client / vendor | [S3] A1-A7; [S6] Q3, Q6, Q10 |
| R6 | OAuth-only authentication plus two-factor authentication; API sessions logged out every day before the next trading day | broker / client | [S1] 5.I(d); [S3] A8, I(c-d); [S4] 10.7, 10.9 |
| R7 | The broker deals only with **exchange-empanelled algo providers**. A provider that places algo orders through a broker API must be empanelled: 2 years of market experience, ISO 27001:2022 (within 3 months), half-yearly CERT-In VAPT, net worth / turnover details, a 3-year incident declaration | algo provider | [S1] 5.III(a); [S4] 4.1, 6; [S5] 4.1-4.2, Annexure 2 |
| R8 | Each algo is registered with the exchange through the broker before use. Turnaround T+10 working days (execution algos T+7). Re-registration is needed for a logic change, a segment or version change, a URL change, a login or password policy change, or a change in the broker API | broker / provider | [S1] 5.II(a); [S4] 5.2, 8, 9 |
| R9 | Categories: white box (logic disclosed and replicable: execution, TWAP, VWAP) or black box (logic not known to the user: arbitrage, alpha seeking, HFT, scalping, others). **A black-box provider must be a SEBI-registered Research Analyst**, keep a research report per algo, and register any logic change as a new algo | provider | [S1] 5.V; [S4] 2.4.5, 2.7, 7.1.3.1.2, 13 |
| R10 | **All strategies run on the broker's servers and order messages originate from the broker's server** (a tech-savvy client hosts at his own end, on his static IP). A provider cannot host the black-box algos of other RAs | provider / broker | [S3] I(h); [S4] 14; [S6] Q4, Q5 |
| R11 | Pre-trade RMS on every algo order: price band, quantity, order value, trade price protection, **no algo order sent as a market order (market price protection)**, cumulative open value, loop / runaway auto-stop, margin, MWPL, position, exposure, turnover and security-wise limits. No IOC or market algo orders in commodities | broker (RMS) | [S4] 11.1, 11.4; [S6] Q11 |
| R12 | Audit trail of every API order, with the actual user id, kept at least 5 years. Unique internal numbering of client orders | broker | [S3] I(a); [S4] 10.3, 10.10 |
| R13 | Registered retail algos take part in a monthly mock / simulation session (the individual tech-savvy client is exempt) | broker / provider | [S4] 12.1; [S6] Q9 |
| R14 | Charges shared between provider and broker are disclosed in full. The broker handles grievances. Confidentiality and NDA agreements between broker, vendor and client | broker / provider | [S1] 5.III(d), 5.II(c); [S4] 10.23, 10.25, 13.1.4 |
| R15 | The exchange can kill orders from one algo ID | exchange | [S1] 5.IV(a)(iii); [S3] J3 |
| R16 | Broker practice, Zerodha example: static IP from 1 Apr 2026 for all API order placement (order endpoints only); more than 10 orders a second gets HTTP 429; a market order with protection 0 is rejected | broker | [S7], [S8] (broker pages, not regulator) |

## 2. What ATP already does (evidence)

| Area | What the code does | Evidence |
|---|---|---|
| PAPER lock | `phase0_paper_only` refuses new LIVE entries at the broker-order chokepoint. Exits still run (ADR-0004) | `platform/phase0_lock.py:31-33, 70-95`; `brokers/rate_budget.py:437-439, 467-469` |
| LIVE switch | The worker skips LIVE deployments while the lock holds. Deploy and CI refuse to start if `live_trading` is on or the lock is off | `workers/trading_worker.py:1704-1707`; `deploy/atp.sh:2120-2130`; `deploy/ci/ci_posture.py:12-13` |
| Routing policy | AUTO_LIVE is always REFUSE. ONE_CLICK makes a proposal, so a person decides | `execution/core/policy.py:6-11, 47-51` |
| Other entry paths | The TradingView webhook places PAPER orders only. The public API refuses LIVE | `webhooks/routes.py:156`; `public_api/routes.py:175-176` |
| Rules as data | An IN-SEBI rule-set that records the source, code, tests and flag of each rule | `compliance/rules.py:1-12`; `compliance/rulesets/in_sebi.json`; `docs/COMPLIANCE_IN.md` |
| Algo ID (R2) | Tag `<algo id>-<strategy>-<client id>-<leg>` on every entry, stop and exit; `orders.algo_tag` stored; an audit row per LIVE order; the owner sets the id on the Team page; LIVE refused without an id (flag) | `execution/tagging.py:1-24`; `db/models.py:940`; `compliance/algo_id.py:1-10, 73-74`; `team/routes.py:134-172`; `execution/signal_execution.py:73-77` |
| OPS throttle (R3) | A bucket per client, broker account and exchange; counts place, modify, cancel and stop. Exits wait in their own lane; entries over the limit are refused before the broker. 10 OPS comes from the rule-set | `execution/ops_throttle.py:1-15`; `in_sebi.json:58, 77`; `core/config.py:245-248` (flag at :247) |
| Broker API budget | A per-tenant budget per second and per minute | `brokers/rate_budget.py:1-12, 51-64` |
| Static IP (R5) | PRIMARY / BACKUP IP per broker, a change log, a cap of 1 change a week, a warning when two organisations share an IP, LIVE entry refused if the server IP is not registered (flag) | `compliance/static_ip.py:1-8`; `in_sebi.json:177`; `workers/trading_worker.py:1722`; `core/config.py:249-252` |
| Market protection (R11) | `market_protection` on MARKET / SL-M orders to Kite and Upstox (flag); a per-broker policy that maps a MARKET entry to a marketable LIMIT (Kotak Neo, Groww) | `core/config.py:678-686`; `compliance/order_policy.py:1-14`; `execution/router.py:263`; `in_sebi.json:144-155` |
| Daily login (R6) | Daily session expiry per broker (Upstox 03:30 IST), a pre-open reminder | `brokers/token_lifecycle.py:1-6, 115`; `brokers/login_reminder.py` |
| MWPL (R11) | F&O ban-period refusal from lake data (flag; status `partial`) | `compliance/fo_limits.py:1-8` |
| Go-live checklist | Evidence items: ISO 27001, CERT-In VAPT, incident register, white/black-box filing, AI RA gate, DPDP. Shown as LIVE-scope readiness items (algo id, static IP) | `compliance/golive.py:1-12`; `platform/readiness.py:216-229` |
| White box (R9) | One declarative DSL: conditions, stop and target are readable JSON with no hidden code. Strategy Lab code never trades | `docs/adr/0005-declarative-strategy-dsl.md:11-25`; `marketplace/service.py:6-11` (frozen config copied to the subscriber) |
| AI (ADR-0006) | AI holds no broker credentials and only writes DSL drafts. A draft needs a backtest and a human approval. The agent only proposes | `docs/adr/0006-ai-holds-no-credentials-acts-only-on-approval.md:12-18`; `ai/compliance_terms.py:41` ("not a SEBI-registered IA / RA") |
| RA gates | `marketplace_ai_listings` and `thesis_stock_targets` stay OFF until the RA/IA position is cleared. The private signal engine is admin / owner only and places no orders | `platform/controls.py:122-124, 224-228` |
| Signals, not advice | No buy / sell / target wording, the user's own settings only, white-box templates, "legal advice before LIVE" | `CLAUDE.md:27-33` |
| Kill switch | Kill switches per scope | `kill_switch/checks.py` |

## 3. Gaps and build items

Every enforcement flag defaults to OFF (`core/config.py:174, 244, 247, 252, 685`). That is correct for Phase 0, but LIVE
needs them ON. The items below follow the owner principle "build fully now, the LIVE switches are his alone".

| ID | Gap (against section 1) | Build item: one-line scope | Size |
|---|---|---|---|
| G1 | **Hosting / provider model (R7, R10, R5).** ATP runs every user's strategy on ATP's own server and sends orders through each user's broker API from one server IP. NSE says provider algos run on the broker's servers and an IP maps to one client. ATP's own code already warns about a shared IP (`compliance/static_ip.py:7`). The code cannot settle this alone | FN6-1: an architecture decision record with three options, (a) empanelled provider hosted at the broker, (b) per-user self-hosted runner on the user's own static IP, (c) signals only with manual orders; the choice waits for the owner and lawyer (A.6 discuss-then-build) | L (decision first) |
| G2 | **Algo ID placement (R2).** ATP puts the id in the free-text broker `tag`. The exchange algo ID and the NNF digit are set by the broker in the exchange message ([S4] 8.2.4). Whether any broker reads ATP's tag is **unconfirmed** | FN6-2: a per-broker field in the rule-set ("algo id: broker-set / API field / tag") from each broker's API documents; the tag is kept for reconciliation only; readiness says which applies | S |
| G3 | **Per-strategy algo registration and category (R8, R9).** There is only one `tenants.algo_id` per organisation and a manual white/black-box evidence item. No per-strategy algo ID, 5-level category, registration date or re-registration trigger | FN6-3: a strategy-version row with algo ID, category (white/black, execution type) and registration status; LIVE refused for an unregistered version; a new DSL version or segment marks it "re-registration needed" | M |
| G4 | **Market orders for algo entries (R11).** Rule-set default `market_for_algo: allow` (`in_sebi.json:144`) and `LIVE_MARKET_PROTECTION` off, while NSE says no algo order goes as a market order | FN6-4: when LIVE is on, the default is protection ON plus `map_to_limit` for entries; exits keep a protected market order (ADR-0004); a broker that accepts protected MARKET is a rule-set override | S |
| G5 | **Black-box exposure (R9).** The private signal engine (`platform/controls.py:224-228`) hides its logic from users by design. If its signals drive another user's orders, that is a black box. Marketplace listings of user strategies are distribution by a provider | FN6-5: a hard guard that refuses LIVE automation from any source not marked white-box and registered; engine signals stay NOTIFY / ONE_CLICK for other users until the RA question is answered; the marketplace stays OFF | S |
| G6 | **Automated 2FA (R6).** The worker logs TOTP brokers in by itself from a stored TOTP secret (`brokers/auto_login.py:1-6`). Whether server-side TOTP meets "the system shall authenticate client access through 2FA" is **unconfirmed** | FN6-6: a per-broker setting that turns auto-login off for LIVE until the lawyer and broker confirm it; LIVE needs a manual daily login then | S |
| G7 | **Audit retention (R12).** `log_retention_days: 365` (`in_sebi.json:235`), while the broker must keep 5 years. Whether ATP as agent must also keep 5 years is **unconfirmed** | FN6-7: order, tag and audit retention as a rule-set value; a LIVE-scope readiness item; an export of a user's full order trail with ATP's unique order numbering | S |
| G8 | **Mock sessions and RMS write-up (R13, R7, R8).** No monthly mock-session record and no RMS write-up or auditor-certificate evidence items | FN6-8: go-live checklist items for monthly mock participation, the RMS write-up, the auditor certificate (Annexure 4/7) and the half-yearly VAPT (data only) | S |
| G9 | **Stale rule-set dates.** `effective_from 2025-08-01`; the 30 Sep 2025 circular and the 1 Apr 2026 date are not recorded | FN6-9: rule-set version 2 with [S2] cited and dates corrected; docs/COMPLIANCE_IN.md regenerated | S |
| G10 | **Runaway / loop stop (R11 item 7) and per-client limits (R11 items 6, 12-15).** There is a throttle and kill switches, but no stated auto-stop when one strategy's order count runs away, and no cumulative open-order value cap | FN6-10: an order-count circuit per deployment (a rule-set value, never a trading rule) that pauses entries and never blocks exits; the open-order value cap is a user setting | M |
| G11 | **Charges disclosure (R14).** No disclosure page that shows ATP's fee and any broker sharing on the order screen | FN6-11: a fee-disclosure line in the order ticket and a plan page once a commercial arrangement exists | S |

## 4. Owner + lawyer steps before LIVE (questions, not conclusions)

Steps (owner):
1. Pick the broker(s) for LIVE and ask each in writing: does it accept third-party platforms, does it have an empanelled
   vendor programme, and what is its hosting model (on the broker's server, or vendor-hosted on a whitelisted IP)?
2. Decide the ATP model (G1) with the lawyer: empanelled algo provider / per-user self-host / signals only.
3. If ATP is a provider: NSE and BSE empanelment (Annexures 1-4 of [S4] / [S5]), ISO 27001:2022, CERT-In VAPT, then
   product and UI registration, then per-strategy registration through each broker (T+10 / T+7).
4. If any black box (G5): SEBI Research Analyst registration in the provider's name, a research report per algo, renewal.
5. Fill the go-live evidence (`compliance/golive.py`), set the algo id(s), register the static IP(s), turn the
   enforcement flags ON, and only then the owner's own `live_trading` switch.

Questions for the lawyer:
- Q1. Is ATP an "algo provider" under [S1] 5.I(a) / [S4] 3 when a user builds his own DSL strategy in ATP and ATP's
  server sends the orders through his own API key? Or is the user a "tech-savvy client" even though the code runs on
  ATP's server?
- Q2. Is a shared ATP server IP allowed for many unrelated clients ([S3] A7: one IP per client)? If not, which model is
  allowed: per-user IPs, broker hosting, or the user's own runner?
- Q3. Does [S4] 14 / [S6] Q5 ("all strategies run on the broker's servers") forbid ATP's vendor-hosted model outright
  for white-box algos too?
- Q4. Are user-built DSL strategies, the idea-library templates and marketplace copies "white box" ([S1] footnote 5)?
  Is the private signal engine, whose logic is not shown, "black box" when another user acts on it automatically or by one click?
- Q5. Does an RA registration change the "signals, not advice" position (CLAUDE.md §3) for signal cards without
  automation? Is the 'not an IA / RA' disclaimer (`ai/compliance_terms.py:41`) sufficient?
- Q6. Is a server-stored TOTP secret with automatic daily login acceptable 2FA under [S4] 10.7 / [S3] I(d)?
- Q7. Which retention period applies to ATP's own order and audit records (5 years for the broker under [S4] 10.3)? Does
  DPDP minimisation conflict with it?
- Q8. Does a subscription fee or brokerage sharing need disclosure under [S1] 5.III(d), and in what form?
- Q9. Is a single owner-only LIVE account (the owner trading his own money through ATP) a different case from offering
  LIVE to customers, and what is the minimum for that first step?
- Q10. Grievances: what must ATP contract with the broker, given that the broker handles algo complaints ([S1] 5.II(c))?
  What NDA / confidentiality terms apply ([S4] 13.1.4)?
- Q11. The BSE notices (operational framework 29 Aug 2025, empanelment 22 Sep 2025) were **not read in primary form**.
  Do they differ from NSE's on any of the points above?

## 5. Sources

- [S1] SEBI circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013, 4 Feb 2025 (primary, read in full):
  https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html ;
  PDF https://www.sebi.gov.in/sebi_data/attachdocs/feb-2025/1738665456458.pdf
- [S2] SEBI circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/132, 30 Sep 2025, extension and glide path, 1 Apr 2026 (primary):
  https://www.sebi.gov.in/legal/circulars/sep-2025/extension-of-timeline-for-implementation-of-sebi-circular-dated-february-04-2025-on-safer-participation-of-retail-investors-in-algorithmic-trading-_96979.html ;
  PDF https://www.sebi.gov.in/sebi_data/attachdocs/sep-2025/1759232056254.pdf
- [S3] NSE/INVG/67858, 5 May 2025, implementation standards (primary): https://nsearchives.nseindia.com/content/circulars/INVG67858.pdf
- [S4] NSE/INVG/69255, 22 Jul 2025, detailed operational modalities + Annexure I (primary):
  https://nsearchives.nseindia.com/content/circulars/INVG69255.zip
- [S5] NSE/INVG/70309, 19 Sep 2025, corrigendum + revised Annexures 1-4 (primary):
  https://nsearchives.nseindia.com/content/circulars/INVG70309.zip
- [S6] NSE FAQ, retail algo, 3 Nov 2025 (primary; NSE's disclaimer says the circulars prevail):
  https://nsearchives.nseindia.com/web/sites/default/files/inline-files/FAQ_Retail_Algo_03112025_NSE.pdf
- [S7] Zerodha support, static IP (broker page): https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/static-ip
- [S8] Zerodha Kite Connect API FAQs (broker page: market protection, 429 above 10 orders a second; the 429 detail is from a
  forum staff reply, **unconfirmed** in an official doc): https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/kite-connect-api-faqs ;
  https://kite.trade/forum/discussion/15912/preparing-to-comply-with-sebis-retail-algo-rules-static-ip-ratelimits-order-types
- [S9] Leads only, **unconfirmed** (secondary): the July 29 2025 SEBI circular number; the BSE notices 20250506-3,
  20250829-12, 20250922-19 and 20250930-66 as summarised at https://teamleaseregtech.com/updates/article/49769/bse-issued-an-adherence-reminder-on-retail-algo-trading-regulations/
  and https://rhnvrm.github.io/stock-market-circulars/ ; NSE circular NSE/MSD/67753 (29 Apr 2025, clause 8.1.1 item 12 and clause 8.1.2 item 1
  on market / IOC orders) is quoted from [S6] Q11 and was not read in full.
