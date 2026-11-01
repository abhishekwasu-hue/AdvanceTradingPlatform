"""Settings redesign part C: "My trading defaults" (`trading_defaults` namespace of `/api/me/prefs`) - empty by default,
strict validation with the deployment API's own limits, each user's own, never a field outside the reviewed list, and
never read by the server to trade."""
import json
from pathlib import Path

import pytest

from app.deployments.routes import ContractRulesRequest, DeploymentCreateRequest, ExitRulesRequest
from app.me import prefs
from app.paper import fno_ticket
from tests.test_ui2_4a1_prefs import BASE, _invite, _put, _who, client

NS = "trading_defaults"
EMPTY = {"instrument_kind": None, "option_position": None, "expiry_rule": None, "strike_rule": None, "strike_offset": None,
         "premium_stop_pct": None, "max_lots": None, "option_strategy": None, "spread_width": None, "target_credit_pct": None,
         "stop_credit_pct": None, "entry_mode": None, "exit_rules": None, "ticket": None}
# The reviewed list (docs/design/SETTINGS_REDESIGN_REVIEW.md R7), and what may never be a default.
NEVER = {"mode", "broker_name", "broker_account_id", "routing_policy", "route_across_brokers", "order_style", "market_protection_pct",
         "strategy_id", "symbol", "exchange", "timeframe", "custom_legs", "strike_filters", "regime_filter", "oi_gates"}


def test_empty_by_default_every_field_none():
    who = _who("td-default")
    got = client.get(BASE, headers=who["headers"]).json()
    assert got["prefs"][NS] == EMPTY and got["versions"][NS] == 0
    assert prefs.defaults(NS) == EMPTY
    assert all(v is None for v in prefs.defaults(NS).values())


def test_round_trip_and_replace():
    who = _who("td-round")
    body = {"instrument_kind": "OPTION", "option_strategy": "BULL_PUT_SPREAD", "spread_width": 3, "expiry_rule": "NEXT",
            "strike_rule": "OTM", "strike_offset": 2, "max_lots": 2, "target_credit_pct": 50, "stop_credit_pct": 150,
            "entry_mode": "APPROVAL", "exit_rules": {"trailing_stop_pct": 1.5, "break_even_at_r": 1},
            "ticket": {"lots": 2, "product": "NRML", "order_type": "LIMIT"}}
    saved = _put(who, body, namespace=NS)
    assert saved.status_code == 200, saved.text
    value = saved.json()["value"]
    assert value["option_strategy"] == "BULL_PUT_SPREAD" and value["ticket"] == {"lots": 2, "product": "NRML", "order_type": "LIMIT"}
    # The time exits stay empty unless the user sets them (SR-10).
    assert value["exit_rules"] == {"trailing_stop_pct": 1.5, "break_even_at_r": 1, "time_exit_minutes": None, "time_exit_at": None}
    cleared = _put(who, {}, namespace=NS, version=1)
    assert cleared.status_code == 200 and cleared.json()["value"] == EMPTY and cleared.json()["version"] == 2


@pytest.mark.parametrize("body", [
    {"mode": "LIVE"},                                   # never a default
    {"order_style": "PROTECTED_LIMIT"},
    {"broker_name": "upstox"},
    {"routing_policy": "MOST_MARGIN"},
    {"holding": "SWING"},                               # follows the strategy's candles, not a default
    {"option_strategy": "CUSTOM"},                      # its legs are drawn per deployment
    {"instrument_kind": "CRYPTO"},
    {"strike_offset": 11},
    {"premium_stop_pct": 4},
    {"max_lots": 0},
    {"max_lots": 501},
    {"max_lots": "2"},                                  # strict: a string is not a number
    {"spread_width": 21},
    {"target_credit_pct": 96},
    {"stop_credit_pct": 501},
    {"entry_mode": "AUTO_LIVE"},
    {"exit_rules": {"trailing_stop_pct": 0}},
    {"exit_rules": {"trailing_stop_pct": 51}},
    {"exit_rules": {"break_even_at_r": 11}},
    {"exit_rules": {"time_exit_minutes": 376}},
    {"exit_rules": {"time_exit_at": "25:00"}},
    {"exit_rules": {"stop_source": "swing"}},           # not an ExitRules field (review R7)
    {"ticket": {"lots": 0}},
    {"ticket": {"product": "CNC"}},
    {"ticket": {"order_type": "STOP"}},
    {"ticket": {"side": "BUY"}},                        # the side is never a default
    {"target_rule": "2R"},                              # review R7: not a field
    {"carry_forward": True},
])
def test_bad_values_are_refused(body):
    who = _who("td-bad")
    got = _put(who, body, namespace=NS)
    assert got.status_code == 422, (body, got.text)


def test_fields_are_the_reviewed_list_and_never_a_live_or_routing_field():
    fields = set(prefs.TradingDefaultsPrefs.model_fields)
    assert not fields & NEVER
    assert fields <= set(DeploymentCreateRequest.model_fields) | {"ticket"}
    assert set(prefs.ExitDefaults.model_fields) == set(ExitRulesRequest.model_fields)
    assert set(prefs.TicketDefaults.model_fields) == {"lots", "product", "order_type"}


def _limits(field) -> list:
    """The field's bounds and pattern as plain data (allow_inf_nan, which only refuses NaN / infinity, left out)."""
    out = []
    for m in field.metadata:
        data = {k: v for k, v in vars(m).items() if k != "allow_inf_nan" and v is not None} if hasattr(m, "__dict__") else \
            {k: getattr(m, k) for k in ("ge", "gt", "le", "lt") if hasattr(m, k)}
        if data:
            out.append((type(m).__name__, sorted(data.items())))
    return sorted(out)


@pytest.mark.parametrize("name", ["strike_offset", "premium_stop_pct", "max_lots", "spread_width", "target_credit_pct", "stop_credit_pct"])
def test_limits_equal_the_deployment_api(name):
    assert _limits(prefs.TradingDefaultsPrefs.model_fields[name]) == _limits(ContractRulesRequest.model_fields[name])


@pytest.mark.parametrize("name", ["trailing_stop_pct", "break_even_at_r", "time_exit_minutes", "time_exit_at"])
def test_exit_limits_equal_the_exit_rules_api(name):
    assert _limits(prefs.ExitDefaults.model_fields[name]) == _limits(ExitRulesRequest.model_fields[name])


def test_ticket_choices_equal_the_ticket_api():
    t = prefs.TicketDefaults.model_fields
    assert set(t["product"].annotation.__args__[0].__args__) == set(fno_ticket.PRODUCTS)
    assert set(t["order_type"].annotation.__args__[0].__args__) == set(fno_ticket.ORDER_TYPES)
    lots = {type(m).__name__: m for m in t["lots"].metadata}
    assert lots["Le"].le == fno_ticket.MAX_LOTS and lots["Ge"].ge == 1


def test_option_strategies_are_the_api_ones_without_custom():
    from app.core.enums import OptionStrategy
    ours = set(prefs.TradingDefaultsPrefs.model_fields["option_strategy"].annotation.__args__[0].__args__)
    assert ours == {s.value for s in OptionStrategy} - {"CUSTOM"}


def test_each_user_has_their_own_defaults():
    owner = _who("td-iso")
    member = _invite(owner, "USER")
    assert _put(owner, {"max_lots": 3}, namespace=NS).status_code == 200
    assert client.get(BASE, headers=member["headers"]).json()["prefs"][NS] == EMPTY
    other_org = _who("td-iso-2")
    assert client.get(BASE, headers=other_org["headers"]).json()["prefs"][NS] == EMPTY
    assert client.get(BASE, headers=owner["headers"]).json()["prefs"][NS]["max_lots"] == 3


def test_a_stored_value_a_newer_release_wrote_still_reads():
    raw = json.dumps({"max_lots": 2, "stop_source": "swing", "exit_rules": {"trailing_stop_pct": 99}})
    # the unknown key is dropped, the bad exit value takes its default (empty), the lots are kept
    assert prefs.stored_value(NS, raw) == {**EMPTY, "max_lots": 2, "exit_rules": {"trailing_stop_pct": None, "break_even_at_r": None,
                                                                                  "time_exit_minutes": None, "time_exit_at": None}}


def test_the_server_never_reads_it_to_trade():
    app_dir = Path(__file__).resolve().parents[1] / "app"
    # M68: the Trade settings registry names it as where a value lived ("today"), and the Order defaults reader keeps a
    # ticket product / order type the user saved there (TS-6) - his own value, never a server default.
    # M68 PR-6: the Automation reader keeps an entry mode he saved there (TS-6) the same way.
    allowed = {"prefs.py", "trade_settings.py", "order_defaults.py", "automation.py"}
    readers = [p for p in app_dir.rglob("*.py") if "trading_defaults" in p.read_text(encoding="utf-8") and p.name not in allowed]
    assert readers == [], readers
