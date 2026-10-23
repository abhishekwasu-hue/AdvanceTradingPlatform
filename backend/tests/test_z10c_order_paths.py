"""Z10-c (G6): "no API order path reachable" (Phase 0 exit criterion, docs/specs/ATP_PHASE0_FREEZE.md §5) - the static guard.

A call graph of `app/` and `scripts/` (tests/order_path_graph.py, built from the source) answers which API routes and
which process entry points can reach a broker order method (`place_order`, `modify_order`, `place_stop_loss_order`,
`exit_position`). Four rules, each failing the build when the code drifts:

1. Every direct call of a broker order method outside `app/brokers/` is in ORDER_CALL_SITES, classified ENTRY, EXIT or
   PROTECTIVE with a reason. A new call site fails until someone classifies it.
2. No API route reaches an ENTRY call site. This is the criterion itself: a new LIVE position can only be opened by
   the trading worker, never by a request.
3. The API routes that reach an EXIT / PROTECTIVE call site are exactly ROUTES_REACHING_BROKER_ORDERS, each with its
   reason. Exits of an existing LIVE position must stay possible (ADR-0004); a new route that reaches the broker - or
   one that stops reaching it - fails until the table says so.
4. The process entry points (modules run as `python -m`, scripts, the app's lifespan) that reach any call site are
   exactly PROCESS_ENTRY_POINTS.

Broker-gated callables (GATED) take their broker as an argument; a call without one cannot reach a broker through
them (PAPER pipelines call them that way). Rule 0 keeps that honest: none of them can obtain a broker by itself.
The runtime guard (flag `phase0_paper_only`) is tested in tests/test_z10c_phase0_lock.py.
"""
from typing import Dict, Set, Tuple

import pytest

from app.main import app
from tests.order_path_graph import CallGraph, endpoint_node
from tests.route_inventory import dependency_calls, iter_api_routes

ENTRY, EXIT, PROTECTIVE = "ENTRY", "EXIT", "PROTECTIVE"

# (function, broker method) -> (kind, why it is there)
ORDER_CALL_SITES: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("app.execution.router.OrderRouter.execute", "place_order"):
        (ENTRY, "the single-leg LIVE entry; reached only with a broker handed in by the trading worker"),
    ("app.execution.router.OrderRouter.execute", "place_stop_loss_order"):
        (PROTECTIVE, "the broker-side stop placed right after a LIVE fill"),
    ("app.execution.multileg._place_live_legs", "place_order"):
        (ENTRY, "the legs of a LIVE option structure; reached only from the trading worker"),
    ("app.execution.multileg._unwind", "place_order"):
        (EXIT, "reverses the filled legs of a structure that failed half way"),
    ("app.trading.position_monitor._square_off_live", "place_order"):
        (EXIT, "closes an open LIVE position (stop, target, square-off, emergency exit, approved exit)"),
    ("app.trading.position_monitor._apply_trade_exit_rules", "modify_order"):
        (PROTECTIVE, "moves the broker-side stop of an open LIVE position (trailing / break-even)"),
    ("app.trading.stop_guard._guard_one", "place_stop_loss_order"):   # (per position, from verify_protective_stops)
        (PROTECTIVE, "re-arms a missing broker-side stop of an open LIVE position"),
}

# Callables that place broker orders only through the broker argument they are given (qualname -> parameter).
GATED: Dict[str, str] = {
    "app.execution.signal_execution.execute_signal_for_user": "broker",
    "app.execution.multileg.execute_structure": "broker",
    "app.execution.router.OrderRouter.__init__": "broker",
    "app.trading.position_monitor.close_position": "broker",
    "app.ai.monitor.execute": "broker",
}
# Where a broker adapter comes from: everything that builds one goes through these.
ADAPTER_FACTORIES = frozenset({"app.brokers.registry.get_broker_adapter", "app.brokers.token_lifecycle.build_adapter"})

# API routes that can reach an EXIT / PROTECTIVE call site, and why that is allowed (never an entry - rule 2).
ROUTES_REACHING_BROKER_ORDERS: Dict[Tuple[str, str], str] = {
    ("POST", "/api/positions/{trade_id}/mark-price"):
        "closes the caller's own open position when the supplied price hits its stop/target; a LIVE one is squared off "
        "at its own broker session (exit, ADR-0004)",
    ("POST", "/api/positions/{trade_id}/exit-now"):
        "\"Exit now\" on the caller's own position; LIVE is refused before any close (approval.exit_now), so only a PAPER "
        "close runs - the graph keeps the edge because close_group can look a broker up",
    ("POST", "/api/paper/fno/basket/close-filled"):
        "TM Part 7 \"Close the filled legs\": the caller's own legs through approval.exit_now one by one (shorts first), "
        "so LIVE is refused before any close exactly as for Exit now - an exit only (ADR-0004)",
    ("POST", "/api/kill-switch/emergency-exit"):
        "the tenant's emergency stop: engages the kill switch, then closes its open positions (exit, ADR-0004)",
    ("POST", "/api/ai/actions/{action_id}/approve"):
        "an approved EXIT_POSITION proposal closes that position at its broker (exit); an approved ENTRY books PAPER only",
    ("POST", "/api/telegram/webhook/{webhook_token}"):
        "the Telegram approve button of a proposal - same monitor.execute path as the web approve (exit only)",
}

# Process entry points (graph node) that reach a broker order call site.
PROCESS_ENTRY_POINTS: Dict[str, str] = {
    "app.workers.trading_worker.<module>":
        "the trading worker: LIVE deployments (behind the plan, the live_trading flag, entry checks and the "
        "phase0_paper_only lock), protective stops and exits",
}


@pytest.fixture(scope="module")
def graph() -> CallGraph:
    return CallGraph(gated=GATED)


def _route_nodes(route) -> Set[str]:
    return {endpoint_node(route.endpoint)} | {endpoint_node(call) for call in dependency_calls(route.dependant)
                                              if hasattr(call, "__qualname__") and hasattr(call, "__module__")}


def _route_sinks(graph: CallGraph, route) -> Set[Tuple[str, str]]:
    found: Set[Tuple[str, str]] = set()
    reaching = _reaching(graph)
    for node in _route_nodes(route) & reaching:
        found |= graph.sinks_reachable_from(node)
    return found


_REACHING: Dict[int, Set[str]] = {}


def _reaching(graph: CallGraph) -> Set[str]:
    if id(graph) not in _REACHING:
        _REACHING[id(graph)] = graph.reaching()
    return _REACHING[id(graph)]


def test_the_graph_sees_the_known_order_paths(graph):
    """Guards the guard: a broken parser would find nothing and pass every rule below."""
    assert len(graph.functions) > 1000
    worker = graph.sinks_reachable_from("app.workers.trading_worker.<module>")
    assert ("app.execution.router.OrderRouter.execute", "place_order") in worker
    assert ("app.trading.position_monitor._square_off_live", "place_order") in worker
    assert endpoint_node(iter_api_routes(app)[0].endpoint) in graph.functions


def test_rule0_gated_callables_cannot_obtain_a_broker_themselves(graph):
    for qualname, param in GATED.items():
        func = graph.functions.get(qualname)
        assert func is not None, f"{qualname} moved - update GATED"
        args = func.node.args  # type: ignore[attr-defined]
        names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
        assert param in names, f"{qualname} has no '{param}' parameter any more"
        chain = graph.reaches_any(qualname, set(ADAPTER_FACTORIES))
        assert chain is None, f"{qualname} can build a broker itself, so a broker-less call is not safe: {' > '.join(chain)}"


def test_rule1_every_broker_order_call_site_is_classified(graph):
    found = {(site.function, site.method) for site in graph.sink_sites()}
    unclassified = sorted(found - set(ORDER_CALL_SITES))
    assert not unclassified, ("New broker order call site(s) - classify each in ORDER_CALL_SITES (ENTRY / EXIT / "
                              f"PROTECTIVE) and check no API route reaches an entry: {unclassified}")
    stale = sorted(set(ORDER_CALL_SITES) - found)
    assert not stale, f"ORDER_CALL_SITES lists call sites that no longer exist: {stale}"


def test_rule2_no_api_route_reaches_a_broker_entry(graph):
    entries = {site for site, (kind, _) in ORDER_CALL_SITES.items() if kind == ENTRY}
    offenders = []
    for route in iter_api_routes(app):
        hit = _route_sinks(graph, route) & entries
        if hit:
            chain = graph.path_to_sink(endpoint_node(route.endpoint)) or []
            offenders.append(f"{route.method} {route.path}: {' > '.join(chain)}")
    assert not offenders, "API route(s) can reach a broker ENTRY (Phase 0: no API order path):\n" + "\n".join(offenders)


def test_rule3_routes_reaching_broker_orders_are_exactly_the_allow_list(graph):
    reaching: Dict[Tuple[str, str], str] = {}
    for route in iter_api_routes(app):
        if _route_sinks(graph, route):
            reaching[route.key] = " > ".join(graph.path_to_sink(endpoint_node(route.endpoint)) or ["(via a dependency)"])
    new = {k: v for k, v in reaching.items() if k not in ROUTES_REACHING_BROKER_ORDERS}
    assert not new, ("Route(s) newly able to reach a broker order method - an exit may be allowed (add it to "
                     "ROUTES_REACHING_BROKER_ORDERS with the reason), an entry never is:\n"
                     + "\n".join(f"{m} {p}: {chain}" for (m, p), chain in sorted(new.items())))
    gone = sorted(set(ROUTES_REACHING_BROKER_ORDERS) - set(reaching))
    assert not gone, f"ROUTES_REACHING_BROKER_ORDERS lists routes that no longer reach a broker (remove them): {gone}"


def test_rule4_process_entry_points_reaching_broker_orders_are_exactly_the_allow_list(graph):
    entry_points = {f"{module}.<module>" for module in graph.main_modules} | {"app.main._lifespan"}
    reaching = {node for node in entry_points if graph.sinks_reachable_from(node)}
    assert reaching == set(PROCESS_ENTRY_POINTS), (
        f"Process entry points reaching a broker order method changed: new {sorted(reaching - set(PROCESS_ENTRY_POINTS))}, "
        f"gone {sorted(set(PROCESS_ENTRY_POINTS) - reaching)}")


def test_a_broker_less_call_is_not_an_order_path():
    """The gating rule on a small example: the same callee is an order path with a broker and none without."""
    source = ("async def place(order, broker=None):\n    if broker is not None:\n        await broker.place_order(order)\n"
              "async def paper(o):\n    await place(o)\n"
              "async def paper_none(o):\n    await place(o, broker=None)\n"
              "async def live(o, b):\n    await place(o, broker=b)\n"
              "async def live_positional(o, b):\n    await place(o, b)\n")
    reaching = CallGraph(roots=(), gated={"m.place": "broker"}, sources={"m": source}).reaching()
    assert {"m.place", "m.live", "m.live_positional"} <= reaching
    assert not {"m.paper", "m.paper_none"} & reaching
    assert {"m.paper", "m.place"} <= CallGraph(roots=(), sources={"m": source}).reaching()   # ungated: an order path
