"""Day-one fix 6 (README row 19): one name per idea - a strategy template an idea adopts carries the idea's own name, so
Ideas, the Trading bot form and Signals never show two names for the same thing (A5 was "Structure flip retest" while its
idea T5 is "Breakout and retest (structure flip)"). Only the display name changed: the template's id, version and
params hash (adopted bots keep their behaviour) are untouched."""
from app.idea_library import content
from app.strategy_templates import library
from app.strategy_templates.compile import params_hash


def test_every_adopted_template_is_named_like_its_idea():
    lib = content.load()
    mapped = [(i, i.adopt.template) for i in lib.ideas if i.adopt is not None]
    assert mapped, "no idea adopts a template"
    for idea, tid in mapped:
        assert library.get(tid).name == idea.name, (idea.id, tid)
    assert library.get("A5").name == "Breakout and retest"


def test_the_rename_keeps_the_strategy_id_and_hash():
    from app.strategy_templates import ids
    t = library.get("A5")
    sid, settings = ids.adopt(t, "1d", None, {})
    assert sid.startswith("template:A5:") and t.version == 1
    assert "Breakout" not in sid and isinstance(params_hash(t, settings), str)
