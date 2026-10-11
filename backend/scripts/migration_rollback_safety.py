#!/usr/bin/env python
"""Z11 / M5: may an older release run on the schema a newer migration left behind?

Every migration in alembic/versions declares, next to its revision ids:

    ROLLBACK_SAFE = True     # the previous release's code keeps working on the upgraded schema (purely additive)
    ROLLBACK_SAFE = False    # it does not: a code-only rollback past this migration is refused; use --restore-db

Two uses:

    python scripts/migration_rollback_safety.py lint
        CI (tests/test_z11_migration_rollback_safety.py). Fails when a migration does not declare ROLLBACK_SAFE, or
        declares True while its upgrade() drops / renames a table or column, changes a column type, makes a column
        NOT NULL, adds a NOT NULL column without a server default, adds a UNIQUE / CHECK constraint, or runs SQL doing any
        of that. A reviewed exception
        sets ROLLBACK_SAFE_REASON = "<why the old code is unaffected>" next to ROLLBACK_SAFE = True.

    python scripts/migration_rollback_safety.py check --db-revision <rev> --target-head <rev>
        deploy/atp.sh rollback, run in the NEWER image (the one that knows every revision). Exit 0 only when <rev> is a
        descendant of <target-head> and every migration between them is ROLLBACK_SAFE; prints one JSON line.
"""
import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

BACKEND = Path(__file__).resolve().parents[1]
VERSIONS = BACKEND / "alembic" / "versions"
_RISKY_SQL = re.compile(r"\b(drop\s+(table|column)|rename\s+(to|column)|alter\s+column\s+\w+\s+(set\s+data\s+)?type|set\s+not\s+null)\b", re.I)
_ALWAYS_RISKY = {"drop_table", "drop_column", "rename_table",
                 # a new UNIQUE / CHECK constraint on an existing table can refuse a write the previous release still makes
                 "create_unique_constraint", "create_check_constraint"}


def _kw(call: ast.Call, name: str):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def _is_false(node) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _strings(node) -> List[str]:
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def risky_operations(source: str) -> List[str]:
    """What upgrade() does that an older release's code may not survive (heuristic, errs on 'risky')."""
    tree = ast.parse(source)
    upgrade = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"), None)
    if upgrade is None:
        return []
    found: List[str] = []
    for call in (n for n in ast.walk(upgrade) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
        op = call.func.attr
        if op in _ALWAYS_RISKY:
            found.append(op)
        elif op == "alter_column":
            for kw in ("new_column_name", "type_"):
                if _kw(call, kw) is not None:
                    found.append(f"alter_column {kw}")
            if _is_false(_kw(call, "nullable")):
                found.append("alter_column nullable=False")
        elif op == "add_column":
            for col in (a for a in call.args if isinstance(a, ast.Call)):
                if _is_false(_kw(col, "nullable")) and _kw(col, "server_default") is None:
                    found.append("add_column NOT NULL without server_default")
        elif op == "execute":
            if any(_RISKY_SQL.search(s) for s in _strings(call)):
                found.append("execute: risky SQL")
    return found


def _declared(source: str) -> Dict[str, object]:
    out: Dict[str, object] = {}
    for node in ast.parse(source).body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id in ("ROLLBACK_SAFE", "ROLLBACK_SAFE_REASON") and isinstance(node.value, ast.Constant):
                    out[t.id] = node.value.value
    return out


def lint(versions: Path = VERSIONS) -> List[str]:
    problems = []
    for path in sorted(versions.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        declared = _declared(source)
        safe = declared.get("ROLLBACK_SAFE")
        if not isinstance(safe, bool):
            risky = sorted(set(risky_operations(source)))
            hint = (f"ROLLBACK_SAFE = False    # {', '.join(risky)}" if risky else
                    "ROLLBACK_SAFE = True     # additive: the previous release runs on this schema")
            problems.append(f"{path.name}: missing ROLLBACK_SAFE (Z11). Add this line under `down_revision` / `depends_on`:\n"
                            f"    {hint}\n"
                            "  True only when the previous release's code keeps working on the upgraded schema; otherwise False "
                            "(a code-only rollback past it is then refused and deploy/atp.sh rollback --restore-db is used).")
            continue
        risky = risky_operations(source)
        if safe and risky and not declared.get("ROLLBACK_SAFE_REASON"):
            problems.append(f"{path.name}: ROLLBACK_SAFE = True but upgrade() has {', '.join(sorted(set(risky)))} (Z11). Either set\n"
                            "    ROLLBACK_SAFE = False\n"
                            "  or, after review, keep True and add next to it\n"
                            "    ROLLBACK_SAFE_REASON = \"<why the previous release's code is unaffected>\"")
    return problems


def _module_flag(path: Optional[str]) -> Optional[bool]:
    if not path or not os.path.exists(path):
        return None
    value = _declared(Path(path).read_text(encoding="utf-8")).get("ROLLBACK_SAFE")
    return value if isinstance(value, bool) else None


def check(db_revision: str, target_head: str) -> Dict[str, object]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    result: Dict[str, object] = {"db": db_revision, "target_head": target_head, "descendant": False, "between": [], "unsafe": []}
    if db_revision == target_head:
        result["descendant"] = True
        return result
    try:
        script.get_revision(db_revision)
        script.get_revision(target_head)
    except Exception:  # noqa: BLE001 - unknown to this image too
        return result
    between, unsafe, frontier, seen = [], [], [db_revision], set()
    while frontier:
        rev_id = frontier.pop()
        if rev_id in seen or rev_id == target_head:
            if rev_id == target_head:
                result["descendant"] = True
            continue
        seen.add(rev_id)
        rev = script.get_revision(rev_id)
        if rev is None:
            continue
        between.append(rev_id)
        if _module_flag(rev.path) is not True:
            unsafe.append(rev_id)
        down = rev.down_revision
        frontier.extend(down if isinstance(down, (tuple, list)) else ([down] if down else []))
    result["between"], result["unsafe"] = between, unsafe
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("lint")
    c = sub.add_parser("check")
    c.add_argument("--db-revision", required=True)
    c.add_argument("--target-head", required=True)
    args = parser.parse_args()
    if args.cmd == "lint":
        problems = lint()
        for p in problems:
            print(p)
        return 1 if problems else 0
    result = check(args.db_revision, args.target_head)
    print(json.dumps(result))
    return 0 if result["descendant"] and not result["unsafe"] else 1


if __name__ == "__main__":
    sys.exit(main())
