"""N2-10 (P1 spec 1.6, docs/design/N2_P1FGH.md): the method library behind `education_pages` (off by default).
The content is static frontend text (checked by frontend/src/progression/learn.test.tsx). Here:
- the flag per organisation;
- the library's links stay in step with the engine: checklist keys (P1-d ticket), journal tags (Z6) and screener
  setup keys (taxonomy).
"""
import asyncio
import json
import re
import uuid
from pathlib import Path

from app.journal.settings import TAGS
from app.paper.ticket import AUTO_CHECKS, CHECKLIST
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import _tenant

ROOT = Path(__file__).resolve().parents[2]
LEARN_TS = ROOT / "frontend" / "src" / "progression" / "learn.ts"
TAXONOMY = ROOT / "backend" / "app" / "screener" / "data" / "taxonomy.json"


def test_flag_registered_off_per_organisation():
    assert "education_pages" in controls.FEATURE_FLAGS and "education_pages" in controls.DEFAULT_OFF_FLAGS
    t = _tenant(f"n2ed-{uuid.uuid4().hex[:6]}@example.com")
    assert client.get("/api/system/features", headers=t["headers"]).json()["education_pages"] is False

    async def on():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags["education_pages"] = {"on": False, "tenants": [t["tenant_id"]]}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    asyncio.run(on())
    assert client.get("/api/system/features", headers=t["headers"]).json()["education_pages"] is True


def test_library_links_match_the_engine():
    src = LEARN_TS.read_text()
    block = src[src.index("CONCEPT_LINKS"):src.index("};", src.index("CONCEPT_LINKS"))]
    checklist = set(re.findall(r'checklist: "([a-z_]+)"', block))
    tags = {t for group in re.findall(r"tags: \[([^\]]*)\]", block) for t in re.findall(r'"([a-z_]+)"', group)}
    setups = {s for group in re.findall(r"setups: \[([^\]]*)\]", block) for s in re.findall(r'"([a-z_]+)"', group)}
    assert checklist and checklist <= set(CHECKLIST) | set(AUTO_CHECKS)
    assert tags and tags <= set(TAGS)
    known = {s.get("key") or s.get("id") for s in json.loads(TAXONOMY.read_text())["setups"]}
    assert setups and setups <= known
