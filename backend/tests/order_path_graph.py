"""Z10-c (G6): a static call graph of `app/` (and `scripts/`) for the "no API order path reachable" guard.

`tests/test_z10c_order_paths.py` uses it to answer one question: which API routes and which process entry points can,
by any chain of calls, reach a broker order method (`place_order`, `modify_order`, `place_stop_loss_order`,
`exit_position`)? The graph is built from the source with `ast` alone; nothing is imported or run.

How calls are resolved (deliberately simple, and documented so a reviewer can judge what it can miss):
- a plain name resolves to a function or class of the same module, or to what an `import` / `from ... import` bound it
  to (imports anywhere in the module count, including the function-local imports this codebase uses a lot);
- `module.f(...)` resolves through the module alias; `pkg.f` re-exported by a package `__init__` is followed;
- `ClassName(...)` is an edge to `ClassName.__init__`, and a variable (or `self.x`) assigned from it gets that class,
  so `router = OrderRouter(...); await router.execute(...)` resolves to `OrderRouter.execute`;
- `self.m(...)` / `cls.m(...)` resolve inside the class and its in-project base classes;
- a nested function or lambda belongs to the function that encloses it;
- any other `obj.m(...)` is not followed - unless `m` is a broker order method, which makes the call a sink;
- broker-gated callables (`gated`: qualname -> parameter) receive their broker as an argument: a call that passes no
  broker (the argument left out, or a literal `None`) cannot place a broker order through it, so it is not an edge
  (for a class, a broker-less construction gives the variable no type, so its methods are not followed). The test
  checks every gated callable can reach no adapter factory on its own, which is what makes the rule sound.

A sink is a call of a broker order method anywhere outside `app/brokers/` (the adapters and their wrappers are the
broker itself, so their internals are not call sites). The graph over-approximates in one direction (an edge is kept
even when the call is guarded at runtime, e.g. `mode == "PAPER"`) and can under-approximate in the other (dynamic
dispatch on an untyped object). The second gap is covered by the call-site inventory: a new direct call of a broker
order method anywhere fails the test whatever the graph says.
"""
from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

BACKEND = Path(__file__).resolve().parents[1]
SINK_METHODS = frozenset({"place_order", "modify_order", "place_stop_loss_order", "exit_position"})
BROKER_PACKAGE = "app.brokers"


@dataclass
class SinkSite:
    function: str
    method: str
    lineno: int


@dataclass
class _Func:
    qualname: str
    module: str
    class_name: Optional[str]
    node: ast.AST
    edges: Set[str] = field(default_factory=set)
    sinks: List[SinkSite] = field(default_factory=list)


def _module_name(path: Path) -> str:
    rel = path.relative_to(BACKEND).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _is_package(path: Path) -> bool:
    return path.name == "__init__.py"


class CallGraph:
    def __init__(self, roots: Iterable[str] = ("app", "scripts"), gated: Optional[Dict[str, str]] = None,
                 sources: Optional[Dict[str, str]] = None) -> None:
        """`sources` (module name -> code) adds in-memory modules - the tests use it for small worked examples."""
        self.gated: Dict[str, str] = dict(gated or {})
        self.functions: Dict[str, _Func] = {}
        self.classes: Dict[str, ast.ClassDef] = {}
        self.class_bases: Dict[str, List[str]] = {}
        self.aliases: Dict[str, Dict[str, str]] = {}
        self.module_paths: Dict[str, Path] = {}
        self.main_modules: Set[str] = set()
        trees: Dict[str, ast.Module] = {}
        files = [(_module_name(path), path, path.read_text(encoding="utf-8"))
                 for root in roots for path in sorted((BACKEND / root).rglob("*.py")) if "__pycache__" not in path.parts]
        files += [(module, BACKEND / (module.replace(".", "/") + ".py"), code) for module, code in (sources or {}).items()]
        for module, path, code in files:
            tree = ast.parse(code, filename=str(path))
            trees[module] = tree
            self.module_paths[module] = path
            self.aliases[module] = self._collect_aliases(module, path, tree)
            self._collect_defs(module, tree)
            if any(isinstance(n, ast.If) and "__main__" in ast.dump(n.test) for n in tree.body):
                self.main_modules.add(module)
        self.class_attr_types: Dict[str, Dict[str, str]] = {}
        for module, tree in trees.items():
            self._collect_class_attr_types(module, tree)
        for func in self.functions.values():
            self._collect_calls(func)
        self.callers: Dict[str, Set[str]] = {}
        for func in self.functions.values():
            for target in func.edges:
                self.callers.setdefault(target, set()).add(func.qualname)

    # --- collection ---------------------------------------------------------------------------------------------

    def _collect_aliases(self, module: str, path: Path, tree: ast.Module) -> Dict[str, str]:
        aliases: Dict[str, str] = {}
        package = module if _is_package(path) else module.rpartition(".")[0]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        aliases[alias.asname] = alias.name
                    else:
                        aliases.setdefault(alias.name.split(".")[0], alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parent = package
                    for _ in range(node.level - 1):
                        parent = parent.rpartition(".")[0]
                    base = f"{parent}.{base}" if base else parent
                for alias in node.names:
                    if alias.name != "*":
                        aliases[alias.asname or alias.name] = f"{base}.{alias.name}"
        return aliases

    def _collect_defs(self, module: str, tree: ast.Module) -> None:
        self.functions[f"{module}.<module>"] = _Func(f"{module}.<module>", module, None, tree)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{module}.{node.name}"
                self.functions[qual] = _Func(qual, module, None, node)
            elif isinstance(node, ast.ClassDef):
                cls = f"{module}.{node.name}"
                self.classes[cls] = node
                self.class_bases[cls] = [b for b in (self._dotted(module, base) for base in node.bases) if b]
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        qual = f"{cls}.{item.name}"
                        self.functions[qual] = _Func(qual, module, cls, item)

    def _collect_class_attr_types(self, module: str, tree: ast.Module) -> None:
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            cls = f"{module}.{node.name}"
            types: Dict[str, str] = {}
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Call):
                    made = self._constructed_class(module, sub.value)
                    for target in sub.targets:
                        if made and isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                            types[target.attr] = made
            self.class_attr_types[cls] = types

    # --- resolution ---------------------------------------------------------------------------------------------

    def _dotted(self, module: str, expr: ast.AST) -> Optional[str]:
        """The project-wide dotted name an expression refers to, or None."""
        if isinstance(expr, ast.Name):
            local = f"{module}.{expr.id}"
            if local in self.functions or local in self.classes:
                return local
            target = self.aliases.get(module, {}).get(expr.id)
            return self._canonical(target) if target else None
        if isinstance(expr, ast.Attribute):
            base = self._dotted(module, expr.value)
            return self._canonical(f"{base}.{expr.attr}") if base else None
        return None

    def _canonical(self, name: str, depth: int = 0) -> str:
        """Follows re-exports: `pkg.f` where `pkg/__init__.py` (or any module) imported `f` from elsewhere."""
        if name in self.functions or name in self.classes or name in self.module_paths or depth > 5:
            return name
        owner, _, attr = name.rpartition(".")
        if owner in self.module_paths:
            target = self.aliases.get(owner, {}).get(attr)
            if target and target != name:
                return self._canonical(target, depth + 1)
        if owner and owner not in self.module_paths:
            resolved_owner = self._canonical(owner, depth + 1)
            if resolved_owner != owner:
                return self._canonical(f"{resolved_owner}.{attr}", depth + 1)
        return name

    def _constructed_class(self, module: str, call: ast.Call) -> Optional[str]:
        target = self._dotted(module, call.func)
        if target not in self.classes:
            return None
        init = self._method(target, "__init__")
        if init in self.gated and not self._passes(call, init):
            return None       # a broker-less instance: its methods cannot reach a broker
        return target

    def _passes(self, call: ast.Call, callee: str) -> bool:
        """Whether a call hands the gated callable its broker parameter (anything but a missing or literal None)."""
        param = self.gated[callee]
        for keyword in call.keywords:
            if keyword.arg == param:
                return not (isinstance(keyword.value, ast.Constant) and keyword.value.value is None)
            if keyword.arg is None:
                return True        # **kwargs: cannot tell, so assume it does
        node = self.functions[callee].node
        assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        positional = [a.arg for a in node.args.posonlyargs + node.args.args]
        if param in positional:
            index = positional.index(param)
            if any(isinstance(a, ast.Starred) for a in call.args):
                return True
            # a bound method call (`obj.m(...)` / `Class(...)`) does not spell `self`
            offset = 1 if positional and positional[0] in ("self", "cls") else 0
            if len(call.args) > index - offset:
                value = call.args[index - offset]
                return not (isinstance(value, ast.Constant) and value.value is None)
        return False

    def _method(self, cls: str, name: str, seen: Optional[Set[str]] = None) -> Optional[str]:
        seen = seen or set()
        if cls in seen:
            return None
        seen.add(cls)
        qual = f"{cls}.{name}"
        if qual in self.functions:
            return qual
        for base in self.class_bases.get(cls, []):
            found = self._method(base, name, seen)
            if found:
                return found
        return None

    def _collect_calls(self, func: _Func) -> None:
        module = func.module
        local_types: Dict[str, str] = {}
        body = func.node.body if isinstance(func.node, ast.Module) else [func.node]
        nodes: List[ast.AST] = []
        for stmt in body:
            if isinstance(func.node, ast.Module) and isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue      # module-level code only; the defs are nodes of their own
            nodes.extend(ast.walk(stmt))
        for node in nodes:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                made = self._constructed_class(module, node.value)
                if made:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            local_types[target.id] = made
        for node in nodes:
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            target = self._dotted(module, callee)
            if target in self.functions:
                if target not in self.gated or self._passes(node, target):
                    func.edges.add(target)
                continue
            if target in self.classes:
                init = self._method(target, "__init__")
                if init:
                    func.edges.add(init)
                continue
            if isinstance(callee, ast.Attribute):
                owner_cls = self._owner_class(func, callee.value, local_types)
                if owner_cls:
                    method = self._method(owner_cls, callee.attr)
                    if method:
                        if method not in self.gated or self._passes(node, method):
                            func.edges.add(method)
                        continue
                if callee.attr in SINK_METHODS and not module.startswith(BROKER_PACKAGE):
                    func.sinks.append(SinkSite(func.qualname, callee.attr, node.lineno))
            elif isinstance(callee, ast.Name) and callee.id in SINK_METHODS and not module.startswith(BROKER_PACKAGE):
                func.sinks.append(SinkSite(func.qualname, callee.id, node.lineno))

    def _owner_class(self, func: _Func, value: ast.AST, local_types: Dict[str, str]) -> Optional[str]:
        if isinstance(value, ast.Name):
            if value.id in ("self", "cls"):
                return func.class_name
            return local_types.get(value.id)
        if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) and value.value.id == "self" and func.class_name:
            return self.class_attr_types.get(func.class_name, {}).get(value.attr)
        if isinstance(value, ast.Call):
            return self._constructed_class(func.module, value)
        return None

    # --- queries ------------------------------------------------------------------------------------------------

    def sink_sites(self) -> List[SinkSite]:
        return [site for func in self.functions.values() for site in func.sinks]

    def reaching(self) -> Set[str]:
        """Every function that can reach a sink (including the ones that call one directly)."""
        seen: Set[str] = set()
        queue = deque(f.qualname for f in self.functions.values() if f.sinks)
        while queue:
            name = queue.popleft()
            if name in seen:
                continue
            seen.add(name)
            queue.extend(self.callers.get(name, ()))
        return seen

    def reaches_any(self, start: str, targets: Set[str]) -> Optional[List[str]]:
        """One call chain from `start` to any of `targets` (None when there is none)."""
        previous: Dict[str, Optional[str]] = {start: None}
        queue = deque([start])
        while queue:
            name = queue.popleft()
            if name in targets:
                chain: List[str] = []
                cursor: Optional[str] = name
                while cursor is not None:
                    chain.append(cursor)
                    cursor = previous[cursor]
                return list(reversed(chain))
            func = self.functions.get(name)
            for nxt in sorted(func.edges if func else ()):
                if nxt not in previous:
                    previous[nxt] = name
                    queue.append(nxt)
        return None

    def path_to_sink(self, start: str) -> Optional[List[str]]:
        """One shortest call chain from `start` to a function that calls a broker order method."""
        previous: Dict[str, Optional[str]] = {start: None}
        queue = deque([start])
        while queue:
            name = queue.popleft()
            func = self.functions.get(name)
            if func is None:
                continue
            if func.sinks:
                chain: List[str] = []
                cursor: Optional[str] = name
                while cursor is not None:
                    chain.append(cursor)
                    cursor = previous[cursor]
                site = func.sinks[0]
                return list(reversed(chain)) + [f".{site.method}() line {site.lineno}"]
            for nxt in sorted(func.edges):
                if nxt not in previous:
                    previous[nxt] = name
                    queue.append(nxt)
        return None

    def sinks_reachable_from(self, start: str) -> Set[Tuple[str, str]]:
        """(function, method) of every direct broker order call reachable from `start`."""
        found: Set[Tuple[str, str]] = set()
        seen: Set[str] = set()
        queue = deque([start])
        while queue:
            name = queue.popleft()
            if name in seen:
                continue
            seen.add(name)
            func = self.functions.get(name)
            if func is None:
                continue
            found.update((site.function, site.method) for site in func.sinks)
            queue.extend(func.edges)
        return found


def endpoint_node(endpoint) -> str:
    """The graph node of a route endpoint or dependency callable (closures map to their enclosing function)."""
    qualname = endpoint.__qualname__.split(".<locals>")[0]
    return f"{endpoint.__module__}.{qualname}"
