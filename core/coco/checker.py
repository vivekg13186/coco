"""Checker for a parsed workflow Module. Validates flows (which stay restricted) and builds the flow IR
the runtime interprets. Steps are plain Python and only get light checks.

Types are lenient, like Python's own hints: unknown types are `Any`, and type mismatches in flows are
warnings, not errors.
"""
from __future__ import annotations

import ast
import importlib.util
from dataclasses import dataclass, field
from types import CodeType
from typing import TYPE_CHECKING, Any

from .diagnostics import Diagnostic
from .model import ANY, ClassDef, FlowDef, Module, Param, StepDef, TypeRef
from .parser import COCO_API

if TYPE_CHECKING:  # pragma: no cover
    from .program import Program

T_INT, T_FLOAT, T_STR, T_BOOL = TypeRef("int"), TypeRef("float"), TypeRef("str"), TypeRef("bool")
T_DICT, T_LIST, T_NONE = TypeRef("dict"), TypeRef("any", array=True), TypeRef("none")

# builtins usable in flow expressions -> result type
BUILTINS: dict[str, TypeRef] = {
    "len": T_INT, "min": ANY, "max": ANY, "abs": ANY, "round": ANY, "sum": ANY,
    "str": T_STR, "int": T_INT, "float": T_FLOAT, "bool": T_BOOL,
    "sorted": T_LIST, "list": T_LIST, "dict": T_DICT, "enumerate": T_LIST, "zip": T_LIST,
    "any": T_BOOL, "all": T_BOOL, "range": T_LIST,
}
# coco functions a flow may call as a statement (they log under the flow)
LOG_FUNCS = {"log", "info", "warn", "warning", "error", "debug", "log_table", "print"}
# names a flow cannot assign to
RESERVED = COCO_API | {"print"}
# JSON spellings that are easy to paste into Python code by mistake
JSON_WORDS = {"true": "True", "false": "False", "null": "None"}


def json_word_message(word: str) -> str:
    return f"`{word}` is JSON, not Python: use `{JSON_WORDS[word]}`"


def is_tuple_outputs(outs: list[Param] | None) -> bool:
    return bool(outs) and len(outs) > 1 and all(p.name == f"result{i + 1}" for i, p in enumerate(outs))


# ---------------------------------------------------------------- flow IR
@dataclass
class Callee:
    kind: str                     # "step" | "flow"
    name: str
    params: list[Param]
    outputs: list[Param] | None   # None: unknown (a step without `->`)
    package: str | None           # import alias of another workflow, None = this one

    @property
    def label(self) -> str:
        return f"{self.package}.{self.name}" if self.package else self.name


@dataclass
class DeclIR:
    names: list[tuple[str, TypeRef]]
    line: int
    value: CodeType | None = None  # `x: int = value`; None leaves x = None


@dataclass
class CallIR:
    callee: Callee
    args: list[CodeType]
    kwargs: dict[str, CodeType]
    targets: list[str]
    line: int


@dataclass
class LogIR:
    func: str                     # log, info, warn, warning, error, debug, log_table, print
    args: list[CodeType]
    kwargs: dict[str, CodeType]
    line: int


@dataclass
class ExprIR:
    """A method call on a value, kept for its effect: `items.append(x)`."""
    expr: CodeType
    line: int


@dataclass
class AssignIR:
    target: str
    type: TypeRef
    expr: CodeType
    line: int


@dataclass
class IfIR:
    id: str
    branches: list[tuple[str, CodeType | None, list]]  # (arm label, condition or None for else, body)
    line: int


@dataclass
class ForIR:
    id: str
    var: str | list[str]          # a name, or names to unpack each item into
    range_args: list[CodeType] | None
    iterable: CodeType | None
    body: list
    line: int


@dataclass
class BreakIR:
    line: int


@dataclass
class ContinueIR:
    line: int


@dataclass
class ReturnIR:
    values: list[tuple[str, CodeType]]  # (output name, value), in declared order
    line: int


@dataclass
class FlowIR:
    name: str
    params: list[Param]
    outputs: list[Param]
    decls: dict[str, TypeRef]
    body: list = field(default_factory=list)


# ---------------------------------------------------------------- checker
class Checker:
    def __init__(self, mod: Module, coco_imports: dict[str, "Program"], diags: list[Diagnostic]):
        self.mod = mod
        self.imports = coco_imports
        self.diags = diags
        self.classes: dict[str, ClassDef] = {}
        self.steps: dict[str, StepDef] = {}
        self.flows: dict[str, FlowDef] = {}
        self.helpers = set(mod.helpers)
        self.constants = set(mod.constants)
        self.calls: dict[str, set[str]] = {}  # flow -> local flows it calls
        self.filename = mod.filename
        self.cur: FlowDef | None = None
        self.loop_depth = 0

    # -- helpers
    def err(self, line: int, col: int, msg: str, code: str = "E200", severity: str = "error",
            end_col: int | None = None) -> None:
        self.diags.append(Diagnostic(line, col, msg, severity, code, end_col))

    def warn(self, line: int, col: int, msg: str, code: str = "E214") -> None:
        self.err(line, col, msg, code, "warning")

    def norm(self, t: TypeRef) -> TypeRef:
        """`alias.Class` stays only when it names a class of an imported workflow; otherwise Any."""
        if "." in t.name:
            alias, _, cname = t.name.rpartition(".")
            prog = self.imports.get(alias)
            if not (prog and cname in prog.module.classes_by_name):
                return TypeRef("any", t.array, t.nullable)
        return t

    def class_def(self, t: TypeRef | None) -> ClassDef | None:
        if t is None or t.array:
            return None
        if t.name in self.classes:
            return self.classes[t.name]
        if "." in t.name:
            alias, _, cname = t.name.rpartition(".")
            prog = self.imports.get(alias)
            if prog and cname in prog.module.classes_by_name:
                return prog.module.classes_by_name[cname]
        return None

    @staticmethod
    def qualify(t: TypeRef, alias: str | None, classes: dict[str, ClassDef]) -> TypeRef:
        """A class named inside another workflow, seen from this one."""
        if alias and t.name in classes:
            return TypeRef(f"{alias}.{t.name}", t.array, t.nullable)
        return t

    @staticmethod
    def compatible(target: TypeRef | None, value: TypeRef | None) -> bool:
        if target is None or value is None or target.is_any or value.is_any:
            return True
        if value == T_NONE:
            return target.nullable or target == T_NONE
        if target.array or value.array:
            if not (target.array and value.array):
                return False
            return target.name == "any" or value.name == "any" or Checker.compatible(target.element(),
                                                                                     value.element())
        tn, vn = target.name, value.name
        if tn == vn:
            return True
        if tn == "float" and vn == "int":
            return True
        if tn == "dict" or vn == "dict":
            return tn not in ("str", "int", "float", "bool", "none") and vn not in ("str", "int", "float", "bool",
                                                                                    "none")
        return False

    # -- top level
    def run(self) -> dict[str, FlowIR]:
        names: dict[str, int] = {}
        for kind, items in (("classes", self.mod.classes), ("steps", self.mod.steps), ("flows", self.mod.flows)):
            for it in items:
                if it.name in names:
                    self.err(it.line, 1, f"`{it.name}` is already defined on line {names[it.name]}", "E204")
                    continue
                names[it.name] = it.line
                getattr(self, kind)[it.name] = it
        for imp in self.mod.imports:
            if not imp.is_from and imp.bound in names:
                self.err(imp.line, 1, f"import name `{imp.bound}` clashes with a definition on line "
                                      f"{names[imp.bound]}", "E204")

        for c in self.mod.classes:
            for f in c.fields:
                f.type = self.norm(f.type)
        for s in self.mod.steps:
            self._norm_sig(s)
        for f in self.mod.flows:
            self._norm_sig(f)
            for p in f.params:
                if p.name in RESERVED:
                    self.err(p.line, 1, f"`{p.name}` is reserved and cannot be a flow input", "E201")
            if f.outputs is None:
                f.outputs = self.infer_outputs(f)

        self.check_json_words()
        irs: dict[str, FlowIR] = {}
        for f in self.mod.flows:
            irs[f.name] = self.check_flow(f)
        self.check_cycles()
        return irs

    def _norm_sig(self, d: StepDef | FlowDef) -> None:
        for p in d.params:
            p.type = self.norm(p.type)
        for p in d.outputs or []:
            p.type = self.norm(p.type)

    def infer_outputs(self, f: FlowDef) -> list[Param]:
        """A flow without `->` gets its outputs from its first `return` with a value."""
        for node in ast.walk(f.node):
            if isinstance(node, ast.Return) and node.value is not None:
                v = node.value
                if isinstance(v, ast.Constant) and v.value is None:
                    continue
                if isinstance(v, ast.Dict) and v.keys and all(
                        isinstance(k, ast.Constant) and isinstance(k.value, str) and k.value.isidentifier()
                        for k in v.keys):
                    return [Param(k.value, ANY, node.lineno) for k in v.keys]  # type: ignore[union-attr]
                if isinstance(v, ast.Tuple):
                    return [Param(f"result{i + 1}", ANY, node.lineno) for i in range(len(v.elts))]
                return [Param("result", ANY, node.lineno)]
        return []

    def check_json_words(self) -> None:
        """Flag `true` / `false` / `null` used as values outside flows, unless the file defines them."""
        tree = self.mod.tree
        if tree is None:
            return
        defined = set(COCO_API)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
                defined.add(node.id)
            elif isinstance(node, ast.arg):
                defined.add(node.arg)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    defined.add(a.asname or a.name.split(".")[0])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
        flow_nodes = {id(f.node) for f in self.mod.flows}
        for top in tree.body:
            if id(top) in flow_nodes:
                continue  # flows report these themselves
            for node in ast.walk(top):
                if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in JSON_WORDS
                        and node.id not in defined):
                    col = node.col_offset + 1
                    self.err(node.lineno, col, json_word_message(node.id), "E235", end_col=col + len(node.id))

    def check_cycles(self) -> None:
        state: dict[str, int] = {}

        def visit(f: str, stack: list[str]) -> None:
            state[f] = 1
            for g in sorted(self.calls.get(f, ())):
                if state.get(g) == 1:
                    cyc = stack[stack.index(g):] + [g] if g in stack else [f, g]
                    self.err(self.flows[f].line, 1, "recursive flow call: " + " -> ".join(cyc), "E207")
                elif g not in state:
                    visit(g, stack + [g])
            state[f] = 2

        for f in self.calls:
            if f not in state:
                visit(f, [f])

    # -- flows
    def check_flow(self, f: FlowDef) -> FlowIR:
        self.cur = f
        self.scope: dict[str, TypeRef] = {p.name: p.type for p in f.params}
        self.decls: dict[str, TypeRef] = {}
        self.counters = {"if": 0, "for": 0}
        self.loop_depth = 0
        self.calls.setdefault(f.name, set())
        body = self.block(f.node.body, top=True)
        if f.outputs and not self.terminates(f.node.body):
            self.err(f.line, 1, f"flow `{f.name}` has outputs ({', '.join(p.name for p in f.outputs)}) "
                                f"but not every path ends in `return`", "E208")
        return FlowIR(f.name, f.params, f.outputs or [], dict(self.decls), body)

    @staticmethod
    def col(node: ast.AST) -> int:
        return getattr(node, "col_offset", 0) + 1

    def compile_expr(self, node: ast.expr) -> CodeType:
        expr = ast.Expression(body=node)
        ast.fix_missing_locations(expr)
        return compile(expr, self.filename, "eval")

    def terminates(self, stmts: list[ast.stmt]) -> bool:
        if not stmts:
            return False
        last = stmts[-1]
        if isinstance(last, ast.Return):
            return True
        if isinstance(last, ast.If):
            return bool(last.orelse) and self.terminates(last.body) and self.terminates(last.orelse)
        return False

    def block(self, stmts: list[ast.stmt], top: bool = False) -> list:
        out = []
        for i, st in enumerate(stmts):
            if top and i == 0 and isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant) \
                    and isinstance(st.value.value, str):
                continue  # docstring
            ir = self.stmt(st)
            if ir is not None:
                out.append(ir)
        return out

    def resolve_callee(self, func: ast.expr) -> Callee | None:
        if isinstance(func, ast.Name) and func.id not in self.scope:
            if func.id in self.steps:
                s = self.steps[func.id]
                return Callee("step", s.name, s.params, s.outputs, None)
            if func.id in self.flows:
                fl = self.flows[func.id]
                return Callee("flow", fl.name, fl.params, fl.outputs, None)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id not in self.scope:
            prog = self.imports.get(func.value.id)
            if prog:
                alias = func.value.id
                m = prog.module
                d: StepDef | FlowDef | None = m.steps_by_name.get(func.attr) or m.flows_by_name.get(func.attr)
                if d is not None:
                    kind = "step" if func.attr in m.steps_by_name else "flow"
                    cls = m.classes_by_name
                    params = [self._q(p, alias, cls) for p in d.params]
                    outs = None if d.outputs is None else [self._q(p, alias, cls) for p in d.outputs]
                    return Callee(kind, d.name, params, outs, alias)
        return None

    def _q(self, p: Param, alias: str, classes: dict[str, ClassDef]) -> Param:
        return Param(p.name, self.qualify(p.type, alias, classes), p.line, p.has_default, p.default)

    @staticmethod
    def _callee_name(func: ast.expr) -> str:
        try:
            return ast.unparse(func)
        except Exception:  # pragma: no cover
            return "?"

    def _not_callable_msg(self, func: ast.expr) -> str:
        name = self._callee_name(func)
        if isinstance(func, ast.Name) and func.id in self.helpers:
            return f"`{name}` is a plain function; add `@step` to call it from a flow"
        if isinstance(func, ast.Name) and func.id in self.classes:
            return f"class `{name}` can only be created inside a step"
        return f"`{name}` is not a step or flow; flows can only call steps, flows and logging functions"

    def declare(self, name: str, t: TypeRef, line: int, col: int) -> None:
        if name in RESERVED:
            self.err(line, col, f"`{name}` is reserved and cannot be assigned in a flow", "E201")
            return
        if name in self.scope:
            if not self.compatible(self.scope[name], t):
                self.warn(line, col, f"`{name}` is {self.scope[name]}, but is assigned {t}")
            return
        self.scope[name] = t
        self.decls[name] = t

    def stmt(self, st: ast.stmt):
        line = st.lineno
        if isinstance(st, ast.Pass):
            return None

        if isinstance(st, ast.Break) or isinstance(st, ast.Continue):
            if self.loop_depth == 0:
                self.err(line, self.col(st), f"`{'break' if isinstance(st, ast.Break) else 'continue'}` "
                                             f"outside a loop", "E215")
                return None
            return BreakIR(line) if isinstance(st, ast.Break) else ContinueIR(line)

        if isinstance(st, ast.AnnAssign):
            if not isinstance(st.target, ast.Name):
                self.err(line, self.col(st), "can only declare a variable", "E211")
                return None
            from .parser import _Hints
            t = self.norm(_Hints(set(self.classes), set(self.imports))(st.annotation))
            name = st.target.id
            if name in RESERVED:
                self.err(line, self.col(st), f"`{name}` is reserved and cannot be declared", "E201")
                return None
            if name in self.scope:
                self.err(line, self.col(st), f"`{name}` is already declared", "E202")
            self.scope[name] = t
            self.decls[name] = t
            if st.value is None:
                return DeclIR([(name, t)], line)
            if isinstance(st.value, ast.Call) and self.resolve_callee(st.value.func):
                callee = self.resolve_callee(st.value.func)
                return self.call(st.value, callee, [name], line)  # type: ignore[arg-type]
            vt = self.expr(st.value)
            if not self.compatible(t, vt):
                self.warn(line, self.col(st), f"cannot assign {vt} to `{name}` of type {t}")
            return DeclIR([(name, t)], line, self.compile_expr(st.value))

        if isinstance(st, ast.Expr):
            if isinstance(st.value, ast.Constant) and isinstance(st.value.value, str):
                return None  # a bare string is a comment
            if isinstance(st.value, ast.Call):
                callee = self.resolve_callee(st.value.func)
                if callee:
                    return self.call(st.value, callee, [], line)
                f = st.value.func
                if isinstance(f, ast.Name) and f.id in LOG_FUNCS and f.id not in self.scope:
                    return self.log_call(st.value, f.id, line)
                if self._is_method_call(f):
                    self.expr(st.value)
                    return ExprIR(self.compile_expr(st.value), line)
                self.err(line, self.col(st), self._not_callable_msg(f), "E209")
                return None
            self.err(line, self.col(st), "this expression does nothing; flows can only call steps and flows", "E210")
            return None

        if isinstance(st, (ast.Assign, ast.AugAssign)):
            if isinstance(st, ast.Assign):
                if len(st.targets) != 1:
                    self.err(line, self.col(st), "chained assignment is not supported", "E211")
                    return None
                tnode, value = st.targets[0], st.value
            else:
                tnode = st.target
                if not isinstance(tnode, ast.Name):
                    self.err(line, self.col(st), "can only assign to a variable", "E211")
                    return None
                if tnode.id not in self.scope:
                    self.err(line, self.col(st), f"`{tnode.id}` is not defined yet", "E212")
                    return None
                value = ast.copy_location(ast.BinOp(left=ast.Name(tnode.id, ast.Load()), op=st.op,
                                                    right=st.value), st.value)
            if isinstance(tnode, ast.Name):
                targets, is_tuple = [tnode.id], False
            elif isinstance(tnode, (ast.Tuple, ast.List)) and all(isinstance(e, ast.Name) for e in tnode.elts):
                targets, is_tuple = [e.id for e in tnode.elts], True  # type: ignore[attr-defined]
            else:
                self.err(line, self.col(st), "can only assign to a variable or a tuple of variables", "E211")
                return None
            for t in targets:
                if t in RESERVED:
                    self.err(line, self.col(st), f"`{t}` is reserved and cannot be assigned in a flow", "E201")
                    return None
            if isinstance(value, ast.Call):
                callee = self.resolve_callee(value.func)
                if callee:
                    return self.call(value, callee, targets, line)
            if is_tuple:
                self.err(line, self.col(st), "tuple assignment is only allowed from a step or flow call", "E211")
                return None
            vt = self.expr(value)
            self.declare(targets[0], vt, line, self.col(st))
            return AssignIR(targets[0], self.scope.get(targets[0], ANY), self.compile_expr(value), line)

        if isinstance(st, ast.If):
            ident = f"if#{self.counters['if']}"
            self.counters["if"] += 1
            branches = []
            node: ast.stmt | None = st
            arm = 0
            while node is not None:
                self.expr(node.test)  # type: ignore[attr-defined]
                label = "then" if arm == 0 else f"elif{arm}"
                branches.append((label, self.compile_expr(node.test), self.block(node.body)))  # type: ignore
                arm += 1
                orelse = node.orelse  # type: ignore[attr-defined]
                if len(orelse) == 1 and isinstance(orelse[0], ast.If):
                    node = orelse[0]
                else:
                    if orelse:
                        branches.append(("else", None, self.block(orelse)))
                    node = None
            return IfIR(ident, branches, line)

        if isinstance(st, ast.For):
            ident = f"for#{self.counters['for']}"
            self.counters["for"] += 1
            if st.orelse:
                self.err(line, self.col(st), "`for ... else` is not supported", "E215")
            if isinstance(st.target, ast.Name):
                var: str | list[str] = st.target.id
                names = [st.target.id]
            elif isinstance(st.target, (ast.Tuple, ast.List)) and all(isinstance(e, ast.Name)
                                                                      for e in st.target.elts):
                names = [e.id for e in st.target.elts]  # type: ignore[attr-defined]
                var = names
            else:
                self.err(line, self.col(st), "loop variable must be a name or a tuple of names", "E215")
                return None
            it = st.iter
            range_args = iterable = None
            if isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "range" \
                    and "range" not in self.scope:
                if not 1 <= len(it.args) <= 3 or it.keywords:
                    self.err(line, self.col(it), "range() takes 1 to 3 positional arguments", "E216")
                for a in it.args:
                    at = self.expr(a)
                    if not self.compatible(T_INT, at):
                        self.warn(line, self.col(a), f"range() arguments should be int, got {at}")
                range_args = [self.compile_expr(a) for a in it.args]
                var_t = T_INT
            else:
                itt = self.expr(it)
                if itt.name in ("int", "float", "bool", "none") and not itt.array:
                    self.warn(line, self.col(it), f"cannot loop over a value of type {itt}", "E216")
                var_t = itt.element() if itt.array else (T_STR if itt == T_STR else ANY)
                iterable = self.compile_expr(it)
            for n in names:
                self.declare(n, var_t if len(names) == 1 else ANY, line, self.col(st))
            self.loop_depth += 1
            body = self.block(st.body)
            self.loop_depth -= 1
            return ForIR(ident, var, range_args, iterable, body, line)

        if isinstance(st, ast.Return):
            return self.ret(st)

        kind = type(st).__name__
        self.err(line, self.col(st), f"`{kind}` is not allowed in a flow; flows may assign variables, call steps "
                                     f"and flows, log, use if/for, and return. Put other code in a @step",
                 "E218")
        return None

    def ret(self, st: ast.Return) -> ReturnIR:
        line = st.lineno
        outs = self.cur.outputs or []  # type: ignore[union-attr]
        v = st.value
        if v is None or (isinstance(v, ast.Constant) and v.value is None and not outs):
            values: list[ast.expr] = []
        elif isinstance(v, ast.Dict) and len(outs) >= 1 and v.keys and all(
                isinstance(k, ast.Constant) and isinstance(k.value, str) for k in v.keys) \
                and {k.value for k in v.keys} == {o.name for o in outs}:  # type: ignore[union-attr]
            by_key = {k.value: val for k, val in zip(v.keys, v.values)}  # type: ignore[union-attr]
            values = [by_key[o.name] for o in outs]
        elif isinstance(v, ast.Dict) and len(outs) > 1:
            self.err(line, self.col(st), f"return keys must be the flow's outputs: "
                                         f"{', '.join(o.name for o in outs)}", "E217")
            return ReturnIR([], line)
        elif isinstance(v, ast.Tuple) and len(outs) != 1:
            values = list(v.elts)
        else:
            values = [v]
        if len(values) != len(outs):
            self.err(line, self.col(st), f"flow `{self.cur.name}` returns {len(outs)} value(s), "  # type: ignore
                                         f"but this `return` gives {len(values)}", "E217")
        for val, o in zip(values, outs):
            vt = self.expr(val)
            if not self.compatible(o.type, vt):
                self.warn(line, self.col(val), f"output `{o.name}` is {o.type}, got {vt}")
        return ReturnIR([(o.name, self.compile_expr(val)) for val, o in zip(values, outs)], line)

    def log_call(self, node: ast.Call, func: str, line: int) -> LogIR:
        if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
            self.err(line, self.col(node), "*args and **kwargs are not allowed in flow calls", "E219")
        for a in node.args:
            if not isinstance(a, ast.Starred):
                self.expr(a)
        for k in node.keywords:
            self.expr(k.value)
        return LogIR(func, [self.compile_expr(a) for a in node.args if not isinstance(a, ast.Starred)],
                     {k.arg: self.compile_expr(k.value) for k in node.keywords if k.arg}, line)

    def call(self, node: ast.Call, callee: Callee, targets: list[str], line: int) -> CallIR:
        if self.cur and callee.kind == "flow" and callee.package is None:
            self.calls.setdefault(self.cur.name, set()).add(callee.name)
        if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
            self.err(line, self.col(node), "*args and **kwargs are not allowed in flow calls", "E219")
        pnames = [p.name for p in callee.params]
        if len(node.args) > len(callee.params):
            self.err(line, self.col(node), f"{callee.kind} `{callee.label}` takes {len(callee.params)} "
                                           f"argument(s) ({', '.join(pnames)}), but {len(node.args)} positional "
                                           f"were given", "E220")
        for a, p in zip(node.args, callee.params):
            at = self.expr(a)
            if not self.compatible(p.type, at):
                self.warn(line, self.col(a), f"argument `{p.name}` of `{callee.label}` is {p.type}, got {at}")
        given = set(pnames[: len(node.args)])
        for k in node.keywords:
            if k.arg is None:
                continue
            if k.arg not in pnames:
                self.err(line, self.col(k.value), f"`{callee.label}` has no input named `{k.arg}`", "E220")
            elif k.arg in given:
                self.err(line, self.col(k.value), f"input `{k.arg}` is given twice", "E220")
            else:
                p = callee.params[pnames.index(k.arg)]
                at = self.expr(k.value)
                if not self.compatible(p.type, at):
                    self.warn(line, self.col(k.value), f"argument `{p.name}` of `{callee.label}` is {p.type}, "
                                                       f"got {at}")
            given.add(k.arg)
        missing = [p.name for p in callee.params if not p.has_default and p.name not in given]
        if missing:
            self.err(line, self.col(node), f"{callee.kind} `{callee.label}` is missing input(s): "
                                           f"{', '.join(missing)}", "E220")

        outs = callee.outputs
        if targets:
            if outs is None:
                for t in targets:
                    self.declare(t, ANY, line, self.col(node))
            elif len(outs) == 0:
                self.err(line, self.col(node), f"{callee.kind} `{callee.label}` returns nothing", "E221")
            elif len(targets) == 1:
                if len(outs) == 1:
                    t_out = outs[0].type
                else:
                    t_out = T_LIST if is_tuple_outputs(outs) else T_DICT
                self.declare(targets[0], t_out, line, self.col(node))
            elif len(targets) != len(outs):
                self.err(line, self.col(node), f"unpacking {len(targets)} values, but {callee.kind} "
                                               f"`{callee.label}` returns {len(outs)} "
                                               f"({', '.join(o.name for o in outs)})", "E221")
            else:
                for t, o in zip(targets, outs):
                    self.declare(t, o.type, line, self.col(node))
        args = [self.compile_expr(a) for a in node.args if not isinstance(a, ast.Starred)]
        kwargs = {k.arg: self.compile_expr(k.value) for k in node.keywords if k.arg}
        return CallIR(callee, args, kwargs, targets, line)

    # -- expressions
    def expr(self, node: ast.expr) -> TypeRef:
        line = getattr(node, "lineno", self.cur.line if self.cur else 1)
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool):
                return T_BOOL
            if isinstance(v, int):
                return T_INT
            if isinstance(v, float):
                return T_FLOAT
            if isinstance(v, str):
                return T_STR
            if v is None:
                return T_NONE
            self.err(line, self.col(node), f"literal {v!r} is not supported", "E222")
            return ANY
        if isinstance(node, ast.Name):
            n = node.id
            if n in self.scope:
                return self.scope[n]
            if n in ("env", "context"):
                return ANY
            if n in self.constants:
                return ANY
            if n in BUILTINS:
                self.err(line, self.col(node), f"builtin `{n}` can only be called", "E223")
                return ANY
            if n in JSON_WORDS:
                c = self.col(node)
                self.err(line, c, json_word_message(n), "E235", end_col=c + len(n))
                return ANY
            if n in self.steps or n in self.flows:
                self.err(line, self.col(node), f"`{n}` must be called as its own statement, "
                                               f"e.g. `x = {n}(...)`", "E226")
                return ANY
            if n in COCO_API:
                self.err(line, self.col(node), f"`{n}` cannot be used as a value in a flow", "E201")
                return ANY
            self.err(line, self.col(node), f"`{n}` is not defined", "E212")
            return ANY
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                self.err(line, self.col(node), f"attribute `{node.attr}` is not accessible in flows", "E224")
                return ANY
            bt = self.expr(node.value)
            cd = self.class_def(bt)
            if cd is not None:
                for f in cd.fields:
                    if f.name == node.attr:
                        alias = bt.name.rpartition(".")[0] if "." in bt.name else None
                        if alias:
                            return self.qualify(f.type, alias, self.imports[alias].module.classes_by_name)
                        return f.type
                self.warn(line, self.col(node), f"class `{cd.name}` has no field `{node.attr}`", "E225")
            return ANY
        if isinstance(node, ast.Subscript):
            bt = self.expr(node.value)
            if isinstance(node.slice, ast.Slice):
                for part in (node.slice.lower, node.slice.upper, node.slice.step):
                    if part is not None:
                        self.expr(part)
                return bt
            self.expr(node.slice)
            if bt.array:
                return bt.element()
            if bt == T_STR:
                return T_STR
            return ANY
        if isinstance(node, ast.BinOp):
            lt, rt = self.expr(node.left), self.expr(node.right)
            if isinstance(node.op, ast.Add) and T_STR in (lt, rt):
                return T_STR
            if isinstance(node.op, ast.Div) and lt.name in ("int", "float") and rt.name in ("int", "float"):
                return T_FLOAT
            if lt in (T_INT, T_FLOAT) and rt in (T_INT, T_FLOAT):
                return T_FLOAT if T_FLOAT in (lt, rt) else T_INT
            if isinstance(node.op, ast.Add) and lt.array and rt.array:
                return lt
            return ANY
        if isinstance(node, ast.UnaryOp):
            t = self.expr(node.operand)
            return T_BOOL if isinstance(node.op, ast.Not) else t
        if isinstance(node, ast.BoolOp):
            ts = [self.expr(v) for v in node.values]
            return ts[0] if all(t == ts[0] for t in ts) else ANY
        if isinstance(node, ast.Compare):
            self.expr(node.left)
            for c in node.comparators:
                self.expr(c)
            return T_BOOL
        if isinstance(node, ast.IfExp):
            self.expr(node.test)
            a, b = self.expr(node.body), self.expr(node.orelse)
            return a if a == b else ANY
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for e in node.elts:
                self.expr(e)
            return T_LIST
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if k is not None:
                    self.expr(k)
            for v in node.values:
                self.expr(v)
            return T_DICT
        if isinstance(node, ast.JoinedStr):
            for v in node.values:
                if isinstance(v, ast.FormattedValue):
                    self.expr(v.value)
            return T_STR
        if isinstance(node, ast.Call):
            f = node.func
            if (isinstance(f, ast.Attribute) and f.attr == "get" and not node.keywords and 1 <= len(node.args) <= 2
                    and self._is_env(f.value)):
                for a in node.args:
                    self.expr(a)
                return ANY  # env.get("key", default)
            if isinstance(f, ast.Name) and f.id in BUILTINS and f.id not in self.scope:
                for a in node.args:
                    self.expr(a)
                for k in node.keywords:
                    self.expr(k.value)
                return BUILTINS[f.id]
            callee = self.resolve_callee(f)
            if callee is None and self._is_method_call(f):
                self.expr(f.value)  # type: ignore[attr-defined]
                for a in node.args:
                    self.expr(a.value if isinstance(a, ast.Starred) else a)
                for k in node.keywords:
                    self.expr(k.value)
                return ANY  # a method of a value: text.upper(), items.get("k")
            if callee:
                self.err(line, self.col(node), f"{callee.kind} `{callee.label}` must be called as its own "
                                               f"statement, e.g. `x = {callee.label}(...)`", "E226")
            elif isinstance(f, ast.Name) and (f.id in self.helpers or f.id in self.classes):
                self.err(line, self.col(node), self._not_callable_msg(f), "E227")
            else:
                self.err(line, self.col(node), f"`{self._callee_name(f)}` cannot be called in a flow "
                                               f"expression; allowed: {', '.join(sorted(BUILTINS))}", "E227")
            return ANY
        self.err(line, self.col(node), f"`{type(node).__name__}` is not allowed in flow expressions; "
                                       f"do this in a @step", "E228")
        return ANY

    def _is_method_call(self, func: ast.expr) -> bool:
        """`value.method(...)` where value is data (a variable, literal or expression), not a module,
        workflow, class or helper."""
        if not isinstance(func, ast.Attribute) or func.attr.startswith("_"):
            return False
        root = func.value
        while isinstance(root, (ast.Attribute, ast.Subscript, ast.Call)):
            root = root.func if isinstance(root, ast.Call) else root.value
        if isinstance(root, ast.Name):
            n = root.id
            if n in self.scope or n in self.constants or n == "env":
                return True
            return False
        return isinstance(root, (ast.Constant, ast.List, ast.Dict, ast.Tuple, ast.JoinedStr, ast.Set))

    @staticmethod
    def _is_env(node: ast.expr) -> bool:
        """`env`, `env.a.b` or `env["a"]`: something read from the run environment."""
        while isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        return isinstance(node, ast.Name) and node.id == "env"


def find_python_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False
