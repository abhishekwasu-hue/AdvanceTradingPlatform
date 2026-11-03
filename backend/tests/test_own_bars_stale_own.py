"""Owner 3 Nov: the Copilot card read the NIFTY Daily structure to 30 Oct (`own:day`) while the official close was
2 Nov (the NSE index file) - the member's own broker Daily history held more bars but had stopped. `prefer_own` now never
picks own bars that end before the shared lake's newest bar; otherwise the rule is unchanged (more bars wins, a tie keeps
the shared lake, one source per read)."""
from datetime import UTC, datetime, timedelta

from app.market_lake.own_bars import newest_bar, prefer_own


def _rows(last: datetime, n: int):
    return [(last - timedelta(days=i), 1, 1.0, 2.0, 0.5, 1.5, 10) for i in range(n)]


OCT30 = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
NOV2 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)


def test_a_longer_own_history_that_stopped_never_hides_newer_shared_bars():
    own, shared = _rows(OCT30, 400), _rows(NOV2, 250)
    assert newest_bar(own) == OCT30 and newest_bar(shared) == NOV2
    assert prefer_own(shared, own) is False


def test_the_rule_is_unchanged_when_own_is_as_new():
    assert prefer_own(_rows(NOV2, 250), _rows(NOV2, 400)) is True       # more bars, as new: own
    assert prefer_own(_rows(NOV2, 250), _rows(NOV2, 250)) is False      # a tie keeps the shared lake
    assert prefer_own(_rows(OCT30, 250), _rows(NOV2, 400)) is True      # own newer and longer: own
    assert prefer_own([], _rows(OCT30, 5)) is True                       # no shared rows: own
    assert prefer_own(_rows(NOV2, 5), []) is False                       # no own rows: shared
