#!/usr/bin/env python
"""Z11 / M5: may an older release run on the schema a newer migration left behind?

Every migration in alembic/versions declares, next to its revision ids:

    ROLLBACK_SAFE = True     # the previous release's code keeps working on the upgraded schema (purely additive)
    ROLLBACK_SAFE = False    # it does not: a code-only rollback past this migration is refused; use --restore-db

Two uses:

    python scripts/migration_rollback_safety.py lint
        CI (the lint job, and tests/test_z11_deploy_bundle.py). Fails when a migration does not declare ROLLBACK_SAFE, or
        declares True while its upgrade() drops / renames a table or column, changes a column type, makes a column
        NOT NULL, drops a server default, adds a NOT NULL column without a server default, adds a UNIQUE / CHECK / FOREIGN
        KEY constraint or a unique index to a table that existed before, or runs SQL doing any of that or rewriting rows
        (UPDATE / DELETE). A reviewed exception
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
_RISKY_SQL = re.compile(r"\b(drop\s+(table|column)|rename\s+(to|column)|alter\s+column\s+\w+\s+(set\s+data\s+)?type|set\s+not\s+null"
                        r"|drop\s+default|add\s+constraint|create\s+unique\s+index"
                        r"|update\s+(only\s+)?[\w.\"]+\s+set|delete\s+from)\b", re.I)   # schema-qualified / quoted names too
_ALWAYS_RISKY = {"drop_table", "drop_column", "rename_table"}
# A new constraint or unique index on a table that existed before can refuse a write the previous release still makes.
# On a table created in the same upgrade() the previous release never writes it, so it is not counted there.
_CONSTRAINT_OPS = {"create_unique_constraint": 1, "create_check_constraint": 1, "create_foreign_key": 1}   # op -> table arg


def _kw(call: ast.Call, name: str):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def _is_false(node) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _strings(node) -> List[str]:
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _arg(call: ast.Call, index: int, name: str):
    value = call.args[index] if len(call.args) > index else _kw(call, name)
    return value.value if isinstance(value, ast.Constant) else None


def risky_operations(source: str) -> List[str]:
    """What upgrade() does that an older release's code may not survive (heuristic, errs on 'risky')."""
    tree = ast.parse(source)
    upgrade = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"), None)
    if upgrade is None:
        return []
    calls = [n for n in ast.walk(upgrade) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    created = {_arg(c, 0, "table_name") for c in calls if isinstance(c.func, ast.Attribute) and c.func.attr == "create_table"}
    # `with op.batch_alter_table("t") as batch_op:` - batch operations name no table; they act on the batch's table.
    batch_tables = {}
    for node in ast.walk(upgrade):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                ctx = item.context_expr
                if (isinstance(ctx, ast.Call) and isinstance(ctx.func, ast.Attribute) and ctx.func.attr == "batch_alter_table"
                        and isinstance(item.optional_vars, ast.Name)):
                    batch_tables[item.optional_vars.id] = _arg(ctx, 0, "table_name")
    found: List[str] = []
    for call in calls:
        func = call.func
        assert isinstance(func, ast.Attribute)
        op = func.attr
        owner = func.value.id if isinstance(func.value, ast.Name) else ""
        batch = owner in batch_tables
        if op in _CONSTRAINT_OPS or (op == "create_index" and _kw(call, "unique") is not None and not _is_false(_kw(call, "unique"))):
            if batch:          # batch_op.create_foreign_key(name, referent, ...), batch_op.create_index(name, cols, ...)
                table = batch_tables[owner]
            elif op == "create_index":
                table = _arg(call, 1, "table_name")
            else:
                table = _arg(call, _CONSTRAINT_OPS[op], "table_name" if op != "create_foreign_key" else "source_table")
            if table not in created:
                found.append(f"{'unique create_index' if op == 'create_index' else op} on an existing table")
        elif op in _ALWAYS_RISKY:
            found.append(op)
        elif op == "alter_column":
            for kw in ("new_column_name", "type_"):
                if _kw(call, kw) is not None:
                    found.append(f"alter_column {kw}")
            if _is_false(_kw(call, "nullable")):
                found.append("alter_column nullable=False")
            default = _kw(call, "server_default")
            if isinstance(default, ast.Constant) and default.value is None:
                found.append("alter_column server_default=None")
        elif op == "add_column":
            for col in (a for a in call.args if isinstance(a, ast.Call)):
                if _is_false(_kw(col, "nullable")) and _kw(col, "server_default") is None:
                    found.append("add_column NOT NULL without server_default")
        elif op == "execute":
            texts = _strings(call)
            if not texts:
                found.append("execute with a non-literal argument: review needed")
            elif any(_RISKY_SQL.search(s) for s in texts):
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
            seen = f" upgrade() has {', '.join(risky)}, so it has to be False." if risky else ""
            problems.append(f"{path.name}: missing ROLLBACK_SAFE (Z11).{seen} Add ONE of these lines under `down_revision` / "
                            "`depends_on` and choose:\n"
                            "    ROLLBACK_SAFE = True     # the previous release's code keeps working on the upgraded schema\n"
                            "    ROLLBACK_SAFE = False    # it does not (drops, renames, retypes, new constraints, rewritten rows)\n"
                            "  False makes deploy/atp.sh refuse a code-only rollback past it (rollback --restore-db is used); "
                            "unsure means False.")
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
