From Abhi — IDEA LIBRARY: the built-in strategy templates, full coding spec (English). Goes with ATP_HANDOVER_PACK.md items 11k (idea library + compliance) and 11l (signal language policy). Same rules: white box, signals only, structure words, PAPER, tests.

GENERAL RULES FOR EVERY TEMPLATE
1. A template = a SIGNAL logic only: context → area / condition → trigger → invalidation → evidence. It never decides the trade: the side mapping (e.g. bullish → bull put), strikes, lots, SL handling, targets and exits are the user's dashboard settings.
2. Reading is logic and comparison, never fixed numbers or clock time. Every adjustable choice is a named parameter (a user setting) with a sensible default and a plain-language label.
3. Building blocks: use ATP's existing layers (market_zones: demand / supply / order-block zones with lifecycle; chart_price_action: swings, legs, breaks, trend per degree; the ScreenQL zone / pattern / AMW functions; the RSI-divergence function; the volume reads). When the Trade reader is ported (E1–E4 entry models, the Price Action Standard), the templates switch to it, using the same template ids.
4. Every template is expressed in an editable form (the strategy DSL / ScreenQL, plus the Pine-subset view when the Lab exists). The user sees and can change the full logic (white box). "Use this idea" copies it to My strategies, with the provenance recorded.
5. Each template page shows:
 - the idea in plain words;
 - an annotated example chart OLDER THAN 3 MONTHS, with no security name on the public page;
 - where it tends to work / fail;
 - the full logic;
 - "Use this idea";
 - no performance claims.
6. Signal text (policy 11l): structure words; the area as a range on the user's own signals only; no buy / sell / target / SL words.
7. Tests per template: fixture charts where it must fire / must not fire; no look-ahead; the signal text passes the language guard; parameters at their bounds.

THE TEMPLATES

T1. Trend pullback to demand / supply (the core method)
- Context: a higher-timeframe trend (default: the Daily trend for a 15M setup; the pair is a parameter).
- Area: the nearest valid demand area (uptrend) / supply area (downtrend) proven by price, on the area timeframe (default 1H), that price is now visiting.
- Pullback quality (evidence, not a gate): the pullback is corrective (overlapping candles, smaller bodies than the impulse, lower volume).
- Trigger: the entry models E1–E4 (until the port: a rejection / commitment candle inside the area, or a lower-timeframe change of character inside the area).
- Invalidation: beyond the area's far edge (the refined base when available).
- Evidence listed: handover form, commitment with volume, LTF CHoCH, RSI divergence, confluence with a line / level.
- Signal text: "Uptrend; the pullback reached a demand area <range>; a handover is forming; continuation setup formed."

T2. Range edges
- Context: a range by structure (between two edges, price fails to make a new extreme on both sides).
- Area: the upper edge (supply) or the lower edge (demand).
- Trigger: rejection at the edge (commitment / LTF CHoCH / a sweep beyond the edge and close back inside).
- Invalidation: an acceptance beyond the edge (a close beyond + a swing formed beyond).
- Signal text: "Range; price at the upper edge <range>; rejection formed." The user's settings map edge → bear call / bull put if he wishes.

T3. RSI divergence at an area
- Context: any trend state.
- Condition: price makes a new extreme into a valid area while RSI does not (regular divergence), compared between the consecutive pushes inside the move into the area.
- Trigger: a LTF change of character or a commitment candle after the divergence.
- Invalidation: beyond the push extreme.
- Signal text: "Price pushed into a supply area <range> with bearish RSI divergence; a lower-timeframe change of character confirmed."

T4. Sweep and reclaim (liquidity grab)
- Context: obvious liquidity (equal highs / lows, a prior swing extreme) near or inside an area.
- Condition: price trades beyond the liquidity and closes back inside (an upthrust / spring).
- Trigger: the next push fails inside the sweep extreme (E4a).
- Invalidation: beyond the sweep extreme.
- Signal text: "Liquidity above the highs was swept and price closed back inside the supply area <range>; the follow-up push failed."

T5. Breakout and retest (structure flip)
- Context: an area or line broken by the method's definition (a close beyond + a swing formed beyond).
- Condition: price returns to the broken area / line from the other side.
- Trigger: a rejection candle closing away from it (E4b).
- Invalidation: an acceptance back through it.
- Signal text: "Former resistance <range> was broken and is being retested from above; rejection formed."
- Note: this is a retest entry after the break, never a breakout-chasing entry.

T6. Channel trading
- Context: a channel (two parallel lines from at least two reactions each) on the setup or higher timeframe.
- Area: the channel boundary in the trend's favour (the lower line in a rising channel; the upper line in a falling one).
- Trigger: rejection at the boundary (commitment / LTF CHoCH).
- Invalidation: a close through the boundary with a swing formed beyond. A broken channel then feeds T5.
- Signal text: "Rising channel; price at the lower channel line; rejection formed."

T7. Pattern at an area (replaces "opening range": that one is clock-based)
- Patterns recognised from swings: double top / bottom, head and shoulders (and inverse), at or from a valid area.
- Trigger: the neckline break, then the retest rejection (via T5), or the LTF CHoCH at the second top / bottom (parameter).
- Invalidation: beyond the pattern's extreme.
- Signal text: "Double top formed at a supply area <range>; the neckline broke and the retest was rejected."

T8. Options seller pack (signals for option sellers; the trade construction stays in the user's settings)
- Bundles T1 + T2 (optionally T6) and emits only the DIRECTION of the structure: "bullish continuation / bearish continuation / range upper edge / range lower edge", plus the area range and the invalidation range.
- The user's dashboard maps each signal type to his own construction (e.g. bullish → bull put spread, bearish → bear call spread, range edges → the matching spread), with his own strikes, width, lots, expiry rule (nearest weekly; on expiry day the next weekly) and exits.
- The template itself never names a strike, a premium or a lot.

LIBRARY UX
- A "Ideas" page grouped as: Trend / Range / Reversal at areas / Patterns / Options sellers. Each card: the name, a one-line idea, a difficulty (Beginner / Intermediate), "works best when …".
- "Use this idea" → My strategies (provenance recorded) → edit the parameters → the user's own backtest → choose the symbols → the dashboard execution settings → approve → PAPER automation.
- A beginner path: "Not sure where to start?" → three questions (trend or range trader; how often he can check; options seller or not) → suggests 2–3 ideas to read (education only).

ORDER: T1, T2, T8 first (they cover Abhi's own method and option sellers), then T3, T4, T5, T6, T7.
