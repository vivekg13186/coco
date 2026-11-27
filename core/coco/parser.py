"""Reads a workflow file. The file is plain Python, so Python's own parser does the work; this module
picks out imports, classes, @step / @flow functions, helpers and constants, and reads type hints leniently.

Allowed at the top level: docstrings, imports, classes, functions (@step, @flow or plain helpers) and
constants (`NAME = value` without calls). Anything else would run when the workflow loads, so it is
reported as an error.
"""
from __future__ import annotations

import ast

from .diagnostics import Diagnostic
from .model import ANY, ClassDef, Field, FlowDef, Import, Module, Param, StepDef, TypeRef

_SCALARS = {"str": "str", "int": "int", "float": "float", "bool": "bool"}
_DICTS = {"dict", "Dict", "Mapping", "MutableMapping"}
_LISTS = {"list", "List", "Sequence", "Iterable", "set", "Set", "frozenset", "FrozenSet", "tuple", "Tuple"}
_ANYS = {"Any", "object"}
COCO_API = {"step", "flow", "Model", "log", "info", "warn", "warning", "error", "debug", "log_table",
            "env", "context"}


def _decorator_kind(dec: ast.expr) -> str | None:
    """`@step`, `@step(...)`, `@coco.step` -> "step"; same for flow. Arguments are ignored."""
    node = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(node, ast.Name) and node.id in ("step", "flow"):
        return node.id
    if isinstance(node, ast.Attribute) and node.attr in ("step", "flow") and isinstance(node.value, ast.Name) \
            and node.value.id == "coco":
        return node.attr
    return None


def _base_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


class _Hints:
    """Turns type hints into TypeRefs. Unknown types become `Any`; nothing here is an error."""

    def __init__(self, classes: set[str], aliases: set[str]):
        self.classes = classes
        self.aliases = aliases

    def __call__(self, node: ast.expr | None) -> TypeRef:
        if node is None:
            return ANY
        if isinstance(node, ast.Constant):
            if node.value is None:
                return TypeRef("none")
            if isinstance(node.value, str):  # forward reference: "Person"
                try:
                    return self(ast.parse(node.value, mode="eval").body)
                except SyntaxError:
                    return ANY
            return ANY
        if isinstance(node, ast.Name):
            n = node.id
            if n in _SCALARS:
                return TypeRef(_SCALARS[n])
            if n in _DICTS:
                return TypeRef("dict")
            if n in _LISTS:
                return TypeRef("any", array=True)
            if n == "None":
                return TypeRef("none")
            if n in self.classes:
                return TypeRef(n)
            return ANY
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in self.aliases:
                return TypeRef(f"{node.value.id}.{node.attr}")  # maybe a class of another workflow
            return self(ast.Name(node.attr, ast.Load()))
        if isinstance(node, ast.Subscript):
            base = _base_name(node.value)
            args = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            if base in ("tuple", "Tuple"):
                return TypeRef("any", array=True)
            if base in _LISTS:
                inner = self(args[0])
                return TypeRef(inner.name, array=True) if not inner.array else TypeRef("any", array=True)
            if base in _DICTS:
                return TypeRef("dict")
            if base == "Optional":
                t = self(args[0])
                return TypeRef(t.name, t.array, True)
            if base == "Union":
                return self._union(args)
            if base == "Annotated":
                return self(args[0])
            return ANY
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return self._union([node.left, node.right])
        return ANY

    def _union(self, parts: list[ast.expr]) -> TypeRef:
        flat: list[ast.expr] = []

        def walk(p: ast.expr) -> None:
            if isinstance(p, ast.BinOp) and isinstance(p.op, ast.BitOr):
                walk(p.left)
                walk(p.right)
            else:
                flat.append(p)

        for p in parts:
            walk(p)
        types = [self(p) for p in flat]
        nullable = any(t.name == "none" for t in types)
        rest = [t for t in types if t.name != "none"]
        if len(rest) == 1:
            return TypeRef(rest[0].name, rest[0].array, nullable or rest[0].nullable)
        return TypeRef("any", nullable=nullable)


def _outputs(fn: ast.FunctionDef, hint: _Hints, diags: list[Diagnostic]) -> list[Param] | None:
    """Outputs from the `->` hint: none = unknown; `-> None` = no outputs; `-> T` = one output named
    `result`; `-> {"a": T, "b": U}` = named outputs; `-> tuple[T, U]` = `result1`, `result2`."""
    r = fn.returns
    if r is None:
        return None
    if (isinstance(r, ast.Constant) and r.value is None) or (isinstance(r, ast.Name) and r.id == "None"):
        return []
    if isinstance(r, ast.Dict):
        out: list[Param] = []
        for k, v in zip(r.keys, r.values):
            if not (isinstance(k, ast.Constant) and isinstance(k.value, str) and k.value.isidentifier()):
                diags.append(Diagnostic(r.lineno, r.col_offset + 1, "named outputs need simple string keys, "
                                        'e.g. -> {"total": int, "ok": bool}', code="E111"))
                return None
            out.append(Param(k.value, hint(v), r.lineno))
        return out
    if isinstance(r, ast.Subscript) and _base_name(r.value) in ("tuple", "Tuple"):
        args = r.slice.elts if isinstance(r.slice, ast.Tuple) else [r.slice]
        return [Param(f"result{i + 1}", hint(a), r.lineno) for i, a in enumerate(args)]
    return [Param("result", hint(r), r.lineno)]


def _params(fn: ast.FunctionDef, hint: _Hints, diags: list[Diagnostic], literal_defaults: bool) -> list[Param]:
    a = fn.args
    if a.vararg or a.kwarg:
        diags.append(Diagnostic(fn.lineno, fn.col_offset + 1, f"`{fn.name}`: *args and **kwargs are not supported; "
                                "list each input", code="E112"))
    positional = a.posonlyargs + a.args
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(a.defaults)) + list(a.defaults)
    pairs = list(zip(positional, defaults)) + list(zip(a.kwonlyargs, a.kw_defaults))
    out: list[Param] = []
    for arg, default in pairs:
        p = Param(arg.arg, hint(arg.annotation), arg.lineno)
        if default is not None:
            p.has_default = True
            if literal_defaults:
                try:
                    p.default = ast.literal_eval(default)
                except ValueError:
                    diags.append(Diagnostic(default.lineno, default.col_offset + 1,
                                            f"default of `{arg.arg}` must be a literal value", code="E113"))
        out.append(p)
    return out


def _is_constant_value(node: ast.expr) -> bool:
    """Module constants may not call anything (that would run when the workflow loads)."""
    return not any(isinstance(n, (ast.Call, ast.Lambda, ast.Await, ast.Yield, ast.YieldFrom, ast.NamedExpr))
                   for n in ast.walk(node))


def parse(source: str, filename: str = "<coco>", name: str = "workflow") -> tuple[Module, list[Diagnostic]]:
    diags: list[Diagnostic] = []
    mod = Module(name=name, filename=filename, source=source)
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        diags.append(Diagnostic(e.lineno or 1, e.offset or 1, f"syntax error: {e.msg}", code="E100"))
        return mod, diags
    mod.tree = tree

    classes = {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
    aliases: set[str] = set()
    for n in tree.body:
        if isinstance(n, ast.Import):
            aliases.update(a.asname or a.name.split(".")[0] for a in n.names)
    hint = _Hints(classes, aliases)

    for i, node in enumerate(tree.body):
        line, col = node.lineno, node.col_offset + 1
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue  # docstring or a bare string
        if isinstance(node, ast.Pass):
            continue
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "coco" or a.name.startswith("coco."):
                    continue
                mod.imports.append(Import(a.name, a.asname, [], False, line, node))
            continue
        if isinstance(node, ast.ImportFrom):
            if node.level:
                diags.append(Diagnostic(line, col, "relative imports are not supported", code="E115"))
                continue
            if node.module == "coco" or (node.module or "").startswith("coco."):
                continue
            mod.imports.append(Import(node.module or "", None, [a.asname or a.name for a in node.names], True, line,
                                      node))
            continue
        if isinstance(node, ast.ClassDef):
            fields = []
            for b in node.body:
                if isinstance(b, ast.AnnAssign) and isinstance(b.target, ast.Name):
                    fields.append(Field(b.target.id, hint(b.annotation), b.lineno, b.value is not None))
            mod.classes.append(ClassDef(node.name, fields, line))
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kinds = [k for k in (_decorator_kind(d) for d in node.decorator_list) if k]
            if len(kinds) > 1:
                diags.append(Diagnostic(line, col, f"`{node.name}` cannot be both a step and a flow", code="E116"))
                continue
            if not kinds:
                mod.helpers.append(node.name)
                continue
            if isinstance(node, ast.AsyncFunctionDef):
                diags.append(Diagnostic(line, col, f"async {kinds[0]}s are not supported yet", code="E114"))
                continue
            if kinds[0] == "step":
                mod.steps.append(StepDef(node.name, _params(node, hint, diags, False), _outputs(node, hint, diags),
                                         line, node))
            else:
                extra = [d for d in node.decorator_list if not _decorator_kind(d)]
                if extra:
                    diags.append(Diagnostic(extra[0].lineno, extra[0].col_offset + 1,
                                            "flows cannot have other decorators", code="E116"))
                mod.flows.append(FlowDef(node.name, _params(node, hint, diags, True), _outputs(node, hint, diags),
                                         line, node))
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if all(isinstance(t, ast.Name) for t in targets) and (value is None or _is_constant_value(value)):
                mod.constants.extend(t.id for t in targets)  # type: ignore[attr-defined]
                continue
            diags.append(Diagnostic(line, col, "module constants cannot call functions (that would run when the "
                                    "workflow loads); compute the value inside a step", code="E110"))
            continue
        kind = type(node).__name__
        diags.append(Diagnostic(line, col, f"`{kind}` is not allowed at the top level: it would run when the "
                                "workflow loads. Allowed: imports, classes, functions, @step, @flow and constants",
                                code="E110"))
    return mod, diags
