"""The owner's ruling (Oct 2026): ATP is English only, except the Copilot strategy interview. `scripts/check_english_only.py`
(CI, lint job) fails on Devanagari anywhere in the repository outside its explicit allow-list. These tests run it on the
real repository (clean) and on small trees: a new Devanagari string outside the allow-list fails, the allow-listed
interview files pass. Devanagari is written here with \\u escapes (which the check does not flag)."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_english_only.py"
MR = "\u0939\u0947 \u092e\u0930\u093e\u0920\u0940 \u0906\u0939\u0947"            # "this is Marathi"
HI = "\u092f\u0939 \u0939\u093f\u0902\u0926\u0940 \u0939\u0948"                  # "this is Hindi"


def _load():
    spec = importlib.util.spec_from_file_location("check_english_only", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_english_only = _load()


def _tree(tmp_path: Path, files: dict) -> Path:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


NAMESPACE = (
    "/** the interview namespace */\n"
    "export const INTERVIEW_MR = {\n"
    f'  start: "{MR}",\n'
    "  // a comment\n"
    f'  back: "{MR}",\n'
    "} as const;\n"
    "export const INTERVIEW_HI = {\n"
    f'  start: "{HI}",\n'
    "} as const;\n"
)
ALLOWED = {
    "backend/app/ai/interview.py": f'Q = ("Which language?", "{MR}")\n',
    "backend/app/ai/advisor.py": f'WHY = "{MR}"\n',
    "backend/tests/test_phase_ap_interview.py": f'PROMPT = "{MR}"\n',
    "frontend/e2e/fixtures/interview_plan.json": f'{{"title_mr": "{MR}"}}\n',
    "frontend/src/i18n/interviewSecondary.ts": NAMESPACE,
    "frontend/src/copilot/i18n.ts": 'import { INTERVIEW_MR } from "../i18n/interviewSecondary";\n',
    "backend/app/ai/copilot.py": 'PROMPT = "Answer in English only"\n',
    "docs/README.md": "# Docs\nEnglish only.\n",
}


def test_the_repository_is_english_only():
    assert check_english_only.check(REPO) == []
    out = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-3000:]
    assert "OK" in out.stdout


def test_the_allow_listed_interview_files_pass(tmp_path):
    root = _tree(tmp_path, ALLOWED)
    assert check_english_only.check(root) == []
    assert check_english_only.main(["--root", str(root)]) == 0


@pytest.mark.parametrize("rel, text", [
    ("backend/app/ai/copilot.py", f'PROMPT = "{MR}"\n'),                                   # a server text outside the interview
    ("backend/app/notifications/templates/eod.txt", f"EOD: {HI}\n"),                      # a notification template
    ("frontend/src/components/Card.tsx", f"export const t = <p>{MR}</p>;\n"),              # a UI string
    ("frontend/src/copilot/locales/en.ts", f'export const X = {{ a: "{MR}" }};\n'),         # an interface locale
    ("docs/GUIDE.md", f"# Guide\n{MR}\n"),                                                # a doc
    ("README.md", f"Read in Marathi: {MR}\n"),
    ("backend/tests/test_new_feature.py", f'TEXT = "{HI}"\n'),                              # a test not on the allow-list
    ("backend/app/ai/data/terms.json", f'{{"mr": ["{MR}"]}}\n'),                              # data
    ("frontend/src/x.ts", "export const v = \"\ua8f2\";\n"),                                # Devanagari Extended (U+A8E0-U+A8FF)
])
def test_a_new_devanagari_string_outside_the_allow_list_fails(tmp_path, rel, text):
    root = _tree(tmp_path, {**ALLOWED, rel: text})
    problems = check_english_only.check(root)
    assert problems and all(p.startswith(f"{rel}:") for p in problems), problems
    assert check_english_only.main(["--root", str(root)]) == 1


def test_the_namespace_file_allows_devanagari_only_as_a_key_value_of_the_interview_objects(tmp_path):
    outside = NAMESPACE + f'export const OTHER = {{\n  x: "{MR}",\n}} as const;\n'
    problems = check_english_only.check(_tree(tmp_path / "a", {**ALLOWED, "frontend/src/i18n/interviewSecondary.ts": outside}))
    assert len(problems) == 1 and "outside a key of INTERVIEW_MR / INTERVIEW_HI" in problems[0]
    comment = NAMESPACE.replace("/** the interview namespace */", f"/** {MR} */")
    problems = check_english_only.check(_tree(tmp_path / "b", {**ALLOWED, "frontend/src/i18n/interviewSecondary.ts": comment}))
    assert len(problems) == 1 and ":1:" in problems[0]


def test_only_the_interview_screens_import_the_namespace(tmp_path):
    importer = 'import { INTERVIEW_MR } from "../i18n/interviewSecondary";\nexport default INTERVIEW_MR;\n'
    problems = check_english_only.check(_tree(tmp_path / "a", {**ALLOWED, "frontend/src/pages/SettingsPage.tsx": importer}))
    assert problems == ["frontend/src/pages/SettingsPage.tsx: imports the interview namespace (frontend/src/i18n/interviewSecondary.ts); "
                        "only frontend/src/copilot/i18n.ts, frontend/src/stories/Copilot.stories.tsx may"]
    assert check_english_only.check(_tree(tmp_path / "b", {**ALLOWED, "frontend/src/copilot/i18n.test.ts": importer})) == []   # its tests may


def test_escaped_devanagari_and_binary_files_are_not_findings(tmp_path):
    root = _tree(tmp_path, {**ALLOWED, "backend/tests/test_x.py": 'TEXT = "\\u0939\\u0947"\n'})
    (root / "frontend/public").mkdir(parents=True)
    (root / "frontend/public/icon.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe" + MR.encode("utf-16"))
    assert check_english_only.check(root) == []


def test_the_allow_list_is_narrow():
    assert set(check_english_only.ALLOWED_FILES) == {
        "backend/app/ai/interview.py", "backend/app/ai/advisor.py",
        "backend/app/ai/indic_safety.py",                 # M72 PR-1a owner order: the Copilot's MR / HI safety filter phrases
        "backend/tests/test_phase_ap_interview.py", "backend/tests/test_phase_aq_advisor.py",
        "frontend/e2e/fixtures/interview_plan.json"}
    assert check_english_only.NAMESPACE_FILE == "frontend/src/i18n/interviewSecondary.ts"
    assert check_english_only.NAMESPACE_OBJECTS == ("INTERVIEW_MR", "INTERVIEW_HI")


BS = "\\"                                             # one backslash: escapes below are built, never typed
ESC = BS + "u0939" + BS + "u0947"                    # Devanagari written as \u escapes


@pytest.mark.parametrize("rel, text", [
    ("backend/app/ai/copilot.py", f'PROMPT = "{ESC}"\n'),                                  # \u escapes in app code
    ("frontend/src/components/Card.tsx", f'export const t = "{BS}U00000939";\n'),                 # \U escapes
    ("backend/app/templates/eod.html", "<p>&#x939;&#x947;</p>\n"),                       # HTML entities (hex)
    ("docs/GUIDE.md", "Read: &#2361;&#2375;\n"),                                         # HTML entities (decimal)
    ("frontend/src/x.ts", "export const v = '&#xA8F2;';\n"),                             # the Extended range
])
def test_devanagari_written_as_escapes_outside_the_tests_fails(tmp_path, rel, text):
    problems = check_english_only.check(_tree(tmp_path, {**ALLOWED, rel: text}))
    assert len(problems) == 1 and problems[0].startswith(f"{rel}:1: Devanagari written as an escape"), problems


@pytest.mark.parametrize("rel", ["backend/tests/test_new.py", "backend/evals/sets/qa.jsonl", "frontend/e2e/fixtures/x.json",
                                 "frontend/src/screener/x.test.tsx", "backend/app/ai/interview.py"])
def test_escapes_stand_in_tests_fixtures_and_the_allow_list(tmp_path, rel):
    assert check_english_only.check(_tree(tmp_path, {**ALLOWED, rel: f'TEXT = "{ESC}"\n'})) == []


def test_a_range_bound_that_detects_devanagari_is_not_text(tmp_path):
    pattern = f'DEVANAGARI = re.compile(r"[{BS}u0900-{BS}u097F{BS}ua8e0-{BS}ua8ff]")\nJSON = "[{BS}{BS}u0900-{BS}{BS}u097F]"\n'
    assert check_english_only.check(_tree(tmp_path, {**ALLOWED, "backend/app/screener/legends.py": pattern})) == []


def test_text_files_must_be_utf8(tmp_path):
    root = _tree(tmp_path, ALLOWED)
    (root / "docs/latin1.txt").write_bytes("caf\u00e9 \u00e0 la carte\n".encode("latin-1"))
    (root / "docs/utf16.md").write_text("hello " + MR, encoding="utf-16")                     # with a BOM
    (root / "backend/app/hidden.py").write_bytes(("X = '" + MR + "'\n").encode("utf-16-le"))      # no BOM: NUL bytes
    (root / "frontend/public/logo.png").parent.mkdir(parents=True, exist_ok=True)
    (root / "frontend/public/logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff")                 # a known binary type: not read
    problems = sorted(check_english_only.check(root))
    assert problems == ["backend/app/hidden.py: a UTF-16 (or NUL-byte) text file - write text files as UTF-8",
                        "docs/latin1.txt: not valid UTF-8 text (byte 3) - write text files as UTF-8, or use a known binary type",
                        "docs/utf16.md: a UTF-16 (or NUL-byte) text file - write text files as UTF-8"]


@pytest.mark.parametrize("line", [
    'import { INTERVIEW_MR } from "../i18n/interviewSecondary.ts";',
    'import "../i18n/interviewSecondary";',
    'const m = await import("../i18n/interviewSecondary");',
    'const { INTERVIEW_MR } = require("../i18n/interviewSecondary.js");',
    "import { INTERVIEW_MR } from './interviewSecondary';",
])
def test_every_import_form_of_the_namespace_is_checked(tmp_path, line):
    problems = check_english_only.check(_tree(tmp_path, {**ALLOWED, "frontend/src/pages/X.tsx": line + "\n"}))
    assert len(problems) == 1 and problems[0].startswith("frontend/src/pages/X.tsx: imports the interview namespace"), problems


def test_a_file_over_8_mb_is_skipped_with_a_notice(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(check_english_only, "_MAX_BYTES", 1024 * 1024 // 2)
    root = _tree(tmp_path, {**ALLOWED, "docs/huge.md": "English only. " * 40_000})
    problems, notices = check_english_only.scan(root)
    assert problems == [] and notices == ["docs/huge.md: over 0 MB - not checked"]
    assert check_english_only.main(["--root", str(root)]) == 0
    assert "notice - docs/huge.md" in capsys.readouterr().err
