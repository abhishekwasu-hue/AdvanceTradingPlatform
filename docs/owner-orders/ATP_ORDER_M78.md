# ATP ORDER M78 - M75 W-1 follow-up: band label legibility (owner order, 1 Nov, verbatim)

M78 (ATP) - M75 W-1 follow-up: band label legibility (review of #847 compare page). Separate addendum; #847 may merge as is, do these in one small follow-up PR.

Owner review of the before/after page found W-1 correct (bands filled, no axis tags, labels clear of Auto/Reset, Charts-tab repaint bug fixed). Remaining items, all in the shared bandShapes / placeBandLabels code (one fix serves every band layer):

W1-F1 Label over candles: on phone 390 the "Buyer area · 15m · tested" and "Buyer area · 15m · fresh" labels are crossed by candle bodies / wicks and are hard to read. Draw each band label on a small rounded background chip (chart background colour, ~85% opacity) above the series layer, so candles never paint over the text.
W1-F2 Contrast / size in dark: seller label (red on dark red band) and buyer label (green on dark green band) are faint at the current small font. Use the band's text token with WCAG AA contrast against the chip, and a font size not below the chart's axis-label size on phone.
W1-F3 Same text everywhere: Copilot answer chart shows only "Seller area"; Charts tab shows "Seller area · 15m · fresh". Use the same label format on every band layer (area side · TF · state) when the data has TF / state; drop only the parts the data lacks.
W1-F4 Left edge: on desktop the left-placed label starts at the pane edge ("Buyer area ..." first letter touches the border). Keep a few px padding inside the pane.
W1-F5 Band price on demand: the axis tags were removed (correct). Give the band's top / bottom prices on tap (phone) / hover (desktop) of the band or its label, in place (tooltip), no navigation.

Not in scope: band logic, which areas are drawn, colours other than label text/chip. No new rule, no default value.
Tests: extend src/charting/bandLabels.test.ts (chip box inside pane, padding, contrast check of the text/chip pair in both themes, same label string from Copilot and Charts layers). Proof: ONE compare page, same 16 shots (phone/desktop x light/dark x Copilot/Charts), before = #847 build, after = this PR. Report in Marathi, <= 25 lines.
