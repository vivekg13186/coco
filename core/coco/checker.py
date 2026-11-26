"""Resolver + checker. Validates a parsed Package and builds the flow IR the runtime executes."""
from __future__ import annotations

import ast
import importlib.util
from dataclasses import dataclass, field
from types import CodeType
from typing import TYPE_CHECKING, Any

from .diagnostics import Diagnostic
from .model import PRIMITIVES, ClassDef, FlowDef, Package, Param, StepDef, TypeRef
from .parser import DECL_MARKER

if TYPE_CHECKING:  # pragma: no cover
    from .program import Program

T_INT, T_FLOAT, T_STR, T_BOOL = TypeRef("int"), TypeRef("float"), TypeRef("string"), TypeRef("boolean")
T_OBJ, T_ARR = TypeRef("object"), TypeRef("array")

# builtins usable in flow expressions -> result type (None = unknown)
BUILTINS: dict[str, TypeRef | None] = {
    "len": T_INT, "min": None, "max": None, "abs": None, "round": None, "sum": None,
    "str": T_STR, "int": T_INT, "float": T_FLOAT, "bool": T_BOOL,
    "sorted": T_ARR, "list": T_ARR,
}
RESERVED = {"self", "env", DECL_MARKER}
# JSON spellings that are easy to paste into Python code by mistake
JSON_WORDS = {"true": "True", "false": "False", "null": "None"}


def json_word_message(word: str) -> str:
    return f"`{word}` is JSON, not Python: use `{JSON_WORDS[word]}`"  # `env` is the run environment, read-only


# ---------------------------------------------------------------- flow IR
@dataclass
class Callee:
    kind: str                 # "step" | "flow"
    name: str
    params: list[Param]
    outputs: list[Param]
    package: str | None       # import alias for another Coco package, None = this package

    @property
    def label(self) -> str:
        return f"{self.package}.{self.name}" if self.package else self.name


@dataclass
class DeclIR:
    names: list[tuple[str, TypeRef]]
    line: int


@dataclass
class CallIR:
    callee: Callee
    args: list[CodeType]
    kwargs: dict[str, CodeType]
    targets: list[str]
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
    var: str
    range_args: list[CodeType] | None
    iterable: CodeType | None
    body: list
    line: int


@dataclass
class ReturnIR:
    values: list[CodeType]
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
    def __init__(self, pkg: Package, coco_imports: dict[str, "Program"], diags: list[Diagnostic]):
        self.pkg = pkg
        self.imports = coco_imports
        self.diags = diags
        self.classes: dict[str, ClassDef] = {}
        self.steps: dict[str, StepDef] = {}
        self.flows: dict[str, FlowDef] = {}
        self.calls: dict[str, set[str]] = {}  # flow -> local flows it calls
        self.filename = pkg.filename

    # -- helpers
    def err(self, line: int, col: int, msg: str, code: str = "E200", severity: str = "error",
            end_col: int | None = None) -> None:
        self.diags.append(Diagnostic(line, col, msg, severity, code, end_col))

    def type_known(self, t: TypeRef) -> bool:
        if t.name in PRIMITIVES or t.name in self.classes:
            return True
        if "." in t.name:
            alias, _, cname = t.name.rpartition(".")
            prog = self.imports.get(alias)
            return bool(prog and cname in prog.classes)
        return False

    def class_def(self, t: TypeRef | None) -> ClassDef | None:
        if t is None or t.array:
            return None
        if t.name in self.classes:
            return self.classes[t.name]
        if "." in t.name:
            alias, _, cname = t.name.rpartition(".")
            prog = self.imports.get(alias)
            if prog and cname in prog.package.classes_by_name:
                return prog.package.classes_by_name[cname]
        return None

    def qualify(self, t: TypeRef, alias: str | None) -> TypeRef:
        """A type named inside another package, seen from this one."""
        if alias and t.name not in PRIMITIVES and "." not in t.name:
            return TypeRef(f"{alias}.{t.name}", t.array)
        return t

    @staticmethod
    def compatible(target: TypeRef | None, value: TypeRef | None) -> bool:
        if target is None or value is None:
            return True
        if target == value:
            return True
        if target.array:
            return value.array and (value.name == target.name or target.name in ("object",)) or value == T_ARR
        if target == T_ARR:
            return value.array or value == T_ARR
        if target == T_FLOAT and value == T_INT:
            return True
        if target == T_OBJ and not value.array and value.name not in PRIMITIVES:
            return True
        if value == T_OBJ and target.name not in PRIMITIVES and not target.array:
            return True  # a dict can be validated into a class
        return False

    def check_types(self, params: list[Param], where: str) -> None:
        seen: set[str] = set()
        for p in params:
            if p.name in RESERVED:
                self.err(p.line, 1, f"`{p.name}` is reserved and cannot be used as a name in {where}", "E201")
            if p.name in seen:
                self.err(p.line, 1, f"`{p.name}` is declared twice in {where}", "E202")
            seen.add(p.name)
            if not self.type_known(p.type):
                self.err(p.line, 1, f"unknown type `{p.type.name}` in {where}", "E203")

    # -- top level
    def run(self) -> dict[str, FlowIR]:
        names: dict[str, int] = {}
        for kind, items in (("class", self.pkg.classes), ("step", self.pkg.steps), ("flow", self.pkg.flows)):
            for it in items:
                if it.name in names:
                    self.err(it.line, 1, f"`{it.name}` is already defined on line {names[it.name]}", "E204")
                    continue
                names[it.name] = it.line
                getattr(self, kind + "es" if kind == "class" else kind + "s")[it.name] = it
        for imp in self.pkg.imports:
            alias = imp.alias or imp.module
            if not imp.is_from and alias in names:
                self.err(imp.line, 1, f"import name `{alias}` clashes with a definition on line {names[alias]}", "E204")

        for c in self.pkg.classes:
            seen: dict[str, int] = {}
            for f in c.fields:
                if f.name in seen:
                    self.err(f.line, 1, f"class `{c.name}`: field `{f.name}` is declared twice "
                                        f"(first on line {seen[f.name]})", "E205")
                seen.setdefault(f.name, f.line)
                if not self.type_known(f.type):
                    self.err(f.line, 1, f"class `{c.name}`: unknown type `{f.type.name}` for field `{f.name}`", "E203")

        for s in self.pkg.steps:
            self.check_types(s.params, f"step `{s.name}` inputs")
            self.check_types(s.outputs, f"step `{s.name}` outputs")
            self.check_step_body(s)

        irs: dict[str, FlowIR] = {}
        for f in self.pkg.flows:
            self.check_types(f.params, f"flow `{f.name}` inputs")
            self.check_types(f.outputs, f"flow `{f.name}` outputs")
            if f.tree is not None:
                irs[f.name] = self.check_flow(f)
        self.check_cycles()
        return irs

    def check_step_body(self, s: StepDef) -> None:
        args = ", ".join(["self"] + [p.name for p in s.params])
        src = f"def {s.name}({args}):\n" + "\n".join("    " + ln if ln else "" for ln in s.body.split("\n"))
        try:
            tree = ast.parse(src, filename=self.filename)
        except SyntaxError as e:
            line = (e.lineno or 2) - 2 + s.body_line
            col = max((e.offset or 1) - 4, 1) + s.body_col
            self.err(line, col, f"Python syntax error in step `{s.name}`: {e.msg}", "E206")
            return
        self.check_json_words(s, tree)

    def check_json_words(self, s: StepDef, tree: ast.Module) -> None:
        """Flag `true` / `false` / `null` used as values in a step unless the code defines them itself."""
        defined = {p.name for p in s.params} | {"self"}
        for imp in self.pkg.imports:
            if imp.is_from:
                defined.update(imp.names)
            else:
                defined.add(imp.alias or imp.module.split(".")[0])
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
        for node in ast.walk(tree):
            if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in JSON_WORDS
                    and node.id not in defined):
                line = node.lineno - 2 + s.body_line
                col = node.col_offset - 4 + s.body_col + 1
                self.err(line, col, json_word_message(node.id), "E235", end_col=col + len(node.id))

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
        self.scope: dict[str, TypeRef | None] = {p.name: p.type for p in f.params}
        self.decls: dict[str, TypeRef] = {}
        self.counters = {"if": 0, "for": 0}
        self.calls.setdefault(f.name, set())
        body = self.block(f.tree.body)
        if f.outputs and not self.terminates(f.tree.body):
            self.err(f.line, 1, f"flow `{f.name}` declares outputs ({', '.join(p.name for p in f.outputs)}) "
                                f"but not every path ends in `return`", "E208")
        return FlowIR(f.name, f.params, f.outputs, dict(self.decls), body)

    def col(self, node: ast.AST) -> int:
        return getattr(node, "col_offset", 0) + self.cur.body_col + 1

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

    def block(self, stmts: list[ast.stmt]) -> list:
        out = []
        for st in stmts:
            ir = self.stmt(st)
            if ir is not None:
                out.append(ir)
        return out

    def resolve_callee(self, func: ast.expr) -> Callee | None:
        if isinstance(func, ast.Name):
            if func.id in self.steps:
                s = self.steps[func.id]
                return Callee("step", s.name, s.params, s.outputs, None)
            if func.id in self.flows:
                fl = self.flows[func.id]
                return Callee("flow", fl.name, fl.params, fl.outputs, None)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            prog = self.imports.get(func.value.id)
            if prog:
                alias = func.value.id
                if func.attr in prog.package.steps_by_name:
                    s = prog.package.steps_by_name[func.attr]
                    return Callee("step", s.name, [self._q(p, alias) for p in s.params],
                                  [self._q(p, alias) for p in s.outputs], alias)
                if func.attr in prog.package.flows_by_name:
                    fl = prog.package.flows_by_name[func.attr]
                    return Callee("flow", fl.name, [self._q(p, alias) for p in fl.params],
                                  [self._q(p, alias) for p in fl.outputs], alias)
        return None

    def _q(self, p: Param, alias: str) -> Param:
        return Param(self.qualify(p.type, alias), p.name, p.line)

    @staticmethod
    def _callee_name(func: ast.expr) -> str:
        try:
            return ast.unparse(func)
        except Exception:  # pragma: no cover
            return "?"

    def stmt(self, st: ast.stmt):
        line = st.lineno
        # declaration marker
        if (isinstance(st, ast.Expr) and isinstance(st.value, ast.Call) and isinstance(st.value.func, ast.Name)
                and st.value.func.id == DECL_MARKER):
            args = [a.value for a in st.value.args]
            tname, names = args[0], args[1:]
            out = []
            for raw in names:
                arr = raw.endswith("[]")
                name = raw[:-2] if arr else raw
                t = TypeRef(tname, arr)
                if name in RESERVED:
                    self.err(line, self.col(st), f"`{name}` is reserved and cannot be declared", "E201")
                elif name in self.scope:
                    self.err(line, self.col(st), f"`{name}` is already declared", "E202")
                if not self.type_known(t):
                    self.err(line, self.col(st), f"unknown type `{tname}`", "E203")
                self.scope[name] = t
                self.decls[name] = t
                out.append((name, t))
            return DeclIR(out, line)

        if isinstance(st, ast.Pass):
            return None

        if isinstance(st, ast.Expr):
            if isinstance(st.value, ast.Call):
                callee = self.resolve_callee(st.value.func)
                if callee:
                    return self.call(st.value, callee, [], line)
                self.err(line, self.col(st), f"`{self._callee_name(st.value.func)}` is not a step or flow; "
                                             f"flows can only call steps and flows", "E209")
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
                value = ast.copy_location(ast.BinOp(left=ast.Name(tnode.id, ast.Load()), op=st.op,
                                                    right=st.value), st.value)
            targets: list[str] = []
            if isinstance(tnode, ast.Name):
                targets = [tnode.id]
                is_tuple = False
            elif isinstance(tnode, (ast.Tuple, ast.List)) and all(isinstance(e, ast.Name) for e in tnode.elts):
                targets = [e.id for e in tnode.elts]
                is_tuple = True
            else:
                self.err(line, self.col(st), "can only assign to a variable or a tuple of variables", "E211")
                return None
            for t in targets:
                if t in RESERVED:
                    self.err(line, self.col(st), f"`{t}` is reserved", "E201")
                elif t not in self.scope:
                    self.err(line, self.col(st), f"`{t}` is not declared; declare it first, e.g. `int {t}`", "E212")
            if isinstance(value, ast.Call):
                callee = self.resolve_callee(value.func)
                if callee:
                    if not is_tuple and len(callee.outputs) > 1:
                        self.err(line, self.col(st), f"{callee.kind} `{callee.label}` returns "
                                                     f"{len(callee.outputs)} values; unpack them with "
                                                     f"`({', '.join(p.name for p in callee.outputs)}) = ...`", "E213")
                    return self.call(value, callee, targets, line)
            if is_tuple:
                self.err(line, self.col(st), "tuple assignment is only allowed from a step or flow call", "E211")
                return None
            vt = self.expr(value)
            target_t = self.scope.get(targets[0])
            if not self.compatible(target_t, vt):
                self.err(line, self.col(st), f"cannot assign {vt} to `{targets[0]}` of type {target_t}", "E214")
            return AssignIR(targets[0], target_t, self.compile_expr(value), line)

        if isinstance(st, ast.If):
            ident = f"if#{self.counters['if']}"
            self.counters["if"] += 1
            branches = []
            node: ast.stmt | None = st
            arm = 0
            while node is not None:
                self.expr(node.test)
                label = "then" if arm == 0 else f"elif{arm}"
                branches.append((label, self.compile_expr(node.test), self.block(node.body)))
                arm += 1
                orelse = node.orelse
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
            if not isinstance(st.target, ast.Name):
                self.err(line, self.col(st), "loop variable must be a single name", "E215")
                return None
            var = st.target.id
            it = st.iter
            range_args = iterable = None
            if isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "range":
                if not 1 <= len(it.args) <= 3 or it.keywords:
                    self.err(line, self.col(it), "range() takes 1 to 3 positional arguments", "E216")
                for a in it.args:
                    at = self.expr(a)
                    if not self.compatible(T_INT, at):
                        self.err(line, self.col(a), f"range() arguments must be int, got {at}", "E214")
                range_args = [self.compile_expr(a) for a in it.args]
                var_t: TypeRef | None = T_INT
            else:
                itt = self.expr(it)
                if itt is not None and not itt.array and itt not in (T_ARR, T_STR, T_OBJ):
                    self.err(line, self.col(it), f"cannot loop over a value of type {itt}", "E216")
                var_t = itt.element() if itt is not None and itt.array else (T_STR if itt == T_STR else None)
                iterable = self.compile_expr(it)
            if var in RESERVED:
                self.err(line, self.col(st), f"`{var}` is reserved", "E201")
            if var in self.scope:
                if not self.compatible(self.scope[var], var_t):
                    self.err(line, self.col(st), f"loop variable `{var}` is {self.scope[var]}, "
                                                 f"but the loop yields {var_t}", "E214")
            else:
                self.scope[var] = var_t
                if var_t is not None:
                    self.decls[var] = var_t
            return ForIR(ident, var, range_args, iterable, self.block(st.body), line)

        if isinstance(st, ast.Return):
            outs = self.cur.outputs
            if st.value is None:
                values = []
            elif isinstance(st.value, ast.Tuple):
                values = list(st.value.elts)
            else:
                values = [st.value]
            if len(values) != len(outs):
                self.err(line, self.col(st), f"flow `{self.cur.name}` returns {len(outs)} value(s), "
                                             f"but this `return` gives {len(values)}", "E217")
            for v, o in zip(values, outs):
                vt = self.expr(v)
                if not self.compatible(o.type, vt):
                    self.err(line, self.col(v), f"output `{o.name}` is {o.type}, got {vt}", "E214")
            return ReturnIR([self.compile_expr(v) for v in values], line)

        kind = type(st).__name__
        self.err(line, self.col(st), f"`{kind}` is not allowed in a flow; flows may only declare variables, "
                                     f"assign, call steps or flows, use if/for, and return", "E218")
        return None

    def call(self, node: ast.Call, callee: Callee, targets: list[str], line: int) -> CallIR:
        if self.cur and callee.kind == "flow" and callee.package is None:
            self.calls.setdefault(self.cur.name, set()).add(callee.name)
        n_args = len(node.args) + len(node.keywords)
        if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
            self.err(line, self.col(node), "*args and **kwargs are not allowed in flow calls", "E219")
        if n_args != len(callee.params):
            self.err(line, self.col(node), f"{callee.kind} `{callee.label}` takes {len(callee.params)} "
                                           f"argument(s) ({', '.join(p.name for p in callee.params)}), "
                                           f"but {n_args} were given", "E220")
        pnames = [p.name for p in callee.params]
        for a, p in zip(node.args, callee.params):
            at = self.expr(a)
            if not self.compatible(p.type, at):
                self.err(line, self.col(a), f"argument `{p.name}` of `{callee.label}` is {p.type}, got {at}", "E214")
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
                    self.err(line, self.col(k.value), f"argument `{p.name}` of `{callee.label}` is {p.type}, "
                                                      f"got {at}", "E214")
            given.add(k.arg)
        if targets:
            if len(targets) > 1 and len(targets) != len(callee.outputs):
                self.err(line, self.col(node), f"unpacking {len(targets)} values, but {callee.kind} "
                                               f"`{callee.label}` returns {len(callee.outputs)}", "E221")
            elif len(callee.outputs) == 0:
                self.err(line, self.col(node), f"{callee.kind} `{callee.label}` returns nothing", "E221")
            else:
                for t, o in zip(targets, callee.outputs):
                    if t in self.scope and not self.compatible(self.scope[t], o.type):
                        self.err(line, self.col(node), f"`{t}` is {self.scope[t]}, but output `{o.name}` "
                                                       f"of `{callee.label}` is {o.type}", "E214")
        args = [self.compile_expr(a) for a in node.args if not isinstance(a, ast.Starred)]
        kwargs = {k.arg: self.compile_expr(k.value) for k in node.keywords if k.arg}
        return CallIR(callee, args, kwargs, targets, line)

    # -- expressions
    def expr(self, node: ast.expr) -> TypeRef | None:
        line = getattr(node, "lineno", self.cur.line)
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
                return None
            self.err(line, self.col(node), f"literal {v!r} is not supported", "E222")
            return None
        if isinstance(node, ast.Name):
            if node.id == "env":
                return None  # the run environment: any key, any type
            if node.id in RESERVED:
                self.err(line, self.col(node), f"`{node.id}` is not available in flows", "E201")
                return None
            if node.id in self.scope:
                return self.scope[node.id]
            if node.id in BUILTINS:
                self.err(line, self.col(node), f"builtin `{node.id}` can only be called", "E223")
                return None
            if node.id in JSON_WORDS:
                c = self.col(node)
                self.err(line, c, json_word_message(node.id), "E235", end_col=c + len(node.id))
                return None
            self.err(line, self.col(node), f"`{node.id}` is not declared", "E212")
            return None
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                self.err(line, self.col(node), f"attribute `{node.attr}` is not accessible in flows", "E224")
                return None
            bt = self.expr(node.value)
            cd = self.class_def(bt)
            if cd is not None:
                for f in cd.fields:
                    if f.name == node.attr:
                        alias = bt.name.rpartition(".")[0] if "." in bt.name else None
                        return self.qualify(f.type, alias)
                self.err(line, self.col(node), f"class `{cd.name}` has no field `{node.attr}`", "E225")
            return None
        if isinstance(node, ast.Subscript):
            bt = self.expr(node.value)
            self.expr(node.slice) if not isinstance(node.slice, ast.Slice) else self._slice(node.slice)
            if isinstance(node.slice, ast.Slice):
                return bt
            if bt is not None and bt.array:
                return bt.element()
            if bt == T_STR:
                return T_STR
            return None
        if isinstance(node, ast.BinOp):
            lt, rt = self.expr(node.left), self.expr(node.right)
            if isinstance(node.op, ast.Add) and T_STR in (lt, rt):
                return T_STR if lt == rt else (None if None in (lt, rt) else T_STR)
            if isinstance(node.op, ast.Div):
                return T_FLOAT
            if lt in (T_INT, T_FLOAT) and rt in (T_INT, T_FLOAT):
                return T_FLOAT if T_FLOAT in (lt, rt) else T_INT
            if isinstance(node.op, ast.Add) and lt is not None and rt is not None and lt.array and rt.array:
                return lt
            return None
        if isinstance(node, ast.UnaryOp):
            t = self.expr(node.operand)
            return T_BOOL if isinstance(node.op, ast.Not) else t
        if isinstance(node, ast.BoolOp):
            for v in node.values:
                self.expr(v)
            return T_BOOL
        if isinstance(node, ast.Compare):
            self.expr(node.left)
            for c in node.comparators:
                self.expr(c)
            return T_BOOL
        if isinstance(node, ast.IfExp):
            self.expr(node.test)
            a, b = self.expr(node.body), self.expr(node.orelse)
            return a if a == b else None
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for e in node.elts:
                self.expr(e)
            return T_ARR
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if k is not None:
                    self.expr(k)
            for v in node.values:
                self.expr(v)
            return T_OBJ
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
                return None  # env.get("key", default)
            if isinstance(node.func, ast.Name) and node.func.id in BUILTINS and node.func.id not in self.scope:
                for a in node.args:
                    self.expr(a)
                for k in node.keywords:
                    self.expr(k.value)
                return BUILTINS[node.func.id]
            callee = self.resolve_callee(node.func)
            if callee:
                self.err(line, self.col(node), f"{callee.kind} `{callee.label}` must be called as its own "
                                               f"statement, e.g. `x = {callee.label}(...)`", "E226")
            else:
                self.err(line, self.col(node), f"`{self._callee_name(node.func)}` cannot be called in a flow "
                                               f"expression; allowed: {', '.join(sorted(BUILTINS))}", "E227")
            return None
        self.err(line, self.col(node), f"`{type(node).__name__}` is not allowed in flow expressions", "E228")
        return None

    @staticmethod
    def _is_env(node: ast.expr) -> bool:
        """`env`, `env.a.b` or `env["a"]`: something read from the run environment."""
        while isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        return isinstance(node, ast.Name) and node.id == "env"

    def _slice(self, s: ast.Slice) -> None:
        for part in (s.lower, s.upper, s.step):
            if part is not None:
                self.expr(part)


def find_python_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False
