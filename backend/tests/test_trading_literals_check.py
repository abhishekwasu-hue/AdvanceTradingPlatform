"""M68 TS-7 (owner order M68): `scripts/check_trading_literals.py` (CI, lint job) fails the build when a new trading
literal appears in execution / risk / strategy / ticket code outside the settings registry; exchange / broker facts and
the other kept values sit on its allow-list, each with a reason. These tests run it on the real repository (clean) and on
small trees: every form of a new literal fails, an allow-listed one passes, a stale allow-list entry fails, comments and
the registry are not read. All code here is made up for the test."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_trading_literals.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_trading_literals", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module               # its dataclass reads its own module
    spec.loader.exec_module(module)
    return module


guard = _load()
PY = "backend/app/execution/new_path.py"
TS = "frontend/src/fno/newTicket.ts"


def _tree(tmp_path: Path, files: dict) -> Path:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def _found(tmp_path: Path, rel: str, text: str) -> list:
    return [(f.name, f.value) for f in guard.scan(_tree(tmp_path, {rel: text}))]


def test_the_repository_is_clean():
    assert guard.check(REPO) == []
    out = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert out.returncode == 0 and "clean" in out.stdout, out.stdout + out.stderr


def test_every_allow_list_entry_has_a_reason_and_is_in_scope():
    for (path, name, value), reason in guard.ALLOWED.items():
        assert reason.strip() and len(reason) > 20, (path, name, value)
        assert any(path == s or path.startswith(s + "/") for s in guard.SCOPE_PY + guard.SCOPE_TS), path
    assert guard.REGISTRY not in {p for p, _n, _v in guard.ALLOWED}


@pytest.mark.parametrize("code, expected", [
    ("MAX_LOTS = 500\n", ("MAX_LOTS", "500")),
    ("RISK_PCT: float = 2.0\n", ("RISK_PCT", "2.0")),
    ("def size(lots: int = 2):\n    return lots\n", ("lots", "2")),
    ("def place(*, product: str = 'MIS'):\n    return product\n", ("product", "MIS")),
    ("send(order_type='MARKET')\n", ("order_type", "MARKET")),
    ("BAND = {'protection_pct': 0.5}\n", ("protection_pct", "0.5")),
    ("def ok(lots):\n    return lots > 500\n", ("lots", "500")),
    ("from datetime import time\nCUT = time(15, 15)\n", ("time", "time(15, 15)")),
    ("SQUARE_OFF_AT = '15:15'\n", ("SQUARE_OFF_AT", "15:15")),
    ("target_rr = (1.5, 2.5)\n", ("target_rr", "(1.5, 2.5)")),
    ("x = params.get('target_rr', 2.0)\n", ("target_rr", "2.0")),
    ("class S(BaseModel):\n    lots: int = Field(default=3)\n", ("lots", "3")),
    ("STOP_LIMIT_BAND_PCT = 1.0\n", ("STOP_LIMIT_BAND_PCT", "1.0")),          # 1.0 is a 1 % band, not "one"
])
def test_a_new_python_trading_literal_fails(tmp_path, code, expected):
    assert expected in _found(tmp_path, PY, code)
    problems = guard.check(tmp_path, allowed={})
    assert any(f"{expected[0]} = {expected[1]}" in p and "Trade settings registry" in p for p in problems), problems


@pytest.mark.parametrize("code", [
    "lots = 1\nquantity = 0\nstop = -1\n",                     # one / none / a sentinel: not a chosen value
    "ORDER_TYPES = ('MARKET', 'LIMIT', 'SL', 'SL-M')\n",       # a list of the choices chooses none
    "if order_type == 'MARKET':\n    pass\n",                   # reading his choice, not setting one
    "TIMEOUT_SECONDS = 30\nname = 'NIFTY'\n",                    # not a trading name / not an order word
    "# MAX_LOTS = 500 in a comment\n",
    "day_start = time(0, 0)\n",
])
def test_not_a_trading_literal(tmp_path, code):
    assert _found(tmp_path, PY, code) == []


@pytest.mark.parametrize("code, expected", [
    ("export const MAX_LOTS = 500;\n", ("MAX_LOTS", "500")),
    ("const ask = { product: \"MIS\", lots: 2 };\n", ("product", "MIS")),
    ("const ask = { product: \"MIS\", lots: 2 };\n", ("lots", "2")),
    ("const [lots, setLots] = useState(2);\n", ("lots", "2")),
    ("const [orderType, setOrderType] = useState<string>(\"MARKET\");\n", ("orderType", "MARKET")),
    ("function f(stopPct: number = 1.5) { return stopPct; }\n", ("stopPct", "1.5")),
    ("const squareOffAt = \"15:15\";\n", ("squareOffAt", "15:15")),
])
def test_a_new_typescript_trading_literal_fails(tmp_path, code, expected):
    assert expected in _found(tmp_path, TS, code)


@pytest.mark.parametrize("code", [
    "// const MAX_LOTS = 500;\n",
    "/* product: \"MIS\" */\nconst x = 1;\n",
    "/**\n * lots: 500\n */\n",
    "const [lots, setLots] = useState<number | null>(null);\n",
    "const url = \"https://example.test/a\"; // lots: 500\n",
    "const width = 640;\n",                                   # a drawing size
])
def test_typescript_comments_and_non_trading_values_are_not_read(tmp_path, code):
    assert _found(tmp_path, TS, code) == []


def test_tests_stories_and_files_outside_the_scope_are_not_read(tmp_path):
    _tree(tmp_path, {"frontend/src/fno/newTicket.test.ts": "const lots = 500;\n",
                     "frontend/src/fno/Ticket.stories.tsx": "const lots = 500;\n",
                     "backend/app/me/trade_settings.py": "LOTS = 500\n",          # the registry itself
                     "backend/app/screener/x.py": "MAX_LOTS = 500\n"})
    assert guard.scan(tmp_path) == []


def test_an_allow_listed_literal_passes_and_a_stale_entry_fails(tmp_path):
    _tree(tmp_path, {PY: "from datetime import time\nRESTS_UNTIL = time(15, 30)\n"})
    allowed = {(PY, "time", "time(15, 30)"): "fact: DAY order validity (TS1-085)"}
    assert guard.check(tmp_path, allowed=allowed) == []
    stale = {**allowed, (PY, "lots", "2"): "gone"}
    assert guard.check(tmp_path, allowed=stale) == [
        f"{PY}: allow-list entry lots = 2 matches nothing any more - remove it from ALLOWED"]
    # the allow-list is by value: a changed fact is a new literal
    _tree(tmp_path, {PY: "from datetime import time\nRESTS_UNTIL = time(15, 45)\n"})
    problems = guard.check(tmp_path, allowed=allowed)
    assert len(problems) == 2 and "time = time(15, 45)" in problems[0]


@pytest.mark.parametrize("name, expected", [
    ("lotSize", True), ("MAX_LOTS", True), ("squareOffAt", True), ("stopPct", True), ("atr_mult_sl", True),
    ("max_loss", True), ("orderType", True), ("cooldown_minutes", True), ("entry_window", True),
    ("slot", False), ("plot", False), ("atmosphere", False), ("window_seconds", False), ("PHONE_MAX_WIDTH", False),
    ("timeout", False), ("slow", False),
])
def test_what_reads_as_a_trading_name(name, expected):
    assert guard.trading_name(name) is expected


def test_the_command_fails_on_a_finding(tmp_path):
    _tree(tmp_path, {PY: "MAX_LOTS = 500\n"})
    out = subprocess.run([sys.executable, str(SCRIPT), "--root", str(tmp_path)], capture_output=True, text=True)
    # the real allow-list names files of the repository that this tree lacks: those read as stale, plus the finding
    assert out.returncode == 1 and f"{PY}:1: MAX_LOTS = 500" in out.stdout


def test_ts1_025_the_risk_default_operator_settings_are_gone():
    from app.core import config
    from app.risk_engine import by_default
    assert not [n for n in dir(config) if n.startswith("RISK_DEFAULT_")]
    assert by_default.legacy_defaults() == {"risk_per_trade_pct": 1.0, "risk_per_trade_amount": 0.0, "daily_loss_pct": 2.0,
                                            "max_trades_per_day": 10, "min_rr": 1.5, "max_sector_pct": 0.0,
                                            "max_correlated_risk_pct": 0.0, "max_open_positions": 0}
    by_default.legacy_defaults()["min_rr"] = 9.0                    # a copy: the marker itself never changes
    assert by_default.LEGACY_DEFAULTS["min_rr"] == 1.5
