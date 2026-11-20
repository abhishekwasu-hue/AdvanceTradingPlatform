# Terminal "Conclusion" panel — PLAN (roadmap TERM-CON; PLAN ONLY, no code until the owner approves)

Source: owner instruction 20 Nov (INBOX "Terminal Conclusion panel (decision agent)", DECISIONS 20 Nov TERM-CON).
His ask, in short: one window on the Terminal that shows the essence of everything ATP already reads (chart conditions,
option-chain / OI changes, his earlier positions, whether his orders went through) so a trading decision can be taken
from one panel; and "is there already something like this in ATP?".
Method: two independent read-only audits of the code (backend sources; Copilot, orders, risk, Terminal UI), key claims
re-checked by hand (file:line below). Discuss-then-build (OWNER_RULES A.6): this is a major architecture item.

Answer to "does ATP already have it?": **No single place.** The parts exist, spread over eight pages / routes. The
nearest things are the Copilot starters "Brief" and "My positions" (`backend/app/ai/copilot_chat.py:778`, `:1610`),
which gather several tools but for the market (NIFTY) or for positions, not for the Terminal's symbol + expiry, and
only when asked in the Copilot, which has no panel on the Terminal. The panel below reuses them; nothing new is computed
that ATP does not already compute.

---------------------------------------------------------------------------------------------------------------------

## 1. AUDIT — what the panel would read (all exist today)

Freshness legend: *req* = computed on request; *snap* = stored snapshot from a worker job.

| # | Source | What it gives | Route / function | Fresh | Flag | On the Terminal today? |
|---|---|---|---|---|---|---|
| S1 | Code chart reading | structure (swings, breaks, protected level), areas + lifecycle, Daily trend / pullback, commitment candle; last CLOSED bar only | `GET /api/charts/read` (`charts/read_chart.py:296`), pure core `read_closed` (:204) | req | `ui_v2` + `read_chart` | no (Ask AI "Analyze this chart" only) |
| S2 | Price-action layer (trend per degree) | swings, legs, real breaks, Dow trend per degree (up / down / range), fingerprint | `GET /api/charts/layers/price-action` (`charts/price_action_layer.py:685`) | cache 30 s (:87) | `chart_price_action` | as chart drawing |
| S3 | Market zones (buyer / seller areas, bases) | up to 6 bands, touches, strength, distance | `GET /api/charts/layers/market-zones` (`charts/zones_layer.py:62`) | snap (nightly job) | `market_zones` | as chart drawing |
| S4 | Analyze a chart / Chart reader (AI) | AI reading of the chart (structure, areas, RSI divergence) | `POST /api/chart-analyze/...`, `POST /api/chart-reader/read` | per closed bar / Redis cache; costs money | `chart_analyze*`, `chart_reader` | no |
| S5 | OI banner / regime | direction (bullish / bearish / neutral / mixed), put / call build-up class, PCR + band, diff and its change, max pain, DTE | `GET /api/option-chain/{u}/banner` (`option_chain/routes.py:140`); pure `oi_regime.evaluate_snapshot` (`oi_regime.py:771`) | snap, 5-min slots (`OI_BANNER_SLOT_MINUTES`) | auth only | yes, in the chain panel |
| S6 | Chain analyses | support / resistance by OI, build-up, walls, PCR, IV + IV rank, exposure | `GET /api/option-chain/analyses` (`option_chain/analyses_routes.py:149`) | live broker read per request | `ui_v2` (+ `markets_view` for the summary) | partly (chain panel) |
| S7 | His open positions | rows (mode, symbol, legs), marked P&L, loss if his stops are hit, bullish / bearish / neutral count | `GET /api/positions?mine=true` (`trading/routes.py:131`); `get_my_positions` (`ai/tools/copilot_trades.py:115`); Greeks `GET /api/positions/greeks` (:188) | req (Terminal polls 30 s) | none | yes (Positions panel) |
| S8 | His orders | today's ledger incl. rejected / failed, trail | `GET /api/orders?mine=true&today=true` (`trading/routes.py:587`), `/orders/{id}/events` (:614) | req (polls 30 s) | none | yes (Orders panel) |
| S9 | Broker state | broker_uncertain + since + reason, last reconciled | `GET /api/reconciliation/status` (`reconciliation/routes.py:78`) | req | none | yes since #1130 (`BrokerCheckV2`) |
| S10 | Broker's own book | his broker's positions / holdings / orders, read-only | `GET /api/me/broker-book` (`me/broker_book.py:198`) | cache 20 s | `ui_v2` + `broker_book` | no |
| S11 | Risk | his limits (per trade / daily), guardian drawdown + open risk by bucket, kill switch | `/api/paper/fno/risk-budget`, `/api/risk-guardian/status` (`risk_engine/guardian_routes.py:42`), `terminal/status.kill_switch` | req | `paper_account` / none | kill switch only (status bar) |
| S12 | His strategy signals on the chart | last_signal, markers, pending (forming bar = display only), position | `POST /api/strategies/v2/plot` (`strategy_v2/routes.py:103`) | req, rate-limited | `strategy_chart` | no (Charts page) |
| S13 | Signals inbox | his waiting proposals, scan matches, autopilot fills | `GET /api/signals/feed` (`signals_feed/routes.py:19`) | req | `ui_v2` | no |
| S14 | Events | market events, results calendar, holidays, his positions expiring | `/api/risk-guardian/events` (:55), `/api/fundamentals/companies/{s}/calendar`, `/api/fno/expiry-checklist` | req | none / `expiry_checklist` | no |
| S15 | My day | per position: nearest area, its last event, sessions to expiry, room to his own stop | `GET /api/today/my-day` (`today/my_day.py:266`) | req (zones snap) | `ui_v2` + `my_day_card` or `terminal` + `term_strike_area` | partly (strike-to-area) |
| S16 | Setup recall | his own similar past closed trades | `GET /api/setup-recall/trades/{id}` | req | `setup_recall` | no (keyed by trade, not symbol) |
| S17 | Context panel | market tiles: regime, breadth, VIX, FII/DII, global, events | `GET /api/context/panel` (`context_panel/service.py:313`) | req, daily | `context_panel` | no (market level, not per symbol) |

Facts that shape the design:
- **Same code path PAPER / LIVE:** positions, orders and `get_my_positions` read TradeRecord / OrderRecord with `mode`
  as a field; only reconciliation filters LIVE. The panel reads them unfiltered and shows the mode per row.
- **No look-ahead already:** S1 (`read_closed`, END <= request time), S2 (causal swings), S3 (`confirmed_at`),
  S12 (signals from closed bars, forming bar display only), S16 (`_closed_before`). S5 is pure, fails closed on stale PCR.
- **Guards exist:** `signals/language.py` (`check` :242, `strict_guard` :68, `withheld` :301); AI output:
  `ai/copilot.grounded` (every number must be in the facts) + `ai/output_filter.check` (:180).
- **His own key:** `ai/resolve.provider_for_user` (user key first under `ai_one_key`, caps, never the org key at the limit).
- **Gaps:** no per-user "risk used vs left" figure (only limits + loss-if-stops-hit); RSI divergence has no own route
  (it is in `charts/workspace_reading.py:140`); no server bar-close push (the browser has `useBarCloseReload`,
  `frontend/src/live/livePush.ts:53`); no symbol filter on the events route.
- **Terminal:** panels in `frontend/src/terminal/layoutModel.ts` (PanelId :22, PRESETS :83, PHONE_TABS :781); symbol =
  `working.underlying`, expiry = `useOptionChain`; panel cases in `TerminalPageV2.tsx:236+` and `TerminalClassicV2.tsx:207`.
  Polls today: status 15 s, positions / orders 30 s, strip 60 s, chain 15 s in market hours.

---------------------------------------------------------------------------------------------------------------------

## 2. WHAT THE PANEL IS (and is not)

- One Terminal panel, **"Conclusion"**, for the Terminal's symbol + expiry (follows the context bar; no own picker).
- It **collects and lines up facts**; it does **not decide**. No buy / sell / target / stop-loss wording, no single
  price, no score, no "take this trade". "Conclusion" = what the readings say together: what agrees, what conflicts,
  what needs him. The decision stays his (CLAUDE.md 3).
- It never places, blocks or changes an order. It has no order button (links only: "Open positions", "Open order trail",
  "Check with <broker>" = the existing #1130 action). Exits are never blocked (ADR-0004); AI never acts (ADR-0006).
- Numbers only from his own settings (stops, limits, strategy) or from the market; ATP adds no rule. Where he has no
  setting: "Not set in your settings", never a default.

---------------------------------------------------------------------------------------------------------------------

## 3. ARCHITECTURE

```
Terminal (symbol, expiry, chart TF)                     backend/app/conclusion/   (new package)
  ConclusionPanelV2 ── GET /api/terminal/conclusion ──▶ routes.py  (ui_v2 + terminal + terminal_conclusion)
        ▲                 ?symbol&expiry&tf               gather.py  calls the EXISTING services in-process
        │  refetch only on a trigger (section 5)          rules.py   pure: facts -> lines, agrees, conflicts, needs-you
        │                                                 model.py   pydantic response
        └── "Summarise" (tap) ─ POST .../conclusion/summary ─▶ summary.py  his own key, grounded + guards
```

- **gather.py** calls the service functions behind S1-S3, S5, S7-S9, S11-S12, S14-S15 (not HTTP; same functions the
  routes use, so each keeps its flag check: a source whose flag is OFF returns `status: "off"` and is hidden). Every
  source runs with its own timeout; one failure never fails the panel (`status: "na"`, plain reason).
- **rules.py** is pure (facts in, lines out; no DB, no clock: `as_of` is passed in). It is the deterministic part.
- AI chart reading (S4) is **not** called by the panel (cost per call). The panel shows "Analyze this chart" as a link to
  the existing flow.
- No migration. No new table. Cache in Redis (section 5).

### 3.1 Response (`atp.conclusion/1`)
```
{ symbol, expiry, tf, as_of, mode_note,
  sections: [ { key: chart|oi|positions|orders|signals|risk|events,
                status: ok|stale|na|off, source, as_of, lines: [ { text, tone: up|down|neutral|warn, source, as_of } ] } ],
  readings: [ { source, tf?, direction: up|down|range|mixed|none, as_of } ],   # one per source / per TF, never merged
  agrees:    [ { text, sources[] } ],
  conflicts: [ { text, sources[] } ],
  needs_you: [ { text, action_link } ],            # failed order, broker uncertain, position expiring, his stop near
  fingerprint }
```

### 3.2 Sections (each line carries its source and time)
| Section | Lines (structure / indicator words only) | Sources |
|---|---|---|
| Chart | trend per TF ("15m: higher highs and higher lows since <time>"), last real break, protected level as an area (range), nearest buyer / seller area as a range + distance, RSI divergence if found, commitment candle on the last closed bar | S1, S2, S3 |
| OI | regime direction + strength, put / call build-up class, diff and its change since the last slot, PCR + band, highest-OI walls (strike ranges), max pain, DTE, IV rank | S5, S6 |
| My positions | per position: mode (PAPER / LIVE), legs, marked P&L (green / red), distance to his own stop (or "Not set in your settings"), nearest area, sessions to expiry; total loss-if-stops-hit | S7, S15 |
| My orders | today's orders on this symbol: placed / filled / rejected / failed with the broker's reason; pending count | S8 |
| Broker | "Books agree" or "Broker state uncertain since <time>" + the existing "Check with <broker> now" | S9 (S10 when `broker_book` is ON) |
| My signals | each of HIS saved strategies on this symbol: last signal (side in structure words, bar time), pending condition (forming bar, marked "not closed"), waiting proposals | S12, S13 |
| Risk | his limits (per trade / daily) and open risk by bucket; kill switch state; "Not set" where unset | S11 |
| Events | results date (stocks), market events this week, holiday, expiry today / next session | S14 |

### 3.3 Agrees / conflicts (deterministic, rules.py)
- Each source gives one **reading direction**, taken AS IS from that source's own output (no new threshold):
  chart trend per TF (S2 Dow trend: up / down / range); OI regime (S5 `direction`); each of his strategies' last
  signal (S12); his net position bias (S7 bullish / bearish / neutral count).
- Pairs with the same direction go to **agrees**, opposite directions to **conflicts** (e.g. "15m structure up · OI
  regime bearish (5-min slot 11:25)"). Range / mixed / none are listed, never counted as either.
- Timeframes are never merged (Z-1u): 15m and 1h are separate readings.
- **No score and no weighting.** The one visual cue on top is a plain count of these readings (the lean cue and the
  confluence meter, section 6A; TC-Q1, TC-Q5).
- **needs_you** only from facts: a failed / rejected order today, broker uncertain, a position expiring today / next
  session, price inside the area of his own stop (his setting), a kill switch engaged.

### 3.4 Optional AI summary (on top, his own key)
- A "Summarise" button (not automatic). Input = the deterministic response only (no new data).
- `ai_resolve.provider_for_user` → his key, his caps; at the limit / no key: no summary, the deterministic panel stays.
- Output passes: `copilot.grounded` (every number in the facts), `output_filter.check`, `signals/language.check` +
  `strict_guard` for the surface; refused twice → "Summary not available", nothing else shown. Cost shown per tap.
- Prompt: describe what agrees and conflicts; never recommend, never name an entry / exit / target; English (EN / MR / HI
  only via the existing language path).

---------------------------------------------------------------------------------------------------------------------

## 4. DETERMINISTIC vs AI
| Deterministic (always, free) | AI (optional, his key, on tap) |
|---|---|
| gather, every section, readings, agrees / conflicts, needs-you, colours | a 3-5 sentence summary of the deterministic response |
| tested by pure unit tests and parity with the source routes | guarded; can only be shown or withheld, never adds a number |

---------------------------------------------------------------------------------------------------------------------

## 5. REFRESH (no polling storm)
- The panel has **no timer of its own**. It refetches on one of these triggers, debounced 2 s, one request in flight:
  1. bar close of the Terminal chart's TF (existing `useBarCloseReload`, data hub ON; without the hub: the next
     already-running 15 s status poll that shows a new bar);
  2. a new OI slot (the chain panel's own read already returns the slot time; a change triggers);
  3. a change in the positions / orders lists the Terminal already polls every 30 s (signature compare, no extra call);
  4. an SSE `notifications` / `ai_actions` ping (existing `/api/live/stream`);
  5. symbol / expiry / TF change, or the refresh button.
- Server: Redis cache keyed by (user, symbol, expiry, tf, last closed bar, OI slot, positions+orders signature), TTL 60 s;
  a repeat request with the same key returns the stored response (`fingerprint` unchanged → 304-style "same").
- Hidden tab / panel not in the layout: no requests. Market closed: one read, then only on triggers 3-5.
- Expected load: about one read per bar close plus one per 5-min OI slot per open Terminal; the broker is not called more
  than today (S6 live chain is the only broker read and it shares the chain panel's cache — to verify in C0).

---------------------------------------------------------------------------------------------------------------------

## 6. LAYOUT (tokens only: text sizes from tailwind `t12..t32`, colours from `styles/tokens.css`; `check:v2-colours`)

Desktop (panel, default in "Option seller" and "Quick trade" presets, right column under the chain; resizable):
```
┌ Conclusion · NIFTY · <expiry> ──────────────── as of <time> ⟳ ┐
│ NEEDS YOU (red)  1 order failed today · Open trail            │
│ AGREES  (green)  15m structure up · OI regime bullish · your  │
│                  strategy "X" last signal up (<time>)         │
│ CONFLICTS (amber) 1h structure range · PCR band low           │
│ ─ Chart ──── 15m up since <time> · area <low>-<high> below      │
│ ─ OI ─────── bullish (slot <time>) · put writing · PCR <x>   │
│ ─ My positions 2 PAPER · P&L <+x> · to your stop: <d>         │
│ ─ My orders ── 3 filled · 1 failed (product invalid)          │
│ ─ Broker ───── books agree (<time>)                          │
│ ─ Events ───── results none · expiry in 4 sessions            │
│ [Summarise with AI · cost <c>]          [Analyze this chart ›] │
└───────────────────────────────────────────────────────────────┘
```
Sections fold (details); the three top rows stay. Each line: source + time on hover / a muted suffix.

Phone 390 (new tab "Conclusion" in PHONE_TABS): top three rows as cards, sections as accordions, buttons full width;
no sideways scroll (PHONE-1 guard).

---------------------------------------------------------------------------------------------------------------------

## 6A. GLANCE DESIGN (owner follow-up 20 Nov: "eka najaret ... ishara kafi hota hai")

Goal: the trader reads the panel in **under 2 seconds** without one buy / sell word. The cue is visual and is built
only from the tokens. The words underneath stay in structure language, and the details sit behind a tap.

**Glance strip** (always visible; the top of the panel on desktop, the top card on phone):
1. **Lean cue.** It shows an arrow with a matching shape and colour:
   - ▲ up in `--up`, ▼ down in `--down`, ◆ sideways in `--range` / `--info`;
   - the word "Lean" and its count ("3 of 4") always sit beside it.
   It is computed only from the readings in 3.3:
   - the majority direction of the directional readings;
   - a tie, or fewer than two directional readings, gives ◆ sideways.
   It is chip-sized, never a hero-sized verdict. How strong it may look is SEBI-sensitive: TC-Q5.
2. **Confluence meter.** One segmented bar with one segment per reading:
   - filled in `--up`, `--down` or `--range`; each segment also carries its arrow glyph, so the meter never relies on colour alone;
   - the numbers on it: "3 agree · 1 conflicts · 1 range".
3. **Source chips.** One chip per source, in this order: Chart structure, OI, Strategy signal, Context, Positions, Orders.
   - Each chip has a lucide icon, a direction glyph (▲ ▼ ◆, or — for none) and a token colour.
   - Chart structure: one chip per TF, never merged.
   - Positions: the net bias of his open legs.
   - Orders: amber `--warn` with ! on a failed or rejected order or when the broker is uncertain; otherwise neutral ✓.
   - A stale source has a dashed outline and a muted colour, and shows its age on tap.
   - Tapping a chip opens that section.
4. **One structure line.** One sentence, at most about 90 characters, built from the facts by rules.py (not by AI), for example:
   - "Buyers holding above area <low>-<high> · OI supports"
   - "Structure up on 15m · 1h range · OI mixed"
   The line passes `signals/language.check`. The area is shown only on his own Terminal (his own surface, CLAUDE.md 3).
5. **Needs you.** A red / amber row, shown only when something needs him: a failed order, the broker uncertain, or an expiry today.

**Colour-blind and contrast:**
- `data-cvd="on"` swaps up / down to blue / orange (`styles/tokens.css:73-74`). The glyph and shape always carry the same meaning.
- Text and icons meet WCAG AA on both themes.
- The CI guards `check:v2-colours` and the prose guard both cover the new component.

**Desktop mockup** (strip above the sections of section 6):
```
┌ Conclusion · NIFTY · <expiry> ───────────────────── <time> ⟳ ┐
│  ▲ Lean up  3 of 4     [▲][▲][▲][▼][◆]  3 agree · 1 conflicts │
│  (▲ 15m)(◆ 1h)(▲ OI)(▲ Strategy)(— Context)(▼ Positions)(✓ Orders) │
│  Buyers holding above area <low>-<high> · OI supports           │
│  ! 1 order failed today · Open trail                            │
│  ▸ Chart  ▸ OI  ▸ My positions  ▸ My orders  ▸ Events  (tap)    │
└─────────────────────────────────────────────────────────────────┘
```

**Phone 390 mockup** (Conclusion tab; the strip fits without scrolling; chips wrap onto two rows and never scroll sideways):
```
┌──────────────────────────────┐
│ NIFTY · <expiry>   <time> ⟳  │
│ ▲ Lean up · 3 of 4           │
│ [▲][▲][▲][▼][◆]              │
│ (▲15m)(◆1h)(▲OI)(▲Strat)     │
│ (—Ctx)(▼Pos)(✓Orders)        │
│ Buyers holding above area    │
│ <low>-<high> · OI supports   │
│ ! 1 order failed · Open      │
│ ▸ Chart        ▸ OI          │
│ ▸ Positions    ▸ Orders      │
│ [ Summarise with AI · <c> ]  │
└──────────────────────────────┘
```
Proof in C2:
- screenshots at 390, 820 and 1440, in dark, light and cvd;
- the phone layout guard (no sideways scroll) and a contrast check;
- a "glance test": the strip is fully visible above the fold at 390 x 844.

## 7. PHASES (one PR each, behind new flag `terminal_conclusion` under `ui_v2` + `terminal`, OFF; migration: none)
| Phase | Scope | Proof |
|---|---|---|
| C0 | `conclusion/` gather + response model + route; sections Chart, OI, Positions, Orders, Broker (sources S1-S3, S5, S7-S9) | unit + route tests; parity: each section's numbers = the source route's |
| C1 | rules.py: readings, agrees / conflicts, needs-you; sections Signals, Risk, Events (S11-S15) | pure tests incl. no-look-ahead fixtures |
| C2 | ConclusionPanelV2 + layout registration (both Terminal variants, presets, phone tab) | before / after page 390 + 820 + 1440, dark + light |
| C3 | refresh triggers + server cache | test: N ticks inside a bar → 0 requests; bar close → 1; cache hit on same key |
| C4 | AI summary (his key, guards, cost) | guard tests (advice words, unknown number → withheld), no-key / at-limit states |

## 8. TESTS AND ACCEPTANCE
- No look-ahead: fixtures where a forming bar would change the trend / a later OI slot exists → the panel shows only the
  closed-bar / current-slot reading; replay at time t equals the live read at t.
- PAPER = LIVE: the same fixture with mode PAPER and LIVE gives identical lines except the mode label.
- Language: every user string through `signals/language.check` in CI (new test lists all templates); the English-only
  and prose guards pass.
- Safety: the panel's routes are GET-only except the summary; a test asserts no order / broker write is reachable from
  `conclusion/`.
- Failure: each source down / OFF → that section "na" / hidden; the panel still renders.
- Acceptance (owner): on the real site, on PAPER, NIFTY in the Terminal, desktop + phone: the panel shows his positions,
  today's orders incl. a failed one, the chart and OI readings with times, agrees / conflicts; it updates at a bar close
  without a page reload; owner ✅.

## 9. RISKS
| Risk | Mitigation |
|---|---|
| Reads as advice ("conclusion" = verdict) | no score, no recommendation; signal-language guard on every line; legal review before LIVE (CLAUDE.md 3) |
| Load / broker calls | trigger-based refresh, shared caches, one request in flight, nothing when hidden |
| Stale facts shown as current | every line has its time; a source older than its own freshness → "stale" tone |
| Drift from the source pages | gather calls the same functions; parity tests |
| AI adds a number or advice | grounded + output filter + strict guard; withheld, never edited |
| Too much text on phone | three summary rows first, sections folded (OWNER_RULES D.2) |

## 10. OPEN QUESTIONS (logged in docs/OPEN_QUESTIONS.md, each with a default)
TC-Q1 tally line (now the confluence meter) · TC-Q2 which chart TFs · TC-Q3 AI summary on tap or automatic ·
TC-Q4 default placement · TC-Q5 how strong the single lean cue may look (SEBI).
