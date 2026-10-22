"""EO-4: `users.ai_language` is dropped (ATP is English only since #273; the column was stored and ignored).

- the preferences API keeps answering (English only); an older client's `ai_language` / `language` is accepted and
  ignored, an unknown code is still a 422, and nothing is written to the user;
- no backend or frontend code reads or writes the column any more, and the User model no longer maps it;
- the migration drops it, is honestly not ROLLBACK_SAFE (the previous release's model still maps the column), keeps a
  single head, and its downgrade puts the column back (NOT NULL, server default 'en') so a rollback works.
"""
import importlib.util
import re
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from app.db.models import User
from tests.test_auth_api import _register, client

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
REVISION = "e6b8d0f2a4c7"
ENGLISH = {"ai_language": "en", "languages": [{"code": "en", "label": "English"}]}


def _script() -> ScriptDirectory:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return ScriptDirectory.from_config(cfg)


def _load(name: str, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preferences_api_still_answers_english_and_ignores_an_older_clients_choice():
    headers = {"Authorization": f"Bearer {_register('eo4-prefs@example.com')}"}
    assert client.get("/api/ai/preferences", headers=headers).json() == ENGLISH
    for body in ({"ai_language": "mr"}, {"ai_language": "en"}, {"language": "mr"}, {}):
        r = client.put("/api/ai/preferences", headers=headers, json=body)
        assert r.status_code == 200, (body, r.text)
        assert r.json() == ENGLISH
    assert client.put("/api/ai/preferences", headers=headers, json={"ai_language": "xx"}).status_code == 422
    assert client.put("/api/ai/preferences", headers=headers, json={"language": "hi"}).status_code == 422
    assert client.get("/api/ai/preferences", headers=headers).json() == ENGLISH
    assert client.get("/api/ai/preferences").status_code == 401


def test_the_model_no_longer_maps_the_column():
    assert "ai_language" not in User.__table__.c
    assert not hasattr(User, "ai_language")


def test_no_code_reads_or_writes_ai_language():
    """Attribute access (`user.ai_language`, getattr/setattr) anywhere in the backend app, and any mention in the
    frontend sources. The preferences response keeps the key "ai_language" for older clients - a literal, not a read.
    Frontend test files are skipped: the English-only guard (englishOnlyOutsideInterview.test.tsx) names the key in
    order to forbid it."""
    access = re.compile(r"\.ai_language\b|(get|set)attr\([^)]*['\"]ai_language['\"]|\b_ai_language\b")
    hits = [f"{p.relative_to(REPO)}:{n}" for p in (BACKEND / "app").rglob("*.py")
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if access.search(line)]
    assert hits == []
    front = [str(p.relative_to(REPO)) for p in (REPO / "frontend" / "src").rglob("*")
             if p.is_file() and p.suffix in {".ts", ".tsx", ".js", ".jsx"} and ".test." not in p.name
             and "ai_language" in p.read_text(encoding="utf-8")]
    assert front == []


def test_migration_is_the_single_head_and_declared_not_rollback_safe():
    script = _script()
    heads = script.get_heads()
    assert len(heads) == 1
    assert REVISION in {r.revision for r in script.iterate_revisions(heads[0], "base")}     # the head, or below a later one
    rev = script.get_revision(REVISION)
    assert rev.down_revision == "d9f1b3c5e7a2"
    lint = _load("migration_rollback_safety", BACKEND / "scripts" / "migration_rollback_safety.py")
    assert lint.lint() == []
    source = Path(rev.path).read_text(encoding="utf-8")
    assert "ROLLBACK_SAFE = False" in source and lint.risky_operations(source) == ["drop_column"]


def test_migration_upgrade_drops_and_downgrade_restores_the_column():
    """Round trip on a scratch SQLite database holding a `users` table as a1c3e5f7b9d2 left it."""
    mig = _load("eo4_migration", _script().get_revision(REVISION).path)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255) NOT NULL, "
                             "ai_language VARCHAR(4) NOT NULL DEFAULT 'en')"))
        conn.execute(sa.text("INSERT INTO users (id, email, ai_language) VALUES (1, 'a@example.com', 'mr')"))
        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
        assert [c["name"] for c in sa.inspect(conn).get_columns("users")] == ["id", "email"]
        assert conn.execute(sa.text("SELECT email FROM users")).scalar_one() == "a@example.com"
        with Operations.context(MigrationContext.configure(conn)):
            mig.downgrade()
        cols = {c["name"]: c for c in sa.inspect(conn).get_columns("users")}
        assert set(cols) == {"id", "email", "ai_language"}
        assert cols["ai_language"]["nullable"] is False and "en" in str(cols["ai_language"]["default"])
        # Existing rows get English, and the previous release can insert a user without naming the column.
        assert conn.execute(sa.text("SELECT ai_language FROM users WHERE id = 1")).scalar_one() == "en"
        conn.execute(sa.text("INSERT INTO users (id, email) VALUES (2, 'b@example.com')"))
        assert conn.execute(sa.text("SELECT ai_language FROM users WHERE id = 2")).scalar_one() == "en"
