"""The CI staging stack's acceptance flow sets the Phase 0 flag posture (deploy/ci/ci_posture.py) through the admin
flag API, the way deploy/ci/acceptance.py `set_posture` does. LIVE-1 (#1006, the one live switch) made
`phase0_paper_only` derived from `live_trading` and refused on its own, which turned the staging `acceptance` job red
(and so blocked `atp.sh deploy`). This keeps the posture and the API in step: every flag the flow sets is accepted,
every derived flag is refused if set on its own, and after the flow's writes the flags read exactly as POSTURE -
the Phase 0 lock included (live_trading OFF, phase0_paper_only ON)."""
import asyncio
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import User
from app.platform import controls, phase0_lock
from tests.test_auth_api import _register, _session_factory, client

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy" / "ci"))
from ci_posture import DERIVED_FLAGS, PHASE0_LOCK, POSTURE  # noqa: E402

pytestmark = pytest.mark.usefixtures("no_open_positions_from_earlier_tests")


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def _restore_flags():
    async def snapshot():
        async with _session_factory() as session:
            return dict((await controls._get(session, controls.KEY_FEATURE_FLAGS)).get("flags") or {})

    async def put_back(flags):
        async with _session_factory() as session:
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()

    before = _run(snapshot())
    yield
    _run(put_back(before))
    phase0_lock.reset_cache()


def test_every_posture_flag_names_a_registered_flag():
    assert set(POSTURE) <= set(controls.FEATURE_FLAGS)
    assert DERIVED_FLAGS == {controls.PAPER_ONLY_FLAG}
    assert PHASE0_LOCK["live_trading"] is False and PHASE0_LOCK["phase0_paper_only"] is True


def test_the_acceptance_posture_is_accepted_and_reads_back(_restore_flags):
    token = _register("ci-posture-admin@example.com")
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()

    async def go():
        async with _session_factory() as session:
            user = await session.scalar(select(User).where(User.id == me["id"]))
            for name in DERIVED_FLAGS:
                with pytest.raises(ValueError, match="follows live_trading"):
                    await controls.set_flag(session, user, name, on=POSTURE[name], tenants=[])
            for name, on in POSTURE.items():           # what acceptance.py set_posture sends, derived flags skipped
                if name not in DERIVED_FLAGS:
                    await controls.set_flag(session, user, name, on=on, tenants=[])
            return await controls.feature_flags(session)

    flags = _run(go())
    wrong = {name: flags[name]["on"] for name, on in POSTURE.items() if flags[name]["on"] != on}
    assert not wrong, f"flags not as the acceptance flow sets them: {wrong}"
