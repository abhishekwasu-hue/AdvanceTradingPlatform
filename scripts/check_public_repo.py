#!/usr/bin/env python3
"""Public repository guard: nothing private enters the tree of this public repository (docs/PUBLIC_REPO_POLICY.md).

The repository is public, so everything committed is public. This check reads every tracked and new (not ignored) file
and fails on:

  - the production server's IP address (matched by its sha256, so the address itself is not written here);
  - any other public IPv4 literal, outside the documentation ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24)
    and the explicit ALLOWED_IPS below (well-known addresses the tests use); private, loopback and other non-global
    addresses are not flagged, nor is a browser version such as "Chrome/120.0.0.0";
  - an e-mail address, except on example / test domains (example.com, *.example, *.test, *.invalid, localhost) and the
    explicit ALLOWED_EMAILS below (public vendor support addresses, placeholders already in tests and docs);
  - a Telegram bot token, or a chat-id-like literal (a chat id / Telegram id of 7 or more digits, a -100... supergroup
    id);
  - a private key header (PEM / OpenSSH / PGP);
  - a secret file type: *.pem, *.key, *.jks, *.keystore, *.p12, *.pfx, *.pepk, google-services.json,
    GoogleService-Info.plist, and an .env file holding values (.env.example / .sample / .template are allowed);
  - a golden / parity fixture or a real-data file (a data file whose path says golden or parity; a CSV, spreadsheet,
    compressed or image file under a fixtures folder; a trade journal, tradebook, contract note or chart export) that
    is not in the explicit GRANDFATHERED_FIXTURES list. Those existing files are pending a move to private storage or
    a synthetic replacement (owner decision, docs/OPEN_QUESTIONS.md PR-2); the list never grows.

Every list here is explicit and narrow; adding an entry is a reviewed change. Findings are printed without the
secret-like value (redacted), because the CI log of a public repository is public too.

    python3 scripts/check_public_repo.py            # from anywhere; exit 0 = clean, 1 = findings
    python3 scripts/check_public_repo.py --root DIR # check another tree (the tests use this)

Files: the tracked and the new (not ignored) files when the root is a git work tree, else every file under the root.
Known binary types are checked by path only; a file over 8 MB is checked by path only, with a notice.
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

# Existing golden / parity / real-data fixture files. Grandfathered only until the owner decides (docs/OPEN_QUESTIONS.md
# PR-2: move to a private store that CI reads with a secret, or regenerate from synthetic data). This list only shrinks.
GRANDFATHERED_FIXTURES: FrozenSet[str] = frozenset({
    "backend/tests/fixtures/charts/pa_layer_golden.json",
    "backend/tests/fixtures/charts/study_parity.json",
    "backend/tests/fixtures/daily_trend/golden.json.gz",
    "backend/tests/fixtures/level_gate/golden.json.gz",
    "backend/tests/fixtures/oi_regime/golden.json",
    "backend/tests/fixtures/options_builder/golden.json.gz",
    "backend/tests/fixtures/price_action/golden_trade_1e6c9ac_ch3b.json.gz",
    "backend/tests/fixtures/strike_breach/golden.json.gz",
    "backend/tests/fixtures/universe/EQUITY_L.csv",
    "backend/tests/fixtures/universe/SME_EQUITY_L.csv",
    "backend/tests/fixtures/universe/eq_etfseclist.csv",
    "backend/tests/fixtures/universe/fo_mktlots.csv",
    "backend/tests/fixtures/universe/fo_secban.csv",
    "backend/tests/fixtures/universe/ind_close_all_09102026.csv",
    "backend/tests/fixtures/universe/ind_testindexlist.csv",
    "backend/tests/fixtures/universe/symbolchange.csv",
    "backend/tests/fixtures/universe/symbolchange_noheader.csv",
    "backend/tests/fixtures/z10a/structure_base_c5f6a2e.json.gz",
    "backend/tests/fixtures/zones/bn_daily.csv",
    "backend/tests/fixtures/zones/golden_level_memory_eafc872.json.gz",
    "backend/tests/fixtures/zones/golden_trade_eafc872.json.gz",
    "backend/tests/fixtures/zones/market_5m.csv",
    "backend/tests/fixtures/zones/session_15m.csv",
    "backend/tests/fixtures/zones/walk_1m.csv",
    "backend/tests/golden/c2_default_models.json",
    "frontend/src/charting/__fixtures__/chart_types_golden.json.gz",
    "frontend/src/charting/__fixtures__/pa_layer_golden.json",
    "frontend/src/charting/__fixtures__/study_parity.json",
    "frontend/src/utils/__fixtures__/indicator_parity.json",
})

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
_PRODUCT_VERSION = re.compile(r"[A-Za-z]/$")          # "Chrome/120.0.0.0": a version after a product token
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+])[A-Za-z0-9_][A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![A-Za-z0-9-])")
_TELEGRAM_TOKEN = re.compile(r"(?<![\d])\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])")
_CHAT_ID = re.compile(r"(?i)(?:chat_?id|telegram_?(?:user_?|chat_?)?id)[\"']?\s*[:=]\s*[\"']?-?\d{7,}")
_SUPERGROUP_ID = re.compile(r"(?<![\d.])-100\d{10}(?!\d)")
# A private key header, written so this file does not hold one itself.
_PRIVATE_KEY = re.compile("-{5}BEGIN (?:(?:RSA|DSA|EC|OPENSSH|ENCRYPTED|PGP) )?PRIVATE KEY(?: BLOCK)?-{5}")

# --- path patterns ----------------------------------------------------------------------------------------------------
_SECRET_EXT = frozenset({".pem", ".key", ".jks", ".keystore", ".p12", ".pfx", ".pepk"})
_SECRET_NAMES = frozenset({"google-services.json", "googleservice-info.plist"})
_ENV_TEMPLATES = (".example", ".sample", ".template")
_ENV_VALUE = re.compile(r"^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*(?P<v>.*)$")
_COMPRESSED = (".gz", ".zip", ".bz2", ".xz", ".7z", ".tgz")
_DATA_EXT = frozenset({".csv", ".tsv", ".json", ".jsonl", ".parquet", ".xlsx", ".xls", ".feather", ".pkl", ".npy", ".npz"})
_TABULAR_EXT = frozenset({".csv", ".tsv", ".parquet", ".xlsx", ".xls", ".feather", ".pkl", ".npy", ".npz"})
_IMAGE_EXT = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"})
_FIXTURE_DIR = re.compile(r"(?:^|/)(?:fixtures|__fixtures__)/")
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
    """Findings from the file's path alone (secret file types, fixtures / real-data files)."""
    problems: List[str] = []
    name = PurePosixPath(rel).name.lower()
    data_ext, outer = _suffixes(rel)
    if outer in _SECRET_EXT or name in _SECRET_NAMES:
        problems.append(f"{rel}: a secret file type ({name}) - keys, keystores and service files never go into git")
    if rel in GRANDFATHERED_FIXTURES:
        return problems
    is_data = data_ext in _DATA_EXT or outer in _COMPRESSED
    reason = None
    if is_data and _GOLDEN.search(rel):
        reason = "a golden / parity fixture"
    elif _FIXTURE_DIR.search(rel) and (data_ext in _TABULAR_EXT or outer in _COMPRESSED or outer in _IMAGE_EXT):
        reason = "a data, compressed or image file in a fixtures folder"
    elif (is_data or outer in _IMAGE_EXT) and _REAL_DATA_NAME.search(name):
        reason = "a trade journal / tradebook / chart export"
    if reason:
        problems.append(f"{rel}: {reason} - real-data and golden fixtures go to the private fixtures store "
                        f"(docs/PUBLIC_REPO_POLICY.md); a public test uses small synthetic data")
    return problems


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
        for m in _IPV4.finditer(line):
            ip = m.group(0)
            if is_production_ip(ip):
                problems.append(f"{rel}:{n}: the production server's IP address - write <server-ip> (docs) or a "
                                f"documentation address such as 203.0.113.10 (tests)")
            elif not _PRODUCT_VERSION.search(line[:m.start()]) and is_flagged_ip(ip):
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
        text = _read(root / rel, rel, notices)
        if text is None:
            continue
        if _is_env_file(PurePosixPath(rel).name.lower()):
            problems.extend(env_problems(rel, text))
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
          f"({len(GRANDFATHERED_FIXTURES)} grandfathered fixtures pending the owner's decision)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
