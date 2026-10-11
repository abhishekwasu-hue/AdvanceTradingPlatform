"""Docs guard: every ADR file is listed in docs/adr/README.md with its status, every ADR states a status, and the
provider seams ADR-0016 names are all in docs/PROVIDERS.md."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADR = ROOT / "docs/adr"


def test_every_adr_is_indexed_with_a_status():
    index = (ADR / "README.md").read_text(encoding="utf-8")
    files = sorted(p.name for p in ADR.glob("0*.md"))
    assert files, "no ADRs found"
    for name in files:
        text = (ADR / name).read_text(encoding="utf-8")
        status = re.search(r"\*\*Status:\*\*\s*(\w+)", text)
        assert status, name
        row = next((line for line in index.splitlines() if f"]({name})" in line), None)
        assert row is not None, f"{name} missing from docs/adr/README.md"
        assert row.rstrip(" |").endswith(status.group(1)), (name, status.group(1), row)


def test_provider_seams_in_adr_0016_are_documented():
    adr = (ADR / "0016-provider-seams.md").read_text(encoding="utf-8")
    providers = (ROOT / "docs/PROVIDERS.md").read_text(encoding="utf-8")
    for seam in ("Market data", "Alt data", "ML pipeline", "Execution venue", "Notification", "Billing", "LLM"):
        assert seam.lower() in providers.lower(), seam
    for interface in re.findall(r"`(app/[a-z_/]+\.py)`", adr):
        assert interface in providers, interface
