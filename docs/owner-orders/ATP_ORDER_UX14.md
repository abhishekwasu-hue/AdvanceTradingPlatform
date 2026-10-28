# OWNER ORDER - UX-14 "Professional palette" (28 Oct 2026)

Verbatim as received (Session B). Roadmap ID UX-14; extends colour_v2 / UI-H / UX-11b.

---

OWNER ORDER — UX-14 "Professional palette" (28 Oct). Extends colour_v2 / UI-H / UX-11b; brand stays ATP blue. File in INBOX verbatim /
DECISIONS / roadmap / README first. Major visual change → build as a new token set behind a flag `colour_v3`, show the owner ONE
comparison page, switch the default only after his ✅. Nothing deleted.
FINDINGS (tokens.css, colour_v2 dark, :461-480): cards 32 43 70 are saturated navy; brand-fill 30 64 175 on them = 1.61:1 (the
invisible Run button); borders 128 144 182 are bright (every card outlined → busy); too many accents (brand blue, --ai violet, --ai-2
cyan, --signal teal, --armed orange, --accent-2 violet, the new purple dot backdrop UX-12/13) — professional trading UIs use ONE accent
+ semantic colours.
PALETTE (colour_v3; verify every ratio in CI, adjust by the smallest step to pass):
  dark : page #0B0E14 · card #131722 · raised #1C2130 · border #2A2F3D · text #E6E9EF · muted #9AA3B2 · brand #2962FF (white text) ·
         up #2EBD85 · down #F6465D · warn #F0A93B · neutral #8B93A7 · ai #8B5CF6 (AI badge only)
  light: page #F7F8FA · card #FFFFFF · raised #F1F3F6 · border #E3E6EB · text #0F172A · muted #5B6475 · brand #2962FF · up #0A7D55 ·
         down #D92D3F · warn #B45309 · neutral #6B7280 · ai #7C3AED
  colour-blind mode: keep the existing up / down swap.
RULES: 60-30-10 (neutral surfaces / cards + text / colour for actions and meaning); no coloured card backgrounds — colour only on
chips (10-15% tint), numbers and buttons; one accent (brand blue) for actions and links; semantic colours only for meaning (UX-11b
colour table); no gradients, glows or dot backdrops behind data (UX-12/13 backdrop off under colour_v3); elevation by surface steps,
1 px subtle borders only where needed; tabular figures, key number large + high contrast, labels small + muted. Map the extra accents
(--ai-2, --signal, --armed, --accent-2) to brand / semantic tokens or remove their use under colour_v3 (keep the tokens).
CONTRAST (CI, dark + light, colour_v3): text ≥ 4.5:1 on every surface; buttons, chips, borders that carry meaning ≥ 3:1 against their
surface; disabled buttons still ≥ 3:1 with a visible reason.
PROOF: ONE comparison page — colour_v2 vs colour_v3 side by side, phone 390 px + desktop, dark + light: Today (Morning brief),
Terminal (chain + basket), Screener (bottom bar Run), My trades (positions with P&L), Signals; plus the contrast table. Owner picks;
then colour_v3 becomes the default (flag kept for rollback). Self-review every diff; deploy line + migration no. Report ≤ 15 lines.
