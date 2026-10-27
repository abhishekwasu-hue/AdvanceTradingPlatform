# Owner order UX-11 — "Numbers first, text on demand" (27 Oct, verbatim)

Roadmap ID UX-11, under UX-2 (Session B). Plan / status: docs/ROADMAP.md row UX-11.

OWNER ORDER — UX-11 "Numbers first, text on demand" (27 Oct). Roadmap ID UX-11 under UX-2 (Session B). File it in INBOX verbatim /
DECISIONS / roadmap / README first. Research basis: users scan, they read ~20–28% of words (NN/g F-pattern); progressive disclosure;
WCAG 1.4.1 (never colour alone). Owner screenshots: My trades (PAPER account, Your figures, Experience level, Contract notes,
Portfolio risk, Decision tools) — long paragraphs on every card.
Rules (apply uniformly across the whole product; keep every piece of information — MOVE text, never delete it; ADR rules unchanged):
U1 Card = title + numbers / table / chart + at most ONE short visible line (≤ ~15 words). Every explanation, method note, "how it is
   computed", source line goes into ONE shared collapsed "ⓘ Details" disclosure (closed by default, open state remembered per card,
   keyboard + screen-reader accessible). No paragraph above the first number.
U2 Direction colour everywhere a value has a direction (P&L, change, % change, won / lost, drawdown, OI change, equity vs start):
   ONE shared <Signed> component: positive = --up + "▲" + "+", negative = --down + "▼" + "−", zero = muted grey, tabular-nums,
   Indian grouping, colour-blind palette from the existing tokens. Colour only for direction, never decoration.
U3 Label / value lists → the shared Table / key-value table; progress items → "0 of 30" with a thin progress bar (Experience level,
   20-trade figures). Lists of 2+ items written in a sentence → bullets (max 5 visible, rest in Details).
U4 Empty states: one line + one action (e.g. "No PAPER trades yet · figures appear at 20 closed trades" + Start a PAPER trade).
U5 Repeated disclaimers ("Information only… nothing here places…", "Phase 0 has PAPER only"): one compact page footer line instead
   of per card; the SEBI signals-not-advice wording stays visible once per page (owner default) and in Details on each card.
U6 Guard: a CI test fails when a card renders visible body text over the limit outside Details (allow-list for legal pages).
Order: PR 1 shared primitives (Details, Signed, KeyValueTable, ProgressStat, EmptyState) + guard + Storybook; PR 2 My trades;
PR 3 Today; PR 4 Terminal; PR 5 Charts; then the remaining pages in the UX-10 order. Behind ui_v2. Per PR: before / after phone +
desktop, light + dark, in ONE comparison page, with the visible-word count per page before → after. Self-review every diff;
migration: no. Report ≤ 20 lines.
