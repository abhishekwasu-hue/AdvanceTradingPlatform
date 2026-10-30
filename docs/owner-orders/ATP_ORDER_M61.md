# ATP_ORDER_M61 - Option chain page: OI tab audit fixes + PCR tab addendum (owner order, 30 Oct, verbatim)

M61 · ATP · Option chain page — OI tab audit fixes (inside M49/M48 OC rules; information only, no buy/sell wording)
Findings (code main 56d7b64 + owner screenshot 30 Oct 22:41, OI tab):
F1 Units inconsistent: the chain table uses model.ts formatCount (85,000 / 12.35 L / 1.24 Cr) but the OI history table uses oi/format.ts formatOi = full digits ("5,04,37,270", "+21,60,470"); charts.ts/analyses.ts have their own tick formatters. Three formatters for one quantity.
F2 OI history after the close: every 5-min row repeats the same numbers (Δ Diff 0) — rows without change are noise; "Sig" column truncated ("BUI (We"); 5/10/15-min view chips while nothing changes.
F3 "OI by strike" x-axis labels overlap into one string (27502280022850…); bars unlabelled; legend says calls red / puts green but the summary card above lists Put first.
F4 Two different "as of" times on one screen (banner 22:35, chart 17:09) with no explanation; "Market closed" chip but history keeps ticking.
F5 Wording: "No clear activity · Put: Flat/unclear · Call: Flat/unclear" and "PCR 1.04 — Mildly bullish (not yet overbought)" — the first is a non-statement, the second is a verdict; both are prose where numbers belong.
Orders:
OI-1 One number formatter for every OI/volume figure app-wide (model.ts formatCount rule: < 1 L plain Indian grouping; ≥ 1 L "12.35 L"; ≥ 1 Cr "1.24 Cr"; changes signed "+21.60 L"); delete oi/format.ts formatOi/formatSigned and the private tick formatters in favour of it; tests updated. Apply to the history table, chips, tooltips, axes.
OI-2 OI history: show only rows where Call OI or Put OI changed (toggle "show all"); columns: Time · Call OI · Put OI · PCR · ΔCall · ΔPut · Read (full words, no truncation; column picker on phone); after the close show one line "Last change at 15:29" and freeze the view chips.
OI-3 OI by strike: strike labels every other tick or rotated 45°, ATM highlighted, bars labelled on hover with "Call OI 27.49 L / Put OI 49.44 L"; calls and puts order and colours identical everywhere on the page (puts green first, calls red, same as the chain header CALLS|PUTS logic — pick one and keep it).
OI-4 One "Data as of HH:MM" for the whole tab (the oldest of the sources shown, with (?) listing each source's time); when "Market closed", stop polling and say "next update at 09:15".
OI-5 Summary card = numbers first: PCR 1.04 · Max pain 23,000 · DTE 4 · highest put OI strike/amount · highest call OI strike/amount; the one-line read in neutral words ("Put OI above call OI (PCR 1.04)"), never "bullish/bearish/overbought"; "Flat/unclear" replaced by "No OI change since 15:29".
OI-6 Proof: before/after desktop + phone screenshots; unit tests for the formatter and the changed-rows filter.

M61 addendum — PCR tab (owner screenshot 30 Oct 22:44)
F6 Snapshots at 06:45 and 06:50 IST (pre-open) with a "spot" of 22,936.75 — either the OI collector runs before the market with yesterday's chain, or the timestamp is wrong; either way the table presents pre-open rows as session data. The 11:51 point (PCR 0.82 from 1.15, back to 1.00 next) looks like a partial snapshot (some strikes missing) rather than a market move — check the strike count per snapshot.
F7 Same full-digit OI (23,04,40,340) — OI-1 applies; x-axis labels overlap (22:22/22:42); flat post-close rows repeated.
PCR-1 Collector: only snapshots taken between 09:15 and 15:30 on a trading day (calendar-aware) count as session rows; pre-open/after-close snapshots stored but labelled "pre-open" / "after close" and excluded from the chart by default; each snapshot stores the strike count and is flagged "partial" when it is below the session's typical count — partial rows drawn hollow and excluded from the headline "moved from X to Y".
PCR-2 Chart: one point per change (not per poll), axis ticks every N minutes without overlap, ATM strike change marked, hover shows Put OI / Call OI / PCR in lakh.
PCR-3 "Now" table: OI in lakh/crore (OI-1); near-ATM band definition behind (?); headline = numbers: "Overall PCR 1.60 · Near-ATM PCR 1.03 (11 strikes 22,800–23,300)".
PCR-4 Tests: pre-open rows excluded; partial snapshot flagged; one point per change.
