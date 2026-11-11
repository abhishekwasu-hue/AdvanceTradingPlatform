# M97 Supply & Demand pack - spec review against the code (11 Nov)

Owner order M97 (INBOX 11 Nov). Public text in our own words only; the private study stays in trade-data.

## 1. What exists today (audit, file:line)

| Order part | Status | Where / gap |
|---|---|---|
| Z1 detector | **Partly** | `price_action/zones/detect.py:80` finds a tight base (up to 4 candles, range <= 0.7 x average range) before one impulse candle (body >= 1.5 x average range). It has **no leg-in, no DBR / RBR / RBD / DBD typing, no body-% test on base or leg candles, and no proximal / distal**. docs/design/AMW_PORT.md:91 says "Have", which overstates it. |
| Z2 states | **Partly, different** | `zones/lifecycle.py`: fresh -> tested -> flipped -> invalidated, where death is a structural break beyond a swing. The order wants Fresh / Tested (count) / Violated = close beyond the distal line. |
| Z3 chart | **Partly** | Market-zones layer: max 6 full-width bands (`charts/zones_layer.py`), M75 W-1 label placer (`charting/geometry.ts:299-352`), tap -> BandTip (`ChartV2.tsx:185-215`). Missing: band from the base start extended right (LayerBand is full width; needs a time-anchored band), formation label, legs / base highlight on tap, a higher timeframe drawn on a lower-timeframe chart. |
| Z4 scanners | **Partly** | `NearZone / ZoneRole / ZoneState / ZoneStrength` (`screener/registry.py:714-721`) read stored Z7a snapshots: no formation, no proximal / distal price, no "new zone formed" or "violated" event. |
| Z5 templates | **Partly** | A1 "Pullback to a demand / supply area" (AreaGate, the owner's ported level gate) rebuilds areas from the frame. Templates cannot read stored zones (`strategy_templates/engine.py:147`; `screener/zones.py:140`). There is no zone price function for "stop beyond distal" or "target at the opposing zone" (`exits.py` NEXT_AREA is "not available yet"). There is no per-template min R:R; A1's ported `min_rr` defaults to 3.0. |
| Z6 presets | **Missing** | Nothing matches the investor ... scalper ladder. Timeframes 10m and 120m exist nowhere (chart TFs 1/3/5/15/30/60m, day, week, month; ScreenQL has no 10m / 2h, `breakouts/style.py` builds 2h itself). |
| Z7 Copilot facts | **Partly** | There is no "facts JSON / M93 H1" in code (M93 was the scanner audit). Copilot facts are text lines from `get_market_zones` (Z7a). Overlay state is not known on the server (ChatContext has page / symbol / timeframe only; precedent: `ChartPrefs.reader_overlay`, `me/prefs.py:108-114`). |

The owner's own method areas (atp_private engine, AreaGate / A1) are a separate system. CLAUDE.md §5: never merge areas.

## 2. Design

- **One new generic module** `price_action/sd_zones.py`: the detector + states. It reuses only `indicators.volatility.atr / true_range` and `zones.detect.average_range`. Z7a, A1 and the AreaGate stay unchanged (they are used by existing screens and templates).
- **Detector:**
  - Leg candle: range >= `leg_mult` x average range(`avg_n`) and body >= `leg_body_pct` % of range. Reference (M98-P1 Q1): 1.0 x and 50 % - the leg-in need not be explosive (was 1.5 x / 75 %); the leg-out stays explosive (`out_mult` 1.5).
  - Base candle: range <= `base_mult` x average and body <= `base_body_pct` %. A flat candle (high = low) is never a base candle (`skip_flat_bars`, reference on; M98-P1 Q2).
  - Base length 1..`base_max`.
  - Leg-in: any leg candle.
  - Leg-out: explosive (range >= `out_mult` x average range or ATR).
  - Formation from the leg-in / leg-out directions.
  - Proximal / distal by side.
  - A zone exists only after the leg-out candle closes.
- **States:** Fresh / Tested (count, a touch of the proximal line) / Violated (a close beyond the distal line). Violated is hidden by default.
- **Computed from the bars of the run** (the A1 way), so scan = backtest = worker. No new store, no migration.
- **ScreenQL functions** (flag `sd_pack`): `SDZoneDistancePct(side, state)`, `SDZoneEnter(side)`, `SDZoneFormed(formation)`, `SDZoneViolated(side)`, `SDZoneEdge(side, "proximal" | "distal")` (a price, for the stop / target), plus the `@tf` read for a higher timeframe.
- **Library group "Supply & Demand"**: the 5 scanners of Z4, the M94 source pattern.
- **Template family "I" Supply & Demand:**
  - I1 Demand zone long and I2 Supply zone short.
  - Entry at the proximal line, or the user's chosen confirmation (the family G `confirmations` mechanism).
  - Stop = distal +- the user's buffer.
  - Target = the opposing zone's proximal (a new `LEVELS` entry), or the user's own exit.
  - An optional reward:risk check that only the user switches on, default OFF, reference 2:1.
  - PAPER through the existing template path (templates are PAPER-only today, `deployments/routes.py:666`).
- **Chart layer** "Supply & Demand zones":
  - time-anchored filled bands from the base start, extended right;
  - labels like "Demand · DBR · Fresh · D" through the W-1 placer;
  - the user's timeframes (a higher TF on a lower TF chart);
  - tap -> legs + base highlighted with their numbers.
- **Style presets**: preselect timeframes only, editable.
- **Copilot**: a server pref `ChartPrefs.sd_zones` set by the overlay toggle; when on, the facts carry the zones (formation, state, proximal / distal) computed on the server.

## 3. Owner questions (each with a default)

| ID | Question | Default |
|---|---|---|
| M97-Q1 | A new separate detector, Z7a / A1 / AreaGate unchanged | Yes: new module; the old ones stay for the screens and templates that use them |
| M97-Q2 | The study gives no numbers for "expanded" / "narrow" / "explosive" | Reference values from ATP's existing zone settings, marked "reference - edit before use": average range 20 candles; leg range >= 1.5x (superseded by M98-P1 Q1: 1.0x); base range <= 0.7x; leg-out range >= 1.5x; body: leg >= 75% (superseded by M98-P1 Q1: 50%), base <= 50% (the study); base length < 6 (the study) |
| M97-Q3 | Proximal of a demand zone: the base's body top or its wick high (the study allows either); the mirror for supply | A user setting "edge: wick / body", reference wick |
| M97-Q4 | 10m and 120m in the presets do not exist anywhere | Add 10m and 2h as timeframes built from the stored 5m / 1h bars (chart + ScreenQL), closed bars only |
| M97-Q5 | Copilot "facts JSON (M93 H1)" does not exist in code | Use the existing Copilot facts (text lines) + the server pref `sd_zones` |
| M97-Q6 | "Tested" = a touch of the proximal line (wick) | Yes; the count is how many separate visits (a visit ends when price leaves the zone) |
| M97-Q7 | New flag `sd_pack` (OFF until the owner turns it on), template family "I" | Yes |

## 4. PR plan
PR-1 detector + states + ScreenQL functions + causal / no-look-ahead tests + the flag; PR-2 scanners (library group); PR-3 templates I1 / I2; PR-4 chart layer (+10m / 2h); PR-5 style presets + Copilot facts; then one proof page (a chart per formation 390 + desktop dark, a scanner run, a PAPER trade).
