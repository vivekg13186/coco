"""compile() / check() entry points and the Program object they produce."""
from __future__ import annotations

import ast
import asyncio
import builtins
import keyword
import os
import threading
from typing import Any, AsyncIterator, Callable

from pydantic import BaseModel, TypeAdapter

from .checker import Checker, FlowIR, find_python_module
from .diagnostics import CocoCompileError, Diagnostic
from .events import Event, RunResult
from .model import PRIMITIVES, Package, Param, TypeRef
from .parser import parse
from .runtime import Execution, StepContext

Loader = Callable[[str], "str | None"]

PY_TYPES = {"string": "str", "boolean": "bool", "int": "int", "float": "float",
            "object": "dict[str, Any]", "array": "list[Any]"}


def annotation(t: TypeRef) -> str:
    inner = PY_TYPES.get(t.name, t.name)
    return f"list[{inner}]" if t.array else inner


def directory_loader(directory: str) -> Loader:
    """Find imported Coco packages as `<name>.coco` files in a directory."""

    def load(name: str) -> str | None:
        if "." in name:
            return None
        path = os.path.join(directory, f"{name}.coco")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        return None

    return load


class _Analysis:
    def __init__(self, pkg: Package, imports: dict[str, "Program"], irs: dict[str, FlowIR],
                 diagnostics: list[Diagnostic]):
        self.package = pkg
        self.imports = imports
        self.irs = irs
        self.diagnostics = diagnostics

    @property
    def ok(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics)


def _analyze(source: str, filename: str, loader: Loader | None, stack: tuple[str, ...]) -> _Analysis:
    pkg, diags = parse(source, filename)
    imports: dict[str, Program] = {}
    for imp in pkg.imports:
        if imp.is_from:
            if not find_python_module(imp.module):
                diags.append(Diagnostic(imp.line, 1, f"no Python module named `{imp.module}`", code="E230"))
            continue
        src = loader(imp.module) if loader else None
        alias = imp.alias or imp.module
        if src is not None:
            if imp.module in stack:
                diags.append(Diagnostic(imp.line, 1, "import cycle: " + " -> ".join(stack + (imp.module,)),
                                        code="E231"))
                continue
            try:
                imports[alias] = compile(src, f"{imp.module}.coco", loader, _stack=stack + (imp.module,))
            except CocoCompileError as e:
                n = sum(1 for d in e.diagnostics if d.severity == "error")
                diags.append(Diagnostic(imp.line, 1, f"Coco package `{imp.module}` has {n} error(s)", code="E232"))
        elif not find_python_module(imp.module):
            diags.append(Diagnostic(imp.line, 1, f"no Coco package or Python module named `{imp.module}`",
                                    code="E230"))
    # names that cannot be Python identifiers
    for c in pkg.classes:
        for f in c.fields:
            if keyword.iskeyword(f.name):
                diags.append(Diagnostic(f.line, 1, f"`{f.name}` is a Python keyword and cannot be a field name",
                                        code="E233"))
    for s in pkg.steps:
        for p in s.params:
            if keyword.iskeyword(p.name):
                diags.append(Diagnostic(p.line, 1, f"`{p.name}` is a Python keyword and cannot be a parameter",
                                        code="E233"))
    irs = Checker(pkg, imports, diags).run()
    diags.sort(key=lambda d: (d.line, d.col))
    return _Analysis(pkg, imports, irs, diags)


def check(source: str, filename: str = "<coco>", loader: Loader | None = None) -> list[Diagnostic]:
    """All diagnostics for a source file. Never raises and never runs Python imports."""
    return _analyze(source, filename, loader, ()).diagnostics


def symbols(source: str, filename: str = "<coco>", loader: Loader | None = None) -> dict[str, Any]:
    """Best-effort symbol table for editors, even when the source has errors."""
    a = _analyze(source, filename, loader, ())
    return _symbols(a.package, a.imports)


def _sig(params: list[Param]) -> list[dict[str, str]]:
    return [{"name": p.name, "type": str(p.type)} for p in params]


def _symbols(pkg: Package, imports: dict[str, "Program"]) -> dict[str, Any]:
    return {
        "package": pkg.name,
        "classes": [{"name": c.name, "line": c.line,
                     "fields": [{"name": f.name, "type": str(f.type)} for f in c.fields]} for c in pkg.classes],
        "steps": [{"name": s.name, "line": s.line, "inputs": _sig(s.params), "outputs": _sig(s.outputs)}
                  for s in pkg.steps],
        "flows": [{"name": f.name, "line": f.line, "inputs": _sig(f.params), "outputs": _sig(f.outputs)}
                  for f in pkg.flows],
        "imports": [{"module": i.module, "alias": i.alias, "names": i.names,
                     "kind": "coco" if (i.alias or i.module) in imports else "python"} for i in pkg.imports],
        "self": ["log(*values)", "debug(*values)", "info(*values)", "warn(*values)", "error(*values)",
                 "log_table(data, columns=None)", "env", "run_id", "flow", "step", "path"],
    }


def compile(source: str, filename: str = "<coco>", loader: Loader | None = None, *,
            _stack: tuple[str, ...] = ()) -> "Program":
    """Parse, check and lower a Coco source file. Raises CocoCompileError on errors."""
    a = _analyze(source, filename, loader, _stack)
    if not a.ok:
        raise CocoCompileError(a.diagnostics, filename)
    prog = Program(a.package, a.imports, a.irs, a.diagnostics)
    prog._lower()
    return prog


def compile_file(path: str) -> "Program":
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    return compile(src, os.path.abspath(path), directory_loader(os.path.dirname(os.path.abspath(path))))


class _PackageProxy:
    """What `import other_coco_package` binds inside step code: its classes."""

    def __init__(self, program: "Program"):
        self._program = program
        for name, model in program.classes.items():
            setattr(self, name, model)

    def __repr__(self) -> str:
        return f"<coco package {self._program.name}>"


class Program:
    def __init__(self, pkg: Package, imports: dict[str, "Program"], irs: dict[str, FlowIR],
                 diagnostics: list[Diagnostic]):
        self.package = pkg
        self.name = pkg.name
        self.filename = pkg.filename
        self.imports = imports
        self.flow_irs = irs
        self.diagnostics = diagnostics  # warnings only, after a successful compile
        self.classes: dict[str, type[BaseModel]] = {}
        self.step_funcs: dict[str, Callable[..., Any]] = {}
        self.namespace: dict[str, Any] = {}
        self._adapters: dict[TypeRef, TypeAdapter] = {}
        self._adapter_lock = threading.Lock()

    # -- lowering
    def _lower(self) -> None:
        ns: dict[str, Any] = {"__name__": f"coco.pkg.{self.name}", "__builtins__": builtins}
        self.namespace = ns
        for imp in self.package.imports:
            alias = imp.alias or imp.module
            if alias in self.imports:
                ns[alias] = _PackageProxy(self.imports[alias])
                continue
            stmt = (f"from {imp.module} import {', '.join(imp.names)}" if imp.is_from
                    else f"import {imp.module}" + (f" as {imp.alias}" if imp.alias else ""))
            try:
                exec(stmt, ns)
            except Exception as e:
                raise CocoCompileError([Diagnostic(imp.line, 1, f"importing `{imp.module}` failed: "
                                                                f"{type(e).__name__}: {e}", code="E234")],
                                       self.filename) from e

        if self.package.classes:
            lines = ["from __future__ import annotations", "from typing import Any",
                     "from pydantic import BaseModel as _coco_BaseModel, ConfigDict as _coco_ConfigDict", ""]
            for c in self.package.classes:
                lines.append(f"class {c.name}(_coco_BaseModel):")
                lines.append("    model_config = _coco_ConfigDict(protected_namespaces=())")
                for f in c.fields:
                    default = f" = {f.default!r}" if f.has_default else ""
                    lines.append(f"    {f.name}: {annotation(f.type)}{default}")
                lines.append("")
            exec("\n".join(lines), ns)
            for c in self.package.classes:
                model = ns[c.name]
                self.classes[c.name] = model
            for model in self.classes.values():
                model.model_rebuild(_types_namespace=ns)

        for s in self.package.steps:
            args = ", ".join(["self"] + [p.name for p in s.params])
            body = "\n".join("    " + ln if ln else "" for ln in s.body.split("\n"))
            tree = ast.parse(f"def {s.name}({args}):\n{body}", filename=self.filename)
            ast.increment_lineno(tree, s.body_line - 2)
            exec(builtins.compile(tree, self.filename, "exec"), ns)
            self.step_funcs[s.name] = ns.pop(s.name) if s.name not in self.classes else ns[s.name]

    def adapter(self, t: TypeRef) -> TypeAdapter:
        ad = self._adapters.get(t)
        if ad is None:
            with self._adapter_lock:
                ad = self._adapters.get(t)
                if ad is None:
                    if t.name not in PRIMITIVES and "." in t.name:
                        alias, _, cname = t.name.rpartition(".")
                        inner: Any = self.imports[alias].classes[cname]
                    elif t.name in self.classes:
                        inner = self.classes[t.name]
                    else:
                        inner = eval(PY_TYPES[t.name], {"Any": Any})
                    py = list[inner] if t.array else inner
                    ad = TypeAdapter(py)
                    self._adapters[t] = ad
        return ad

    # -- introspection
    @property
    def flows(self) -> dict[str, dict[str, Any]]:
        return {f.name: {"inputs": _sig(f.params), "outputs": _sig(f.outputs)} for f in self.package.flows}

    def signature(self, flow: str) -> dict[str, Any]:
        return self.flows[flow]

    def symbols(self) -> dict[str, Any]:
        return _symbols(self.package, self.imports)

    # -- running
    def run(self, flow: str, inputs: dict[str, Any] | None = None, *,
            on_event: Callable[[Event], Any] | None = None, run_id: str | None = None,
            cancel: threading.Event | None = None, collect_events: bool = True,
            env: dict[str, Any] | None = None, env_name: str | None = None) -> RunResult:
        """Run a flow synchronously. Never raises for flow failures; check result.status.

        `env` is a JSON object available as `env` in flows and `self.env` in steps.
        """
        return Execution(self, run_id, on_event, cancel, collect_events, env, env_name).run(flow, inputs)

    async def stream(self, flow: str, inputs: dict[str, Any] | None = None, *, run_id: str | None = None,
                     cancel: threading.Event | None = None, env: dict[str, Any] | None = None,
                     env_name: str | None = None) -> AsyncIterator[Event]:
        """Run a flow on a worker thread and yield its events as they happen."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        done = object()

        def on_event(ev: Event) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, ev)

        def work() -> RunResult:
            try:
                return self.run(flow, inputs, on_event=on_event, run_id=run_id, cancel=cancel,
                                collect_events=False, env=env, env_name=env_name)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, done)

        fut = loop.run_in_executor(None, work)
        while True:
            item = await queue.get()
            if item is done:
                break
            yield item
        await fut


__all__ = ["compile", "compile_file", "check", "symbols", "Program", "StepContext", "directory_loader"]
