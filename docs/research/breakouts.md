# Breakout screens - research notes (U6 BK1, BK2)

Source spec: `docs/specs/ATP_BREAKOUT_SCREENERS_ADDENDUM.md` (§1 research summary). Code: `backend/app/screener/breakouts/`.

Rule: no fitted numbers. Every threshold is an editable parameter (`params.py`) whose default is a published convention or
the practitioner range named below, with a one-line note. The table at the end is the parameter list; a test
(`tests/test_z7_bk1_breakouts.py`) checks that every parameter and its note appear here word for word (BK2's intraday
parameters included).

## What the sources say (spec §1, summarised)

1. **Quality breakouts come from tight, contracting bases after a prior advance.**
   - Mark Minervini, *Trade Like a Stock Market Wizard* (2013) - the trend template and the volatility contraction pattern
     (VCP): each contraction smaller than the previous one, volume drying up in the base, the pivot broken on expanding volume.
   - Kristjan Kullamagi ("Qullamaggie"), public trading notes - breakouts from tight flags after a strong move, on
     volume well above average.
   - William J. O'Neil, *How to Make Money in Stocks* (CAN SLIM) - bases of at least ~5 weeks (flat base), breakout volume
     at least 40-50 % above average, new highs as the trigger.
   - Practitioner default for relative volume on the break: about 1.5-2x the average.
   - A close beyond the level, not a wick; the trend agreeing on the higher timeframe.
2. **False-breakout filters** (practitioners plus the one quantitative study the spec cites):
   - higher-timeframe trend agreement (the largest effect in the study);
   - a confirmation bar in the breakout direction;
   - a volatility-regime filter by ATR percentile (breakouts from low-volatility regimes did best);
   - avoiding the first minutes / pre-market noise (intraday, BK2);
   - a minimum distance beyond the level scaled by ATR, not fixed points;
   - a failed-break rule: back inside within N bars = failure.
3. **Market structure** (the price_action engine): a break without acceptance is a liquidity sweep or trap, not a breakout.
   Acceptance = N closes beyond, or a retest that holds. A 5th-leg break into a major level is late (the maturity flag; it
   needs the Trade engine port and is not in BK1).
4. **Indicator conventions used as defaults:**
   - J. Welles Wilder, *New Concepts in Technical Trading Systems* (1978) - ATR(14).
   - Richard Donchian / the Turtle rules (Curtis Faith, *Way of the Turtle*, 2007) - 20-bar breakout channel.
   - Toby Crabel, *Day Trading with Short Term Price Patterns and Opening Range Breakout* (1990) - NR4 / NR7 and range
     expansion after contraction.
   - Nicolas Darvas, *How I Made $2,000,000 in the Stock Market* (1960) - the tight box.
   - Thomas George and Chuan-Yang Hwang, "The 52-Week High and Momentum Investing", *Journal of Finance* (2004) - nearness
     to the 52-week high as a momentum signal.
   - Alexander Elder, *Trading for a Living* (1993) - the triple screen: each higher screen about 4-6x the one below (the
     context ladder).
   - Charles Dow / Dow theory - trend = higher highs and higher lows (lower highs and lower lows).

## How BK1 uses them

- **Screens** (B1 range / consolidation, B2 swing structure with BOS / CHoCH / BREAK, B4 inside bar / NR4 / NR7, B7 N-week high,
  B13 range expansion, B14 volume breakout) are causal series on closed bars. ATR is read on the bar before the signal, so
  a wide breakout bar does not set its own yardstick.
- **Higher-timeframe trend.** A Dow trend is read only after 40 bars at D0. Measured on the session generator (40 seeds,
  daily and weekly): the trend on the last bar from N bars agrees with the full history 95.5 % at N = 30, 98.7 % at 40,
  99.9 % at 60 (`dow_min_bars`). An unknown trend blocks "not against" - the cautious reading - and earns no point.
- **One signal per leg.** After a signal the side re-arms after a confirmed pullback swing, a failed break, or a new base
  (`leg_rearm_bars` bars without a new extreme).
- **Quality overlay.** An equal-weight checklist of 6 points: higher-timeframe agreement (one point per context
  timeframe), volume expansion, base dry-up, low-volatility regime, room to the next opposing level. A = 5+, B = 3+, else
  C. A break against every context timeframe is C. The weights are not fitted: every check counts the same. Higher-timeframe
  agreement counts twice only because it is read on two timeframes.
- **Follow-up status** (failed / accepted / pending / unconfirmed) tags each result for the journal and for the fitness
  statistics (BK5). Fitness statistics will show results by grade, timeframe and regime. They are informational, and
  greyed out when samples are small.
- **Wording.** Results are "matches", never advice. Research only: nothing places, changes or blocks an order.

## Intraday breakouts (U6 BK2: B16 / B17 / B18)

Code: `backend/app/screener/breakouts/intraday.py` (the screens) and `session.py` (the session clock, session VWAP and
relative volume by time of day). Tests: `backend/tests/test_z7_bk2_intraday.py`.

What the sources say:
- **Opening range breakout.** Toby Crabel, *Day Trading with Short Term Price Patterns and Opening Range Breakout*
  (1990): the range of the first minutes of the session is the reference, and a move beyond it tends to carry on. The
  common windows are 15, 30 and 60 minutes (spec B16). The platform's own ORB strategy (`strategy_engine/
  combo_strategies.py`) already uses a 15-minute range, the first close beyond it, and a stop at the range midpoint.
- **First-hour, previous-day and first-candle levels.** These are practitioner references, not studied effects: the
  first hour's high / low, the previous session's high / low (PDH / PDL) and the first candle (spec B17). The pre-open
  high needs pre-open bars, which most broker feeds do not carry, so this level is used "where data allows".
- **Relative volume by time of day.** Intraday volume is U-shaped: heavy at the open and the close, light at midday
  (Jain & Joh, "The Dependence between Hourly Prices and Trading Volume", *JFQA* 1988; Admati & Pfleiderer, "A Theory of
  Intraday Patterns", *RFS* 1988). A plain n-bar average therefore calls every opening bar "high volume" and every midday
  bar "low". The fair yardstick is the same time of day in earlier sessions (spec §1 and B16). The 1.5-2x practitioner
  range carries over (`tod_vol_mult` 1.5).
- **VWAP.** The session's volume-weighted average price is the standard intraday execution benchmark (Berkowitz, Logue &
  Noser, "The Total Cost of Transactions on the NYSE", *Journal of Finance* 1988). Price on the breakout side of VWAP means
  the session's average buyer (seller) is in profit - the spec's "VWAP on the breakout side" (B16, B18).
- **Avoid the first minutes** (spec §1 false-break filters): the opening auction and the first prints are noisy. The
  default skip is 15 minutes, the same window as the default opening range.

How BK2 uses them:
- **Session clock from the exchange calendar** (`app/market_data/calendar.EXCHANGE_SESSIONS`: NSE 09:15-15:30 IST, no new
  entries after 15:00). Nothing in the screens hardcodes 09:15. Bars are placed by their start in exchange time.
- **Half days and special sessions.** The calendar has holidays but no special sessions, so:
  - a session is the bars its date has inside the window, and a half day just has fewer bars;
  - a time-of-day slot that a half day never reached is averaged over the sessions that had it;
  - an exchange circular can override a date's window (`session.calendar_for(exchange, special={date: (open, close)})`);
  - an evening-only date (Muhurat) is recognised from its first bar and opens there.
  A session that opens at another time than the regular open is left out of the time-of-day averages, and it is not the
  next session's "previous day".
- **Signals.** Each signal is a close beyond the level by `min_break_atr` x ATR, on a bar inside the time-of-day window.
  That bar must also have the time-of-day volume and VWAP agreement. B16 / B17 levels are fixed for the session, so the
  event is the cross (one per leg by construction), capped at `intraday_max_per_session` per side per session. B18's
  level moves with each new extreme: it uses the BK1 swing-leg rule, re-armed at each session start. The
  confirmations must pass on the crossing bar itself: a break on ordinary volume is not signalled later, when volume
  arrives while price is still beyond the level.
- **Quality overlay.** The overlay is the same equal-weight checklist. For B16-B18 two checks change: "volume expansion"
  reads the time-of-day relative volume, and "base dry-up" asks whether the bars before the break traded at or below
  their usual volume for the time of day. The time-of-day window (spec quality overlays; BK1-7) is a hard filter on the
  intraday screens (`skip_first_min`, `tod_window_end`), not a grade point, so BK1's grade thresholds keep their meaning.
- **"Then" template stop.** For the ORB templates the stop reference is the opening-range midpoint (`stop_mode` =
  midpoint). `base` gives the opposite side of the range.

## Parameters

| Parameter | Group | Default | Research note |
|---|---|---|---|
| `direction` | common | both | User choice (spec §2); no default bias. |
| `atr_n` | common | 14 | Wilder (1978) original ATR length; the platform-wide default. |
| `vol_avg_n` | common | 20 | 20 bars = about one trading month on Daily, the usual relative-volume window. |
| `vol_mult` | common | 1.5 | Low end of the 1.5-2x practitioner range (O'Neil: 40-50% above average; Minervini / Qullamaggie: 1.5-2x). |
| `min_break_atr` | common | 0.2 | ATR-scaled minimum distance, not fixed points (spec §1 false-break filter); 0 = any close beyond. |
| `close_loc` | common | 0.3333 | Top / bottom third of the bar (spec B13 wording; common 'close near the high' rule). |
| `range_mode` | B1 | fixed | Both forms named in spec B1. |
| `range_n` | B1 | 20 | 20 bars = Donchian / Turtle short channel (Faith, Way of the Turtle). |
| `base_min_bars` | B1 | 5 | One trading week on Daily; raise for O'Neil-style bases (flat base >= 5 weeks). |
| `base_max_bars` | B1 | 60 | About three months on Daily (upper end of O'Neil's base lengths); bounds the cost. |
| `base_max_height_atr` | B1 | 3 | A box taller than ~3 ATR is a trend leg, not a consolidation (Darvas-box tightness rule of thumb). |
| `base_contraction` | B1 | True | Contracting range into the pivot (Minervini VCP). |
| `prior_advance_pct` | B1 | 0 | Minervini asks for a prior advance (often 25-30%) before a VCP; off by default for a plain range break. |
| `prior_advance_n` | B1 | 60 | About three months on Daily. |
| `swing_degree` | B2 | 1 | D1 = 3 x ATR ZigZag (pa_settings): a structure swing, not bar noise. |
| `nr_setup` | B4 | nr7 | Crabel (1990) NR4 / NR7; the inside bar is the classic mother-bar setup. |
| `nr_trigger_bars` | B4 | 1 | Crabel: the trade is the break on the next bar; widen for slower follow-through. |
| `high_weeks` | B7 | 52 | 52-week high (George & Hwang 2004 52-week-high momentum; O'Neil's new-high rule). |
| `fresh_min_days` | B7 | 35 | 5 weeks, O'Neil's minimum flat-base length (calendar days, so Daily and Weekly read the same). |
| `expansion_k` | B13 | 2 | Wide-range bar convention (Crabel range expansion; 2 x ATR). |
| `shock_mult` | B14 | 2 | Upper end of the 1.5-2x practitioner range: a shock, not a confirmation. |
| `level_tol_atr` | B14 | 0.25 | A quarter ATR: inside one bar's noise. |
| `htf_degree` | quality | 0 | D0 (1.5 x ATR) is already a structure swing on a higher timeframe; Dow theory labels. |
| `dow_min_bars` | quality | 40 | Measured on the session generator (40 seeds): the last-bar trend from N bars agrees with the full history 95.5 % at 30, 98.7 % at 40, 99.9 % at 60. |
| `htf_filter` | quality | not_against | HTF agreement had the largest effect in the one quantitative study (spec §1); not_against keeps range-bound context, never an unknown one. |
| `acceptance_closes` | quality | 2 | N closes beyond = acceptance (spec §1); 2 matches the price_action break rule (break_no_reclaim_bars = 1). |
| `retest_tol_atr` | quality | 0.25 | A quarter ATR: inside one bar's noise. |
| `failed_break_bars` | quality | 3 | Failed-break rule 'back inside within N bars' (spec §1); 3 bars = practitioner 'it should work at once'. |
| `dryup_n` | quality | 5 | Minervini: volume dries up into the pivot. |
| `dryup_ratio` | quality | 1 | 1.0 = simply lower than average; no fitted ratio. |
| `atr_pct_lookback` | quality | 100 | One regime window (about five months on Daily). |
| `atr_pct_low` | quality | 33.33 | Lower tercile; breakouts from low-volatility regimes did best in the study (spec §1). |
| `atr_pct_high` | quality | 66.67 | Upper tercile. |
| `stop_mode` | quality | level_atr (ORB templates: midpoint) | Spec §5 stop forms; editable. |
| `stop_atr_mult` | quality | 1 | One ATR: one bar of noise past the level. |
| `room_min_r` | quality | 2 | 2R = conventional minimum reward:risk. |
| `room_swing_degree` | quality | 1 | Same structure degree as B2. |
| `liq_n` | quality | 20 | About one trading month on Daily. |
| `liq_min_value` | quality | 0 | Off by default: the U1 universes (F&O, NIFTY 500) are liquid by construction (open question BK1-3). |
| `grade_a_min` | quality | 5 | Equal-weight checklist; no fitted weights (HTF counts twice because it is two timeframes). |
| `grade_b_min` | quality | 3 | Equal-weight checklist; no fitted weights. |
| `leg_degree` | quality | 0 | D0 = the smallest confirmed pullback (pa_settings). |
| `leg_rearm_bars` | quality | 20 | As long as the default B1 range: a base that long is a new leg. |
| `skip_first_min` | intraday | 15 | Spec §1 'avoid the first minutes'; 15 minutes = the NSE opening-noise window (09:15-09:30) the platform's ORB strategy also waits out. |
| `tod_window_end` | intraday | calendar | The platform's own intraday cut-off (no new entries after 15:00 on NSE) is the time-of-day window of the spec's quality overlay. |
| `tod_sessions` | intraday | 20 | 20 sessions = about one trading month, as vol_avg_n; the same-time average removes the U-shaped intraday volume curve (Jain & Joh 1988). |
| `tod_min_sessions` | intraday | 5 | One trading week of history before the average is trusted. |
| `tod_mode` | intraday | bar | Both forms are in use: bar confirms the break bar (like vol_mult); cumulative is the scanners' 'relative volume at time of day'. |
| `tod_vol_mult` | intraday | 1.5 | Low end of the 1.5-2x practitioner range, as vol_mult, measured against the same time of day. |
| `vwap_agree` | intraday | True | Spec B16 / B18: VWAP on the breakout side; VWAP is the intraday execution benchmark (Berkowitz, Logue & Noser 1988). |
| `intraday_max_per_session` | intraday | 1 | The platform's ORB strategy takes only the first close beyond the range; a re-cross is often the whipsaw the failed-break rule warns of. |
| `or_minutes` | B16 | 15 | Crabel (1990) opening range; 15 / 30 / 60 minutes are the windows named in spec B16 (15 = the platform's ORB strategy). |
| `day_level` | B17 | first_hour | The B17 levels named in the spec. |
| `first_hour_min` | B17 | 60 | The first trading hour (09:15-10:15 on NSE) by name. |
| `context_ladder` | style | 15m -> 1h + 1d, 1h -> 1d + 1w, 1d -> 1w, ... (see params.py) | Elder's triple screen: each screen about 4-6x the one below (spec §2 examples 15m -> 1h + 1d, 1h -> 1d + 1w). |
