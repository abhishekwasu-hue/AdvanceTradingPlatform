"""The repository is public (docs/PUBLIC_REPO_POLICY.md). `scripts/check_public_repo.py` (CI lint job, pre-commit hook)
fails on a server IP, a private e-mail address, a Telegram token or chat id, a private key, a secret file type and a new
golden / parity / real-data fixture. These tests run it on the real repository (clean) and on small trees. Every
private-looking value below is built at run time from parts, so this file holds none of them literally (the check and
the secret scans read this file too)."""
import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_public_repo.py"

PUBLIC_IP = ".".join(["45", "33", "32", "156"])           # a public address that is not in the allow-list
PRIVATE_EMAIL = "someone" + "@" + "gmail.com"
BOT_TOKEN = "12345678" + ":" + "AAH" + "x" * 32                  # the shape of a Telegram bot token (35 characters)
LONG_CHAT_ID = "1" + "23456789"
SUPERGROUP = "-100" + "1234567890"
KEY_HEADER = "-" * 5 + "BEGIN RSA " + "PRIVATE KEY" + "-" * 5


def _load():
    spec = importlib.util.spec_from_file_location("check_public_repo", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_public_repo = _load()


def _tree(tmp_path: Path, files: dict) -> Path:
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    return tmp_path


def test_the_repository_is_clean():
    problems = check_public_repo.check(REPO)
    assert problems == [], "\n".join(problems)


def test_every_grandfathered_fixture_still_exists_and_is_still_caught():
    # The list only shrinks: a file moved to the private store (or replaced by synthetic data) leaves the list too.
    for rel in sorted(check_public_repo.GRANDFATHERED_FIXTURES):
        assert (REPO / rel).is_file(), f"{rel} is gone - remove it from GRANDFATHERED_FIXTURES"
        assert check_public_repo.path_problems(rel) == []
    flagged = {rel for rel in check_public_repo.GRANDFATHERED_FIXTURES if _without_grandfathering(rel)}
    assert flagged == set(check_public_repo.GRANDFATHERED_FIXTURES), "an entry the rules would not flag needs no grandfathering"


def _without_grandfathering(rel: str) -> list:
    saved = check_public_repo.GRANDFATHERED_FIXTURES, check_public_repo.ALLOWED_TEST_DATA
    check_public_repo.GRANDFATHERED_FIXTURES, check_public_repo.ALLOWED_TEST_DATA = {}, {}
    try:
        return check_public_repo.path_problems(rel)
    finally:
        check_public_repo.GRANDFATHERED_FIXTURES, check_public_repo.ALLOWED_TEST_DATA = saved


def test_every_listed_data_file_is_pinned_to_its_current_content():
    for rel, pinned in {**check_public_repo.GRANDFATHERED_FIXTURES, **check_public_repo.ALLOWED_TEST_DATA}.items():
        assert (REPO / rel).is_file(), f"{rel} is gone - remove it from the list"
        assert check_public_repo.content_sha256((REPO / rel).read_bytes()) == pinned, rel
        assert _without_grandfathering(rel), f"{rel} would not be flagged - it needs no entry"


def test_a_clean_tree_passes(tmp_path):
    root = _tree(tmp_path, {
        "app/a.py": 'HOST = "203.0.113.10"\nLAN = "10.0.0.5"\nLOOP = "127.0.0.1"\nUA = "Chrome/120.0.0.0 Safari/537.36"\n'
                    'DNS = "8.8.8.8"\nOWNER = "owner@example.com"\nOTHER = "a@b.example"\nVERSION = "300.1.1.1"\n',
        "tests/test_t.py": 'TELEGRAM = {"bot_token": "123456:ABC-DEF-ghi", "chat_id": "987654"}\nGROUP = "-100777"\n',
        ".env.example": "JWT_SECRET_KEY=change-me\n",
        "backend/.env": "# keys only\nJWT_SECRET_KEY=\nPOSTGRES_PASSWORD=''\n",
        "tests/fixtures/cases.json": "{}\n",
        "src/chart.png": b"\x89PNG\r\n",
    })
    assert check_public_repo.check(root) == []


def test_the_production_ip_is_refused_everywhere(tmp_path, monkeypatch):
    # The real address is matched by its sha256; here a documentation address stands in for it, so even an address the
    # other rules let pass is refused, and after a product token too.
    stand_in = "198.51.100.77"
    monkeypatch.setattr(check_public_repo, "PRODUCTION_IP_SHA256", frozenset({hashlib.sha256(stand_in.encode()).hexdigest()}))
    root = _tree(tmp_path, {"docs/a.md": f"ssh atp@{stand_in}\nAgent/{stand_in}\n"})
    problems = check_public_repo.check(root)
    assert len(problems) == 2 and all("production server's IP" in p for p in problems)
    assert all(stand_in not in p for p in problems)


def test_the_production_ip_hash_is_not_a_documentation_or_allowed_address():
    for ip in [*check_public_repo.ALLOWED_IPS, "203.0.113.10", "192.0.2.1", "198.51.100.1"]:
        assert not check_public_repo.is_production_ip(ip)


def test_a_public_ip_is_refused(tmp_path):
    root = _tree(tmp_path, {"docs/a.md": f"the host is {PUBLIC_IP}\n", "tests/t.ts": f'const ip = "{PUBLIC_IP}";\n'})
    problems = check_public_repo.check(root)
    assert len(problems) == 2 and all("public IPv4 address" in p for p in problems)


def test_the_ip_allow_list_is_small_and_explicit():
    assert set(check_public_repo.ALLOWED_IPS) == {"1.1.1.1", "8.8.8.8", "9.9.9.9", "1.2.3.4", "5.6.7.8", "93.184.216.34", "224.0.0.1"}
    assert not check_public_repo.is_flagged_ip("172.31.0.1") and not check_public_repo.is_flagged_ip("100.64.0.1")
    assert check_public_repo.is_flagged_ip(PUBLIC_IP) and check_public_repo.is_flagged_ip(".".join(["239", "1", "2", "3"]))


@pytest.mark.parametrize("address, allowed", [
    ("owner@example.com", True), ("x@atp.example.com", True), ("t@example.invalid", True), ("a@host.test", True),
    ("apihelp@dhan.co", True), ("git@github.com", True), ("Someone@Example.COM", True),
    (PRIVATE_EMAIL, False), ("support" + "@" + "upstox.com", False), ("x" + "@" + "example.co", False),
])
def test_email_rule(address, allowed):
    assert check_public_repo.is_allowed_email(address) is allowed


def test_a_private_email_is_refused_and_redacted(tmp_path):
    root = _tree(tmp_path, {"docs/a.md": f"write to {PRIVATE_EMAIL} today\n", "deploy/x.sh": 'R="${R:-git@github.com:o/r.git}"\n'})
    problems = check_public_repo.check(root)
    assert len(problems) == 1 and "e-mail address" in problems[0] and PRIVATE_EMAIL not in problems[0]


def test_telegram_token_and_chat_ids_are_refused_and_redacted(tmp_path):
    root = _tree(tmp_path, {
        "a.py": f'TOKEN = "{BOT_TOKEN}"\n',
        "b.py": f'cfg = {{"chat_id": "{LONG_CHAT_ID}"}}\n',
        "c.env.example": f"TELEGRAM_CHAT_ID={LONG_CHAT_ID}\n",
        "d.md": f"the group {SUPERGROUP}\n",
    })
    problems = check_public_repo.check(root)
    assert sorted(p.split(":")[0] for p in problems) == ["a.py", "b.py", "c.env.example", "d.md"]
    assert all(BOT_TOKEN not in p and LONG_CHAT_ID not in p for p in problems)


def test_a_private_key_header_is_refused(tmp_path):
    root = _tree(tmp_path, {"notes.txt": f"{KEY_HEADER}\nMIIB...\n"})
    problems = check_public_repo.check(root)
    assert len(problems) == 1 and "private key header" in problems[0]


@pytest.mark.parametrize("rel", ["deploy/server.pem", "certs/tls.key", "android/upload.jks", "android/release.keystore",
                                 "a/cert.p12", "a/cert.pfx", "a/encrypted.pepk", "android/app/google-services.json",
                                 "ios/App/GoogleService-Info.plist", "deploy/putty.ppk", "android/keystore.properties",
                                 "android/signing.properties", "deploy/fcm-service-account.json", "a/gcp_service_account_prod.json",
                                 "a/broker_credentials.json"])
def test_secret_file_types_are_refused(tmp_path, rel):
    root = _tree(tmp_path, {rel: b"x"})
    problems = check_public_repo.check(root)
    assert len(problems) == 1 and "secret file type" in problems[0]


@pytest.mark.parametrize("rel", [".env", "backend/.env", "frontend/.env.production", "frontend/.env.android.local", "prod.env"])
def test_an_env_file_with_values_is_refused(tmp_path, rel):
    root = _tree(tmp_path, {rel: "# settings\nAPP_KEY=\nJWT_SECRET_KEY=abc123\n"})
    problems = check_public_repo.check(root)
    assert len(problems) == 1 and "an .env file with values" in problems[0] and ":3:" in problems[0]


@pytest.mark.parametrize("rel", [
    "backend/tests/fixtures/reader/golden_layer1.json.gz",
    "backend/tests/fixtures/charts/new_parity.json",
    "frontend/src/charting/__fixtures__/golden_new.json",
    "backend/tests/golden/new_models.json",
    "backend/tests/fixtures/zones/nifty_1m.csv",
    "backend/tests/fixtures/data/bars.parquet",
    "backend/tests/fixtures/data/blob.gz",
    "backend/tests/fixtures/charts/abhi_chart.png",
    "docs/trade_journal_2026.csv",
    "data/Tradebook-2026.xlsx",
    "exports/chart_export_bn.png",
])
def test_a_new_golden_parity_or_real_data_file_is_refused(tmp_path, rel):
    root = _tree(tmp_path, {rel: b"1,2,3\n"})
    problems = check_public_repo.check(root)
    assert len(problems) == 1 and "private fixtures store" in problems[0]


@pytest.mark.parametrize("rel", [
    "backend/tests/golden_reference.py", "backend/tests/fixtures/zones/make_golden.py", "backend/tests/test_zones_parity.py",
    "backend/app/journal/service.py", "frontend/e2e/fixtures/notifications.json", "backend/tests/fixtures/zones/README.md",
    "backend/app/instruments/data/nse_index_expiries.csv", "docs/design/ui-audit/01-dashboard.png",
])
def test_code_docs_and_ordinary_data_are_not_fixtures(tmp_path, rel):
    root = _tree(tmp_path, {rel: "x = 1\n"})
    assert check_public_repo.check(root) == []


def test_a_grandfathered_fixture_passes_but_a_sibling_does_not(tmp_path):
    kept = "backend/tests/fixtures/zones/bn_daily.csv"
    root = _tree(tmp_path, {kept: (REPO / kept).read_bytes(), "backend/tests/fixtures/zones/bn_daily_v2.csv": "1,2\n"})
    problems = check_public_repo.check(root)
    assert len(problems) == 1 and problems[0].startswith("backend/tests/fixtures/zones/bn_daily_v2.csv")


def test_a_listed_file_with_new_content_is_refused(tmp_path):
    kept = "backend/tests/fixtures/zones/bn_daily.csv"
    allowed = "frontend/src/charting/__fixtures__/chart_bands_cases.json"
    original = (REPO / kept).read_bytes()
    same_with_crlf = _tree(tmp_path / "crlf", {kept: original.replace(b"\n", b"\r\n")})
    assert check_public_repo.check(same_with_crlf) == []
    root = _tree(tmp_path / "changed", {kept: original + b"9,9,9,9,9\n", allowed: (REPO / allowed).read_bytes() + b" "})
    problems = sorted(check_public_repo.check(root))
    assert len(problems) == 2
    assert problems[0].startswith(kept) and "grandfathered fixture with new content" in problems[0]
    assert problems[1].startswith(allowed) and "update its sha256 in ALLOWED_TEST_DATA" in problems[1]


@pytest.mark.parametrize("rel", [
    "backend/tests/data/cases.json", "backend/tests/fixtures/new/arrays.npz", "backend/tests/model.pkl",
    "backend/tests/fixtures/charts/bars.json", "backend/tests/fixtures/x/snapshot.json.gz", "backend/tests/cache.sqlite",
    "frontend/src/charting/__fixtures__/new_cases.json", "frontend/src/utils/__fixtures__/bars.csv",
])
def test_a_new_data_file_in_the_tests_is_refused(tmp_path, rel):
    root = _tree(tmp_path, {rel: b"{}"})
    problems = check_public_repo.check(root)
    assert len(problems) == 1 and "private fixtures store" in problems[0]


@pytest.mark.parametrize("rel", ["frontend/e2e/fixtures/new.json", "backend/evals/sets/new.jsonl", "backend/tests/conftest.py",
                                 "backend/tests/fixtures/zones/NOTES.md"])
def test_data_outside_the_test_data_folders_is_not_pinned(tmp_path, rel):
    assert check_public_repo.check(_tree(tmp_path, {rel: "{}\n"})) == []


@pytest.mark.parametrize("rel", [
    "docs/knowledge/CHART_READER_METHOD.md", "docs/knowledge/zones.md", "backend/app/reader/method_pack.json",
    "packs/Method-Pack-v3.zip", "docs/KNOWLEDGE_BASE.md", "kb/knowledge_base_2026.md", "notes/ZONES_METHOD_NOTES.md",
    "docs/chart_reader_method_v2.md",
])
def test_the_kb_and_method_pack_are_refused(tmp_path, rel):
    problems = check_public_repo.check(_tree(tmp_path, {rel: "x\n"}))
    assert len(problems) == 1 and "PR-1" in problems[0]


@pytest.mark.parametrize("rel", ["docs/knowledge.md", "backend/app/reader/methods.py", "docs/design/TRADE_TAKEOVER.md",
                                 "backend/app/kb/base.py"])
def test_ordinary_paths_are_not_the_kb(tmp_path, rel):
    assert check_public_repo.check(_tree(tmp_path, {rel: "x\n"})) == []


def test_the_command_line_exit_codes(tmp_path):
    clean = _tree(tmp_path / "clean", {"a.md": "nothing private here\n"})
    dirty = _tree(tmp_path / "dirty", {"a.md": f"token {BOT_TOKEN}\n"})
    ok = subprocess.run([sys.executable, str(SCRIPT), "--root", str(clean)], capture_output=True, text=True)
    bad = subprocess.run([sys.executable, str(SCRIPT), "--root", str(dirty)], capture_output=True, text=True)
    assert ok.returncode == 0 and "OK" in ok.stdout
    assert bad.returncode == 1 and "Telegram bot token" in bad.stderr and BOT_TOKEN not in bad.stderr


@pytest.mark.parametrize("rel, text, flagged", [
    (".npmrc", "//registry.npmjs.org/:_authToken=" + "npm_" + "x" * 20 + "\n", True),
    ("frontend/.npmrc", "_auth=" + "dXNlcjpwYXNz" + "\n", True),
    (".npmrc", "//registry.npmjs.org/:_authToken=${NPM_TOKEN}\nsave-exact=true\n", False),
    (".pypirc", "[pypi]\nusername = __token__\npassword = " + "pypi-" + "x" * 20 + "\n", True),
    (".pypirc", "[pypi]\nusername = __token__\npassword = ${PYPI_TOKEN}\n", False),
])
def test_npmrc_and_pypirc_with_a_token(tmp_path, rel, text, flagged):
    problems = check_public_repo.check(_tree(tmp_path, {rel: text}))
    assert bool(problems) is flagged
    assert all("token or password" in p and "x" * 20 not in p for p in problems)


@pytest.mark.parametrize("spelling", ["{a}-{b}-{c}-{d}", "ip-{a}-{b}-{c}-{d}.host", "{a}_{b}_{c}_{d}", "{a} {b} {c} {d}",
                                      "{a}.{b}.{c}.{d}", "srv/{a}.{b}.{c}.{d}"])
def test_the_production_ip_is_refused_in_other_notations(tmp_path, monkeypatch, spelling):
    a, b, c, d = "198", "51", "100", "77"
    monkeypatch.setattr(check_public_repo, "PRODUCTION_IP_SHA256", frozenset({hashlib.sha256(f"{a}.{b}.{c}.{d}".encode()).hexdigest()}))
    problems = check_public_repo.check(_tree(tmp_path, {"docs/a.md": "host " + spelling.format(a=a, b=b, c=c, d=d) + "\n"}))
    assert len(problems) == 1 and "production server's IP" in problems[0]


def test_only_known_browser_tokens_excuse_a_version_like_address(tmp_path):
    root = _tree(tmp_path, {
        "ua.txt": f"Mozilla/5.0 Chrome/{PUBLIC_IP} Safari/{PUBLIC_IP} HeadlessChrome/{PUBLIC_IP} Firefox/{PUBLIC_IP}\n",
        "url.txt": f"proxy foo/{PUBLIC_IP}\nhttp://{PUBLIC_IP}/x\n",
    })
    problems = check_public_repo.check(root)
    assert len(problems) == 2 and all(p.startswith("url.txt") for p in problems)
