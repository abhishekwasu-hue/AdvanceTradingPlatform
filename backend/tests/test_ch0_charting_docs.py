"""Part CH0: the charting spec, design note and ADR-0023 are stored, indexed and carry the build's rules."""
import pathlib

DOCS = pathlib.Path(__file__).resolve().parents[2] / "docs"


def test_charting_spec_design_and_adr_are_in_place():
    assert (DOCS / "specs" / "ATP_CHARTING_SPEC.md").exists()
    design = (DOCS / "design" / "CHARTING.md").read_text(encoding="utf-8")
    adr = (DOCS / "adr" / "0023-chart-engine-abstraction.md").read_text(encoding="utf-8")
    assert "(0023-chart-engine-abstraction.md)" in (DOCS / "adr" / "README.md").read_text(encoding="utf-8")
    assert "**Status:** provisional" in adr and "ADR-0006" in adr
    for rule in ("never from pixels", "No action places an order", "no future data", "CH-2", "CH1", "ScreenQL"):
        assert rule in design
