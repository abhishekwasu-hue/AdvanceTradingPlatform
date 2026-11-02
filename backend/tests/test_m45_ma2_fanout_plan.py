"""M45 MA-2 (docs/design/M45_MULTI_ACCOUNT.md): the routing matrix resolver and per-account sizing - pure functions.
Exits follow positions on every account (MB-4), entries follow the matrix / override (MB-3), switched-off accounts are
skipped for entries with a reason but never for exits, lots round down, under one lot is skipped (MB-2), freeze slices."""
import pytest

from app.accounts import fanout_plan as fp

P = fp.Account(id=1, role=fp.PRIMARY, capital=500_000)
S_FAM = fp.Account(id=2, role=fp.SECONDARY, group="Family", capital=200_000)
S_HUF = fp.Account(id=3, role=fp.SECONDARY, group="HUF", capital=100_000)
S_OFF = fp.Account(id=4, role=fp.SECONDARY, group="Family", enabled=False, capital=300_000)
NO_ROLE = fp.Account(id=5, role=None, capital=50_000)
ALL = [P, S_FAM, S_HUF, S_OFF, NO_ROLE]


def ids(route):
    return [a.id for a in route.accounts]


def test_entry_defaults_to_the_primary_only():
    route = fp.resolve(fp.ENTRY, ALL)
    assert ids(route) == [1] and route.skipped == []


def test_matrix_and_override_pick_groups_and_skip_a_switched_off_account_with_a_reason():
    route = fp.resolve(fp.ENTRY, ALL, matrix={fp.ENTRY: (fp.PRIMARY, "Family")})
    assert ids(route) == [1, 2]
    assert route.skipped == [fp.Skip(4, "switched off: no new entries or adjustments")]
    assert ids(fp.resolve(fp.ENTRY, ALL, override="PRIMARY+HUF")) == [1, 3]
    everyone = fp.resolve(fp.ENTRY, ALL, override="all")
    assert ids(everyone) == [1, 2, 3] and [s.account_id for s in everyone.skipped] == [4]   # no-role account never in
    with pytest.raises(ValueError):
        fp.resolve(fp.ENTRY, ALL, override="SOME")


@pytest.mark.parametrize("order_class", sorted(fp.EXIT_CLASSES))
def test_exits_follow_positions_on_every_holding_account_whatever_the_matrix_says(order_class):
    # The matrix and an override say Primary only; the switched-off and the no-role accounts hold the position too.
    route = fp.resolve(order_class, ALL, matrix={order_class: (fp.PRIMARY,)}, override="PRIMARY", holding=[2, 4, 5])
    assert ids(route) == [2, 4, 5] and route.skipped == []
    assert fp.exit_quantities({2: 75, 4: 150, 5: 0}) == {2: 75, 4: 150}


def test_adjustment_follows_positions_by_default_but_not_onto_a_switched_off_account():
    route = fp.resolve(fp.ADJUSTMENT, ALL, holding=[1, 4])
    assert ids(route) == [1] and [s.account_id for s in route.skipped] == [4]


def test_sizing_modes_round_down_and_skip_under_one_lot():
    route = fp.resolve(fp.ENTRY, ALL, override="ALL")
    plan = fp.size(route, {1: fp.Sizing(fp.FIXED, 3), 2: fp.Sizing(fp.MULTIPLIER, 0.5), 3: fp.Sizing(fp.PCT_CAPITAL, 10)},
                   primary_lots=3, lot_size=75, margin_per_lot=12_000)
    legs = {leg.account_id: leg for leg in plan.legs}
    assert legs[1].lots == 3 and legs[1].slices == (225,)
    assert legs[2].lots == 1                       # 3 x 0.5 = 1.5 -> 1
    assert 3 not in legs                           # 10% of 1,00,000 / 12,000 = 0.83 -> under one lot
    reasons = {s.account_id: s.reason for s in plan.skipped}
    assert reasons[3] == "under one lot after rounding down" and reasons[4].startswith("switched off")


def test_no_sizing_of_his_own_skips_a_secondary_and_pct_needs_capital_and_margin():
    # M68 PR-2 (TS1-024): no MULTIPLIER x 1 is chosen for him - the Primary takes the order's own lots, a Secondary with no
    # sizing of the user's own is skipped with the setting it needs (before M68: every account MULTIPLIER x 1).
    route = fp.resolve(fp.ENTRY, ALL, matrix={fp.ENTRY: (fp.PRIMARY, "Family", "HUF")})
    plan = fp.size(route, {}, primary_lots=2, lot_size=50)
    assert [(leg.account_id, leg.lots) for leg in plan.legs] == [(1, 2)]
    assert {s.account_id: s.reason for s in plan.skipped if s.account_id in (2, 3)} == {2: fp.NEEDS_SIZING, 3: fp.NEEDS_SIZING}
    own = fp.size(route, {2: fp.Sizing(fp.MULTIPLIER, 1), 3: fp.Sizing(fp.MULTIPLIER, 1)}, primary_lots=2, lot_size=50)
    assert [(leg.account_id, leg.lots) for leg in own.legs] == [(1, 2), (2, 2), (3, 2)]
    lots, reason = fp.lots_for(fp.Account(id=9, role=fp.SECONDARY), fp.Sizing(fp.PCT_CAPITAL, 5), primary_lots=1, margin_per_lot=1000)
    assert lots == 0 and reason == "no capital known for this account"
    lots, reason = fp.lots_for(S_FAM, fp.Sizing(fp.PCT_CAPITAL, 5), primary_lots=1, margin_per_lot=None)
    assert lots == 0 and reason == "no margin per lot known for this order"


def test_each_account_is_cut_into_freeze_limit_slices_that_add_up():
    route = fp.resolve(fp.ENTRY, [P])
    plan = fp.size(route, {1: fp.Sizing(fp.FIXED, 30)}, primary_lots=30, lot_size=75, freeze_qty=1800)
    (leg,) = plan.legs
    assert sum(leg.slices) == 30 * 75 and max(leg.slices) < 1800 and all(q % 75 == 0 for q in leg.slices)
