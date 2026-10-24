From Abhi — THE ATP TRADING TERMINAL (a dedicated "Terminal" tab: manual trading + algo control on one screen), design + coding order (English)

Status: this order REPLACES Part 6 ("Trade terminal") of ATP_SESSION_TERMINAL_MASTER.md and MOVES it forward: the Terminal is built right after Part 1 (data hub). Everything else in the master order stays as it is and keeps running in parallel (smart execution, Kotak Neo / Groww, chain history, etc.).

## 0. Record first, then review
- docs/owner-orders: INBOX (verbatim), a new README row (with "supersedes master Part 6; moved after Part 1"), and DECISIONS:
  - T-D1: the Terminal tab is visible to every USER. The default layout for a new user is the simple "Option seller" preset, with a page tour.
  - T-D2: every option leg / spread exit can trigger on the PREMIUM or on the UNDERLYING (spot) price. This is a per-order choice plus a user default in Settings. For spreads the shipped default is "spot".
  - T-D3: the Terminal comes right after Part 1 (data hub). Master Part 6 is superseded.
- An independent review of this order against the real code (a separate agent, CLAUDE.md §6), then fix the findings. Report the review to Abhi in Marathi before Part T1 starts.
- Behind `ui_v2` plus a new flag `terminal` (default OFF until Abhi's ✅ on the screenshots). Keep what is built: REUSE, never rewrite. Use OrderTicketV2, ChainTradingV2, ChainActionSheetV2, BasketPanelV2, SmartExecutionV2 (hedge first, freeze slicing, INCOMPLETE state), CockpitDeskV2 / desk.ts, PositionsV2, the chart core (src/chart/chartCore.ts), the PAPER execution adapter (TM-1), ExitRules, and the data hub.
- No paper / live split (owner principle). Every feature works today on the PAPER adapter and LIVE unchanged when Abhi switches LIVE on.

Reference research (ideas only, never copy a look): Sahi Scalper 2.0, Dhan Power Scalper, Fyers Scalper Terminal, Zerodha Kite Terminal Mode, Upstox (MTM view, Exit all), NeoPlus for Kotak, AlgoTest (lock and trail). ATP keeps its own design system (blue accent, colour by meaning).

## T1. The workspace
- **Navigation:** a top-level "Terminal" entry in the Trading group (sidebar, More sheet). Route `/terminal`.
- **Desktop layout:** a grid of dockable panels. The user can move, resize, open and close each one.
  - Panels: Chart (1–4); Option chain (compact ladder around the ATM, with the user's own strikes marked); Order panel; Positions (grouped, as in master Part 4); Orders (open / executed / rejected, with the order trail); Basket; Bots & signals (T5); Payoff / what-if (T4).
  - A fixed bottom status bar: total MTM (today), the MTM of open positions, funds available, margin used, the data-feed state (live / reconnecting / delayed), the mode chip (Paper / Live), the kill switch, and the privacy mask (₹••••).
- **Layouts:**
  - presets "Option seller" (default), "Quick trade" and "Multi-chart";
  - the user can save up to 10 of his own, rename them, switch in one click, and reset to a preset;
  - stored on the server per user (not only localStorage), so a layout follows him to another device.
- **Phone (360 / 390):** the same panels as tabs (Chart · Chain · Order · Positions · Orders · Bots), one panel at a time. The status bar shrinks to MTM + funds + mode. Every target is at least 44 px. The chart gesture rules of the chart order apply.
- **Empty and loading states** are in plain words (e.g. "Connect your broker to see live prices"). No developer text and no ids.

## T2. Fast manual trading (every order through the ONE order path)
- **Every entry point** (chart, chain, order panel, keyboard, basket) creates the SAME order object. That object goes through the same risk checks, confirmations, idempotency keys, smart execution (hedge first, freeze slicing, INCOMPLETE state) and order trail as OrderTicketV2. No second order path.
- **Trade from the chart:**
  - right-click or long-press at a price → Buy / Sell limit or SL order at that price;
  - open positions and pending orders are drawn as lines;
  - drag a line to modify the order, or to set or move the stop / target (the approved modify path);
  - an × on the line cancels the order / removes the stop. An exit line can never be "dragged away" so that no exit remains without a confirm.
- **Split view:** the underlying chart next to the chosen option's chart (CE / PE / the spread's combined premium), with the crosshair and timeframe synced (a toggle).
- **Trade from the chain:** tap a bid / ask cell → the order panel prefilled. Build multi-leg orders straight into the basket from the chain.
- **Quantity:** always in lots, with the lot size shown. The user's default lots per underlying (Settings → Trading defaults). Quick buttons ×1 / ×2 / ×5 lots, editable.
- **Limit price helpers:** Bid, Ask, Mid, LTP, ± tick buttons. The 1-tap exit uses the user's default exit type (Market or Limit at Bid / Ask / Mid). A limit exit that does not fill within the user's set time is re-priced or converted to market, per his setting, and shown in the trail.
- **Partial exit and reverse:** exit N of M lots; "reverse" means exit plus an opposite entry, which always needs a confirm.
- **Each trade tracked on its own:** each fill is a separate trade line (entry, its own stop / target, its own P&L), never merged into a day average. The grouped view stays available.
- **Funds always visible:** the pre-order margin (with the hedge benefit, from the broker margin API where available) and the free funds AFTER the order, shown on the order panel before Place.
- **Keyboard shortcuts (desktop):** the user can edit them; they are listed in a "?" sheet, and there is a "shortcuts on / off" switch. Defaults:
  - B / S open a buy / sell on the focused instrument;
  - Enter places (with the confirm unless the user turned confirms off);
  - Esc closes;
  - X exits the focused position (confirm);
  - Shift+X = Exit all (confirm);
  - F2 modifies.
  - Shortcuts never fire while typing in an input.
- **Confirms:** ON by default. A user may switch confirms off per order type in Settings. "Exit all" and "reverse" ALWAYS confirm.

## T3. Exits for option sellers (ATP's edge: not available on most brokers)
- **T3.1 Exit trigger on the underlying (spot) or on the premium (T-D2).**
  - For any option leg, spread or position group the user chooses the trigger source: "premium" (the option's price) or "underlying" (e.g. "NIFTY closes above 24,650 on the 5-minute chart" / "NIFTY trades above 24,650").
  - The candle timeframe and "close vs touch" are user settings. Spot comes from the data hub.
  - Executed by ATP's own trigger engine (the platform trigger engine of master Part 7, the same semantics in backtest = paper = live, parity tests). When the broker has a native order for the premium case, it may be used.
  - The UI says plainly that an underlying trigger is held by ATP, not by the broker, and that it needs ATP's server running. Health is shown on the status bar.
  - A heartbeat check: a stalled trigger engine raises an alert (Telegram + in-app), never a silent miss.
- **T3.2 Group exits:**
  - for a spread / strategy group: a combined-premium stop and target, an MTM stop / target in ₹, "lock profit at ₹X" and "lock and trail" (every value the user's own);
  - the same at account level for the day (all positions);
  - shown in words + ₹ on the group ("Exits if the spread's premium reaches ₹…, or NIFTY closes above …").
- **T3.3 Trailing stop:** user-defined (step, start after ₹ / points in favour, trail by points or %), for premium or spot triggers. Parity tests.
- **T3.4 Exits are never blocked (ADR-0004).** No lock, limit, kill switch or failed check may stop an exit, Exit all or a stop from firing.

## T4. Option-seller tools inside the Terminal (reuse master Parts 4, 9, 11)
- **Live payoff of the basket / selected group**, with what-if (days forward, IV change, price slider), break-evens, the most you can lose (₹) and the margin.
- **Charts:**
  - the ATM straddle premium chart;
  - premium decay of the user's position (CE, PE, combined);
  - portfolio MTM vs spot on one chart;
  - IV with the India VIX overlay.
- **Spread from the chart (a tool, not advice):**
  - the user selects a level / area on the chart and picks "bear call above" or "bull put below";
  - the basket is pre-filled ONLY from his own settings: expiry rule (weekly; next weekly on expiry day), short-strike rule, hedge width, lots, exits;
  - nothing is suggested by ATP; if a setting is missing, the panel asks him to fill it;
  - he reviews and places from the basket.
- **Roll in one tap:** roll a group to the next weekly or another strike, shown as a what-if (credit / debit, new break-evens, risk, margin), then opened as a basket to review. Never automatic.
- **Expiry helpers:** positions expiring today; the physical-settlement warning for stock F&O (master Part 7).

## T5. Algo on the same screen
- **Bots & signals panel:**
  - every running paper / live bot and strategy with its status, its MTM, and Freeze (no new entries; exits keep working), Reduce, Force stop, Exit all for that bot (master Part 5, reused);
  - signals waiting for approval with Approve / Skip and the countdown, exactly as on the Signals page (the same component).
- **Positions show their origin:** a "Manual" or "<bot name>" tag. Filters manual / bot / all. Exit all can be scoped (all / manual only / one bot), always with a confirm.
- **On the user's OWN signals only:** the chart shows the signal's area / line as a band (structure words; the symbol and the area as a price range are allowed, CLAUDE.md §3). No buy / sell / target wording from ATP.
- AI (Copilot) can explain and answer risk questions inside the Terminal. It never places, blocks or changes orders (ADR-0006).

## T6. Discipline tools (the user's own limits; trader-friendly)
- **User settings, all optional, all OFF until he sets them:**
  - daily loss limit (₹);
  - max new trades per day;
  - cool-off after N losses in a row (no new entries for the time he sets);
  - max lots per order / per underlying.
- **When a limit is hit:** NEW entries are refused with a plain reason and the time it lifts. Exits, stops, Exit all and bot exits keep working (T3.4). The limits cannot be raised for the rest of that day from the Terminal (a deliberate friction, shown in words); lowering them is always allowed.
- **Before Place:** the most this order can lose (₹, where defined), the margin and the free funds after, and the risk-limit check in words.
- **Kill switch** on the status bar: stops all new orders (manual + bots). Exits stay available.
- **Privacy mask** (master Part 4) on the status bar.
- **Journal:** every manual trade is journaled automatically with a chart snapshot at entry and exit, the order trail and the user's optional note (the existing journal).

## T7. Practice mode
- The same Terminal runs on Market Replay (master Part 10): pick a past period, play / pause / step / speed, place paper orders, see P&L.
- The reader overlay appears as it forms (strictly causal).
- A clear "Replay" chip on the status bar; replay orders never reach a broker.

## T8. Speed and reliability (measured, in the PR)
- All prices come from the data hub websocket (no polling from the Terminal).
- Targets (measured in the PR, desktop + a mid-range phone):
  - a tick reaches the screen in under 300 ms after the hub receives it;
  - an order click reaches the adapter call in under 150 ms;
  - 60 fps chart pan with 5,000 bars;
  - no layout shift when panels update.
- Show and test these states:
  - reconnecting;
  - token expired (one renew prompt, everything resumes);
  - broker rate limit (orders queued with a visible state, never dropped silently, never duplicated — idempotency keys);
  - partial fills;
  - a rejected leg (the INCOMPLETE state with "complete / undo").
- Broker order-rate limits (e.g. Kotak Neo 10 / sec) are respected by the order queue.
- Do NOT claim exchange-latency numbers like a broker. Say only what is measured.

## T9. Compliance and safety (CLAUDE.md §3–4)
- **Signals, not advice:** ATP never suggests strikes, lots, stops or targets. Every value comes from the user's own input or settings. No performance claims.
- LIVE stays exactly as Abhi sets it. The Terminal shows the mode and never changes it.
- Static IP / algo rules: add to the lawyer's list (A6) "manual orders placed through ATP's API connection: tagging and static-IP requirements per broker".
- No secrets, server addresses or strategy-sensitive content in the public repo.

## T10. Tests and proof
- **Unit tests:** the order object from every entry point is identical; trigger engine (spot / premium, close / touch, trailing, group, lock and trail) with parity backtest = paper = live; discipline limits never block exits; shortcuts don't fire in inputs; layout save / restore.
- **e2e (Playwright), desktop + phone:**
  - chart trade with line drag;
  - chain → basket → place;
  - partial exit;
  - Exit all scoped;
  - limit hit → new entry refused, exit allowed;
  - kill switch;
  - reconnect;
  - layouts.
- **Screenshots** 360 / 390 / 1440, light / dark, before / after, in ONE comparison page for Abhi. Plus short screen recordings of chart trading and of the line drag. No flag ON before Abhi's ✅.

## Order of work (one `claude/<name>` branch and one draft PR per part)
1. Review of this order against the code → fixes → a report to Abhi.
2. T1 (workspace, layouts, status bar) on the data hub.
3. T2 (fast manual trading).
4. T3 (spot / premium triggers, group exits, trailing) — with the trigger-engine parity tests.
5. T5 (algo panel) + T6 (discipline tools).
6. T4 (option-seller tools).
7. T7 (practice on replay, when master Part 10 lands).

Every PR: self-review of the diff, tests, screenshots, the deploy line, and whether a migration is needed (layouts and the trigger store probably need one: after 15:30 IST). Report to Abhi in Marathi after each part.
