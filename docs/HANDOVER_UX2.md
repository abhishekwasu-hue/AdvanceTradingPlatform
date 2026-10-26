# Handover - Session B, UX-2 UI redesign (26 Oct, night)
Plan: docs/design/UX2_PLAN.md (owner OK; self-merge on green CI, DECISIONS 26 Oct). Rules: CLAUDE.md §6.
Done (merged): PR 1 H9 #559, PR 2 UX-6 #560 (M1 complete; comparison page sent), PR 4 UX-5 combobox #564.
Open: PR 3 UX-2 symbol search #563 (one frontend re-run pending); CI timeout 40->60 #561; docs #558.
Backend CI flakes (IST-midnight clock tests): Session A fixes in #565; after it merges, merge main into #561/#563.
Next: PR 5 UX-7 live feel -> PR 6 UX-4 font -> PR 7 UX-8 density (M3); PR 8 Terminal only after Session A's T2/T3.
Local tips: run Playwright on a worktree with its own port config; never `git add -A` there (node_modules link).
