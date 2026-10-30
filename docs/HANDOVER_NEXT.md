# Next session - exact steps (30 Oct)

1. #729 / #730: merge on green (`gh api -X POST .../pulls/N/ccr/ready_for_review`, then PUT merge with the head sha).
   A screenshot near the 2% line: merge main in, `npm run build`, `CI=1 npx playwright test --project=phone <spec>
   --update-snapshots=all`, look at the image, `git checkout` any baseline that changed but was passing, update the
   hash in scripts/check_public_repo.py for pinned files (copy the line the guard prints on stderr).
2. PROSE-1 #732 (branch claude/prose-hide, main merged at 9880f358+): CI frontend red, 194 e2e.
   - 8 functional tests to fix first (reproduce with `CI=1 npx playwright test <spec> --retries=0 -g "<name>"`):
     topbar-title-v2 H9 /strategies at 360/390 (REAL: the new (?) for the subtitle pushes the phone bar past the edge -
     fix in src/shell/TopBarV2.tsx, e.g. let the (?) sit inside the title's wrap or drop a gap); optionsBuilder
     "flag is off"; day-one-trader "Today -> watchlists"; palette-v3 "no page subtitle on My positions";
     portfolio-v2 tiles (toHaveAttribute); settings-index "Telegram first" (strict mode: 4 `#channels summary` -
     scope the locator); signals-day-one "first visit". Most look for text now inside a closed Details: open it in the
     test (click its summary) or assert toBeAttached - never move the text back out.
   - Then regenerate the ~180 screenshot baselines (one spec at a time, port 4173 is shared - no parallel runs), view
     ~10, fix pinned hashes, push, mark ready, merge on green.
   - Guard: src/ui/proseGuard.test.tsx (ALLOWED = owner item 4 + one switch label); scanner src/ui/proseScan.ts.
3. Then the docs: roadmap status rows (TERM-2, CP-UX, SB-UX, CT-1, PROSE-1 merged; REV-1 waits) - no new PR: ride
   the next code PR.
4. Final report to Abhi (Marathi, Latin letters) with the deploy line and the 4 migrations.
