"""AUD-V4 (owner audit 28 Oct): open, untagged trades are not yet "clean" decisions - with no closed decision the
discipline score is "n/a until a trade closes"; otherwise it says what it covers: "(N open · M closed)"."""
from app.journal.score import NOT_YET, TagState, weekly_score
from app.journal.settings import DisciplineSettings


def test_open_trades_only_give_no_score_yet():
    out = weekly_score({1: "t:1", 2: "t:2"}, [], DisciplineSettings(), closed=[])
    assert out["score"] is None and out["note"] == NOT_YET and (out["open"], out["closed"]) == (2, 0)


def test_with_a_closed_decision_the_score_says_what_it_covers():
    out = weekly_score({1: "t:1", 2: "t:2", 3: "g:x", 4: "g:x"}, [TagState(1, "REVENGE", "CONFIRMED")], DisciplineSettings(), closed=[1, 3])
    assert out["score"] is not None and out["note"] == "(2 open · 1 closed)"         # the structure g:x has an open leg
    assert weekly_score({}, [], DisciplineSettings(), closed=[])["note"] is None      # no decisions: no score, no note


def test_callers_without_closed_ids_keep_the_old_score():
    out = weekly_score({1: "t:1"}, [], DisciplineSettings())
    assert out["score"] == 100 and out["note"] == "(0 open · 1 closed)"
