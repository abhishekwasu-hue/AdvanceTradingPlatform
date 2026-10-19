# Test results (this session) — all commands actually executed

| When | Environment | Command | Result |
|---|---|---|---|
| Baseline (b63af78) | dev machine, root (sandbox active) | `python -m pytest -q` | 163 passed, 1 skipped |
| After batch 1 | same | `python -m pytest -q` | 172 passed, 1 skipped |
| After batch 3 | same | `python -m pytest -q` | 188 passed, 1 skipped |
| Final | fresh venv in /opt from requirements-dev.txt | `ruff check .` | All checks passed |
| Final | fresh venv in /opt | `python -m pytest -q -rs` | **191 passed, 1 skipped** (1m55s); skipped = PostgreSQL test without AMW_TEST_PG_URL |
| Final | production Docker image, CAP_SYS_ADMIN | `python -m pytest -q` | **191 passed, 1 skipped** (2m02s) |
| Final | local PostgreSQL 16 (DB previously at schema v3) | `AMW_TEST_PG_URL=… python -m pytest -k postgres` | 1 passed; schema upgraded to v5 in place |
| Final | API + Streamlit started | `/health/ready`, `/_stcore/health`, `/api/v1/plan` | ready (schema 5) / ok / 200 |

Not run: GitHub Actions (no repository access), Windows execution, live OpenAI/Anthropic/Telegram/SMTP calls (no
credentials; mocked), load tests. Failed: none at the end of the session.

## Continuation audit (2026-10-19, after v2.5.0)
| When | Environment | Command | Result |
|---|---|---|---|
| Baseline | dev machine, root | `python -m pytest -q` | 229 passed, 2 skipped |
| After A-001/A-002/A-004 | same | `python -m pytest -q` | 1 failed (A-003, intermittent), 230 passed, 2 skipped |
| After A-003 | same | `python -m pytest -q` | 231 passed, 2 skipped |
| Fix verification | same | regression tests with the fix reverted | A-001 and A-002 tests FAIL without the fix, PASS with it |
| Clean copy, new venv, PostgreSQL 16 | `AMW_TEST_PG_URL=… pytest -q` | first run: 1 failed (PostgreSQL contract test caught A-002 `rowcount` issue) → fixed with RETURNING → **233 passed** |
| Docker image (v2.5.1), CAP_SYS_ADMIN | `python -m pytest -q` | **231 passed, 2 skipped** |
| Dev machine (v2.5.1) | `python -m pytest -q`, `ruff check .` | 231 passed, 2 skipped; ruff clean |

## v2.6.0 (remaining-work session)
| Environment | Command | Result |
|---|---|---|
| Dev machine, after B1–B5 (first run) | `python -m pytest -q` | 1 failed (migration test fixture lacked the new v9 index → fixture corrected + stronger assertion), 262 passed, 2 skipped |
| Dev machine (final) | `python -m pytest -q` · `ruff check .` | **263 passed, 2 skipped** · clean |
| Clean copy, new venv, PostgreSQL 16 (DB at v7 upgraded in place to v9; index created) | `AMW_TEST_PG_URL=… pytest -q` | **265 passed** |
| Docker image, CAP_SYS_ADMIN | `python -m pytest -q` | **263 passed, 2 skipped** |
| Built-in scan fingerprint | /tmp benchmark | unchanged (a95857642897f5c5) |
| Earlier in session | full suite under load | 1 failure of test_local_trusted_mode_other_runtimes[javascript] (T-001), passed 5/5 on rerun and in all later full runs |

## v2.7.0
| Environment | Command | Result |
|---|---|---|
| Dev machine | `python -m pytest -q` · `ruff check .` | **272 passed, 2 skipped** · clean |
| Clean copy, new venv, PostgreSQL 16 | `AMW_TEST_PG_URL=… pytest -q` | **274 passed** |
| Docker image, CAP_SYS_ADMIN | `python -m pytest -q` | **272 passed, 2 skipped** |
| Fix check A-008 | regression test with the old behaviour patched in | fails (as expected); passes with the fix |

## v2.8.0
| Environment | Command | Result |
|---|---|---|
| Dev machine | `python -m pytest -q` · `ruff check .` | **275 passed, 2 skipped** · clean |
| Clean copy, new venv, PostgreSQL 16 (DB upgraded to schema 11) | `AMW_TEST_PG_URL=… pytest -q` | **277 passed** |
| Docker image, CAP_SYS_ADMIN | `python -m pytest -q` | **275 passed, 2 skipped** |
| Multi-process API | `uvicorn api_server:app --workers 4` + 20 concurrent clients | 0 errors; see PERFORMANCE_RESULTS.md |

## v2.9.0
| Environment | Command | Result |
|---|---|---|
| Dev machine (first runs) | targeted suites | expected failures from the new rules: report-mismatch test edited a stored payload (now blocked by A-005 trigger → test simulates a provider revision); retention tests exposed A-009 (fixed); two tests aligned with the new filename pattern and the 30-minute delivery protection |
| Dev machine (final) | `python -m pytest -q` · `ruff check .` | **291 passed, 2 skipped** · clean |
| Clean copy, new venv, PostgreSQL 16 (schema upgraded to 13) | `AMW_TEST_PG_URL=… pytest -q` | **293 passed** |
| Docker image, CAP_SYS_ADMIN | `python -m pytest -q` | **291 passed, 2 skipped** |
| PostgreSQL direct check | /tmp/pgimm.py | payload/score updates blocked by trigger; transition True then retry False; 2 events |
| PostgreSQL workers 1/2/4/8 load | uvicorn + 20 concurrent clients | 0 errors, 0 tracebacks (PERFORMANCE_RESULTS v2.9.0) |

