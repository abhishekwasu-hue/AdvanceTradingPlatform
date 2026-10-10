"""Part D: the SEBI rule-set is data, and the human table, the code and the tests agree with it.

- the rule-set loads; ids are unique and prefixed; statuses are known; an enforced rule names code and tests;
- every test a rule names exists (file + function), every code path exists, every flag is a real config setting;
- docs/COMPLIANCE_IN.md has one row per rule with the same status;
- a malformed rule-set is refused (wrong prefix, unknown status, duplicate id).
"""
import ast
import json
from pathlib import Path

import pytest

from app.compliance import rules
from app.core import config

BACKEND = Path(__file__).resolve().parents[1]
DOC = BACKEND.parent / "docs/COMPLIANCE_IN.md"


def _test_names(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_the_sebi_rule_set_loads_and_is_well_formed():
    rs = rules.load("IN-SEBI")
    assert rs.jurisdiction == "IN" and "IN-SEBI" in rules.available() and len(rs.rules) >= 10
    for rule in rs.rules.values():
        assert rule.status in rules.STATUSES and rule.source and rule.title
        if rule.status == "enforced":
            assert rule.code and rule.tests, rule.id
    assert rs.param("IN-SEBI.ops.throttle", "ops_per_second") > 0             # numbers come from the file


def test_every_named_test_code_path_and_flag_exists():
    rs = rules.load()
    for rule in rs.rules.values():
        for ref in rule.tests:
            file, _, name = ref.partition("::")
            path = BACKEND / file
            assert path.exists(), (rule.id, file)
            assert name in _test_names(path), (rule.id, ref)
        for code in rule.code:
            assert (BACKEND / code).exists(), (rule.id, code)
        if rule.flag and rule.status in ("enforced", "partial"):
            assert hasattr(config, rule.flag), (rule.id, rule.flag)       # planned flags arrive with their code


def test_the_compliance_doc_is_generated_from_the_rule_set():
    import sys
    sys.path.insert(0, str(BACKEND / "scripts"))
    from compliance_doc import render
    assert DOC.read_text(encoding="utf-8") == render(), "run: python backend/scripts/compliance_doc.py"


def test_the_compliance_doc_has_one_row_per_rule_with_its_status():
    doc = DOC.read_text(encoding="utf-8")
    for rule in rules.load().rules.values():
        row = next((line for line in doc.splitlines() if line.startswith(f"| `{rule.id}` |")), None)
        assert row is not None, rule.id
        assert f"| {rule.status} |" in row, (rule.id, rule.status)


@pytest.mark.parametrize("bad,message", [
    ({"id": "OTHER.x", "title": "t", "source": "s", "status": "enforced"}, "start with the rule-set id"),
    ({"id": "IN-SEBI.x", "title": "t", "source": "s", "status": "maybe"}, "status must be one of"),
])
def test_a_malformed_rule_set_is_refused(bad, message):
    raw = {"id": "IN-SEBI", "jurisdiction": "IN", "version": "1", "effective_from": "2025-08-01", "rules": [bad]}
    with pytest.raises(ValueError, match=message):
        rules._parse(raw)
    dup = {**raw, "rules": [{"id": "IN-SEBI.a", "title": "t", "source": "s", "status": "planned"}] * 2}
    with pytest.raises(ValueError, match="duplicate"):
        rules._parse(dup)
    json.dumps(raw)
