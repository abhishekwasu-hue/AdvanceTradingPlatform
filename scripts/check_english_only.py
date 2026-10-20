#!/usr/bin/env python3
"""English only: no Devanagari anywhere in ATP except the strategy interview (the owner's ruling, Oct 2026).

ATP's UI, Copilot / AI replies, Telegram / email / push texts, reports and docs are English. The one exception is the
Copilot STRATEGY INTERVIEW, which is trilingual (English + Marathi + Hindi). This check reads every text file of the
repository - backend, frontend, templates, notification texts, docs, scripts, configs - and fails on any Devanagari
character (U+0900-U+097F, U+A8E0-U+A8FF) outside the allow-list below.

The allow-list is explicit and narrow: the interview's own files and named test fixtures of the interview, nothing else.
A test that needs non-English text (to prove it is withheld or answered in English) writes it with \\u escapes, which
this check does not flag. To add an entry, the owner's approval is needed (docs/journal/PLAN.md OD-2).

    python3 scripts/check_english_only.py            # from anywhere; exit 0 = clean, 1 = findings
    python3 scripts/check_english_only.py --root DIR # check another tree (the tests use this)

Files: the tracked and the new (not ignored) files when the root is a git work tree, else every file under the root.
Known binary types (images, fonts, archives...) are skipped; any other file must be UTF-8 text - a file in another
encoding, UTF-16 or with NUL bytes is a finding; a file over 8 MB is skipped with a notice. Devanagari written as
\\u escapes or HTML entities counts too, outside the tests, the e2e / eval fixtures and the allow-listed files (a range
bound in a pattern that detects Devanagari, "[\\u0900-\\u097F]", is not text). No exclusions by directory.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

DEVANAGARI = re.compile("[\u0900-\u097f\ua8e0-\ua8ff]")

# Whole files that may hold Devanagari, with the reason. Paths are relative to the repository root, "/" separated.
ALLOWED_FILES: Dict[str, str] = {
    # the strategy interview - server texts
    "backend/app/ai/interview.py": "the strategy interview: questions, options, tips and plan texts in English + Marathi",
    "backend/app/ai/advisor.py": "the strategy interview's options round and its 'not this one' feedback texts",
    # test fixtures of the interview
    "backend/tests/test_phase_ap_interview.py": "interview test fixture (Marathi prompts and plan lines)",
    "backend/tests/test_phase_aq_advisor.py": "interview test fixture (the options round in Marathi)",
    "frontend/e2e/fixtures/interview_plan.json": "interview e2e fixture (a plan with its Marathi lines)",
}

# The interview namespace of the frontend: Devanagari only as the value of a key of these objects.
NAMESPACE_FILE = "frontend/src/i18n/interviewSecondary.ts"
NAMESPACE_OBJECTS = ("INTERVIEW_MR", "INTERVIEW_HI")      # INTERVIEW_HI: the Hindi bundle of the interview
# The only frontend files that may import the namespace: the Copilot's i18n instance (the `interview` namespace, used by
# the Idea Builder - the interview screen) and its catalogue story.
NAMESPACE_USERS = frozenset({"frontend/src/copilot/i18n.ts", "frontend/src/stories/Copilot.stories.tsx"})
# import ... from "../i18n/interviewSecondary(.ts)", import "...", import("..."), require("...") - as the old .mjs did.
_NAMESPACE_IMPORT = re.compile(r"""(?:\bfrom|\bimport|\brequire)\s*\(?\s*["'][^"']*interviewSecondary(?:\.[cm]?[jt]sx?)?["']""")
_TEST_FILE = re.compile(r"\.test\.tsx?$")              # the namespace's own tests may import it
_KEY_VALUE = re.compile(r"""^\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*"[^"]*",?\s*(//.*)?$""")

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", "coverage", ".pytest_cache",
              ".mypy_cache", ".ruff_cache", "playwright-report", "test-results", "storybook-static"}
_MAX_BYTES = 8 * 1024 * 1024
# Known binary types: not text, not read. Any other file must be UTF-8 text (no UTF-16, no NUL bytes) - a file in another
# encoding would hide Devanagari from this check.
_BINARY_EXT = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp", ".ico", ".icns", ".tif", ".tiff", ".woff", ".woff2",
                         ".ttf", ".otf", ".eot", ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".parquet", ".feather", ".arrow",
                         ".pkl", ".npy", ".npz", ".h5", ".mp3", ".mp4", ".webm", ".wav", ".ogg", ".sqlite", ".db", ".xlsx", ".docx",
                         ".pptx", ".jar", ".so", ".dylib", ".dll", ".exe", ".bin", ".pyc", ".wasm"})
# Devanagari written as escapes: \uXXXX / \U0000XXXX, HTML entities &#xXXX; and &#NNNN;. A range bound such as
# "[\u0900-\u097F]" (a pattern that detects Devanagari) is not text and is not counted.
_ESCAPED = re.compile(r"\\u(?:09[0-7][0-9a-fA-F]|[aA]8[eEfF][0-9a-fA-F])|\\U0000(?:09[0-7]|[aA]8[eEfF])[0-9a-fA-F]"
                      r"|&#[xX]0*(?:9[0-7][0-9a-fA-F]|[aA]8[eEfF][0-9a-fA-F]);"
                      r"|&#0*(?:23(?:0[4-9]|[1-9][0-9])|24(?:[0-2][0-9]|3[01])|432(?:3[2-9]|[45][0-9]|6[0-3]));")
_RANGE = re.compile(r"\\+[uU](?:0000)?[0-9a-fA-F]{4}\s*-\s*\\+[uU](?:0000)?[0-9a-fA-F]{4}")
# Where escaped Devanagari may stand (tests write non-English input that way): the backend tests and evals' fixtures,
# the e2e fixtures, the frontend unit tests and the allow-listed files.
_ESCAPES_ALLOWED = re.compile(r"^(?:backend/tests/|backend/evals/sets/|frontend/e2e/fixtures/)|^frontend/.*\.test\.tsx?$")


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


def _read(path: Path, rel: str, problems: List[str], notices: List[str]) -> Optional[str]:
    """The file's text, or None when there is none to check: a known binary type, or a file over 8 MB (a notice). A file
    that is not UTF-8 text - another encoding, UTF-16, NUL bytes - is a finding: it could hide Devanagari."""
    try:
        if not path.is_file():
            return None
        if Path(rel).suffix.lower() in _BINARY_EXT:
            return None
        if path.stat().st_size > _MAX_BYTES:
            notices.append(f"{rel}: over {_MAX_BYTES // (1024 * 1024)} MB - not checked")
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in data:
        problems.append(f"{rel}: a UTF-16 (or NUL-byte) text file - write text files as UTF-8")
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        problems.append(f"{rel}: not valid UTF-8 text (byte {exc.start}) - write text files as UTF-8, or use a known binary type")
        return None


def _namespace_problems(rel: str, text: str) -> List[str]:
    """Inside the namespace file: Devanagari only as the value of a key of an allowed object."""
    problems: List[str] = []
    inside: Optional[str] = None
    for n, line in enumerate(text.splitlines(), 1):
        start = re.match(r"\s*export\s+const\s+(\w+)\s*=\s*\{", line)
        if start:
            inside = start.group(1)
            continue
        if inside and re.match(r"\s*\}\s*(as\s+const)?\s*;?\s*$", line):
            inside = None
            continue
        if not DEVANAGARI.search(line):
            continue
        if inside not in NAMESPACE_OBJECTS or not _KEY_VALUE.match(line):
            problems.append(f"{rel}:{n}: Devanagari outside a key of {' / '.join(NAMESPACE_OBJECTS)}: {line.strip()[:100]}")
    return problems


def scan(root: Path) -> Tuple[List[str], List[str]]:
    """(findings, notices). Findings fail the check; notices (files over 8 MB, not read) do not."""
    root = root.resolve()
    problems: List[str] = []
    notices: List[str] = []
    for rel in files(root):
        text = _read(root / rel, rel, problems, notices)
        if text is None:
            continue
        if (rel.startswith("frontend/") and rel not in NAMESPACE_USERS and rel != NAMESPACE_FILE and not _TEST_FILE.search(rel)
                and _NAMESPACE_IMPORT.search(text)):
            problems.append(f"{rel}: imports the interview namespace ({NAMESPACE_FILE}); only {', '.join(sorted(NAMESPACE_USERS))} may")
        if rel in ALLOWED_FILES:
            continue
        if not _ESCAPES_ALLOWED.search(rel) and rel != NAMESPACE_FILE:
            for n, line in enumerate(text.splitlines(), 1):
                if _ESCAPED.search(_RANGE.sub(" ", line)):
                    problems.append(f"{rel}:{n}: Devanagari written as an escape or an HTML entity: {line.strip()[:110]}")
        if not DEVANAGARI.search(text):
            continue
        if rel == NAMESPACE_FILE:
            problems.extend(_namespace_problems(rel, text))
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if DEVANAGARI.search(line):
                problems.append(f"{rel}:{n}: {line.strip()[:120]}")
    return problems, notices


def check(root: Path) -> List[str]:
    """Every finding as 'path:line: text'. Empty = English only."""
    return scan(root)[0]


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent), help="the repository root (default: this script's repository)")
    args = parser.parse_args(list(argv) if argv is not None else None)
    problems, notices = scan(Path(args.root))
    for note in notices:
        print(f"check_english_only: notice - {note}", file=sys.stderr)
    if problems:
        print(f"English only: {len(problems)} finding(s) - Devanagari outside the strategy interview's allow-list "
              f"(scripts/check_english_only.py). Translate the text to English; a test writes non-English text with \\u escapes.",
              file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    print(f"check_english_only: OK - Devanagari only in the interview allow-list ({len(ALLOWED_FILES)} files) and the "
          f"{' / '.join(NAMESPACE_OBJECTS)} keys of {NAMESPACE_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
