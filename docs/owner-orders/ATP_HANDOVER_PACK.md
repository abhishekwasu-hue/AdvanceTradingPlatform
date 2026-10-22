ATP HANDOVER PACK — for a new ATP Claude Code session (English)
From Abhi. This pack contains everything still pending for ATP (abhishekwasu-hue/AdvanceTradingPlatform), in priority order. Three larger orders are attached as separate files: ATP_SESSION_FNO_WORKSPACE.md, ATP_SESSION_UX_AUDIT_MASTER.md, ATP_SESSION_GROWTH_MASTER.md.

STEP 0 — FIRST, before any coding
1. Read the repo: docs/WORK_LOG.md, docs/OPEN_QUESTIONS.md, docs/deploy/HOSTINGER_RUNBOOK.md, docs/design/UI_V2_PROPOSAL.md, the git log since PR #300, and all open PRs (#277, #303, #304, #305, and any newer ones). Some items below may be partly done (e.g. the Broker page #328, the Option chain #326 / #327). For each item, say done / partial / not started before building.
2. Create CLAUDE.md at the repo root with the STANDING RULES below (Claude Code loads it every session). Open it as the first PR (docs only).

STANDING RULES (put them in CLAUDE.md)
Product
- ATP is a business SaaS for retail and beginner traders. 100% user-friendly is the top rule: a user logs in to work; show what he needs, make the important things stand out, hide or remove everything else. No developer text, internal ids, sample data or technical terms outside "Advanced" / the admin console.
- Exactly TWO groups: USER (the trading view, the same for beginners and pros; beginners learn through the page tours) and ADMIN (SUPER_ADMIN: everything technical: system, flags, users / business, testing tools, diagnostics, "view as user"). Enforced on the server.
- Every page gives a "ready-made dish": prepared, personal information on open (Master Order 4).
- Think proactively: when building a feature, research what brokers / Sensibull / StockEdge / TradingView offer and add what makes trading easier. Never only the literal minimum.
- English only in code and UI (except the strategy interview: EN / MR / HI). Reports to Abhi in plain Marathi.
Safety
- Phase 0: PAPER only. live_trading OFF, phase0_paper_only ON. LIVE untouched without Abhi's explicit words. Exits are never blocked. AI never places, blocks or changes orders.
- Information and education, never buy / sell advice for general users (SEBI; the compliance pack BT7 decides later).
- No secrets in git / logs / chat. The repo is PUBLIC: no strategy-sensitive content (KB, Method Pack, golden fixtures, charts, journal); the private method lives in atp-private.
- Never run anything on the server yourself. Abhi runs ONE consolidated command (Hostinger: `su - atp -c "/opt/atp/deploy/atp.sh deploy && /opt/atp/deploy/atp.sh health"`); say whether a migration is needed (if yes: after 15:30 IST, or MIGRATION_FORCE=1 only when Abhi says so).
Trading logic (when ATP ports the Trade reader)
- Abhi is a price-action trader, not an intraday trader; chart reading by structure, no day / date / time framing, no fixed numbers in reading rules (only in settings). R:R / SL / target / lots / exits are the user's own settings; never add trading rules nobody stated. RSI divergence is the only indicator in Abhi's method.
Work style
- Self-review every diff before pushing; tests for everything; before / after screenshots (desktop + phone, light + dark) for UI.
- Work in parallel worktrees; never sit idle; log questions in OPEN_QUESTIONS with a provisional default and continue. No frequent "still waiting" check-ins (they cost credits); report when something real is ready.
- Keep what is built; extend, don't rewrite.
Environment facts
- Production: https://aialgodesk.tech (Hostinger KVM 2; host details in docs/deploy/HOSTINGER_RUNBOOK.md and the owner's notes, not here). www redirect live. R2 off-site backup not set yet (owner).
- Upstox: ATP must use its OWN Upstox app / API key (never the Trade bot's: a new login there kills the bot's token). The Analytics Token (1 year, data only) is the default connection for data and PAPER.
- Flags turned OFF by Abhi on 21 Oct: self_signup, scan_auto_paper, marketplace, public_api. ui_v2 is ON.

PRIORITY ORDER (each a small PR series; deploy line after each merge)
1. Font size (a small PR)
2. Option chain: analysis + Sensibull-style charts
3. F&O workspace (attached file ATP_SESSION_FNO_WORKSPACE.md)
4. Default watchlists
5. AI Copilot: a professional trader's brain
6. Master Order 2: AI keys + page tours
7. Front page P3 (if not merged: open its PR; the branch was ready)
8. Master Order 3: the full UX audit (attached file ATP_SESSION_UX_AUDIT_MASTER.md) + its additions below
9. Master Order 4: the ready-made dish, the daily rhythm, plans (attached file ATP_SESSION_GROWTH_MASTER.md)
10. Analyze a chart (#304 / #305 drafts + the result-screen spec)
11. Strategy Lab for expert users: Pine Script + Python (below) + the AMW port status audit
12. Idea Library templates: full coding spec in the attached file ATP_SESSION_IDEA_LIBRARY.md (after 11k / 11l)
13. Strategy Template Library (price action, breakout, breakout + retest, Supertrend / MA / RSI / MACD / ADX / Bollinger / VWAP …): full design + coding spec in the attached file ATP_SESSION_STRATEGY_TEMPLATES.md. The idea library's T1–T8 are its family A.
Also open: the login error message bug; Google sign-in (if not done); the Upstox OAuth redirect fix (if not done).

────────────────────────────────────────
1. FONT SIZE
The overall font size is far too small. Fix it in the design system, not page by page:
- body text at least 16px on desktop and phone; secondary text no smaller than 14px; table / chain cells at least 14px; headings and key numbers (P&L, LTP, positions) clearly larger and bold; scale the spacing and the tap targets with it;
- "Text size" in Appearance: Normal (the new default) / Large / Extra large, stored per user on the server, applied with one root scale;
- chart axis / price / crosshair labels follow it;
- nothing clipped, no sideways scroll on phone; screenshot tests at each size (desktop + phone, light + dark).

────────────────────────────────────────
2. OPTION CHAIN: ANALYSIS + CHARTS (under ui_v2)
a. Wire the already-merged analysis tabs and Ask AI + saved analyses (#327) into the v2 Option chain page, below the chain.
b. Port the Trade repo's option-chain analysis (Trade: oi_analysis.py and the dashboard page that uses it; read them; port the logic with parity tests on fixture chains):
 - the Call vs Put OI difference with its trend (hysteresis-smoothed);
 - OI rotation confirmed over consecutive snapshots;
 - OI walls near the psychological levels;
 - OI + price build-up per strike and in total;
 - the PCR zone label;
 - Max Pain;
 - the rollover proxy;
 - days to expiry;
 - the IV change over recent sessions;
 - the OI history at 5 / 10 / 15-minute views.
 INFORMATION only; never a gate or rule in ATP; no buy / sell words.
c. A "Charts" tab next to the chain (strike range around ATM adjustable, default about ATM ± 10; the shared expiry picker):
 - OI by strike (CE vs PE bars, with today's OI change);
 - change in OI by strike;
 - multi-strike OI over the session;
 - cumulative CE vs PE OI with spot overlaid;
 - PCR over the session;
 - Max Pain over the session vs spot;
 - ATM straddle premium over the session;
 - ATM IV over the session + the IV smile / skew;
 - futures price vs futures OI.
 A one-line plain caption each; live updates; phone: one chart at a time, swipeable.
d. Data from the user's broker only (BrokerGate otherwise); intraday OI history from ATP's snapshot collector; "builds through the session" when early. Never sample data.
e. Earlier option-chain requirements, still binding:
 - no developer tools (Sample / lookback / manual LTP / Sample OI tilt);
 - automatic symbol + expiry (nearest; on expiry day the next weekly);
 - the summary strip (spot, futures, ATM, PCR, Max Pain, ATM IV, DTE);
 - the ATM centred; plain-word states.

────────────────────────────────────────
4. DEFAULT WATCHLISTS — indices, index stocks and sectors as SEPARATE lists (overrides UI4C-1)
- Three separate groups of read-only system lists, shown first:
 a. "Indices": ONE list of the indices themselves (every NSE broad / sectoral / thematic / strategy index covered; SENSEX / BANKEX where available);
 b. "Index stocks": one list PER INDEX with its constituents, named "<INDEX> stocks (<count>)";
 c. "Sectors": one list PER SECTOR with ALL NSE-listed stocks of that sector (NSE's official industry classification), named by the sector ("Banking", "IT", "Automobile", "Pharma", …).
- Sources: the NSE Indices constituent CSVs (niftyindices.com) and NSE's industry classification, fetched by a server job (never in git), mapped to the instrument master; unmapped symbols are logged for the admin; record the terms of use.
- Scheduled refresh for reconstitutions; the admin sees the last refresh and the changes.
- "Copy to my list"; the user's own lists under "My lists".
- Search, LTP, % change, sort.
- Tests for the three groups, the reconstitution diff, the read-only rules, copy, phone (collapsible groups).

────────────────────────────────────────
5. AI COPILOT — a professional trader's brain (under ui_v2)
A. Role: a senior professional trader + risk manager + mentor at the user's side. It reads, plans, checks, warns, reviews and teaches; it NEVER places, blocks or changes an order. Every answer is grounded in ATP's tools and data (it cites the numbers; it says "I don't know" when the data is missing; "How I worked this out"). For general users: the situation, the possibilities and the conditions, never buy / sell advice; the disclaimer is one short line.
B. Jobs:
 1. Pre-market brief: global cues (US markets, bond yields, dollar index), FII / DII, India VIX, this week's events, the structure of NIFTY / BANKNIFTY per timeframe, today's key areas; also to Telegram if the user wants it.
 2. Chart reader: by the Price Action Standard (until the Trade reader is ported: ATP's market_zones / chart_price_action layers, said so).
 3. Pre-trade check: trend side, area, entry model, invalidation, the risk against the user's OWN settings, event risk; it lists what is missing and never adds rules.
 4. Position watch: warnings when the risk rises; explanation only.
 5. Post-trade coach: a review of each trade, the repeated behaviour patterns (discipline engine), a weekly review.
 6. Strategy builder: the idea → readable rules → an honest backtest (in- / out-of-sample, deflated, costs).
 7. Option chain and risk: explains the chain; checks the size against the user's settings.
 8. Teacher: concepts on the user's own charts.
C. The page (chat-first, like ChatGPT):
 - the top line "Ask me anything about the market or your trades." (no jargon);
 - a large question box (phone: at the bottom);
 - starter buttons: "Today's market brief", "Read the NIFTY chart", "Check my trade idea", "How are my open positions?", "Review my last trade", "Turn my idea into a strategy and test it", "What does the option chain show?", "Teach me: what is a demand zone?";
 - "What I can do": 8 cards;
 - today's market in one plain line (with the reason when empty);
 - inline charts / tables; with no AI key: "Add a free AI key to start" → the key guide;
 - the page tour.
 The indicator regime (ADX / EMA / ATR, ai/regime.py) becomes an admin diagnostic only; regime_blocks never blocks anything. After the reader port, the regime comes from the reader's structure-based market state.
D. Quality: an evaluation set per job (the expected tool calls and the facts) in CI on recorded fixtures; a refusal / safety set.
E. Order: the page + jobs 1, 2, 7, 8; then 3, 4, 5; then 6.

────────────────────────────────────────
6. MASTER ORDER 2 — easy AI keys + first-time guidance (under ui_v2)
PART A — AI provider choice (BYOK) with free options:
 - provider tags ("Free tier" / "Paid" / "Free credits (run out)") and support ("Text" / "Chart images (vision)"); check each provider's current docs (record the date and source):
  - Google Gemini (AI Studio), free with vision: RECOMMENDED, shown first;
  - OpenRouter ":free" models (low daily limit);
  - Groq (text; vision only if the docs confirm it);
  - Mistral, Z.ai GLM, Cloudflare Workers AI;
  - the paid providers;
 - a privacy notice before saving a free-tier key ("Free tiers of some providers may use your prompts, charts and data to improve or train their models. Use a paid tier for private strategies.") + a one-line data term per provider;
 - ATP's private method prompts are never sent to a free tier that trains on data (blocked with a plain message); Abhi's tenant uses paid tiers only (his Claude Console API key);
 - vision features list only the vision-capable models;
 - limits in plain words and a friendly limit-reached message.
PART B — beginner key guides:
 - an explainer line at the top ("An API key is like a password that lets ATP use an AI service on your account. Free keys take about 2 minutes to create.");
 - a guide per provider inside ATP: an "Open <provider> key page" button (verify each link; expected: aistudio.google.com/apikey, openrouter.ai/keys, console.groq.com/keys, console.mistral.ai/api-keys, platform.openai.com/api-keys, console.anthropic.com/settings/keys); numbered steps with screenshots; what the key looks like; "Is it free?";
 - "Start with free Google Gemini" in one click;
 - a paste helper (trim, detect the provider, warn on a wrong key); "Test key" (one cheap call + one tiny image for vision);
 - common errors with their fixes;
 - each guide's last check date + a CI reminder.
PART C — first-time guidance on every page:
 - a welcome tour (3–4 cards: What ATP does / Connect your broker / Your paper bot / Get alerts on your phone; Next / Skip) ending on the Connect-broker prompt;
 - page tours on the first visit (3–5 spotlight steps: what is here, where to start, what the result means; Next / Back / Skip) for every user page;
 - a "?" button per page header to replay; Help → "Show all tips again"; empty states with a one-line hint + the next action;
 - the state stored on the server per user ("Skip all tips"); never blocking; stable element ids; phone-specific steps;
 - one English content file per page;
 - tests (first visit / replay / reset; CI fails if a step's target is missing on desktop or phone).
Order: C and B first, then A.

────────────────────────────────────────
8. ADDITIONS TO MASTER ORDER 3 (apply with the attached audit file)
a. Exactly TWO groups: USER and ADMIN; drop the "Simple / Pro" proposal in the file entirely. In the audit, tag every element User / Admin / Remove. The admin console holds:
 - System (health, workers, logs, errors, the data feed, the instrument master, backups, the deploy history, the go-live checklist internals);
 - Controls (feature flags, maintenance, kill switches);
 - Users & business (users, plans, trials, subscriptions, sign-ups / active / revenue, AI cost per user and per feature);
 - Testing tools (sample data, test signals, debug panels, moved out of the user pages);
 - Support (a user's errors and broker status, never secrets);
 - "View as user" (read-only);
 - a per-page "Diagnostics" drawer for SUPER_ADMIN only.
 Enforced on the server (require_role + MFA); tests: a user token gets 403 on every admin endpoint and never sees diagnostic fields.
b. Flags UX: a page / feature whose flag is OFF is hidden from the user's navigation; a direct URL shows a friendly "Coming soon", never the flag name or a reference id. In the admin Feature flags list: a plain description, the default, what turns on with it, its dependencies, and "ready for users" vs "unfinished". Send Abhi a Marathi list of the OFF flags and which are safe to turn on.
c. Signals page: no filter pre-applied by default (today "NIFTY 50" hides other signals); filters only when the user picks them.
d. Login bug: a wrong email / password shows "Sign in from the Account page to use this feature." (frontend/src/api/client.ts ~L278: the anonymous-401 mapping also catches /auth/login). Exclude /auth/* so the login shows "Wrong email or password". Add a server-side password reset command (atp.sh reset-password <email>, the password never echoed or logged) until SMTP is configured. Tests.
e. Go-live checklist: OFF the front page (the "Get started" card only); the full checklist under Settings → Advanced.
f. Broker connect: simple (already ordered: the Broker tab first in the sidebar with a status dot; logos; 1–2 fields per broker; the Analytics Token as the Upstox default; renewal via one banner; the OAuth redirect built from PUBLIC_BASE_URL https://aialgodesk.tech with the stored IP-based redirect_uri cleared). Verify what #328 delivered and finish the rest.

────────────────────────────────────────
10. ANALYZE A CHART (drafts #304 / #305 exist; merge after the load test)
- Upload / link → mode Image-only or Hybrid (recognise symbol / timeframe / range → editable fields → load the bars from the broker or ATP's stored data → rebuild the chart → a match check → analyse on the real data); paid plans + the free trial (limits as admin settings); private storage with a retention setting; method prompts only in atp-private; the vendor-data licence and SEBI questions in OPEN_QUESTIONS.
- The result screen:
 - the annotated chart (swings, areas with states, lines, the divergence, commitment / CHoCH, the setup lines);
 - the story;
 - the setup per the method (side, label A / B, formed / forming / none, trigger, invalidation, next areas: educational wording);
 - the evidence checklist ✓ / ✗;
 - the scenarios;
 - "what to wait for" + "Alert me when this happens";
 - the higher-timeframe strip;
 - the key levels table;
 - option strikes from the user's settings (flagged);
 - PNG + PDF export.

────────────────────────────────────────
11. STRATEGY LAB FOR EXPERT USERS — Pine Script + Python; one platform for every concept (Abhi's vision)
Vision: a trader finds every concept and every logic he needs in ONE place: no-code builders for beginners, Pine Script and Python for experts, with the same backtest → paper → alerts → (later) live path.
a. AMW PORT STATUS AUDIT first (Abhi gave ATP the AMW screener v2.9 files; nothing may be lost): go through docs/design/AMW_PORT.md, every P- / L- / AI- / C- row and AMW-1..AMW-25. For each, say ported / partial / missing, with the PR or file. Send Abhi a plain Marathi list of what is still missing, in order of user value.
b. Owner approval: Abhi APPROVES the Strategy Lab threat model + ADR-0027 (AMW-18, PR #264) as the design. Record it in OPEN_QUESTIONS / the ADR. The G-LAB checklist still gates every code-mode flag.
c. Priority (Abhi + research: Indian expert traders mostly use TradingView Pine and Python):
 1. AMW-19 Lab core + AMW-20 host modes: the visual builder and the PINE SCRIPT SUBSET, translated to ScreenQL and NEVER executed (no runner needed).
  - Paste Pine → line-numbered diagnostics → which functions are supported / unsupported, with the closest supported alternative.
  - The same source runs as a screen, a backtest, a paper forward test, a PAPER Autopilot deployment and alerts.
  - Templates (common Pine strategies / indicators).
 2. AMW-21 gVisor runner + AMW-22 PYTHON mode, after the G-LAB evidence:
  - strategies and screens in Python, plus a research notebook-style view on the user's own data (closed bars only);
  - no network, no secrets, resource limits from the plan.
  - Recommendation for the host: run the lab runners on a SEPARATE small server, so user code can never slow the trading worker on the 2 vCPU / 8 GB Hostinger host (LAB-6). Put the sizing and the monthly cost in OPEN_QUESTIONS for Abhi; build and test the runner so it works either way.
 3. SQL / Lua / JS / TS / R (AMW-23..25): DEFERRED. Keep them in the plan; build them only if users ask, since each adds security work for little Indian-retail demand.
d. One path for every mode: write → validate → backtest (the same engine and cost model) → paper forward test → Autopilot (PAPER) → Telegram / app alerts → LIVE only behind the existing gates. Screens follow the same path (scan → alert → paper).
e. "Idea → code" for users who know neither Python nor Pine. OWNER DECISION: this overrides AMW_PORT C-3; the AI MAY write code for every Lab mode, including Python, under the safety rules below.
 Flow:
 1. The user describes his idea in plain words (EN / MR / HI, reusing the strategy interview).
 2. The AI asks only the missing essentials (e.g. timeframe, symbol, exit, size), using the user's own settings as defaults.
 3. The AI picks the SIMPLEST safe mode that can express the idea (DSL / visual → Pine subset / ScreenQL → Python only when needed) and generates the code.
 4. Beside the code: a plain-language explanation, a small flowchart, and a mapping "your idea → this part of the code".
 5. An automatic backtest (the same engine, costs included, in-sample / out-of-sample, deflated), with the result in plain words.
 6. Edits by chat ("tighter SL", "also BANKNIFTY"): each edit creates a new version and re-runs the backtest; the versions can be compared.
 7. Only the user's explicit "Approve" sends it to a PAPER deployment / scan / alerts.
 Safety (ADR-0006 + the threat model stay in force):
 - AI-written code is treated as untrusted exactly like user code: the same validator, the same gVisor runner for Python, the same limits; never published, scanned or run live automatically;
 - the evaluation set: the generated code must match the stated idea on fixtures (a CI check), and refuse ideas that need forbidden features (network, other users' data, orders outside the paper path);
 - "explain an error", "convert a TradingView Pine script (showing what changed)" and "explain a backtest" are part of the same assistant.
f. Version history, compare versions, templates, private by default; sharing / the marketplace only after compliance (the flag stays OFF).
g. The TradingView bridge already exists (webhooks): make it visible in the Lab ("Send your TradingView alerts to ATP paper"), with the beginner guide.
h. Plans: the code modes and higher run limits in Pro; Free gets the visual builder + a few Pine runs per day (admin settings).
i. Tests per the AMW_PORT slices and the G-LAB checklist; the Pine translator fuzzed (size / nesting limits); cross-mode consistency (the same logic in visual / Pine / Python gives the same signals on fixtures).
j. "Idea → scan" for the Screener / Scanner: the same assistant as 11e, for screens. Extend what exists (screener_nl = deterministic NL → ScreenQL; screener_copilot = the plain-language screen box) instead of building a second system.
 1. The user describes the scan in plain words (EN / MR / HI); e.g. "F&O stocks in a Daily uptrend, pulling back into a 1H demand zone on falling volume, RSI near 40".
 2. The AI asks only what is missing (universe: index / sector / F&O / watchlist; timeframe), defaulting to the user's lists.
 3. It generates the screen in the simplest mode that fits (ScreenQL / Pine subset; Python only when needed and only via the sandbox), with a plain-language "what this scan finds" and the mapping idea → condition.
 4. It runs at once: the matches with a mini chart each and "why it matched" (the evidence_charts / evidence_context flags).
 5. History in one tap: how often it matched over the past months and what followed (screener_history fitness, deflated).
 6. Edits by chat ("only banks", "drop the volume condition"), each a new screen version (screener_versions), compare versions.
 7. Save → alerts (bar-close default; intrabar optional) to the app / Telegram / WhatsApp. "Turn this scan into a strategy" → 11e (backtest → paper) in one tap.
 Same safety as 11e: AI-written scans are validated like user scans; nothing is published, alerted or traded without the user's explicit save / approve; information only, no advice.
k. IDEA LIBRARY for beginners with no idea of their own + the compliance frame (owner: ATP only GENERATES SIGNALS from logic the user adopted and configured; the user decides the trade, lots and execution on his dashboard).
 1. A library of METHOD ideas, never stock tips. Examples:
  - trend pullback to demand / supply;
  - range edges;
  - RSI divergence at an area;
  - sweep and reclaim;
  - breakout and retest;
  - channel trading;
  - pattern at an area;
  - the options seller pack.
  The full spec is in ATP_SESSION_IDEA_LIBRARY.md.
 Each idea shows: how it works (plain words + an annotated example chart older than 3 months), where it tends to work / fail, and its full logic (white box: visible and replicable).
 2. "Use this idea" copies it into the user's "My strategies" with the provenance recorded honestly ("Based on ATP template: <name>", kept in the audit trail). The user names it, edits the parameters, runs his own backtest, picks the symbols, and sets the execution on his dashboard. It goes to automation (PAPER now) only on his explicit approval. No "buy / sell this stock" anywhere; no performance claims; backtests come only from the user's own run, with "past results don't guarantee future results".
 3. Compliance frame, built in now (record it in docs/COMPLIANCE_IN.md; the owner gets legal advice before LIVE / public launch):
  - white box only: every template's logic is visible to the user;
  - provenance + the user's adoption / edit / approval logged per strategy version;
  - the engine generates signals from the user's adopted logic; trade construction comes only from the user's settings;
  - LIVE order routing only through brokers, with the exchange algo ID (already modelled); note in OPEN_QUESTIONS that LIVE automation of ATP-provided templates likely needs exchange empanelment as an algo provider through each broker (SEBI retail algo framework, effective 1 Apr 2026), and that black-box logic would need RA registration, so ATP stays white box;
  - SEBI education rule (Jan 2025): education / teacher / brief content must not use the last 3 months of market prices of a NAMED security to imply advice or a future price. The daily brief and "today's setups" are framed as the results of the user's OWN adopted scans and strategies, never as ATP's picks; the learning examples use charts older than 3 months;
  - a disclaimer line on every signal and scan result; no testimonials or return promises in marketing.
 4. Tests: no template text contains a recommendation for a named security; the provenance and approval are recorded; learning examples respect the 3-month rule; signals carry the user's strategy id.
l. SIGNAL LANGUAGE POLICY + MARKETPLACE TIMING (owner decision; add to CLAUDE.md as a standing rule)
 - The marketplace stays OFF. It is the LAST phase: considered only about six months from now, once every feature is complete, stable and production-grade. Don't build marketplace features before then.
 - Showing strategies / ideas = EDUCATION. "Buy this stock, SL here, target there" = research / advice: ATP never does it.
 - The strategy engine only GENERATES SIGNALS. A signal is described in STRUCTURE WORDS only, e.g. "the market is in a downtrend; the pullback reached a seller area; a handover is forming inside the area; a trend-continuation setup has formed; evidence: lower-timeframe change of character, bearish divergence". In signal descriptions there are NO single prices or other numbers (no candle sizes, no "at X"); only the area range is allowed, and only on the user's own signals (b). NO buy / sell / target / stop-loss wording anywhere.
 - Two places, two rules:
  a. PUBLIC / EDUCATION surfaces (Idea Library, Copilot teacher, daily brief text, learning examples, marketing, share cards): no security names and no prices. Learning examples use anonymised charts older than 3 months (SEBI Jan 2025 education rule).
  b. The user's OWN signals (from a strategy he adopted, on symbols he chose): the symbol is shown, because it is his own selection. The description is in structure words; the AREA may be shown as a price RANGE (e.g. "Seller area: 23,000–23,100. The pullback reached this area; a handover is forming."), because it is a fact from his own tool and he needs it to approve. No single-price calls, no buy / sell / target / stop-loss wording. What his automation then did (orders, lots, fills) is a factual record of his own account on the Orders / Positions pages, never part of the signal text.
 - The engine may use numbers internally (it must, to compute); the policy governs what is SHOWN as the signal.
 - Enforce it: a signal-text formatter with tests (public surfaces: no security names, no prices; the user's own signals: only the area range pattern allowed, no other numbers; no buy-sell-target-SL words anywhere); a CI guard over the templates and the locale strings.
 - Record in docs/COMPLIANCE_IN.md that this reduces advisory risk but does not replace legal advice before LIVE / public launch.
