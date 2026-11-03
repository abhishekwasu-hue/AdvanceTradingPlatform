"""FN1 (owner order P2-F): docs/FN1_CHECKLIST.md lists every flag of the admin flag list in exactly one feature row,
with its live M80 state, and docs/WORKING_RULES.md carries the definition of done D1-D7 the checklist ticks."""
import re
from pathlib import Path

from app.platform.flag_state import FLAG_STATUS

DOCS = Path(__file__).resolve().parents[2] / "docs"
CHECKLIST = (DOCS / "FN1_CHECKLIST.md").read_text(encoding="utf-8")
RULES = (DOCS / "WORKING_RULES.md").read_text(encoding="utf-8")

ABBREV = {"ready": "R", "behind_the_scenes": "BTS", "needs_setup": "SET", "decision": "DEC", "not_built": "NB"}
FLAG_CELL = re.compile(r"`([a-z0-9_]+)` (R|BTS|SET|DEC|NB)\b")


def _flag_cells() -> list[tuple[str, str]]:
    rows = [line for line in CHECKLIST.split("## The list", 1)[1].splitlines() if line.startswith("| ")]
    found: list[tuple[str, str]] = []
    assert rows[0].startswith("| # | Feature |")
    for row in rows[1:]:                     # past the header (the |---| separator does not start with "| ")
        cells = row.split("|")
        found.extend(FLAG_CELL.findall(cells[5]))   # the Flags (state) column
    return found


def test_the_table_is_read() -> None:
    assert len(_flag_cells()) > 100          # a broken parse must not pass vacuously


def test_every_flag_in_exactly_one_row() -> None:
    names = [name for name, _ in _flag_cells()]
    assert sorted(set(FLAG_STATUS) - set(names)) == []
    assert sorted(set(names) - set(FLAG_STATUS)) == []
    assert sorted({n for n in names if names.count(n) > 1}) == []


def test_states_match_the_admin_list() -> None:
    wrong = {name: (state, ABBREV[FLAG_STATUS[name].state]) for name, state in _flag_cells()
             if name in FLAG_STATUS and ABBREV[FLAG_STATUS[name].state] != state}
    assert wrong == {}


def test_definition_of_done_in_working_rules() -> None:
    section = RULES.split("## 4. Definition of done for a feature", 1)
    assert len(section) == 2
    for point in ("D1", "D2", "D3", "D4", "D5", "D6", "D7"):
        assert f"**{point} " in section[1].split("\n## ", 1)[0]
    assert "docs/FN1_CHECKLIST.md" in section[1]
