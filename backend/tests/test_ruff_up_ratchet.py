"""AUD-G1.6: ruff's pyupgrade family (UP) as a ratchet, not a gate.

The code base had ~19,700 UP findings when the family was considered (mostly `List[...]` / `Optional[...]` annotations and
`timezone.utc`). Rewriting them all in one PR is a large, risky diff, so UP is NOT in backend/ruff.toml's `select`.
Instead this test counts the findings per rule over the same paths the CI lint job checks and fails when any rule's count
rises above the recorded baseline: new code is written the modern way, old code is upgraded when it is touched.

When a PR lowers a count, lower the number here in the same PR (the message says which). Never raise a number.
The CI `lint` job runs this file with the pinned ruff; elsewhere (no ruff installed) it skips.
"""
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
PATHS = ("app", "tests", "evals")            # as the CI lint job: `ruff check app tests evals`

# ruff 0.16.10 (the CI pin), recorded on the AUD-G1.6 branch. Total 19,660.
BASELINE = {
    "UP006": 9655,   # non-pep585-annotation: List[int] -> list[int]
    "UP045": 6397,   # non-pep604-annotation-optional: Optional[X] -> X | None
    "UP035": 1818,   # deprecated-import: typing.List / Dict / Callable ...
    "UP017": 1452,   # datetime-timezone-utc: timezone.utc -> datetime.UTC
    "UP037": 196,    # quoted-annotation
    "UP007": 60,     # non-pep604-annotation-union: Union[X, Y] -> X | Y
    "UP042": 57,     # replace-str-enum
    "UP041": 11,     # timeout-error-alias: asyncio.TimeoutError -> TimeoutError
    "UP034": 4,      # extraneous-parentheses
    "UP033": 4,      # lru-cache-with-maxsize-none
    "UP031": 3,      # printf-string-formatting
    "UP012": 3,      # unnecessary-encode-utf8
}


def _ruff() -> list:
    """The ruff installed beside this interpreter (the CI pin), else one on PATH, else skip."""
    try:
        import ruff  # noqa: F401 - only probing that the module is installed
        return [sys.executable, "-m", "ruff"]
    except ImportError:
        exe = shutil.which("ruff")
        if not exe:
            pytest.skip("ruff is not installed here; the CI lint job runs this ratchet")
        return [exe]


def _counts() -> Counter:
    done = subprocess.run([*_ruff(), "check", *PATHS, "--select", "UP", "--output-format", "json", "--exit-zero", "--no-cache"],
                          cwd=BACKEND, capture_output=True, text=True, check=True)
    return Counter(item["code"] for item in json.loads(done.stdout or "[]"))


def test_pyupgrade_findings_never_rise():
    counts = _counts()
    risen = {code: (n, BASELINE.get(code, 0)) for code, n in counts.items() if n > BASELINE.get(code, 0)}
    assert not risen, ("ruff UP findings rose above the AUD-G1.6 baseline (write new code with built-in generics, `X | None` "
                       "and `datetime.UTC`; run `ruff check --select UP <your files>`): "
                       + ", ".join(f"{code} {n} > {base}" for code, (n, base) in sorted(risen.items())))
    lowered = {code: (counts.get(code, 0), base) for code, base in BASELINE.items() if counts.get(code, 0) < base}
    if lowered:
        print("ruff UP ratchet: lower BASELINE in tests/test_ruff_up_ratchet.py: "
              + ", ".join(f"{code} {base} -> {n}" for code, (n, base) in sorted(lowered.items())))
