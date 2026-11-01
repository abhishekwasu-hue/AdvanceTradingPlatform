"""M72 PR-9 (CP-9, F11): the Copilot PREPARES the order ticket in the browser (frontend src/copilot/ticket); the server is
unchanged - the ticket's own preview / place routes, pressed by the user. Here: the flag exists and ships OFF, and no
Copilot (app.ai) module imports an order-placing function (the ticket's Place is the only path)."""
from app.platform import controls, flag_catalog
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixture
from tests.test_m72_pr1c_planner import _run, test_no_copilot_chat_module_imports_an_order_placing_function  # noqa: F401
from tests.test_ui2_4a1_prefs import _who


def test_the_ticket_flag_ships_off():
    assert "copilot_ticket" in controls.FEATURE_FLAGS and flag_catalog.FLAG_INFO["copilot_ticket"].depends_on == ("ai_copilot", "ui_v2")
    who = _who("pr9-flag")

    async def go():
        async with _session_factory() as session:
            return await controls.flag_enabled(session, "copilot_ticket", who["tenant_id"])
    assert _run(go()) is False
