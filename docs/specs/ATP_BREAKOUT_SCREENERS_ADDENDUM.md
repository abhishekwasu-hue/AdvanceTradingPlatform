From Abhi — Screener addendum U6: breakout screeners, price-action and indicator based (English). Many traders trade breakouts; ATP must serve them with quality screens even though the owner's own method is pullback-based. Applies with SCREENER_SPEC v2 (§2.1/§2.2), U2 (builder blocks), U3 (gates), U4 ("Then"). Same working rules; draft PRs; tests; no fitted numbers in code — every threshold is an editable parameter with a documented default and a research note.

1. Research summary that shapes the design (sources in docs/research/breakouts.md)
 - Quality breakouts come from tight, contracting bases after a prior advance (Minervini VCP/trend template; Qullamaggie): each contraction smaller than the previous, volume drying up in the base, breakout bar on expanding volume (≈1.5–2× relative volume is the common practitioner default), close beyond the level (not a wick), trend aligned on the higher timeframe.
 - False-breakout filters that practitioners and the one quantitative study we found support: higher-timeframe trend agreement (largest effect in the study), confirmation bar in breakout direction, volatility regime filter (ATR percentile — breakouts from low-volatility regimes performed best), avoid the first minutes/pre-market noise, minimum distance beyond the level (ATR-scaled, not fixed points), failed-break rule (back inside within N bars = failure).
 - Market-structure reading (our price_action engine): a breakout through a level without acceptance (no closes beyond, heavy volume on re-entry) is a liquidity sweep/trap, not a breakout; acceptance = N closes beyond or a successful retest. Breakouts that occur as the 5th leg of a mature impulse into a major prior level are late (maturity flag).
 - Intraday: opening-range breakout (15/30/60-min range), break of first-hour high/low, VWAP side agreement, relative volume vs same time-of-day average; one alert per leg, confirmed on bar close.

2. Timeframe and style selection (user-selectable, one setting set)
 - Style: Intraday (1m/3m/5m/15m) or Swing (30m, 1H, 2H, 4H, Daily, Weekly). The user picks the signal timeframe; the screen always evaluates the higher-timeframe context from the next two higher TFs (e.g., 15m signal → 1H + Daily context; 1H signal → Daily + Weekly). All on closed candles (U3 G2).
 - Direction: long breakouts, short breakdowns, or both.
 - Universe: U1 picker (F&O default for intraday; NIFTY 500 default for swing).

3. Breakout screen catalogue (each = pre-built screen + ScreenQL functions + builder block fields; all parameters editable)
 Price-action breakouts
 - B1 Range/consolidation breakout: N-bar range (or auto-detected consolidation from price_action: ≥ K overlapping bars with contracting range) — close beyond range high/low by ≥ x × ATR, breakout bar volume ≥ v × average, prior-advance filter optional. Outputs base length, base height %, breakout distance in ATR.
 - B2 Swing-high / swing-low breakout (structure): close beyond the last confirmed swing extreme of the chosen TF (swings2 degree), with higher-TF Dow trend agreeing; flags BOS vs CHoCH.
 - B3 Volatility contraction (VCP-style) + breakout: ≥ 2 successive contractions each smaller than the previous (ratio editable), volume declining through the base, then close above the pivot (base high) on expanding volume; prerequisite trend template (price above rising long MAs, within x% of 52-week high, RS rating ≥ r) — all editable; "Watchlist" variant lists bases that are ready but not yet broken.
 - B4 Inside-bar / NR4 / NR7 breakout: narrow-range setup bar, next bar closes beyond its high/low with volume; mother-bar failure rule.
 - B5 Trendline / channel breakout: close beyond a trendline or channel edge from trendlines2 with ≥ 2 touches; angle/age filters; retest flag.
 - B6 Flag / pennant / triangle breakout: pattern detector (patterns2 / chart-pattern module) + breakout bar; measured-move target.
 - B7 52-week / all-time high breakout (swing, Daily/Weekly): close above the prior 52-week high (or N-week high) with volume; distance from the old high in ATR; days since last 52-week high (fresh vs extended).
 - B8 Gap breakout: gap beyond a level/base on the open, holding above the level by the end of the first N minutes; gap-and-go vs gap-fill classification.
 - B9 Retest entry (second chance): breakout already happened; price returns to the level and holds (reversal candle at the level — same commitment forms as the pullback screens); the preferred lower-risk entry for traders who missed the break.
 Indicator breakouts
 - B10 Donchian channel breakout (N-period high/low; classic 20/55 as editable defaults) with ATR filter.
 - B11 Bollinger squeeze → expansion: band width at an N-period low (squeeze), then close outside the band with volume; TTM-squeeze variant (Bollinger inside Keltner, momentum direction).
 - B12 Moving-average breakout: close crossing above a flat/rising key MA after consolidation around it; MA stack alignment.
 - B13 ATR / range expansion: bar range ≥ k × ATR with directional close (top/bottom third), volume confirmation.
 - B14 Volume breakout: volume ≥ v × average with a directional candle at/through a level (volume shocker with structure).
 - B15 RSI/ADX regime breakout: RSI leaving a long compression range, ADX rising from a low base (trend birth) with a price break.
 Intraday specials
 - B16 Opening range breakout: OR window 15/30/60 min (editable), close beyond OR high/low, VWAP on the breakout side, relative volume vs the same time-of-day average, skip-first-N-minutes option, one signal per leg; stop reference = OR midpoint or opposite side (for the "Then" template).
 - B17 First-hour high/low break; previous-day high/low break; pre-market/first-candle high break (where data allows).
 - B18 Intraday new high/low of day with relative volume and VWAP agreement.
 Quality overlays (apply to any breakout screen; shown as grade A/B/C and as "why it matched")
 - Higher-TF trend agreement (Dow on the next two TFs); acceptance (N closes beyond or retest held) vs wick-only; volume expansion on the break and dry-up in the base; volatility regime (ATR percentile low → better); distance to the next opposing level (room to run ≥ R× stop distance); not a 5th-leg/mature-impulse break into a major level (maturity flag from the Trade engine port); not an event day; liquidity floor; time-of-day window; failed-break detector (back inside within N bars) that auto-tags the result as "failed" for the journal and fitness stats.

4. Builder integration (U2): a "Breakout" block family with the fields above; a "Breakout quality" overlay block; templates per style (Intraday ORB, Swing 1H range breakout, Daily VCP, Weekly 52-week high); each result row shows base metrics, breakout distance in ATR, relative volume, HTF agreement, grade.
5. Alerts and "Then" (U4): breakout alerts fire on bar close only; retest alerts as a follow-up on the same symbol; the trade template suggests stop = base low / OR midpoint / breakout level minus x × ATR (editable) and target = measured move or next opposing level; one signal per leg (dedupe).
6. Screen fitness (SCREENER_SPEC §2.6): for every breakout screen, forward-return stats and failed-break rate by grade, by timeframe and by volatility regime — informational, shown greyed when samples are small; this is how users see which breakout style works in which regime, without the platform recommending anything.
7. Tests: synthetic fixtures for each screen (true breakout, wick-only fake, low-volume break, break against HTF trend, retest hold/fail, ORB with and without VWAP agreement, VCP with 2/3 contractions, NR7 follow-through); closed-candle/no-look-ahead tests on multi-TF context; one-alert-per-leg dedupe; grade determinism; parity between builder block, ScreenQL text and pre-built template.
8. Order: BK1 B1/B2/B4/B7/B13/B14 + quality overlay + timeframe/style selector → BK2 B16/B17/B18 intraday + VWAP/relative-volume-by-time-of-day series → BK3 B3 VCP + B10/B11/B12/B15 indicator breakouts → BK4 B5/B6/B8/B9 (trendline, pattern, gap, retest) → BK5 templates, fitness stats, docs. Slot inside SCREENER_SPEC S5 after the price-action screens.
