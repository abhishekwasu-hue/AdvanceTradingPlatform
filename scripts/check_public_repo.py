#!/usr/bin/env python3
"""Public repository guard: nothing private enters the tree of this public repository (docs/PUBLIC_REPO_POLICY.md).

The repository is public, so everything committed is public. This check reads every tracked and new (not ignored) file
and fails on:

  - the production server's IP address, matched by its sha256 in dotted form and in dashed / underscored / spaced
    forms ("ip-a-b-c-d"), so the literal is not written here (a sha256 of an IPv4 address is reversible in seconds:
    this only keeps the literal out of the tree, it hides nothing);
  - any other public IPv4 literal, outside the documentation ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24)
    and the explicit ALLOWED_IPS below (well-known addresses the tests use); private, loopback and other non-global
    addresses are not flagged, nor is a version after a known browser token ("Chrome/120.0.0.0", "Safari/...");
  - an e-mail address, except on example / test domains (example.com, *.example, *.test, *.invalid, localhost) and the
    explicit ALLOWED_EMAILS below (public vendor support addresses, placeholders already in tests and docs);
  - a Telegram bot token, or a chat-id-like literal (a chat id / Telegram id of 7 or more digits, a -100... supergroup
    id);
  - a private key header (PEM / OpenSSH / PGP);
  - a secret file type: *.pem, *.key, *.ppk, *.jks, *.keystore, *.p12, *.pfx, *.pepk, keystore.properties,
    signing.properties, google-services.json, GoogleService-Info.plist, *service-account*.json, *credentials*.json;
    an .env file holding values (.env.example / .sample / .template are allowed); an .npmrc / .pypirc holding a token
    or password (a ${VARIABLE} reference is allowed);
  - the KB / Method Pack / Chart Reader method, by path: docs/knowledge/, *method_pack*, KNOWLEDGE_BASE*,
    *METHOD_NOTES*, CHART_READER_METHOD* (any case; open question PR-1);
  - a golden / parity fixture or a real-data file (a data file whose path says golden or parity; a CSV, spreadsheet,
    compressed or image file under a fixtures folder; a trade journal, tradebook, contract note or chart export), and
    any data or compressed file under backend/tests/ or a __fixtures__ folder, that is not in GRANDFATHERED_FIXTURES or
    ALLOWED_TEST_DATA. Both lists pin each file's sha256, so replacing the content of a listed file fails too. The
    grandfathered files are pending a move to private storage or a synthetic replacement (owner decision,
    docs/OPEN_QUESTIONS.md PR-2); that list never grows.

Every list here is explicit and narrow; adding an entry is a reviewed change. Findings are printed without the
secret-like value (redacted), because the CI log of a public repository is public too.

    python3 scripts/check_public_repo.py            # from anywhere; exit 0 = clean, 1 = findings
    python3 scripts/check_public_repo.py --root DIR # check another tree (the tests use this)

Files: the tracked and the new (not ignored) files when the root is a git work tree, else every file under the root.
Known binary types are checked by path only; a file over 8 MB is checked by path only, with a notice.

Out of scope (a person reviews these; docs/PUBLIC_REPO_POLICY.md section 4): an IP address written as an integer or
in hex, or split across lines; values inside binary files (images, archives, PDFs), UTF-16 text or files over 8 MB;
the content of screenshots under docs/.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

# The production server's IPv4 address, as the sha256 of its dotted-quad text. Not a secret (the address is in the
# history), only a way to keep the literal out of the tree. It is refused wherever it appears, even where another rule
# would let an address pass.
PRODUCTION_IP_SHA256: FrozenSet[str] = frozenset({
    "293e736c1025fa34d61e5e793268df1f745b8277f10530679e50130405c1fb10",
})

# RFC 5737 documentation ranges: never a real host.
DOC_NETWORKS = tuple(ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))

# Well-known public addresses the tests and the reference code use as examples (public resolvers, the old
# example.com address, a multicast address, obvious placeholders). Explicit; never a server of ours.
ALLOWED_IPS: Dict[str, str] = {
    "1.1.1.1": "Cloudflare public DNS (a test / reference-code example)",
    "8.8.8.8": "Google public DNS (egress / static-IP tests)",
    "9.9.9.9": "Quad9 public DNS (egress tests)",
    "1.2.3.4": "placeholder address (egress tests)",
    "5.6.7.8": "placeholder address (egress tests)",
    "93.184.216.34": "the historical example.com address (egress / injection tests)",
    "224.0.0.1": "the all-hosts multicast address (egress tests)",
}

# E-mail domains that are never a real mailbox (RFC 2606 / 6761), and the explicit addresses allowed anywhere.
_EXAMPLE_DOMAIN = re.compile(r"(?:^|\.)(?:example\.(?:com|org|net|in|invalid)|example|test|invalid|localhost)$")
ALLOWED_EMAILS: Dict[str, str] = {
    "apihelp@dhan.co": "Dhan's public API support address (docs/specs/ATP_PARTNER_EMAIL_DRAFTS.md)",
    "git@github.com": "GitHub's SSH user in the git remote of the deploy scripts, not a mailbox",
    "user@api.twilio.com": "the user-info part of a URL in an injection test (backend/tests/test_alert_gateway_injection.py)",
    "a@b.com": "a placeholder address in tests",
    "c@d.com": "a placeholder address in tests",
    "x@y.com": "a placeholder address in tests",
    "colleague@company.com": "a placeholder in the team page's invite field (frontend/src/pages/TeamPage.tsx)",
    "demo@atpdemo.in": "the UI audit's demo login (docs/design/UI_AUDIT_2026-10.md)",
    "fresh@atpdemo.in": "the UI audit's empty-account login (docs/design/UI_AUDIT_2026-10.md)",
}

# Generated files whose third-party metadata names people's contact addresses (npm deprecation notices): the e-mail rule
# only skips these; every other rule still applies.
EMAIL_EXEMPT_FILES: FrozenSet[str] = frozenset({"frontend/package-lock.json"})

# Existing golden / parity / real-data fixture files, each pinned by the sha256 of its content (CRLF read as LF).
# Grandfathered only until the owner decides (docs/OPEN_QUESTIONS.md PR-2: move to a private store that CI reads with a
# secret, or regenerate from synthetic data). This list only shrinks, and a listed file's content never changes.
GRANDFATHERED_FIXTURES: Dict[str, str] = {
    "backend/tests/fixtures/charts/pa_layer_golden.json":
        "1668c82ad3cd0aeb6d9620192117d36cfc8ce714407cfe15ee0a355674677e11",
    "backend/tests/fixtures/charts/study_parity.json":
        "790d11653b0056049d8207378cecf1bafce0de40a9e4d0a1ae38bf83518a518b",
    "backend/tests/fixtures/daily_trend/golden.json.gz":
        "87fa3fb6e6c094db5c1e281771857963ab746e1308f662404797073b10ae19f2",
    "backend/tests/fixtures/level_gate/golden.json.gz":
        "459d69bc0e0fbc6c1c9a7f83526645b06a6dca6ae4fc92e844f109dd6e75f07d",
    "backend/tests/fixtures/oi_regime/golden.json":
        "497a1c2d89806e372cb9ea8e0aa9c41aabbce1dc840480b908276bca59cfd38a",
    "backend/tests/fixtures/options_builder/golden.json.gz":
        "50123884799210b9488aecd90ca1770edb9274f4aa9e461b741c6a8b3c16b72d",
    "backend/tests/fixtures/price_action/golden_trade_1e6c9ac_ch3b.json.gz":
        "99a4c37576bdb0f5b9e7440079b489772cc90deba94e09c46a17f4b57fc687e5",
    "backend/tests/fixtures/strike_breach/golden.json.gz":
        "1bc64569fa0e9d93d24e205339698b21014cb614b2b5b15b3f492964bd2f72e2",
    "backend/tests/fixtures/universe/EQUITY_L.csv":
        "c614720d7952f79158ca3470a71b2ef56d653bf83b07646901e1d387a1cc6113",
    "backend/tests/fixtures/universe/SME_EQUITY_L.csv":
        "8c2c511250ed3789508566e9bd633845f1bf8caad2e0b406a947910f87d50b19",
    "backend/tests/fixtures/universe/eq_etfseclist.csv":
        "c6303d9f5bfb829376c315ff264219568c33104120cad2a697df04b18f801b48",
    "backend/tests/fixtures/universe/fo_mktlots.csv":
        "4c557f37797f1b105478ad38eac5cb6ce3e36be38d3e04d6f75542f0a237acbb",
    "backend/tests/fixtures/universe/fo_secban.csv":
        "63089b8fa563cac692080edefd8be8288c69a810c149b7b139b83406f9a6a1ad",
    "backend/tests/fixtures/universe/ind_close_all_09102026.csv":
        "d2128ebbe5ef12c6dc22928c11107f94461aea93b90acd98d62ccabbca092a39",
    "backend/tests/fixtures/universe/ind_testindexlist.csv":
        "544401c201de5479761f00ecc1ddc0e5605aeada400ee74db1db56ace85a9ee3",
    "backend/tests/fixtures/universe/symbolchange.csv":
        "625b27594c67da488945466f9d9ae4b96847b3fb69316ef04ce565a56dd00a5c",
    "backend/tests/fixtures/universe/symbolchange_noheader.csv":
        "5a9f35b030909aa202976eb08e24fcf3d488a37e1629d03768d68fee9ecb6c39",
    "backend/tests/fixtures/z10a/structure_base_c5f6a2e.json.gz":
        "2c5cad35f7a0b3447ff4327eeec6924b3583053cf74f099df70c6ab4e1d635de",
    "backend/tests/fixtures/zones/bn_daily.csv":
        "c0c34ac840bb0fb8e61aabcf0d191edb1c074c4a5c6ab1366cb60a0204599ec2",
    "backend/tests/fixtures/zones/golden_level_memory_eafc872.json.gz":
        "4de210c7aa0e51ad2b80acd209315f370ddc29a0a1eccd4ec6d877256be9d992",
    "backend/tests/fixtures/zones/golden_trade_eafc872.json.gz":
        "17c5f29440fe9bbe0f474c91c247718dd9bc765a711790de89ea32cc74da3c8e",
    "backend/tests/fixtures/zones/market_5m.csv":
        "7420b55dfac3550831281ad6689e513fcfd3c1c673c4b38a8b2fbb749ec4f370",
    "backend/tests/fixtures/zones/session_15m.csv":
        "03ade33bd2f57085ac25474e723a6892e0bccb623111f689d72a6864d0ae0c43",
    "backend/tests/fixtures/zones/walk_1m.csv":
        "9d0fa549c0df43a2e8b1dfe087e74e533bfe968de718accd0f003c100695719a",
    "backend/tests/golden/c2_default_models.json":
        "cf7840286077656ce099682a5c52adb0f6bb5a53658963f84efcc9fab27c6d79",
    "frontend/src/charting/__fixtures__/chart_types_golden.json.gz":
        "6be053c5f27e263e435f0a7a1ef9e74dc73e11b178597ea9fcedc21d2ec49ceb",
    "frontend/src/charting/__fixtures__/pa_layer_golden.json":
        "1668c82ad3cd0aeb6d9620192117d36cfc8ce714407cfe15ee0a355674677e11",
    "frontend/src/charting/__fixtures__/study_parity.json":
        "790d11653b0056049d8207378cecf1bafce0de40a9e4d0a1ae38bf83518a518b",
    "frontend/src/utils/__fixtures__/indicator_parity.json":
        "670d39f2624d4b16062fb4cd9d7d0775e10701a76b32f7784ae1892b8e0496a2",
}

# Other existing data files under backend/tests/ and __fixtures__ folders: synthetic, hand-made cases. Pinned by sha256
# like the grandfathered ones, so a changed or new data file there is a reviewed change (update the hash here).
ALLOWED_TEST_DATA: Dict[str, str] = {
    "backend/tests/fixtures/zones/chart_bands_cases.json":
        "66a90beb9b4e75201b937005f46ad53e81494bdc19b5520458b25958eb7c3436",
    "frontend/src/charting/__fixtures__/chart_bands_cases.json":
        "66a90beb9b4e75201b937005f46ad53e81494bdc19b5520458b25958eb7c3436",
}

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", "coverage", ".pytest_cache",
              ".mypy_cache", ".ruff_cache", "playwright-report", "test-results", "storybook-static"}
_MAX_BYTES = 8 * 1024 * 1024
_BINARY_EXT = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp", ".ico", ".icns", ".tif", ".tiff", ".woff", ".woff2",
                         ".ttf", ".otf", ".eot", ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".parquet", ".feather", ".arrow",
                         ".pkl", ".npy", ".npz", ".h5", ".mp3", ".mp4", ".webm", ".wav", ".ogg", ".sqlite", ".db", ".xlsx", ".xls",
                         ".docx", ".pptx", ".jar", ".so", ".dylib", ".dll", ".exe", ".bin", ".pyc", ".wasm", ".jks", ".keystore",
                         ".p12", ".pfx", ".pepk"})

# --- content patterns -------------------------------------------------------------------------------------------------
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d]|\.\d)")
# A version after a known browser token ("Chrome/120.0.0.0" in a user agent) is not an address; any other "x/1.2.3.4" is.
_BROWSER_VERSION = re.compile(r"(?<![A-Za-z])(?:Mozilla|AppleWebKit|Gecko|Chrome|Chromium|HeadlessChrome|CriOS|Firefox|FxiOS"
                              r"|Safari|Version|Edg|EdgA|EdgiOS|Edge|OPR|Opera|Trident|MSIE|SamsungBrowser|YaBrowser|Mobile)/$")
# Four numbers joined by one of . - _ or a space ("ip-a-b-c-d", "a_b_c_d"): checked against the production hash only.
_IP_ANY_SEPARATOR = re.compile(r"(?<!\d)(?=(\d{1,3})[-_. ](\d{1,3})[-_. ](\d{1,3})[-_. ](\d{1,3})(?!\d))")
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+])[A-Za-z0-9_][A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![A-Za-z0-9-])")
_TELEGRAM_TOKEN = re.compile(r"(?<![\d])\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])")
_CHAT_ID = re.compile(r"(?i)(?:chat_?id|telegram_?(?:user_?|chat_?)?id)[\"']?\s*[:=]\s*[\"']?-?\d{7,}")
_SUPERGROUP_ID = re.compile(r"(?<![\d.])-100\d{10}(?!\d)")
# A private key header, written so this file does not hold one itself.
_PRIVATE_KEY = re.compile("-{5}BEGIN (?:(?:RSA|DSA|EC|OPENSSH|ENCRYPTED|PGP) )?PRIVATE KEY(?: BLOCK)?-{5}")

# --- path patterns ----------------------------------------------------------------------------------------------------
_SECRET_EXT = frozenset({".pem", ".key", ".ppk", ".jks", ".keystore", ".p12", ".pfx", ".pepk"})
_SECRET_NAMES = frozenset({"google-services.json", "googleservice-info.plist", "keystore.properties", "signing.properties"})
_SECRET_JSON = re.compile(r"(?:service[_-]?account|credentials).*\.json$", re.IGNORECASE)
# .npmrc / .pypirc: a token or password with a literal value (a ${VARIABLE} reference is fine).
_NPMRC_TOKEN = re.compile(r"(?:_authToken|_auth|_password)\s*=\s*(?!\$\{)\S")
_PYPIRC_TOKEN = re.compile(r"^\s*password\s*[=:]\s*(?!\$\{|%\()\S", re.IGNORECASE)
# The KB, the Method Pack and the Chart Reader method stay out of this public repository (open question PR-1).
_METHOD_PATH = re.compile(r"(?:^|/)docs/knowledge/|method[_-]?pack|(?:^|/)knowledge[_-]?base[^/]*$|method[_-]?notes"
                          r"|(?:^|/)chart[_-]?reader[_-]?method[^/]*$", re.IGNORECASE)
_ENV_TEMPLATES = (".example", ".sample", ".template")
_ENV_VALUE = re.compile(r"^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*(?P<v>.*)$")
_COMPRESSED = (".gz", ".zip", ".bz2", ".xz", ".7z", ".tgz")
_DATA_EXT = frozenset({".csv", ".tsv", ".json", ".jsonl", ".parquet", ".xlsx", ".xls", ".feather", ".pkl", ".npy", ".npz"})
_TABULAR_EXT = frozenset({".csv", ".tsv", ".parquet", ".xlsx", ".xls", ".feather", ".pkl", ".npy", ".npz"})
_IMAGE_EXT = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"})
_FIXTURE_DIR = re.compile(r"(?:^|/)(?:fixtures|__fixtures__)/")
# Any data or compressed file here must be listed (GRANDFATHERED_FIXTURES or ALLOWED_TEST_DATA).
_TEST_DATA_DIR = re.compile(r"^backend/tests/|(?:^|/)__fixtures__/")
_TEST_DATA_EXT = _DATA_EXT | frozenset({".arrow", ".h5", ".db", ".sqlite"})
_GOLDEN = re.compile(r"golden|parity", re.IGNORECASE)
_REAL_DATA_NAME = re.compile(r"journal|trade_?book|trade_?log|trade_?history|contract_?note|p_?n_?l_?report|chart_?export"
                             r"|tradingview|ledger", re.IGNORECASE)


def _git_files(root: Path) -> Optional[List[str]]:
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, check=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    if not top or Path(top).resolve() != root.resolve():
        return None                         # a sub-directory of another repository: walk it instead
    return sorted({p for p in out.decode("utf-8", "surrogateescape").split("\0") if p})


def _walk(root: Path) -> List[str]:
    found: List[str] = []
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x not in _SKIP_DIRS]
        for name in names:
            found.append(Path(d, name).relative_to(root).as_posix())
    return sorted(found)


def files(root: Path) -> List[str]:
    return _git_files(root) or _walk(root)


def _suffixes(rel: str) -> Tuple[str, str]:
    """(the data suffix, the outer suffix): "a/b.json.gz" -> (".json", ".gz"); "a/b.csv" -> (".csv", ".csv")."""
    path = PurePosixPath(rel.lower())
    outer = path.suffix
    if outer in _COMPRESSED:
        return PurePosixPath(path.stem).suffix or outer, outer
    return outer, outer


def is_production_ip(ip: str) -> bool:
    return hashlib.sha256(ip.encode("ascii")).hexdigest() in PRODUCTION_IP_SHA256


def production_ip_count(line: str) -> int:
    """How many times the production IP appears in the line, dotted or with - _ or space between the numbers."""
    return sum(1 for m in _IP_ANY_SEPARATOR.finditer(line) if is_production_ip(".".join(m.groups())))


def content_sha256(data: bytes) -> str:
    """The sha256 the fixture lists pin: of the file's bytes, CRLF read as LF (a Windows checkout gives the same)."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def is_flagged_ip(ip: str) -> bool:
    """True for a public IPv4 address that is neither a documentation address nor in ALLOWED_IPS."""
    try:
        addr = ipaddress.IPv4Address(ip)
    except ValueError:
        return False                        # "300.1.1.1" and the like: a version, not an address
    if ip in ALLOWED_IPS or any(addr in net for net in DOC_NETWORKS):
        return False
    return addr.is_global or addr.is_multicast


def is_allowed_email(address: str) -> bool:
    address = address.lower()
    return address in ALLOWED_EMAILS or bool(_EXAMPLE_DOMAIN.search(address.rsplit("@", 1)[1]))


def _redact_email(address: str) -> str:
    local, domain = address.rsplit("@", 1)
    return f"{local[:1]}***@{domain}"


def path_problems(rel: str) -> List[str]:
    """Findings from the file's path alone (secret file types, the KB / method, fixtures / real-data files). A listed
    fixture passes here; its content is checked against its pinned sha256 by `listed_data_problems`."""
    problems: List[str] = []
    name = PurePosixPath(rel).name.lower()
    data_ext, outer = _suffixes(rel)
    if outer in _SECRET_EXT or name in _SECRET_NAMES or _SECRET_JSON.search(name):
        problems.append(f"{rel}: a secret file type ({name}) - keys, keystores and service files never go into git")
    if _METHOD_PATH.search(rel):
        problems.append(f"{rel}: the KB / Method Pack / Chart Reader method - not in this public repository "
                        f"(docs/OPEN_QUESTIONS.md PR-1)")
    if rel in GRANDFATHERED_FIXTURES or rel in ALLOWED_TEST_DATA:
        return problems
    is_data = data_ext in _DATA_EXT or outer in _COMPRESSED
    reason = None
    if _TEST_DATA_DIR.search(rel) and (data_ext in _TEST_DATA_EXT or outer in _COMPRESSED):
        reason = "a new data file in the tests (not in ALLOWED_TEST_DATA)"
    elif is_data and _GOLDEN.search(rel):
        reason = "a golden / parity fixture"
    elif _FIXTURE_DIR.search(rel) and (data_ext in _TABULAR_EXT or outer in _COMPRESSED or outer in _IMAGE_EXT):
        reason = "a data, compressed or image file in a fixtures folder"
    elif (is_data or outer in _IMAGE_EXT) and _REAL_DATA_NAME.search(name):
        reason = "a trade journal / tradebook / chart export"
    if reason:
        problems.append(f"{rel}: {reason} - real-data and golden fixtures go to the private fixtures store "
                        f"(docs/PUBLIC_REPO_POLICY.md); a public test uses small synthetic data")
    return problems


def listed_data_problems(rel: str, data: bytes) -> List[str]:
    """A grandfathered or allowed data file whose content no longer matches its pinned sha256."""
    pinned = GRANDFATHERED_FIXTURES.get(rel) or ALLOWED_TEST_DATA.get(rel)
    if pinned is None or content_sha256(data) == pinned:
        return []
    if rel in GRANDFATHERED_FIXTURES:
        return [f"{rel}: a grandfathered fixture with new content - it may not change; replace it with synthetic data "
                f"under a new name in ALLOWED_TEST_DATA, or move it to the private store (docs/OPEN_QUESTIONS.md PR-2)"]
    return [f"{rel}: test data with new content - check it holds no real data, then update its sha256 in ALLOWED_TEST_DATA"]


def config_token_problems(rel: str, text: str) -> List[str]:
    """An .npmrc / .pypirc that holds a token or password."""
    name = PurePosixPath(rel).name.lower()
    pattern = _NPMRC_TOKEN if name == ".npmrc" else _PYPIRC_TOKEN if name == ".pypirc" else None
    if pattern is None:
        return []
    for n, line in enumerate(text.splitlines(), 1):
        if pattern.search(line):
            return [f"{rel}:{n}: {name} with a token or password (redacted) - use a ${{VARIABLE}} from the secret store"]
    return []


def _is_env_file(name: str) -> bool:
    return (name == ".env" or name.startswith(".env.") or name.endswith(".env")) and not name.endswith(_ENV_TEMPLATES)


def env_problems(rel: str, text: str) -> List[str]:
    """An .env file (not a template) that holds a value."""
    for n, line in enumerate(text.splitlines(), 1):
        m = _ENV_VALUE.match(line)
        if m and m.group("v").split("#", 1)[0].strip().strip("\"'"):
            return [f"{rel}:{n}: an .env file with values - real settings live only on the server or in the secret store"]
    return []


def text_problems(rel: str, text: str) -> List[str]:
    """Findings in the file's text. Values are never echoed in full."""
    problems: List[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        for _ in range(production_ip_count(line)):
            problems.append(f"{rel}:{n}: the production server's IP address - write <server-ip> (docs) or a "
                            f"documentation address such as 203.0.113.10 (tests)")
        for m in _IPV4.finditer(line):
            ip = m.group(0)
            if is_production_ip(ip):
                continue                    # reported above
            if not _BROWSER_VERSION.search(line[:m.start()]) and is_flagged_ip(ip):
                problems.append(f"{rel}:{n}: public IPv4 address {ip} - use a documentation address (192.0.2.x, "
                                f"198.51.100.x, 203.0.113.x) or a placeholder")
        for m in _EMAIL.finditer(line):
            if rel not in EMAIL_EXEMPT_FILES and not is_allowed_email(m.group(0)):
                problems.append(f"{rel}:{n}: e-mail address {_redact_email(m.group(0))} - use an example.com address")
        if _TELEGRAM_TOKEN.search(line):
            problems.append(f"{rel}:{n}: a Telegram bot token (redacted) - rotate it with @BotFather if it was real")
        if _CHAT_ID.search(line) or _SUPERGROUP_ID.search(line):
            problems.append(f"{rel}:{n}: a Telegram chat id (redacted) - use a short fake id such as 987654")
        if _PRIVATE_KEY.search(line):
            problems.append(f"{rel}:{n}: a private key header - a private key never goes into git")
    return problems


def _read(path: Path, rel: str, notices: List[str]) -> Optional[str]:
    try:
        if not path.is_file() or PurePosixPath(rel.lower()).suffix in _BINARY_EXT:
            return None
        if path.stat().st_size > _MAX_BYTES:
            notices.append(f"{rel}: over {_MAX_BYTES // (1024 * 1024)} MB - checked by path only")
            return None
        return path.read_bytes().decode("utf-8", "replace")
    except OSError:
        return None


def scan(root: Path) -> Tuple[List[str], List[str]]:
    """(findings, notices). Findings fail the check; notices do not."""
    root = root.resolve()
    problems: List[str] = []
    notices: List[str] = []
    for rel in files(root):
        if not (root / rel).is_file():
            continue                        # deleted in the work tree, not yet committed
        problems.extend(path_problems(rel))
        if rel in GRANDFATHERED_FIXTURES or rel in ALLOWED_TEST_DATA:
            try:
                problems.extend(listed_data_problems(rel, (root / rel).read_bytes()))
            except OSError:
                pass
        text = _read(root / rel, rel, notices)
        if text is None:
            continue
        if _is_env_file(PurePosixPath(rel).name.lower()):
            problems.extend(env_problems(rel, text))
        problems.extend(config_token_problems(rel, text))
        problems.extend(text_problems(rel, text))
    return problems, notices


def check(root: Path) -> List[str]:
    """Every finding as 'path[:line]: text'. Empty = clean."""
    return scan(root)[0]


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent), help="the repository root (default: this script's repository)")
    args = parser.parse_args(list(argv) if argv is not None else None)
    problems, notices = scan(Path(args.root))
    for note in notices:
        print(f"check_public_repo: notice - {note}", file=sys.stderr)
    if problems:
        print(f"Public repository guard: {len(problems)} finding(s) (scripts/check_public_repo.py, docs/PUBLIC_REPO_POLICY.md). "
              f"This repository is public: everything committed is public.", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    print(f"check_public_repo: OK - no server IP, private e-mail, Telegram id, key or new real-data fixture "
          f"({len(GRANDFATHERED_FIXTURES)} grandfathered fixtures pending the owner's decision, "
          f"{len(ALLOWED_TEST_DATA)} pinned test data files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
