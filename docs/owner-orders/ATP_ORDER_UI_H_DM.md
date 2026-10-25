# Owner order UI-H-DM - dark mode contrast (25 Oct, verbatim)

Phone dark theme looks dim (research R18: text contrast is fine — primary 17.9:1, muted ≥10:1 — but card vs page is 1.15:1 and borders 1.9–2.2:1, below the 3:1 non-text contrast for UI boundaries, so everything melts together). Fix in colour_v2 dark tokens only (light theme unchanged unless it fails the same checks). ≤15 lines.
DM1 Page surface one step lighter (toward Material's ~#121212 depth, keep the navy hue); card surface lighter again so card vs page ≥1.3:1; raised (inputs, chips) a further step.
DM2 Borders ≥3:1 against the surface they sit on (cards, inputs, tab underline, chart frame).
DM3 Icons, inactive tabs, nav labels ≥4.5:1; primary buttons and the active tab ≥7:1 (outdoor phone use).
DM4 Settings → Appearance: add "High contrast" (stronger borders, brighter text/icons) beside the existing text size option.
DM5 A token test that computes these ratios (fails below the numbers above). One phone comparison page (Today, Charts, Option chain, Terminal; dark; normal + high contrast).
