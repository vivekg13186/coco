"""compile() / check() entry points and the Program object they produce."""
from __future__ import annotations

import ast
import asyncio
import builtins
import os
import re
import threading
from typing import Any, AsyncIterator, Callable, Optional

from pydantic import TypeAdapter

from . import dsl
from .checker import Checker, FlowIR, find_python_module
from .diagnostics import CocoCompileError, Diagnostic
from .events import Event, RunResult
from .model import Module, Param, TypeRef
from .parser import parse
from .runtime import Execution, StepContext

Loader = Callable[[str], "str | None"]

_SIMPLE: dict[str, Any] = {"str": str, "int": int, "float": float, "bool": bool, "dict": dict[str, Any],
                           "any": Any, "none": type(None)}
GLOBALS = ["log(*values)", "info(*values)", "warn(*values)", "error(*values)", "debug(*values)",
           "log_table(data, columns=None)", "env", "context.run_id", "context.flow", "context.step",
           "context.path"]


def workflow_name(filename: str) -> str:
    """The workflow name from a file name: `onboard.coco` -> `onboard`."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    name = re.sub(r"\W", "_", stem)
    if not name or name[0].isdigit():
        name = "w_" + name
    return name if name != "_coco_" else "workflow"


def _looks_like_workflow(src: str) -> bool:
    return bool(re.search(r"^\s*(from\s+coco\s+import|import\s+coco\b)", src, re.M)) or "@flow" in src \
        or "@step" in src


def directory_loader(directory: str) -> Loader:
    """Find imported workflows as `<name>.coco`, or `<name>.py` files that use coco, in a directory."""

    def load(name: str) -> str | None:
        if "." in name:
            return None
        for ext in (".coco", ".py"):
            path = os.path.join(directory, name + ext)
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as fh:
                    src = fh.read()
                if ext == ".coco" or _looks_like_workflow(src):
                    return src
        return None

    return load


class _Analysis:
    def __init__(self, mod: Module, imports: dict[str, "Program"], irs: dict[str, FlowIR],
                 diagnostics: list[Diagnostic]):
        self.module = mod
        self.imports = imports
        self.irs = irs
        self.diagnostics = diagnostics

    @property
    def ok(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics)


def _analyze(source: str, filename: str, loader: Loader | None, stack: tuple[str, ...],
             name: str | None) -> _Analysis:
    mod, diags = parse(source, filename, name or workflow_name(filename))
    imports: dict[str, Program] = {}
    for imp in mod.imports:
        top = imp.module.split(".")[0]
        src = loader(imp.module) if loader else None
        if src is not None:
            if imp.is_from:
                diags.append(Diagnostic(imp.line, 1, f"`{imp.module}` is a workflow; use `import {imp.module}` "
                                                     f"and call `{imp.module}.name(...)`", code="E236"))
                continue
            if imp.module in stack or imp.module == mod.name:
                diags.append(Diagnostic(imp.line, 1, "import cycle: " + " -> ".join(stack + (imp.module,)),
                                        code="E231"))
                continue
            try:
                imports[imp.bound] = compile(src, f"{imp.module}.coco", loader, name=imp.module,
                                             _stack=stack + (mod.name, imp.module))
            except CocoCompileError as e:
                n = sum(1 for d in e.diagnostics if d.severity == "error")
                diags.append(Diagnostic(imp.line, 1, f"workflow `{imp.module}` has {n} error(s)", code="E232"))
        elif not find_python_module(top):
            diags.append(Diagnostic(imp.line, 1, f"no workflow or Python module named `{imp.module}`",
                                    code="E230"))
    irs: dict[str, FlowIR] = {}
    if mod.tree is not None:
        irs = Checker(mod, imports, diags).run()
    diags.sort(key=lambda d: (d.line, d.col))
    return _Analysis(mod, imports, irs, diags)


def check(source: str, filename: str = "<coco>", loader: Loader | None = None,
          name: str | None = None) -> list[Diagnostic]:
    """All diagnostics for a source file. Never raises and never runs the file."""
    return _analyze(source, filename, loader, (), name).diagnostics


def symbols(source: str, filename: str = "<coco>", loader: Loader | None = None,
            name: str | None = None) -> dict[str, Any]:
    """Best-effort symbol table for editors, even when the source has errors."""
    a = _analyze(source, filename, loader, (), name)
    return _symbols(a.module, a.imports)


def _sig(params: list[Param] | None) -> list[dict[str, Any]] | None:
    if params is None:
        return None
    out = []
    for p in params:
        d: dict[str, Any] = {"name": p.name, "type": str(p.type)}
        if p.has_default:
            d["default"] = p.default
            d["optional"] = True
        out.append(d)
    return out


def _symbols(mod: Module, imports: dict[str, "Program"]) -> dict[str, Any]:
    return {
        "workflow": mod.name,
        "classes": [{"name": c.name, "line": c.line,
                     "fields": [{"name": f.name, "type": str(f.type)} for f in c.fields]} for c in mod.classes],
        "steps": [{"name": s.name, "line": s.line, "inputs": _sig(s.params), "outputs": _sig(s.outputs)}
                  for s in mod.steps],
        "flows": [{"name": f.name, "line": f.line, "inputs": _sig(f.params), "outputs": _sig(f.outputs or [])}
                  for f in mod.flows],
        "imports": [{"module": i.module, "alias": i.alias, "names": i.names,
                     "kind": "coco" if i.bound in imports and not i.is_from else "python"} for i in mod.imports],
        "helpers": list(mod.helpers),
        "globals": GLOBALS,
    }


def compile(source: str, filename: str = "<coco>", loader: Loader | None = None, *, name: str | None = None,
            _stack: tuple[str, ...] = ()) -> "Program":
    """Parse, check and load a workflow file. Raises CocoCompileError on errors."""
    a = _analyze(source, filename, loader, _stack, name)
    if not a.ok:
        raise CocoCompileError(a.diagnostics, filename)
    prog = Program(a.module, a.imports, a.irs, a.diagnostics)
    prog._lower()
    return prog


def compile_file(path: str) -> "Program":
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    path = os.path.abspath(path)
    return compile(src, path, directory_loader(os.path.dirname(path)), name=workflow_name(path))


class _WorkflowProxy:
    """What `import other_workflow` binds inside step code: its classes and steps."""

    def __init__(self, program: "Program"):
        self._program = program
        for n, obj in program.classes.items():
            setattr(self, n, obj)
        for n, fn in program.step_funcs.items():
            setattr(self, n, fn)

    def __repr__(self) -> str:
        return f"<coco workflow {self._program.name}>"


class _Lowering(ast.NodeTransformer):
    """Prepares the module for exec: drops imports of other workflows (bound as proxies instead) and
    gives classes without a base the coco Model base."""

    def __init__(self, workflow_aliases: set[str]):
        self.aliases = workflow_aliases

    def visit_Module(self, node: ast.Module) -> ast.Module:
        body: list[ast.stmt] = []
        for st in node.body:
            if isinstance(st, ast.Import):
                names = [a for a in st.names if (a.asname or a.name.split(".")[0]) not in self.aliases]
                if not names:
                    continue
                st.names = names
            elif isinstance(st, ast.ClassDef) and not st.bases and not st.keywords:
                st.bases = [ast.copy_location(ast.Name("_coco_Model", ast.Load()), st)]
            body.append(st)
        node.body = body
        return node


class Program:
    def __init__(self, mod: Module, imports: dict[str, "Program"], irs: dict[str, FlowIR],
                 diagnostics: list[Diagnostic]):
        self.module = mod
        self.name = mod.name
        self.filename = mod.filename
        self.imports = imports
        self.flow_irs = irs
        self.diagnostics = diagnostics  # warnings only, after a successful compile
        self.classes: dict[str, type] = {}
        self.step_funcs: dict[str, Callable[..., Any]] = {}
        self.namespace: dict[str, Any] = {}
        self.constants: dict[str, Any] = {}
        self._adapters: dict[TypeRef, TypeAdapter] = {}
        self._adapter_lock = threading.Lock()

    # -- loading
    def _lower(self) -> None:
        ns: dict[str, Any] = {"__name__": f"coco.workflow.{self.name}", "__builtins__": builtins,
                              "_coco_Model": dsl.Model, **dsl.API}
        for alias, prog in self.imports.items():
            ns[alias] = _WorkflowProxy(prog)
        self.namespace = ns
        tree = _Lowering(set(self.imports)).visit(ast.parse(self.module.source, filename=self.filename))
        ast.fix_missing_locations(tree)
        try:
            exec(builtins.compile(tree, self.filename, "exec"), ns)
        except Exception as e:
            line = 1
            tb = e.__traceback__
            while tb is not None:
                if tb.tb_frame.f_code.co_filename == self.filename:
                    line = tb.tb_lineno
                tb = tb.tb_next
            raise CocoCompileError([Diagnostic(line, 1, f"loading the workflow failed: {type(e).__name__}: {e}",
                                               code="E234")], self.filename) from e
        for c in self.module.classes:
            obj = ns.get(c.name)
            if isinstance(obj, type):
                self.classes[c.name] = obj
                rebuild = getattr(obj, "model_rebuild", None)
                if rebuild is not None:
                    try:
                        rebuild(_types_namespace=ns)
                    except Exception:  # pragma: no cover - leave it to validation time
                        pass
        for s in self.module.steps:
            self.step_funcs[s.name] = ns[s.name]
        for n in self.module.constants:
            if n in ns:
                self.constants[n] = ns[n]

    def adapter(self, t: TypeRef) -> TypeAdapter:
        ad = self._adapters.get(t)
        if ad is None:
            with self._adapter_lock:
                ad = self._adapters.get(t)
                if ad is None:
                    inner: Any = Any
                    if "." in t.name:
                        alias, _, cname = t.name.rpartition(".")
                        prog = self.imports.get(alias)
                        inner = prog.classes.get(cname, Any) if prog else Any
                    elif t.name in self.classes:
                        inner = self.classes[t.name]
                    else:
                        inner = _SIMPLE.get(t.name, Any)
                    py: Any = list[inner] if t.array else inner
                    if t.nullable:
                        py = Optional[py]
                    try:
                        ad = TypeAdapter(py)
                    except Exception:  # a class pydantic cannot validate: accept anything
                        ad = TypeAdapter(Any)
                    self._adapters[t] = ad
        return ad

    # -- introspection
    @property
    def flows(self) -> dict[str, dict[str, Any]]:
        return {f.name: {"inputs": _sig(f.params), "outputs": _sig(f.outputs or [])} for f in self.module.flows}

    def signature(self, flow: str) -> dict[str, Any]:
        return self.flows[flow]

    def symbols(self) -> dict[str, Any]:
        return _symbols(self.module, self.imports)

    # -- running
    def run(self, flow: str, inputs: dict[str, Any] | None = None, *,
            on_event: Callable[[Event], Any] | None = None, run_id: str | None = None,
            cancel: threading.Event | None = None, collect_events: bool = True,
            env: dict[str, Any] | None = None, env_name: str | None = None) -> RunResult:
        """Run a flow synchronously. Never raises for flow failures; check result.status.

        `env` is a JSON object available as `env` in flows and steps.
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


__all__ = ["compile", "compile_file", "check", "symbols", "Program", "StepContext", "directory_loader",
           "workflow_name"]
