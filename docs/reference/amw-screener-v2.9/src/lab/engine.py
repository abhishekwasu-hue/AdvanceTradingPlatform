"""Strategy Lab execution engine: one entry point for all authoring modes.

  validate(lang, source/ir, spec)  -> syntax + strategy-API validation (never "executes successfully")
  execute(lang, source/ir, spec, contexts, user) -> list[LabResult] (each validated against the result schema)
Visual and Pine are evaluated by the host IR interpreter (no user code). Python/Lua/SQL/JS/TS/R run in the process
sandbox. Contexts are always built by the host (`contract.build_context`) so timestamps, indexing and warm-up are
identical across languages.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import sys
import time
from functools import lru_cache
from pathlib import Path

from . import sandbox
from .contract import LabResult, StrategySpec, language_enabled, validate_result
from .ir import evaluate_ir, validate_strategy_ir
from .pine import translate_pine

PHASE2_MSG = "{lang}: this authoring mode is disabled by the operator (AMW_LAB_LANGUAGES / settings)."
WORKERS = Path(__file__).resolve().parent / "workers"
CODE_LANGS = {"python", "javascript", "typescript", "sql", "lua", "r"}
MAX_SOURCE = 100_000
R_CHUNK = 40


def _read(name: str) -> str:
    return (WORKERS / name).read_text(encoding="utf-8")


def _parse_worker(out: str) -> dict | None:
    for line in out.splitlines()[::-1]:
        if line.startswith("@@AMW@@"):
            try:
                return json.loads(line[7:])
            except json.JSONDecodeError:
                return None
    return None


@lru_cache(maxsize=1)
def ts_path() -> str | None:
    import os
    if os.environ.get("AMW_TS_PATH") and (Path(os.environ["AMW_TS_PATH"]) / "package.json").exists():
        return str(Path(os.environ["AMW_TS_PATH"]).resolve())
    node = shutil.which("node")
    if not node:
        return None
    appdata = os.environ.get("APPDATA")
    win_global = (Path(appdata) / "npm" / "node_modules" / "typescript") if appdata else Path("/nonexistent")
    for cand in (win_global,Path(node).resolve().parent.parent / "lib" / "node_modules" / "typescript",
                 Path(node).resolve().parent / "node_modules" / "typescript"):
        if (cand / "package.json").exists():
            return str(cand.resolve())
    return None


# ------------------------------------------------------------------ static checks (defence in depth; NOT the boundary)
R_FORBIDDEN = [r"\bsystem2?\s*\(", r"\bshell\s*\(", r"\bpipe\s*\(", r"\bfile\s*\(", r"\burl\s*\(", r"download\.file", r"\bsocket",
               r"Sys\.(setenv|getenv|setlocale)", r"\.Internal", r"\.Call", r"\.External", r"\.Primitive", r"dyn\.load",
               r"\beval\s*\(", r"\bparse\s*\(", r"\bsource\s*\(", r"sys\.source", r"readRDS|saveRDS|load\s*\(|save\s*\(",
               r"\bsetwd\s*\(", r"\bunlink\s*\(", r"file\.", r"\bget\s*\(", r"assign\s*\(", r"environment\s*\(",
               r"\binstall\.packages", r"Recall|do\.call|match\.fun", r":::"]
R_ALLOWED_LIBS = {"stats", "utils"}


def static_check(lang: str, source: str) -> list[str]:
    errs = []
    if len(source) > MAX_SOURCE:
        return [f"source too large (> {MAX_SOURCE} chars)"]
    if lang == "r":
        for pat in R_FORBIDDEN:
            m = re.search(pat, source)
            if m:
                errs.append(f"forbidden R construct: '{m.group(0)}'")
        for lib in re.findall(r"(?:library|require|requireNamespace)\s*\(\s*['\"]?([A-Za-z0-9.]+)", source):
            if lib not in R_ALLOWED_LIBS:
                errs.append(f"package '{lib}' is not on the allowlist {sorted(R_ALLOWED_LIBS)}")
        for ns in re.findall(r"([A-Za-z0-9.]+)::", source):
            if ns not in R_ALLOWED_LIBS:
                errs.append(f"namespace '{ns}::' is not allowed")
    if lang in ("javascript", "typescript"):
        for pat in (r"\brequire\s*\(", r"\bimport\s*\(", r"^\s*import\s", r"\bprocess\b", r"\bglobalThis\b", r"\bFunction\s*\(",
                    r"\beval\s*\("):
            m = re.search(pat, source, re.M)
            if m:
                errs.append(f"forbidden construct: '{m.group(0).strip()}'")
    if lang == "python":
        import ast
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return [f"line {e.lineno}: {e.msg}"]
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for m in mods:
                    if m.split(".")[0] not in ("math", "statistics"):
                        errs.append(f"line {node.lineno}: import '{m}' not allowed (math, statistics only)")
            if isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
                errs.append(f"line {node.lineno}: dunder attribute access '{node.attr}' not allowed")
            if isinstance(node, ast.Name) and node.id in ("eval", "exec", "compile", "open", "globals", "locals", "vars",
                                                          "getattr", "setattr", "delattr", "__import__", "breakpoint"):
                errs.append(f"line {node.lineno}: '{node.id}' not allowed")
        if not any(isinstance(n, ast.FunctionDef) and n.name == "evaluate" for n in tree.body):
            errs.append("define a top-level function evaluate(ctx)")
    if lang == "lua" and not re.search(r"function\s+evaluate\s*\(", source):
        errs.append("define function evaluate(ctx) ... end")
    if lang == "sql":
        from .sqlcheck import check_sql
        errs += check_sql(source)
    return errs


# ------------------------------------------------------------------ R literal serialisation of contexts
def _r_lit(o) -> str:
    if o is None:
        return "NA"
    if isinstance(o, bool):
        return "TRUE" if o else "FALSE"
    if isinstance(o, (int, float)):
        return "NA" if isinstance(o, float) and not math.isfinite(o) else repr(float(o))
    if isinstance(o, str):
        return json.dumps(o)
    if isinstance(o, list):
        if all(x is None or (isinstance(x, (int, float)) and not isinstance(x, bool)) for x in o):
            return "c(" + ",".join(_r_lit(x) if x is not None else "NA_real_" for x in o) + ")" if o else "numeric(0)"
        if all(isinstance(x, str) for x in o):
            return "c(" + ",".join(json.dumps(x) for x in o) + ")"
        return "list(" + ",".join(_r_lit(x) for x in o) + ")"
    if isinstance(o, dict):
        return "list(" + ",".join(f"`{k}`={_r_lit(v)}" for k, v in o.items()) + ")"
    return "NA"


# ------------------------------------------------------------------ public API
def validate(lang: str, source: str | None, ir: dict | None, spec: StrategySpec, user: str = "local") -> dict:
    """Return {"ok", "errors", "warnings", "stage", "ir"(for pine/visual)}. Stage tells WHAT was validated."""
    if not language_enabled(lang):
        return {"ok": False, "errors": [PHASE2_MSG.format(lang=lang)], "warnings": [], "stage": "release gate"}
    if lang == "visual":
        errs = validate_strategy_ir(ir or {}, {p.name for p in spec.params})
        return {"ok": not errs, "errors": errs, "warnings": [], "stage": "IR schema + rule-tree validation", "ir": ir}
    if lang == "pine":
        ir2, errs, warns = translate_pine(source or "")
        if ir2 is not None:
            errs += validate_strategy_ir(ir2, {p.name for p in spec.params} | set(ir2.get("_params", {})))
        return {"ok": not errs, "errors": errs, "warnings": warns, "stage": "Pine subset parse + translation to IR", "ir": ir2}
    errs = static_check(lang, source or "")
    if errs:
        return {"ok": False, "errors": errs, "warnings": [], "stage": "static checks"}
    try:
        w = _call(lang, "check", source or "", [], spec, user)
    except (PermissionError, sandbox.QuotaExceeded, RuntimeError) as e:
        return {"ok": False, "errors": [str(e)], "warnings": [], "stage": "runtime unavailable or failed"}
    return {"ok": w["check"]["ok"], "errors": w["check"]["errors"], "warnings": [],
            "stage": f"syntax + evaluate() definition check in sandboxed {w.get('runtime_version', lang)}"}


def execute(lang: str, source: str | None, ir: dict | None, spec: StrategySpec, contexts: list[dict],
            user: str = "local", cancel=None) -> list[LabResult]:
    if not contexts:
        return []
    if not language_enabled(lang):
        return [LabResult(False, None, c["meta"]["last_completed_bar"], [PHASE2_MSG.format(lang=lang)], runtime=lang) for c in contexts]
    t0 = time.perf_counter()
    if lang in ("visual", "pine"):
        if lang == "pine":
            ir, errs, _ = translate_pine(source or "")
            if ir is None:
                return [LabResult(False, None, None, errs, runtime="pine-subset") for _ in contexts]
        out = []
        for c in contexts:
            try:
                if ir.get("_params"):
                    c = {**c, "params": {**ir["_params"], **(c.get("params") or {})}}
                raw = evaluate_ir(ir, c)
                out.append(_finish(raw, None, "", c, f"AMW IR engine v1 ({lang})", t0))
            except Exception as e:  # noqa: BLE001
                out.append(LabResult(False, None, c["meta"]["last_completed_bar"], [f"{type(e).__name__}: {e}"], runtime=lang))
        return out
    errs = static_check(lang, source or "")
    if errs:
        return [LabResult(False, None, c["meta"]["last_completed_bar"], errs, runtime=lang) for c in contexts]
    try:
        w = _call(lang, "run", source or "", contexts, spec, user, cancel)
    except (PermissionError, sandbox.QuotaExceeded, RuntimeError) as e:
        return [LabResult(False, None, c["meta"]["last_completed_bar"], [str(e)], runtime=lang) for c in contexts]
    if not w["check"]["ok"]:
        return [LabResult(False, None, c["meta"]["last_completed_bar"], w["check"]["errors"], runtime=w.get("runtime_version", lang))
                for c in contexts]
    res = w["results"]
    if len(res) != len(contexts):
        return [LabResult(False, None, c["meta"]["last_completed_bar"], ["runtime returned an incomplete batch"],
                          runtime=w.get("runtime_version", lang)) for c in contexts]
    return [_finish(r["raw"], r["error"], r.get("logs", ""), c, w.get("runtime_version", lang), t0) for r, c in zip(res, contexts)]


def _finish(raw, error, logs, ctx, runtime, t0) -> LabResult:
    st = ctx["meta"]["last_completed_bar"]
    el = round(time.perf_counter() - t0, 3)
    if error:
        return LabResult(False, None, st, [error], logs=logs, runtime=runtime, elapsed_s=el)
    close = ctx["series"]["close"][-1]
    r, errs, warns = validate_result(raw, close)
    return LabResult(r is not None, r, st, errs, warns, logs, runtime, el)


def _call(lang: str, mode: str, source: str, contexts: list[dict], spec: StrategySpec, user: str, cancel=None) -> dict:
    lim = spec.limits
    if lang in ("python", "lua", "sql"):
        params = contexts[0]["params"] if contexts else {}
        payload = json.dumps({"language": lang, "mode": mode, "source": source, "contexts": contexts,
                              "limits": {"lua_mem_mb": min(lim.memory_mb, 256), "lua_instructions": 50_000_000,
                                         "sql_steps": 5_000_000, "sql_rows": 1000},
                              "sql_params": params})
        r = sandbox.run([sys.executable, "-I", "-B", "py_worker.py"],
                        payload, lim.timeout_s, lim.memory_mb + 256, lim.max_output_kb * max(1, len(contexts)) + 8, user,
                        extra_files={"py_worker.py": _read("py_worker.py")}, cancel=cancel)
    elif lang in ("javascript", "typescript"):
        node = shutil.which("node")
        if not node:
            raise RuntimeError("Node.js runtime not installed")
        if lang == "typescript":
            tsp = ts_path()
            if not tsp:
                raise RuntimeError("TypeScript compiler not installed")
            c = sandbox.run([node, "--permission", f"--allow-fs-read={tsp}", "--max-old-space-size=256", "ts_compile.js"],
                            json.dumps({"source": source, "sdk": _read("amw-sdk.d.ts")}), max(lim.timeout_s, 20), None, 512, user,
                            extra_files={"ts_compile.js": _read("ts_compile.js").replace("process.env.AMW_TS_PATH", json.dumps(tsp))}, cancel=cancel)
            cw = _parse_worker(c.stdout)
            if cw is None:
                raise RuntimeError(f"TypeScript compiler failed: {c.stderr[-300:]}")
            if not cw["ok"]:
                return {"check": {"ok": False, "errors": cw["errors"]}, "results": [], "runtime_version": f"TypeScript {cw['version']}"}
            source = cw["js"]
            tsver = f"TypeScript {cw['version']} -> "
        else:
            tsver = ""
        payload = json.dumps({"mode": mode, "source": source, "contexts": contexts, "timeout_ms": int(lim.timeout_s * 1000)})
        r = sandbox.run([node, "--permission", f"--max-old-space-size={lim.memory_mb}", "-e", _read("js_worker.js")], payload,
                        lim.timeout_s, None, lim.max_output_kb * max(1, len(contexts)) + 8, user, cancel=cancel)
        w = _parse_worker(r.stdout)
        if w:
            w["runtime_version"] = tsver + w.get("runtime_version", "")
        return _checked(w, r)
    elif lang == "r":
        rs = shutil.which("Rscript")
        if not rs:
            raise RuntimeError("R runtime (Rscript) not installed")
        if len(contexts) > R_CHUNK:          # R parses contexts as literals: bounded batches keep memory predictable
            merged = None
            for i in range(0, len(contexts), R_CHUNK):
                part = _call(lang, mode, source, contexts[i:i + R_CHUNK], spec, user, cancel)
                if merged is None:
                    merged = part
                else:
                    merged["results"] += part["results"]
                if not part["check"]["ok"]:
                    return part
            return merged
        data = "contexts <- list(" + ",".join(_r_lit(c) for c in contexts) + ")\n"
        r = sandbox.run([rs, "--vanilla", "--no-echo", "r_worker.R"], "", max(lim.timeout_s, 15), max(lim.memory_mb, 256) + 768,
                        lim.max_output_kb * max(1, len(contexts)) + 8, user,
                        extra_files={"r_worker.R": _read("r_worker.R"), "strategy.R": source, "ctx_data.R": data, "mode.txt": mode}, cancel=cancel)
    else:
        raise RuntimeError(f"unsupported language {lang}")
    return _checked(_parse_worker(r.stdout), r)


def _checked(w: dict | None, r) -> dict:
    if "cancelled by user" in (r.stderr or "")[-40:]:
        raise RuntimeError("execution cancelled by user")
    if r.timed_out:
        raise RuntimeError(f"execution timed out after {r.elapsed_s}s")
    if r.truncated:
        raise RuntimeError("output limit exceeded")
    if w is None:
        tail = (r.stderr or r.stdout)[-400:]
        raise RuntimeError(f"runtime crashed (exit {r.returncode}): {tail}")
    return w
