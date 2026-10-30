"""Z10-c (G6): the table-driven tenancy and scope sweep over every `/api/*` route.

The route list comes from the FastAPI app itself (tests/route_inventory.py), so a new route is swept the day it merges -
and fails `test_every_api_route_is_classified` until tests/tenancy_route_table.py says who may call it. Then:

- an anonymous request is refused by every route that is not public;
- a VIEWER, and a member whose every scope is denied, are refused by every trader / owner write;
- a plain member is refused by every owner route, a tenant OWNER by every admin route;
- every write under a catalogue scope (`risk:write`, `alerts:write`, ...) refuses a member denied that one scope;
- another organisation's OWNER - with its own login token and its own API key - calls every route with tenant A's row
  ids (tests/tenancy_probe.py plants one row of A in every tenant-owned table, all with the same id and a marker in
  every text column): no response carries A's marker, no request fails with a 5xx, a route with a row id answers
  403/404 (or refuses the body), and afterwards every one of A's rows is exactly as it was.
Nothing leaves the process: outbound HTTP is refused for the whole module.
"""
import asyncio
import json
from typing import Dict, List, Optional, Tuple

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.compliance_terms import ACK_VERSION
from app.auth.scopes import SCOPES
from app.auth.security import decode_access_token, hash_password
from app.db.models import User
from app.main import app
from app.platform import controls
from app.public_api.keys import SCOPES as API_KEY_SCOPES
from tests import tenancy_probe as probe
from tests.route_inventory import Route, authenticates, gate_of, iter_api_routes
from tests.tenancy_route_table import ROUTE_TABLE, SELF_COMPUTE_UNDER_GUARDED_PREFIX
from tests.test_auth_api import _register, _session_factory, client
from tests.test_trading_worker import _upgrade_plan

MARKER = "z10c-tenant-a-private-marker"
REFUSED = (401, 403)

# Requests the other organisation's owner does not send: they end its own login session, which would turn every later
# request into a 401 that proves nothing. (They act on the caller's own sessions only - covered by test_sessions.py.)
NOT_SENT_CROSS_TENANT = frozenset({("POST", "/api/auth/logout"), ("POST", "/api/auth/logout-all")})

# Routes with a row id where another organisation may get a 2xx, and why that is not a leak.
CROSS_TENANT_2XX: Dict[Tuple[str, str], str] = {
    ("POST", "/api/marketplace/{listing_id}/unsubscribe"):
        "idempotent: ends the caller's own subscription to that listing if there is one (204 either way)",
    ("GET", "/api/marketplace/{listing_id}"):
        "the public marketplace: a PUBLISHED listing is visible to every subscriber organisation by design",
}

# Catalogue scope each kind of write must honour (docs: app/auth/scopes.py SCOPES). Exits are never gated by a scope,
# so approving an AI exit proposal (/api/ai/actions) is deliberately not here.
SCOPE_BY_PREFIX: List[Tuple[str, str]] = [
    ("/api/risk-settings", "risk:write"), ("/api/paper/risk", "risk:write"), ("/api/risk/limits", "risk:write"), ("/api/risk-guardian/events", "risk:write"),
    ("/api/alert-channels", "alerts:write"), ("/api/alerts/rules", "alerts:write"), ("/api/alerts/policy", "alerts:write"),
    ("/api/alerts/deliveries", "alerts:write"), ("/api/alerts/email-opt-outs", "alerts:write"),
    ("/api/custom-strategies", "strategies:write"), ("/api/options-builder/strategies", "strategies:write"),
    ("/api/broker/", "brokers:write"), ("/api/accounts/", "brokers:write"),
    ("/api/billing/", "billing:manage"), ("/api/marketplace/listings", "marketplace:publish"),
    ("/api/api-keys", "api_keys:manage"), ("/api/ai/provider", "ai:manage"), ("/api/ai/drafts", "ai:manage"),
    ("/api/option-chain/{underlying}/alerts", "alerts:write"),
]

# Known gaps in files owned by open PRs: route -> (the status a member denied the scope gets today, why). The test
# asserts exactly that status, so the day the route honours its scope (403) the test fails and the entry is removed.
# None today.
KNOWN_SCOPE_GAPS: Dict[Tuple[str, str], Tuple[int, str]] = {}
# Trader writes a member denied every scope can still make (role gate without a scope), same rule as above.
KNOWN_UNSCOPED_WRITES: Dict[Tuple[str, str], str] = {}

# Writes gated by role alone, on purpose: contributing shared reference data needs a trading role of an active
# organisation (`require_contributor`), and no catalogue scope covers it (OPEN_QUESTIONS Z10c-1). The VIEWER sweep
# still covers them; the "every scope denied" sweep does not apply.
ROLE_ONLY_WRITE_PREFIXES = ("/api/fundamentals/", "/api/news-events")

# 5xx another organisation's request may still get, and why it is not a tenancy failure (none today).
KNOWN_5XX: Dict[Tuple[str, str], str] = {}

# Bodies the schema alone cannot make valid (a model validator wants more than the required fields), so that a request
# reaches the code that must refuse - or, for tenant A, serve - it.
BODY_OVERRIDES: Dict[Tuple[str, str], dict] = {
    ("PUT", "/api/custom-strategies/{strategy_id}"): {
        "name": "Z10c", "timeframe": "1min", "short_conditions": [],
        "long_conditions": [{"left": {"type": "indicator", "indicator": "RSI", "period": 14}, "operator": "CROSSES_ABOVE",
                             "right": {"type": "value", "value": 50}}]},
    ("PUT", "/api/charts/drawings/{drawing_id}"): {"version": 1, "drawing": {"kind": "hline", "anchors": [{"p": 100.0}]}},
    ("POST", "/api/news-feed/items/{item_id}/feedback"): {"verdict": "useful"},
    ("POST", "/api/ai/actions/{action_id}/snooze"): {"minutes": 30},
    ("POST", "/api/journal/trades/{trade_id}/tags"): {"tag": "fomo_entry"},
    ("PATCH", "/api/team/members/{member_id}"): {"role": "VIEWER"},
    ("PUT", "/api/screener/screens/{screen_id}"): {"name": "Z10c", "source": "close > SMA(close, 20)"},
    ("PUT", "/api/screener/rankings/{system_id}"): {"name": "Z10c", "tree": {"nodes": [{"name": "r", "factor": "PctChange(close, 20)", "weight": 100}]}},
    # TT-9: the client's own reference has a shape the schema sample cannot guess.
    ("POST", "/api/paper/fno/positions/{trade_id}/exit-part"): {"lots": 1, "client_ref": "z10c-sweep-part"},
    ("POST", "/api/paper/fno/positions/{trade_id}/reverse"): {"client_ref": "z10c-sweep-reverse", "preview": True},
}

# Path parameters the planted rows cannot stand for: a real member, invite and login session of tenant A, made fresh
# for each request (the request may remove them).
REAL_ID_PARAMS = ("member_id", "invite_id", "target_id")

# Id routes where tenant A's own request may still be a 404 / 422, and why the other organisation's 403/404 on them
# still proves something (or what the route really keys on).
A_CONTROL_EXCEPTIONS: Dict[Tuple[str, str], str] = {
    ("POST", "/api/news-feed/items/{item_id}/feedback"):
        "item_id names a shared news-feed item (platform data, not a tenant row) - each member's verdict is its own row",
    ("POST", "/api/paper/fno/group-exits/{exit_id}/edit"):
        "the planted row is not an ARMED combined exit, and only an armed one can be edited (422 'has ended', by design); "
        "the other organisation still gets 404 and changes nothing (CX; tests/test_cx_api.py edits a real one)",
    ("GET", "/api/setup-recall/proposals/{action_id}"):
        "the planted ai_actions row is not an ENTRY proposal, and recall answers only for one (404 'No such proposal', by "
        "design); the other organisation still gets 404 (AUD-T6; tests/test_aud_t6_setup_recall.py serves a real one)",
    **{key: why for why, keys in (
        ("a checklist answer must name one of the trader's own rules, and the planted world has none (422 by design); the "
         "other organisation still gets 404 (M62; tests/test_m62_journal_layer.py answers real rules)",
         (("PATCH", "/api/journal/trades/{trade_id}/checklist"), ("PATCH", "/api/journal/manual/{manual_id}/checklist"))),
        ("the sweep sends no image file (422 'choose an image', by design); the other organisation still gets 404 before the "
         "body is read (M62; tests/test_m62_journal_layer.py uploads a real one)",
         (("POST", "/api/journal/trades/{trade_id}/attachments"), ("POST", "/api/journal/manual/{manual_id}/attachments"))),
        ("the planted attachment row has no file in the private store (404 'No such image'); the other organisation still gets "
         "404 (M62; tests/test_m62_journal_layer.py reads a real one)", (("GET", "/api/journal/attachments/{attachment_id}"),)),
        ("no candles are stored for the planted trade, so no snapshot can be drawn (404); the other organisation still gets "
         "404 (M62; tests/test_m62_journal_layer.py draws one from stored candles)",
         (("GET", "/api/journal/trades/{trade_id}/chart"), ("GET", "/api/journal/manual/{manual_id}/chart"))),
    ) for key in keys},
}

# Id routes where the other organisation may get a status other than 403/404, and why nothing of tenant A's is exposed.
CROSS_TENANT_STATUS: Dict[Tuple[str, str], Tuple[Tuple[int, ...], str]] = {
    ("POST", "/api/marketplace/{listing_id}/subscribe"): (
        (400,), "marketplace listings are meant to be discovered by every organisation; an unpublished one is refused "
                "with 400 and nothing of it is returned or changed"),
}

ROUTES: List[Route] = iter_api_routes(app)
OPENAPI = app.openapi()


def _run(coro):
    return asyncio.run(coro)


def _cls(route: Route) -> str:
    return ROUTE_TABLE.get(route.key, "").split(":")[0]


def _of(*classes: str) -> List[Route]:
    return [r for r in ROUTES if _cls(r) in classes]


def _id(route: Route) -> str:
    return f"{route.method} {route.path}"


def _suggest(route: Route) -> str:
    gate = gate_of(route) or ""
    if gate == "admin" or gate.startswith("role:SUPER_ADMIN"):
        return "admin"
    if gate.startswith("api_key"):
        return "key"
    if not authenticates(route):
        return "public: <why no login>"
    if gate.startswith("role:OWNER") and "USER" not in gate:
        return "owner"
    if gate:
        return "trader" if route.mutating else "member"
    return "self: <why any member may>" if route.mutating else "member"


def _request(sweep: TestClient, route: Route, headers: Dict[str, str], ids: Optional[Dict[str, int]] = None):
    url = probe.path_for(route.path, probe.int_path_params(route.dependant), overrides=ids)
    body = (BODY_OVERRIDES.get(route.key) or probe.body_for(OPENAPI, route.method, route.path)) if route.mutating else None
    return sweep.request(route.method, url, headers=headers, params=probe.query_for(OPENAPI, route.method, route.path),
                         json=body)


# --- the world: two organisations, tenant A's members, A's planted rows ---------------------------------------------

def _login(email: str) -> Dict[str, str]:
    response = client.post("/api/auth/login", json={"email": email, "password": "S3cur3Pass!"})
    assert response.status_code == 200, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    assert client.post("/api/ai/acknowledgement", headers=headers, json={"version": ACK_VERSION}).status_code == 200
    return headers


def _owner(email: str) -> Tuple[Dict[str, str], dict]:
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    _upgrade_plan(me["tenant_id"])
    return headers, me


def _user_id(email: str) -> int:
    async def go():
        async with _session_factory() as session:
            return (await session.scalar(select(User).where(User.email == email))).id
    return _run(go())


def _member(email: str, tenant_id: int, role: str, deny: Tuple[str, ...] = ()) -> Dict[str, str]:
    async def create():
        async with _session_factory() as session:
            session.add(User(tenant_id=tenant_id, email=email, hashed_password=hash_password("S3cur3Pass!"), role=role,
                             scope_overrides=json.dumps({"deny": sorted(deny), "grant": []}) if deny else None))
            await session.commit()
    _run(create())
    return _login(email)


@pytest.fixture(scope="module")
def world():
    with pytest.MonkeyPatch.context() as mp:
        async def no_network(self, request):
            raise httpx.ConnectError("Z10-c sweep: no outbound network", request=request)

        def no_network_sync(self, request):
            raise httpx.ConnectError("Z10-c sweep: no outbound network", request=request)
        mp.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)
        mp.setattr(httpx.HTTPTransport, "handle_request", no_network_sync)

        a_headers, a = _owner("z10c-sweep-a@example.com")
        b_headers, b = _owner("z10c-sweep-b@example.com")
        members = {
            "viewer": _member("z10c-sweep-viewer@example.com", a["tenant_id"], "VIEWER"),
            "user": _member("z10c-sweep-user@example.com", a["tenant_id"], "USER"),
            "stripped": _member("z10c-sweep-stripped@example.com", a["tenant_id"], "USER",
                                tuple(s for s in SCOPES if s != "admin:platform")),
        }
        for scope in sorted({s for _, s in SCOPE_BY_PREFIX}):
            members[scope] = _member(f"z10c-sweep-deny-{scope.replace(':', '-')}@example.com", a["tenant_id"], "OWNER", (scope,))

        async def setup():
            async with _session_factory() as session:
                # Every feature on (the lock and the live switch keep their state), so flag-gated routes are swept too.
                flags = {n: {"on": True, "tenants": []} for n in controls.FEATURE_FLAGS if n != "phase0_paper_only"}
                await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
                await session.commit()
                return await probe.plant_rows(session, tenant_id=a["tenant_id"], user_id=a["id"], marker=MARKER)
        planted = _run(setup())
        created = client.post("/api/api-keys", headers=b_headers, json={"name": "z10c", "scopes": sorted(API_KEY_SCOPES)})
        b_key = created.json().get("key") if created.status_code in (200, 201) else None
        sweep = TestClient(app, raise_server_exceptions=False)
        counter = iter(range(1, 10_000))

        def real_ids(route: Route) -> Dict[str, int]:
            """Fresh tenant-A ids for the parameters planted rows cannot stand for (only those the route names)."""
            ids: Dict[str, int] = {}
            if "{member_id}" in route.path:
                email = f"z10c-sweep-member-{next(counter)}@example.com"
                _member(email, a["tenant_id"], "USER")
                ids["member_id"] = _user_id(email)
            if "{invite_id}" in route.path:
                made = client.post("/api/team/invites", headers=a_headers, json={"email": f"z10c-sweep-invite-{next(counter)}@example.com"})
                assert made.status_code in (200, 201), made.text
                ids["invite_id"] = made.json()["id"]
            if "{version_number}" in route.path:
                ids["version_number"] = 1                       # the planted version row of the planted strategy
            if route.path.startswith("/api/screener/screens/{screen_id}/versions/{version}"):
                ids["version"] = 1                              # LAB-18: the planted version row of the planted screen
            if route.path.startswith("/api/news-events/{event_id}"):
                made = client.post("/api/news-events", headers=a_headers, json={
                    "category": "RBI_POLICY", "headline": "Z10c", "event_date": "2026-10-09", "sentiment": "Neutral",
                    "source": {"source": "RBI", "source_url": "https://www.rbi.org.in/"}})
                assert made.status_code == 201, made.text
                ids["event_id"] = made.json()["id"]             # shared reference data: tenant A's own entry, not a planted row
            if "{target_id}" in route.path:
                response = client.post("/api/auth/login", json={"email": "z10c-sweep-a@example.com", "password": "S3cur3Pass!"})
                ids["target_id"] = int(decode_access_token(response.json()["access_token"])["sid"])
            return ids

        def replant() -> None:
            async def go():
                async with _session_factory() as session:
                    await probe.remove_rows(session, planted)
                    again = await probe.plant_rows(session, tenant_id=a["tenant_id"], user_id=a["id"], marker=MARKER)
                    assert again == planted
            _run(go())

        yield {"a": a, "a_headers": a_headers, "b_headers": b_headers, "b_key": b_key, "members": members,
               "planted": planted, "sweep": sweep, "real_ids": real_ids, "replant": replant}

        async def teardown():
            async with _session_factory() as session:
                await probe.remove_rows(session, planted)
        _run(teardown())


# --- classification ---------------------------------------------------------------------------------------------------

def test_every_api_route_is_classified():
    live = {r.key: r for r in ROUTES}
    missing = sorted(k for k in live if k not in ROUTE_TABLE)
    assert not missing, ("Route(s) without a tenancy classification - add each to tests/tenancy_route_table.py "
                         "(suggested class from its dependencies):\n"
                         + "\n".join(f'    ("{m}", "{p}"): "{_suggest(live[(m, p)])}",' for m, p in missing))
    stale = sorted(k for k in ROUTE_TABLE if k not in live)
    assert not stale, f"tests/tenancy_route_table.py lists routes that no longer exist: {stale}"


def test_classification_matches_each_routes_dependencies():
    problems = []
    for route in ROUTES:
        cls, _, why = ROUTE_TABLE.get(route.key, "").partition(":")
        gate = gate_of(route) or ""
        if cls in ("public", "self") and not why.strip():
            problems.append(f"{route.key}: a '{cls}' route needs its reason")
        checks = {
            "public": not authenticates(route),
            "key": gate.startswith("api_key"),
            "admin": gate == "admin" or gate.startswith("role:SUPER_ADMIN"),
            "owner": gate.startswith("role:OWNER") and "USER" not in gate,
            "trader": route.mutating and bool(gate) and "VIEWER" not in gate,
            "member": authenticates(route) and not route.mutating,
            "self": authenticates(route) and route.mutating,
        }
        if not checks.get(cls, False):
            problems.append(f"{route.method} {route.path}: classified '{cls}' but its dependencies say '{_suggest(route)}' (gate {gate!r})")
    assert not problems, "\n".join(problems)


def test_no_shared_data_or_order_write_is_open_to_a_viewer_by_classification():
    """The 'self' class is for the caller's own data and pure computations; a write to data every organisation shares,
    or anything on the order path, must never be 'self' (Z10-c fixed the shared-data writes that were)."""
    for route in _of("self"):
        if route.key in SELF_COMPUTE_UNDER_GUARDED_PREFIX:          # M55 O2: compute only, listed by name
            continue
        assert not route.path.startswith(("/api/fundamentals/", "/api/news-events", "/api/reconciliation", "/api/deployments/{",
                                          "/api/positions", "/api/kill-switch", "/api/strategies/")), route.key


# --- who may call what -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("route", [r for r in ROUTES if _cls(r) not in ("public", "")], ids=_id)
def test_anonymous_request_is_refused(world, route):
    response = _request(world["sweep"], route, {})
    assert response.status_code in REFUSED, f"{route.method} {route.path} answered {response.status_code} without a login"


@pytest.mark.parametrize("route", _of("trader", "owner"), ids=_id)
def test_viewer_is_refused_every_write(world, route):
    response = _request(world["sweep"], route, world["members"]["viewer"])
    assert response.status_code == 403, f"VIEWER got {response.status_code}: {response.text[:200]}"


_SCOPE_SWEPT = [r for r in _of("trader", "owner") if not r.path.startswith(ROLE_ONLY_WRITE_PREFIXES)]
_STRIPPED = [pytest.param(r, marks=pytest.mark.xfail(strict=True, reason=KNOWN_UNSCOPED_WRITES[r.key]))
             if r.key in KNOWN_UNSCOPED_WRITES else r for r in _SCOPE_SWEPT]


@pytest.mark.parametrize("route", _STRIPPED, ids=[_id(r) for r in _SCOPE_SWEPT])
def test_member_denied_every_scope_is_refused_every_write(world, route):
    response = _request(world["sweep"], route, world["members"]["stripped"])
    assert response.status_code == 403, f"member with every scope denied got {response.status_code}: {response.text[:200]}"


@pytest.mark.parametrize("route", _of("owner"), ids=_id)
def test_plain_member_is_refused_every_owner_route(world, route):
    response = _request(world["sweep"], route, world["members"]["user"])
    assert response.status_code == 403, f"USER member got {response.status_code}: {response.text[:200]}"


@pytest.mark.parametrize("route", _of("admin"), ids=_id)
def test_tenant_owner_is_refused_every_admin_route(world, route):
    response = _request(world["sweep"], route, world["a_headers"])
    assert response.status_code == 403, f"tenant OWNER got {response.status_code}: {response.text[:200]}"


def _scoped_writes() -> List[Tuple[Route, str]]:
    found = []
    for route in _of("trader", "owner"):
        if route.mutating:
            scope = next((s for prefix, s in SCOPE_BY_PREFIX if route.path.startswith(prefix)), None)
            if scope:
                found.append((route, scope))
    return found


@pytest.mark.parametrize("route,scope", _scoped_writes(), ids=[f"{r.method} {r.path} [{s}]" for r, s in _scoped_writes()])
def test_each_catalogue_scope_bites(world, route, scope):
    """An OWNER-role member denied only this scope is refused - and says which scope is missing."""
    response = _request(world["sweep"], route, world["members"][scope])
    if route.key in KNOWN_SCOPE_GAPS:
        status, why = KNOWN_SCOPE_GAPS[route.key]
        assert response.status_code == status, (f"known gap ({why}) now answers {response.status_code} - if it is a 403 for "
                                                f"'{scope}', remove it from KNOWN_SCOPE_GAPS")
        return
    assert response.status_code == 403 and response.headers.get("X-Missing-Scope") == scope, \
        f"denied '{scope}' but got {response.status_code} {response.headers.get('X-Missing-Scope')}: {response.text[:200]}"


def test_scope_table_has_no_stale_entries():
    keys = {r.key for r in ROUTES}
    assert not [k for k in KNOWN_SCOPE_GAPS if k not in keys], "remove KNOWN_SCOPE_GAPS entries for routes that are gone"
    assert not [k for k in KNOWN_UNSCOPED_WRITES if k not in keys], "remove KNOWN_UNSCOPED_WRITES entries for routes that are gone"
    assert set(KNOWN_SCOPE_GAPS) <= {r.key for r, _ in _scoped_writes()}


# --- another organisation -------------------------------------------------------------------------------------------

def test_the_probe_reaches_tenant_a_rows(world):
    """Guards the sweep below: tenant A itself finds its planted rows through the API (otherwise every 404 another
    organisation gets would prove nothing)."""
    assert len(world["planted"]) >= 50, f"only {len(world['planted'])} tables planted"
    by_id, listed = [], []
    for route in ROUTES:
        if route.method == "GET" and _cls(route) in ("member", "owner", "trader"):
            response = _request(world["sweep"], route, world["a_headers"])
            if response.status_code == 200 and MARKER in response.text:
                (by_id if probe.int_path_params(route.dependant) else listed).append(route.path)
    assert len(by_id) >= 6, f"tenant A reached its own probe rows by id through only {by_id}"
    assert len(listed) >= 15, f"tenant A saw its own probe rows in only these lists: {listed}"


def test_another_organisation_never_sees_or_changes_tenant_a_rows(world):
    sweep, b_headers = world["sweep"], world["b_headers"]

    async def snap():
        async with _session_factory() as session:
            return await probe.snapshot(session, world["planted"])
    before = _run(snap())
    leaks, errors, opened = [], [], []
    for route in ROUTES:
        cls = _cls(route)
        if cls in ("admin", "public") or route.key in NOT_SENT_CROSS_TENANT:
            continue
        headers = {"X-API-Key": world["b_key"]} if cls == "key" and world["b_key"] else b_headers
        response = _request(sweep, route, headers)
        where = f"{route.method} {route.path} -> {response.status_code}"
        if MARKER in response.text:
            leaks.append(where)
        if response.status_code >= 500 and route.key not in KNOWN_5XX:
            errors.append(f"{where}: {response.text[:120]}")
        if probe.int_path_params(route.dependant) and response.status_code < 300 and route.key not in CROSS_TENANT_2XX:
            opened.append(f"{where}: {response.text[:120]}")
        if response.status_code == 401 and cls != "key":
            assert client.get("/api/auth/me", headers=b_headers).status_code == 200, f"{where} ended the caller's session"
    after = _run(snap())
    changed = [t for t in world["planted"] if before[t] != after[t]]
    assert not leaks, "tenant A's data reached another organisation:\n" + "\n".join(leaks)
    assert not changed, f"another organisation changed tenant A's rows in: {changed}"
    assert not opened, "a route with a row id answered another organisation's request for tenant A's row:\n" + "\n".join(opened)
    assert not errors, "5xx on another organisation's request (a refusal must be a 4xx):\n" + "\n".join(errors)


_ID_ROUTES = [r for r in ROUTES if _cls(r) in ("member", "owner", "trader", "self") and r.key not in NOT_SENT_CROSS_TENANT
              and (probe.int_path_params(r.dependant) or any("{" + p + "}" in r.path for p in REAL_ID_PARAMS))]


@pytest.mark.parametrize("route", _ID_ROUTES, ids=_id)
def test_an_id_route_refuses_another_organisation_and_serves_tenant_a(world, route):
    """Per route, on freshly planted rows: the other organisation's OWNER is refused (403/404) and changes nothing;
    then tenant A's OWNER sends the same request and is served - not a 404 or 422 - which proves the refusal was about
    the organisation, not a row that was never there or a body that never got past validation."""
    world["replant"]()
    ids = world["real_ids"](route)

    async def snap():
        async with _session_factory() as session:
            return await probe.snapshot(session, world["planted"])
    before = _run(snap())
    other = _request(world["sweep"], route, world["b_headers"], ids)
    assert MARKER not in other.text, f"tenant A's data reached another organisation: {other.text[:200]}"
    if route.key not in CROSS_TENANT_2XX:
        allowed = (403, 404) + CROSS_TENANT_STATUS.get(route.key, ((), ""))[0]
        assert other.status_code in allowed, f"another organisation got {other.status_code}: {other.text[:200]}"
    after = _run(snap())
    assert before == after, f"another organisation changed tenant A's rows in {[t for t in before if before[t] != after[t]]}"
    own = _request(world["sweep"], route, world["a_headers"], ids)
    if route.key in A_CONTROL_EXCEPTIONS:
        return
    assert own.status_code not in (404, 422), (f"tenant A's own request got {own.status_code} - the probe never reached a "
                                               f"real row or a valid body: {own.text[:200]}")
