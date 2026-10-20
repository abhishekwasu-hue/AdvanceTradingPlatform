"""Drift check: does a newer Trade revision still give the outputs ATP's Daily trend port is pinned to?

    git -C /path/to/trade archive <new rev> | tar -x -C /tmp/trade_new        # a read-only export, never the repo itself
    python drift_check.py /tmp/trade_new <the full 40-character SHA of that rev>

Regenerates the goldens from that export with make_golden.py (same seeded inputs) into a temporary file and compares
them with the pinned golden.json.gz case by case - the pinned file is read from the private fixtures store
(ATP_PRIVATE_FIXTURES; owner decision PR-2, docs/PUBLIC_REPO_POLICY.md section 5). Prints one line per changed section
with the first changed cases, and exits 1 when anything changed (0 when nothing did). A change is ported by a reviewed
PR (code + goldens + the pinned revision in app/price_action/daily_trend/settings.py) - never by regenerating the goldens alone.
"""
import gzip
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(HERE))))      # backend/, for tests.private_fixtures
from tests import private_fixtures as PF  # noqa: E402

GOLDEN = "backend/tests/fixtures/daily_trend/golden.json.gz"


def load(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    trade_dir, rev = sys.argv[1], sys.argv[2]
    found = PF.locate(GOLDEN)
    if found is None:
        raise SystemExit(f"{GOLDEN} not found: set {PF.ENV} to the private fixtures store "
                         f"(python3 scripts/export_private_fixtures.py --to DIR fills it)")
    pinned = load(str(found))
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "golden.json.gz")
        subprocess.run([sys.executable, os.path.join(HERE, "make_golden.py"), trade_dir, rev, out], check=True,
                       stdout=subprocess.DEVNULL)
        fresh = load(out)
    changed = 0
    print(f"pinned {pinned['trade_revision']} -> {rev}")
    for key in sorted(set(pinned) | set(fresh)):
        if key in ("trade_revision", "coverage"):
            continue
        a, b = pinned.get(key), fresh.get(key)
        if a == b:
            continue
        changed += 1
        if isinstance(a, list) and isinstance(b, list):
            diff = [i for i in range(max(len(a), len(b))) if i >= len(a) or i >= len(b) or a[i] != b[i]]
            print(f"  {key}: {len(diff)} of {max(len(a), len(b))} cases changed (first: {diff[:10]})")
        else:
            print(f"  {key}: changed")
    print("no drift" if not changed else f"{changed} section(s) changed - port the change in a reviewed PR")
    sys.exit(1 if changed else 0)


if __name__ == "__main__":
    main()
