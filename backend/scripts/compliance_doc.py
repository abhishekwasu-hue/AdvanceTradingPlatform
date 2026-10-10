"""Regenerates docs/COMPLIANCE_IN.md from the IN-SEBI rule-set (the JSON is the source of truth).

    python scripts/compliance_doc.py            # writes the file
    python scripts/compliance_doc.py --check    # exit 1 when the file is out of date (CI: tests/test_compliance_rules.py)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.compliance.rules import load  # noqa: E402

DOC = Path(__file__).resolve().parents[2] / "docs/COMPLIANCE_IN.md"


def render() -> str:
    r = load("IN-SEBI")
    lines = ["# Compliance - India (SEBI / NSE retail-algo framework)", "",
             f"Rule-set `IN-SEBI` (`backend/app/compliance/rulesets/in_sebi.json`, version {r.version}, in force from {r.effective_from}). One row per rule:",
             "what it implements, where the code enforces it, the tests that prove it, and the flag that switches enforcement on.",
             "The JSON file is the source of truth (parameters live there, never in code); this page is generated from it",
             "(`python backend/scripts/compliance_doc.py`) and `tests/test_compliance_rules.py` fails when it is out of date or a",
             "named test does not exist. Design: `docs/design/D_SEBI.md`.", "",
             "Status: **enforced** (code + tests), **partial** (some of it), **planned** (design only, PR order in the design note).", "",
             "| Rule | What | Source | Status | Code | Tests | Flag |", "|---|---|---|---|---|---|---|"]
    for x in r.rules.values():
        code = "<br>".join(f"`{c}`" for c in x.code) or "-"
        tests = "<br>".join(f"`{t.split('::')[-1]}`" for t in x.tests) or "-"
        lines.append(f"| `{x.id}` | {x.title} | {x.source} | {x.status} | {code} | {tests} | {f'`{x.flag}`' if x.flag else '-'} |")
    lines += ["", "Parameters (current values in the JSON):", ""]
    for x in r.rules.values():
        if x.params:
            lines.append(f"- `{x.id}`: " + ", ".join(f"`{k}` = `{v}`" for k, v in x.params.items()))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        sys.exit(0 if DOC.read_text(encoding="utf-8") == text else 1)
    DOC.write_text(text, encoding="utf-8")
