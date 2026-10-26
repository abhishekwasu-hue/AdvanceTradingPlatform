# Handover now (26 Oct 2026)
Plan (owner-approved, traders first): docs/ROADMAP.md + HANDOVER order PR-3. Rules: CLAUDE.md §6 + TB-10..TB-16.
Step 0 open:
- #540 TF-1: both owner fixes pushed (9c51f515; icon not "More", phone bottom sheet). Run CI; regenerate only the
  picker baselines that fail; merge.
- Merge on green: #551 (this), docs #548 #549 #550. Park #536 (DM), #438, #452.
- Then ONE deploy block, no migration (ships #539, #543-#547 data fixes).
Step 1 data: owner presses "Enable trader features", runs `atp.sh lake-backfill`, sends probe-candles / probe-chain;
then DATA-H DH4/DH5/DH2 close-out, DH6 (22,600), sector/watchlist lists, P1-8 phone Watchlist entry.
Then: day-one test (J1, J3) -> Terminal T2/T3 + CX PR 3 + SX -> FN2-FN4 -> FN5 + CP-V -> alerts.
Parallel docs: M49 step 1 (audit + spec + mockups, owner approves first). M45 after step 2.
