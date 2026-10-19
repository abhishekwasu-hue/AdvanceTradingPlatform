# AMW AI Stock Screener v2.9 - reference source (not run, not imported)

This folder is the owner-supplied AMW AI Stock Screener v2.9 source (19 Oct), kept as the **reference for porting its
features into ATP**. It is not part of ATP's build, tests, lint or deployment:

- Nothing in `backend/` or `frontend/` imports from it, and no Dockerfile, compose file or CI job uses it.
- Every feature is ported natively into ATP (FastAPI + React, tenancy, flags off by default, English, ADR-0006), slice by
  slice, as planned in [docs/design/AMW_PORT.md](../../design/AMW_PORT.md). A ported slice cites the files here it came
  from; the copy here is never edited to make ATP work.
- When the port is complete, this folder can be deleted.

Changes from the supplied archive:

- `.git/`, `__pycache__/` and `*.pyc` left out.
- `tests/test_ai_byok.py`, `tests/test_feed.py`, `tests/test_ui.py`: the fake provider keys, FYERS app id / secret /
  token and UI secret in the test data were renamed to carry `FAKEFAKE` (ATP's marker for
  obviously fake keys, allow-listed in `.gitleaks.toml`), so the repository's secret scan does not flag them. They were
  never real keys.
