"""Part D (+ v1.2 jurisdiction rules engine): compliance rules as data.

A rule-set is one JSON file in `app/compliance/rulesets/` (`<id>.json`): its jurisdiction, version, the date it took
effect, and a list of rules. Each rule has an id (`IN-SEBI.ops.throttle`), the source it implements (circular / exchange
/ broker policy), its parameters (numbers live here, never in code), where it is enforced (`code`), the tests that
prove it (`tests`), the feature flag that switches its enforcement on (`flag`, None = always on) and a status
(`enforced` | `partial` | `planned`). SEBI is one rule-set among the ones v1.2/v1.3 add (crypto venues, IBKR).

docs/COMPLIANCE_IN.md is the human table of the same rules; tests/test_compliance_rules.py keeps the two in step and
checks that every test a rule names exists.
"""
import json
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

RULESETS_DIR = Path(__file__).resolve().parent / "rulesets"
STATUSES = ("enforced", "partial", "planned")
DEFAULT_RULESET = "IN-SEBI"


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    source: str
    status: str
    params: Dict[str, Any] = field(default_factory=dict)
    code: List[str] = field(default_factory=list)
    tests: List[str] = field(default_factory=list)
    flag: Optional[str] = None


@dataclass(frozen=True)
class RuleSet:
    id: str
    jurisdiction: str
    version: str
    effective_from: date
    rules: Dict[str, Rule]

    def rule(self, rule_id: str) -> Rule:
        return self.rules[rule_id]

    def param(self, rule_id: str, name: str, default: Any = None) -> Any:
        return self.rules[rule_id].params.get(name, default)


def _parse(raw: Dict[str, Any]) -> RuleSet:
    rules: Dict[str, Rule] = {}
    for item in raw["rules"]:
        rule = Rule(id=item["id"], title=item["title"], source=item["source"], status=item["status"],
                    params=dict(item.get("params") or {}), code=list(item.get("code") or []),
                    tests=list(item.get("tests") or []), flag=item.get("flag"))
        if rule.status not in STATUSES:
            raise ValueError(f"{rule.id}: status must be one of {STATUSES}")
        if not rule.id.startswith(raw["id"] + "."):
            raise ValueError(f"{rule.id}: rule ids start with the rule-set id {raw['id']}")
        if rule.id in rules:
            raise ValueError(f"duplicate rule id {rule.id}")
        rules[rule.id] = rule
    return RuleSet(id=raw["id"], jurisdiction=raw["jurisdiction"], version=str(raw["version"]),
                   effective_from=date.fromisoformat(raw["effective_from"]), rules=rules)


@lru_cache(maxsize=None)
def load(ruleset_id: str = DEFAULT_RULESET) -> RuleSet:
    path = RULESETS_DIR / f"{ruleset_id.lower().replace('-', '_')}.json"
    return _parse(json.loads(path.read_text(encoding="utf-8")))


def available() -> List[str]:
    return sorted(json.loads(p.read_text(encoding="utf-8"))["id"] for p in RULESETS_DIR.glob("*.json"))
