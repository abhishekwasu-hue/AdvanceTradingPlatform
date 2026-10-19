# Project status (autonomous session)

Recovery point: read this file, then `git status` (changes are NOT committed — decision D-001), then IMPLEMENTATION_LOG.md.

* Branch `main` · starting commit `b63af78` (v2.2.0) · final: uncommitted working-tree changes = v2.3.0.
* Final verification: fresh venv 191 passed / 1 skipped; Docker 191 passed / 1 skipped; PostgreSQL test passed; ruff clean.
* Completed: batches 1–5 (see IMPLEMENTATION_LOG.md). Open owner questions: QUESTION_LOG.md. Next tasks: ROADMAP.md.
* v2.4.0 (follow-up session, also uncommitted): live 1-minute data from the user's own FYERS account
  (docs/LIVE_DATA_FYERS.md, decisions D-011..D-015, questions Q-001 update and Q-007). Results: docs/TEST_REPORT.md.
* v2.5.0 (same follow-up session, uncommitted): durable jobs + orphan recovery, forward tests, shared FYERS minute
  budget, report retention/quota, signal → chart/report navigation (D-016..D-018).
* Continuation audit (v2.5.1, uncommitted): AUDIT_FINDINGS.md A-001..A-006, PERFORMANCE_RESULTS.md. This ROADMAP.md
  is the improvement roadmap (IMPROVEMENT_ROADMAP equivalent; not duplicated).
* v2.6.0 (uncommitted): remaining-work batches B1–B5 (IMPLEMENTATION_LOG.md); open owner items Q-001, Q-002, Q-005, Q-007, Q-008..Q-014.
