"""Owner audit (3 Nov, NIFTY 50 Daily advanced analysis): the read had 14 Daily bars and no 1-hour row."""
from datetime import UTC, datetime, timedelta

from app.charts import price_action_layer as pal
from app.market_lake.own_bars import prefer_own, stitch


def _rows(first: datetime, n: int, src: str) -> list[tuple]:
    return [(first + timedelta(days=i), 1, 100.0, 101.0, 99.0, 100.5, 10, src) for i in range(n)]


def test_a_short_newer_shared_lake_gets_the_older_own_history_in_front():
    own = _rows(datetime(2023, 11, 1, tzinfo=UTC), 1095, "own")                 # ends ~30 Oct 2026
    shared = _rows(datetime(2026, 10, 15, tzinfo=UTC), 20, "nse")               # newer, but 20 bars only
    assert not prefer_own(shared, own)                                           # the stale own history never hides NSE
    joined = stitch(shared, own)
    assert len(joined) > 1000 and joined[-1] == shared[-1]
    first_shared = shared[0][0]
    assert all(r[7] == "own" for r in joined if r[0] < first_shared)
    assert all(r[7] == "nse" for r in joined if r[0] >= first_shared)            # the shared bar wins where both exist
    assert len({r[0] for r in joined}) == len(joined)                            # one source per bar


def test_nothing_to_stitch():
    shared = _rows(datetime(2026, 1, 1, tzinfo=UTC), 300, "nse")
    assert stitch(shared, []) == [] and stitch([], shared) == []
    assert stitch(shared, _rows(datetime(2026, 2, 1, tzinfo=UTC), 10, "own")) == []   # no own bar older than the lake's


def test_every_intraday_top_down_window_fits_the_rebuild_cap():
    for when in (datetime(2026, 11, 3, 17, tzinfo=UTC), datetime(2026, 3, 31, 9, tzinfo=UTC), datetime(2026, 7, 1, tzinfo=UTC)):
        for tf in ("1h", "15m"):
            start = pal._anchor(when, pal.MTF_DAYS[tf])                          # noqa: SLF001
            assert when - start <= timedelta(days=pal.REBUILD_MAX_DAYS), (tf, when, start)
