# ATP ORDER M79 - Engine takeover: port engine-snapshot-v2 (supersedes v1) (owner order, 1 Nov, verbatim)

M79 (ATP) - Engine takeover: port engine-snapshot-v2 (supersedes v1). ONE message; read this first, then the full plan file named below.

Plan file (read whole, it is the work list T1-T14): private repo abhishekwasu-hue/trade-data, branch claude/fix-proof-gallery, file handover/2026-10-31_ATP_takeover/ATP_SESSION_PROMPT.md (with PLAN.md and M65_REPORT.md beside it). It was written for v1; apply these corrections everywhere in it:

C1 Snapshot = engine-snapshot-v2, Trade commit 813358bc1064645fcf2ca005959d9658804633e3 (Trade main; includes the BREAK-2 follow-through target recency fix, Trade DECISIONS 151). v1 (79feb62) is superseded: do NOT merge atp-private branch import/engine-snapshot-v1; merge import/engine-snapshot-v2 instead (review it as step 1 says).
C2 Tag: do not wait for the git tag. Verify the snapshot by commit: IMPORT_MANIFEST.json commit = 813358b..., and the fixtures MANIFEST.json commit = 813358b... (its source_commit 8e2b079 has the identical tree). If the tag appears on Trade later, record it; nothing else changes.
C3 Fixtures = trade-data parity/engine-snapshot-v2/ (188 files, sha256 in MANIFEST.json), currently on trade-data branch claude/fix-proof-gallery (not main). The host copy command must name that branch (or main once merged) and stop on any sha256 mismatch. parity/engine-snapshot-v1 and the layer1..4 folders are superseded.
C4 Existing ATP work: anything already built against v1 (M66 parity CI, chart_analyze provider, docs/TRADE_PORT_MAP 3a "Stable tag") is re-pointed to v2 in the same PRs; never mix two snapshots in one layer.
C5 Overseer kit: atp-private PR #1 (claude/overseer-method-kit, docs only) - review and merge it first; it holds METHOD_DECISIONS and answer key batch 1 v4 used for review.
C6 Known test failures in the copy (515 pass / 10 fail / 17 errors) are left-out ATP-GENERIC deps (config, cloud_db, repo parquet, scripts read by path): each becomes an ATP adapter or a visible skip with reason, never a logic change.

Everything else in the plan file stands: AS IS copy, byte-equal parity per layer (T1-T6) before English modules, public ATP repo holds only the provider/loader boundary (no method text, no fixture, no Trade logic), flags OFF, PAPER only, exits never blocked, AI never places/blocks/changes orders, no paid API or broker calls without owner approval, owner runs ONE host command block, draft PRs one step at a time, merge on green CI. Order-path PRs one at a time with review.
Owner prerequisites from the plan (step "What the owner has done"): report which are missing in ONE line each with the exact click path; do not stop other steps for them.
Report in Marathi, <= 25 lines: per step done / next, parity result per layer (cases equal / total), any port difference found (a difference = port bug, never a fixture change).
