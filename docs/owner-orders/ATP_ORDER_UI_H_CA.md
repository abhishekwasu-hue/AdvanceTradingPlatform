# Owner order UI-H-CA: colour accents (25 Oct 2026, verbatim)

Roadmap: UI-H-CA (part of UI-H; extends colour_v2). Builds on Wave 0 (#510) shared components.

UI-H colour accents (extends colour_v2; brand stays ATP blue; research: 60-30-10 rule, gradients only as accents, never colour alone). ≤15 lines.

CA1 Palette: 60% neutral base (as now), 30% surfaces, 10% accent. Add ONE secondary accent (violet) and a blue→violet gradient token, both in colour_v2 tokens, light + dark.

CA2 Gradient only on: the single primary action per page (border or fill), the "new" badge, the Today hero card, the area-of-interest highlight. Never on text, tables, chain, forms, or charts.

CA3 Section identity: each main section gets its own tint for its icon chip and page-header icon (Today, Charts, Signals, My trades, Option chain, Terminal, Copilot…) — round coloured chips like the reference; tints from tokens, AA contrast on both themes.

CA4 Summary cards: soft tinted backgrounds by meaning (profit / loss / risk / neutral info), always with a sign, icon or word — colour is never the only signal.

CA5 Check colour-blind simulation (protanopia / deuteranopia) on the comparison page.

Proof: one before/after comparison page (Today, Charts, Option chain, Terminal; phone + desktop; light + dark).
