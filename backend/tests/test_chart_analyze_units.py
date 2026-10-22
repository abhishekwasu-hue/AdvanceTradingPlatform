"""Analyze a chart: the safe image path, the output contract, the settings, the private store, the provider boundary and
the AI plumbing - unit tests on made-up inputs (synthetic images drawn in code, a fake engine)."""
import asyncio
import ast
import io
import json
import struct
import zlib
from pathlib import Path

import pytest
from PIL import Image

from app.ai.metering import MeteredProvider
from app.ai.providers import Completion
from app.chart_analyze import contract, imaging, provider, settings as settings_mod, storage
from tests.chart_analyze_fakes import HEIGHT, WIDTH, FakeProvider, answer, synthetic_chart

SETTINGS = settings_mod.merge({})


# --- imaging -----------------------------------------------------------------------------------------------------------

def _png_with_chunk(png: bytes, kind: bytes, data: bytes) -> bytes:
    """Inserts an extra chunk right after IHDR (a text chunk, for example)."""
    ihdr_end = 8 + 8 + 13 + 4
    chunk = struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return png[:ihdr_end] + chunk + png[ihdr_end:]


def test_png_upload_is_re_encoded_without_metadata_or_trailing_bytes():
    original = _png_with_chunk(synthetic_chart(), b"tEXt", b"Author\0someone@example.com") + b"TRAILING-PAYLOAD"
    clean = imaging.sanitize(original, SETTINGS)
    assert clean.png != original and (clean.width, clean.height) == (WIDTH, HEIGHT)
    assert imaging.png_chunks(clean.png) == ["IHDR", "IDAT", "IEND"] or set(imaging.png_chunks(clean.png)) == {"IHDR", "IDAT", "IEND"}
    assert b"someone@example.com" not in clean.png and b"TRAILING-PAYLOAD" not in clean.png
    assert len(clean.sha256) == 64


def test_jpeg_exif_is_stripped():
    exif = Image.Exif()
    exif[0x010F] = "MadeUpCameraMaker"         # Make
    exif[0x0131] = "MadeUpSoftware"           # Software
    original = synthetic_chart("JPEG", exif=exif.tobytes())
    with Image.open(io.BytesIO(original)) as probe:
        assert probe.getexif().get(0x010F) == "MadeUpCameraMaker"
    clean = imaging.sanitize(original, SETTINGS)
    assert clean.png.startswith(imaging.PNG_MAGIC) and b"MadeUpCameraMaker" not in clean.png
    with Image.open(io.BytesIO(clean.png)) as out:
        assert dict(out.getexif()) == {} and "exif" not in out.info and "icc_profile" not in out.info


def test_jpeg_orientation_is_applied_before_the_metadata_goes():
    exif = Image.Exif()
    exif[0x0112] = 6                          # rotate 90 degrees
    clean = imaging.sanitize(synthetic_chart("JPEG", exif=exif.tobytes()), SETTINGS)
    assert (clean.width, clean.height) == (HEIGHT, WIDTH)


def test_oversize_file_is_refused_413():
    with pytest.raises(imaging.ImageRejected) as err:
        imaging.sanitize(synthetic_chart(), {**SETTINGS, "max_image_bytes": 100})
    assert err.value.status == 413


@pytest.mark.parametrize("data", [b"GIF89a" + b"\0" * 100, b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"%PDF-1.7", b"RIFF....WEBPVP8 ",
                                  b"<html>chart.png</html>"])
def test_wrong_type_is_refused_415_by_magic_bytes(data):
    with pytest.raises(imaging.ImageRejected) as err:
        imaging.sanitize(data, SETTINGS)
    assert err.value.status == 415


def test_too_many_pixels_or_too_large_dimensions_are_refused_before_decoding():
    with pytest.raises(imaging.ImageRejected) as err:
        imaging.sanitize(synthetic_chart(width=600, height=400), {**SETTINGS, "max_image_pixels": 600 * 400 - 1})
    assert err.value.status == 413
    with pytest.raises(imaging.ImageRejected) as err:
        imaging.sanitize(synthetic_chart(width=600, height=400), {**SETTINGS, "max_image_width": 599})
    assert err.value.status == 413


def test_a_decompression_bomb_header_is_refused_without_decoding():
    """A tiny PNG whose header claims 60000 x 60000 pixels (the classic bomb): refused from the header alone."""
    ihdr = struct.pack(">IIBBBBB", 60000, 60000, 8, 2, 0, 0, 0)
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)  # noqa: E731
    bomb = imaging.PNG_MAGIC + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\0" * 1000)) + chunk(b"IEND", b"")
    with pytest.raises(imaging.ImageRejected) as err:
        imaging.sanitize(bomb, SETTINGS)
    assert err.value.status == 413


@pytest.mark.parametrize("data", [imaging.PNG_MAGIC + b"garbage" * 20, imaging.JPEG_MAGIC + b"\xe0" + b"\0" * 50, b"", synthetic_chart()[:200]])
def test_corrupt_images_are_a_clear_4xx_never_a_crash(data):
    with pytest.raises(imaging.ImageRejected) as err:
        imaging.sanitize(data, SETTINGS)
    assert 400 <= err.value.status < 500


# --- the output contract -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["formed", "forming", "none"])
def test_contract_accepts_the_three_setup_states(kind):
    result = contract.validate(answer(kind), mode="image_only", width=WIDTH, height=HEIGHT)
    assert result["label"] == "Image-only analysis: prices and volume are read from the picture and may be approximate"
    assert result["disclaimer"] == contract.DISCLAIMER and result["approximate_prices"] is True
    assert result["setup"]["status"] == kind
    assert (result["what_to_wait_for"] is None) == (kind == "formed")
    if kind == "forming":
        assert result["setup"]["waiting_for"] and result["what_to_wait_for"]["alert"] == {"kind": "above", "price": 120.5}
    if kind == "none":
        assert result["setup"]["label"] is None
    for block in ("story", "trend", "current_state", "rsi", "setup", "evidence", "scenarios", "htf_context", "key_levels", "annotations"):
        assert block in result


def test_contract_withholds_advice_and_drops_scores_orders_and_off_image_points():
    raw = answer("formed")
    raw["story"] = ["Buy NIFTY now at 24800, it is a sure-shot trade.", "A calm description of the swings."]
    raw["score"] = 87
    raw["order"] = {"side": "BUY", "qty": 50}
    raw["evidence"].append({"name": "Confidence", "present": True, "reason": "made up", "score": 0.9})
    raw["annotations"]["swings"].append({"label": "HH", "x": WIDTH + 50, "y": 10})
    raw["annotations"]["areas"][0]["x1"] = -5
    result = contract.validate(raw, mode="image_only", width=WIDTH, height=HEIGHT)
    assert result["story"][0] == contract.WITHHELD and result["story"][1] == "A calm description of the swings."
    flat = json.dumps(result)
    assert "score" not in flat and '"order"' not in flat and "BUY" not in flat
    assert all(s["x"] <= WIDTH for s in result["annotations"]["swings"]) and result["annotations"]["areas"] == []


@pytest.mark.parametrize("missing", ["story", "setup", "scenarios", "trend"])
def test_contract_refuses_an_answer_without_a_required_block(missing):
    raw = answer("formed")
    raw.pop(missing)
    with pytest.raises(contract.ContractError):
        contract.validate(raw, mode="image_only", width=WIDTH, height=HEIGHT)


def test_contract_bounds_enums_and_prices():
    raw = answer("forming")
    raw["setup"]["side"] = "short everything"
    raw["trend"]["side"] = "rocket"
    raw["key_levels"].append({"kind": "area", "price_low": "abc", "price_high": None})
    raw["what_to_wait_for"]["alert"] = {"kind": "between", "low": 130, "high": 120}
    result = contract.validate(raw, mode="image_only", width=WIDTH, height=HEIGHT)
    assert result["setup"]["side"] == "none" and result["trend"]["side"] == "unclear"
    assert all(lv["price_low"] is not None or lv["price_high"] is not None for lv in result["key_levels"])
    assert result["what_to_wait_for"]["alert"] == {"kind": "between", "low": 120.0, "high": 130.0}


# --- settings ------------------------------------------------------------------------------------------------------------

def test_settings_defaults_and_bounds():
    assert SETTINGS["retention_days"] == 30
    assert settings_mod.merge({"retention_days": 0, "max_image_bytes": "x"})["retention_days"] == 30
    with pytest.raises(settings_mod.SettingsError):
        settings_mod.validate_update(SETTINGS, {"retention_days": 0})
    with pytest.raises(settings_mod.SettingsError):
        settings_mod.validate_update(SETTINGS, {"limits": {"trial": {"daily": 50, "monthly": 10}}})
    with pytest.raises(settings_mod.SettingsError):
        settings_mod.validate_update(SETTINGS, {"nonsense": 1})
    updated = settings_mod.validate_update(SETTINGS, {"retention_days": 7, "limits": {"trial": {"daily": 1}}})
    assert updated["retention_days"] == 7 and updated["limits"]["trial"]["daily"] == 1
    assert SETTINGS["limits"]["trial"]["monthly"] < SETTINGS["limits"]["pro"]["monthly"]       # the trial is smaller


def test_no_number_in_the_logic_modules_duplicates_a_setting_default():
    """Settings-driven: sizes, retention, limits and costs come from settings.DEFAULTS only."""
    base = Path(__file__).resolve().parents[1] / "app" / "chart_analyze"
    d = settings_mod.DEFAULTS
    defaults = {d["share_expiry_days"], d["max_image_bytes"], d["max_image_width"], d["max_image_height"], d["max_image_pixels"], d["retention_days"],
                d["counter_keep_days"], d["max_output_tokens"], d["estimate_input_tokens"], d["estimate_output_tokens"]}
    defaults |= {tier["monthly"] for tier in d["limits"].values() if tier["monthly"] > 5}
    for name in ("imaging.py", "linkfetch.py", "storage.py", "limits.py", "engine.py", "service.py", "routes.py", "share.py"):
        tree = ast.parse((base / name).read_text())
        digits = {id(c.args[1]) for c in ast.walk(tree) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "round" and len(c.args) > 1}
        digits |= {id(sl.upper) for sl in ast.walk(tree) if isinstance(sl, ast.Slice) and sl.upper is not None}   # [:20] column widths
        numbers = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
                   and not isinstance(n.value, bool) and id(n) not in digits}       # round() digits and column widths are not limits
        assert not (numbers & defaults), f"{name} repeats a setting default: {numbers & defaults}"


# --- the private store --------------------------------------------------------------------------------------------------

def test_store_writes_private_files_from_server_names_only(tmp_path, monkeypatch):
    monkeypatch.setenv(storage.ENV, str(tmp_path / "store"))
    name = storage.new_name(7)
    storage.write(name, b"\x89PNGdata")
    full = tmp_path / "store" / name
    assert full.read_bytes() == b"\x89PNGdata" and oct(full.stat().st_mode & 0o777) == "0o600"
    assert oct((tmp_path / "store" / "7").stat().st_mode & 0o777) == "0o700"
    assert storage.read(name) == b"\x89PNGdata"
    for bad in ("../../etc/passwd", "7/../../x.png", "/etc/passwd", "7/notahexname.png", "0/" + "a" * 32 + ".png"):
        assert storage.read(bad) is None
        with pytest.raises(ValueError):
            storage.write(bad, b"x")
    assert storage.delete(name) and not full.exists() and storage.delete(name)


def test_store_refuses_the_source_tree_and_relative_roots(monkeypatch):
    monkeypatch.setenv(storage.ENV, str(Path(__file__).resolve().parent / "private"))
    with pytest.raises(storage.StorageUnavailable):
        storage.root()
    monkeypatch.setenv(storage.ENV, "relative/dir")
    with pytest.raises(storage.StorageUnavailable):
        storage.root()


# --- the provider boundary ------------------------------------------------------------------------------------------------

def test_engine_not_installed_without_the_private_package():
    provider.reset_for_tests()
    try:
        assert provider.load() is None                     # public CI never has atp_private
        assert provider.describe() == {"installed": False, "message": "Analysis engine not installed"}
    finally:
        provider.reset_for_tests()


def test_a_fake_engine_satisfies_the_interface():
    provider.set_for_tests(FakeProvider())
    try:
        engine = provider.load()
        assert isinstance(engine, provider.AnalysisProvider) and provider.describe()["installed"] is True
    finally:
        provider.reset_for_tests()


def test_no_method_text_in_the_public_package():
    """The public package carries no instructions for the model: only the interface and the loader."""
    base = Path(__file__).resolve().parents[1] / "app" / "chart_analyze"
    for path in base.glob("*.py"):
        text = path.read_text()
        assert "atp_private" not in text or path.name == "provider.py", path.name
        assert "You are" not in text and "Answer with ONE JSON" not in text, path.name


# --- no order path ---------------------------------------------------------------------------------------------------------

def test_the_feature_has_no_order_path():
    base = Path(__file__).resolve().parents[1] / "app" / "chart_analyze"
    banned = ("app.execution", "app.paper", "app.trading", "app.live", "app.brokers", "place_order", "submit_order", "OrderRecord",
              "approval", "proposal")
    for path in base.glob("*.py"):
        tree = ast.parse(path.read_text())
        imported = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        imported += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        for name in imported:
            assert not any(name.startswith(b) for b in banned if b.startswith("app.")), f"{path.name} imports {name}"
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not names & {"place_order", "submit_order", "OrderRecord"}, path.name


# --- the AI plumbing ---------------------------------------------------------------------------------------------------------

class _Inner:
    name, model = "anthropic", "claude-test"

    def __init__(self):
        self.seen = None

    async def complete_full(self, system, user, *, max_tokens=2000, image_png=None):
        self.seen = image_png
        return Completion(text="ok", model=self.model)

    async def complete(self, system, user, *, max_tokens=2000):
        return "ok"


def test_metered_provider_passes_the_chart_image_through_and_stays_unchanged_without_one():
    from tests.test_auth_api import _session_factory

    async def go():
        async with _session_factory() as session:
            inner = _Inner()
            llm = MeteredProvider(inner, session, tenant_id=1, feature="chart_analyze")
            await llm.complete_full("s", "u", image_png=b"\x89PNG")
            first = inner.seen
            await llm.complete_full("s", "u")
            await session.rollback()
            return first, inner.seen
    first, second = asyncio.run(go())
    assert first == b"\x89PNG" and second is None


@pytest.mark.parametrize("condition", ["close > 120.5", "close < 101", "close >= 98 and close <= 101"])
def test_the_alert_conditions_the_page_builds_are_valid_screenql(condition):
    """'Set an alert on this area' / 'Alert me when this happens' create an ordinary instrument rule (POST
    /api/alerts/rules) with one of these condition shapes (frontend src/chartAnalyze/alerts.ts)."""
    from app.screener import compile_screen
    _, validated = compile_screen(condition, base_tf="15m")
    assert validated.ok, [p.as_dict() for p in validated.problems]
