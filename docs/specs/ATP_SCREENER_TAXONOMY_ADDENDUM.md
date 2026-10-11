From Abhi — Screener addendum U9: screen library taxonomy and gallery (English). Applies to SCREENER_SPEC §2/§3, U2, U5, U6, U7, U8. Same working rules; draft PRs; tests; English strings.

1. Library structure (two axes + tags; every screen must be filed on both axes)
 Axis 1 — Method (how the screen reasons)
  A. Price action (structure, levels, candles, liquidity; no indicator required)
  B. Indicator (moving averages, oscillators, bands, channels, volatility)
  C. Volume & order flow (relative volume, delivery %, OI/IV, participant data)
  D. Fundamental & ownership
  E. Legends (named after traders/investors; U7)
  F. My screens (user-built) and Shared (team) — later: Marketplace
 Axis 2 — Setup type (what the trader is looking for)
  1. Breakout / breakdown (U6)
  2. Pullback / continuation (trend + correction + trigger)
  3. Reversal (exhaustion, divergence, failed break, sweep at level)
  4. Range / mean reversion (range edges, bands, squeeze-before-move)
  5. Momentum / relative strength (leaders, RS, RRG)
  6. Options & volatility (buildups, IV rank, PCR, premium decay, credit-spread finder)
  7. Events & flow (results, ex-dates, SAST/bulk, FII/DII, gaps on news)
  8. Market internals (breadth, regime, sector rotation)
 Tags (filters in the gallery): timeframe family (intraday / swing / positional), direction (long / short / both), universe default, data tier (live-per-user / EOD), difficulty (beginner / intermediate / advanced), evidence-ready (U8), fitness available.
 So "Indicator → Breakout" and "Price action → Breakout" are distinct folders, as are "Indicator → Pullback" and "Price action → Pullback"; Legends is its own method folder with the same setup-type sub-folders.
2. Gallery UI (U5 tokens): left rail = Method; top chips = Setup type; grid of screen cards (name, one-line definition, timeframe chips, data-tier badge, fitness sparkline, Open / Alert / Show on chart); search across names and definitions; "Recently run", "Pinned"; a compare view (two screens side by side on today's results). Every card opens the builder with the screen loaded (U2) and the evidence chart on first result (U8).
3. Ideas beyond the two axes (owner asked; include as library features)
 - Setup-of-the-day digest: at a user-chosen time, one message per method folder with the top results of the user's pinned screens (data tier respected), evidence images attached, no advice wording.
 - Confluence view: run several screens and rank symbols by how many independent methods flag them (price action + indicator + volume + fundamental), with the evidence of each; shows agreement, never a recommendation.
 - Regime-aware library: each screen declares the regimes it is designed for (trend / range / high-vol / low-vol); the gallery greys screens that do not fit today's regime (breadth/VIX/Dow on the index) and says why.
 - Mirror screens: every long screen auto-generates its short/breakdown mirror (where the logic is symmetric) so users do not maintain two definitions.
 - "Find similar" from any chart: select a bar region → the builder pre-fills the conditions present there (U2), filed under My screens.
 - Watch-then-trigger lists: screens that produce a "ready" list (e.g. VCP base ready, pullback approaching level) and a separate "triggered" list, with alerts on the transition.
 - Learn mode: each card links to the method page (P1 §1.6) and shows three past examples with evidence charts and what happened next (from screen fitness), framed as education.
 - Versioning and provenance: each screen shows its version, change log and the source note (for Legends: the book/interview); users can fork a pre-built screen into My screens.
 - Quality labels from the gates: evidence-ready, no-look-ahead verified, fitness sample size — shown as small badges so users learn to trust verified screens.
 - Lint rules for the library: every screen filed on both axes, has a definition ≤ 2 lines, an evidence drawable per block, fixtures, and no advice wording; CI fails otherwise.
4. Order: TX1 taxonomy model + migration of existing/pre-built screens + gallery rail/chips + lint → TX2 confluence view + mirror screens + watch/trigger lists → TX3 regime-aware greying + setup-of-the-day digest → TX4 learn mode + provenance/forking. Slot with U5 D3.
