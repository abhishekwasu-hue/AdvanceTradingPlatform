"""Part S0: the Screener spec, design note and its two ADRs are stored, indexed and say the rules that bind the build."""
import pathlib

DOCS = pathlib.Path(__file__).resolve().parents[2] / "docs"


def test_screener_spec_design_and_adrs_are_in_place_and_indexed():
    assert (DOCS / "specs" / "ATP_SCREENER_SPEC.md").exists()
    design = (DOCS / "design" / "SCREENER.md").read_text(encoding="utf-8")
    index = (DOCS / "adr" / "README.md").read_text(encoding="utf-8")
    for adr in ("0021-screener-engine.md", "0022-notification-service.md"):
        text = (DOCS / "adr" / adr).read_text(encoding="utf-8")
        assert f"({adr})" in index and "**Status:** provisional" in text
        assert "ADR-0006" in text                                       # alerts and screens never place an order
    for rule in ("server data only", "SC-1", "SC-2", "S1a", "parity", "output filter"):
        assert rule in design


def test_every_adr_file_is_indexed():
    index = (DOCS / "adr" / "README.md").read_text(encoding="utf-8")
    missing = [p.name for p in (DOCS / "adr").glob("0*.md") if f"({p.name})" not in index]
    assert missing == []
