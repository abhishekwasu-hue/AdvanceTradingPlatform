From Abhi — Screener addendum U7: "Legends" screens — pre-built screens modelled on the published methods of great traders and investors, each named after them, with a short card about the person, the style and the screen (English). Applies with SCREENER_SPEC v2, U2 (builder), U6 (breakouts), P1 (education). Same working rules; draft PRs; tests; editable parameters; English strings.

1. Why and how (rules for the whole library)
 - Purpose: a loss-making retail trader learns faster from "this is how Minervini finds a base" than from a bare filter list. Each legend card is education + a working screen the user can open in the builder, edit, backtest (screen fitness) and alert on.
 - Source discipline: every screen is derived only from the person's PUBLIC writings/interviews/books (cite title in the card). No invented rules. Where a legend's method is discretionary, the screen is an honest approximation and the card says so ("approximation of the published rules").
 - Naming and legal: name screens "<Name>-style <screen>" (e.g. "Minervini-style trend template"); the library header and every card carry: "Inspired by publicly described methods. Not affiliated with, endorsed by, or representing <Name>. Educational tool, not investment advice." Avoid registered trade-marked method names in screen titles (use descriptive names instead, e.g. "O'Neil-style growth breakout" rather than the trademarked acronym; "stage 2 breakout" rather than a branded course name). Living persons: no photos/logos; name + public source only. Legal review of the card texts before Phase 1 (business owner).
 - Card format (English, ≤ 5 lines total): who (1 line), style (1 line), what this screen looks for (1–2 lines), source (book/interview), best timeframe and market context; plus a "Learn more" link to the method-library page (P1 §1.6) and the screen's fitness stats.
 - Every screen is a normal pre-built screen: ScreenQL source visible, parameters editable, universe/timeframe selectable, alertable, usable in the "Then" template; no black boxes.

2. Catalogue (first release; each row = card + screen; parameters are defaults, editable)
 Trend / breakout (swing)
 - Mark Minervini — trend template (price > 50 > 150 > 200 MA, 200 rising ≥ 1 month, within 25% of 52-week high, ≥ 30% above 52-week low, RS ≥ 70) and the volatility-contraction base + pivot breakout (U6 B3). Source: Trade Like a Stock Market Wizard.
 - William O'Neil — growth breakout from a base: earnings and sales acceleration (fundamental fields, point-in-time), RS rating high, new 52-week high from a cup/flat base with volume ≥ 40–50% above average. Source: How to Make Money in Stocks.
 - Nicolas Darvas — box breakout: price boxes (consolidation with clear top/bottom), buy on breakout above the box top with volume; stop just below the box. Source: How I Made $2,000,000 in the Stock Market.
 - Jesse Livermore — pivotal-point / all-time-high breakout with volume; new highs after a long base; no averaging down. Source: Reminiscences of a Stock Operator; How to Trade in Stocks.
 - Stan Weinstein — stage analysis: stage-2 breakout above a flat/rising 30-week MA with volume, relative strength turning up; stage-4 breakdown mirror for shorts. Source: Secrets for Profiting in Bull and Bear Markets.
 - Richard Dennis / Turtles — Donchian 20-day (and 55-day) channel breakouts with ATR-based sizing and 2N stop (shown in the "Then" template). Source: The Complete TurtleTrader; the public Turtle rules.
 - Kristjan Kullamägi ("Qullamaggie") — momentum-leader breakouts: large prior move (e.g. 30–100% in 1–3 months), tight flag/consolidation above rising 10/20 EMA, breakout on the opening range; episodic pivot (gap on news with huge volume). Source: his public streams/blog.
 - Dan Zanger — classic chart-pattern breakouts (cup-and-handle, flags, channels) on heavy volume in leading names. Source: public interviews/newsletter descriptions.
 - Richard Wyckoff — accumulation spring and sign-of-strength breakout; distribution upthrust (links to our liquidity-sweep logic). Source: The Richard D. Wyckoff Method.
 Pullback / mean-reversion / short-term (intraday and swing)
 - Linda Raschke — "Holy Grail" pullback (ADX > 30, pullback to the 20 EMA in a trend, buy the break of the pullback bar high) and "Turtle Soup" (false 20-day breakout reversal). Source: Street Smarts.
 - Al Brooks — second-entry pullback (H2/L2) in a trend; failed-breakout reversals. Source: Trading Price Action series.
 - Toby Crabel — NR7 / inside-day range-contraction breakout; opening-range breakout. Source: Day Trading with Short Term Price Patterns and Opening Range Breakout.
 - Larry Williams — volatility breakout (open ± k × previous range), short-term patterns. Source: Long-Term Secrets to Short-Term Trading.
 - Larry Connors — RSI(2) pullback in an uptrend (price above 200 MA, RSI(2) < 10). Source: Short Term Trading Strategies That Work.
 - Paul Tudor Jones — 200-day MA regime rule (only long above, defensive below) as a context overlay. Source: public interviews.
 Options (income / volatility)
 - Tom Sosnoff / tastytrade methodology — high IV rank (≥ 50), ~45 DTE, ~16-delta strangles / defined-risk spreads, manage at ~50% profit or 21 DTE. Source: tastylive public research. (India adaptation: weekly expiries; lot sizes from the instrument master.)
 - Sheldon Natenberg — volatility edge: implied vs realised volatility spread; IV rank/percentile screens. Source: Option Volatility and Pricing.
 Value / quality / growth (investing)
 - Benjamin Graham — net-net and Graham-number screens; margin of safety. Source: The Intelligent Investor; Security Analysis.
 - Warren Buffett / Charlie Munger — quality compounders: consistent ROE/ROCE, low debt, strong free cash flow, stable margins, reasonable price. Source: shareholder letters.
 - Peter Lynch — PEG < 1 growth at a reasonable price; categories (stalwarts, fast growers). Source: One Up on Wall Street.
 - Joel Greenblatt — magic formula (earnings yield + return on capital rank). Source: The Little Book That Beats the Market.
 - Joseph Piotroski — F-score ≥ 8 on low price-to-book names. Source: his 2000 paper.
 - Philip Fisher — scuttlebutt-style growth quality (sales growth, R&D/margins) as an approximation. Source: Common Stocks and Uncommon Profits.
 India
 - Vijay Kedia — "SMILE" framework (Small in size, Medium in experience, Large in aspiration, Extra-large in market potential) as a fundamental approximation: small/mid cap, ≥ 10 years listed, sales/profit growth, promoter holding stable/rising. Source: his public talks/interviews.
 - Saurabh Mukherjea — "Coffee Can" (10 years of ≥ 10% revenue growth and ≥ 15% ROCE). Source: Coffee Can Investing.
 - Rakesh Jhunjhunwala — long-term quality and under-researched mid caps; card is explicitly an approximation of a discretionary style (no formal rules published). Source: public interviews.
 (Add Indian traders with published rule sets as they are verified; never attribute rules that were not publicly stated.)
3. Screens that are NOT included and why: anyone whose method cannot be stated from public sources, anyone associated with fraud or regulatory action, and anything requiring data ATP does not have (noted in OPEN_QUESTIONS).
4. UI: "Legends" tab in the Screener template gallery with the cards (name, one-line style, timeframe chips, fitness sparkline, Open / Edit / Alert); filter by style (trend, breakout, pullback, options, value, India); search; each card opens the builder with the screen loaded and the card text in a side panel; method-library page per legend (P1 §1.6).
5. Tests: every legend screen compiles from ScreenQL, has fixtures (positive/negative), parameters within documented ranges, card text ≤ 5 lines with source and disclaimer present (lint test), no trademarked method names in titles (lint list), fitness stats computed like any screen.
6. Order: LG1 library model + card lint + disclaimer + 8 screens (Minervini, Darvas, Weinstein, Turtles, Raschke Holy Grail, Crabel NR7/ORB, Graham, Greenblatt) → LG2 remaining trend/pullback/options → LG3 value/India + method-library pages → LG4 fitness and gallery polish. Slot after U6 BK2.
