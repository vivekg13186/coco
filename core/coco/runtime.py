"""Flow interpreter: runs the checked flow IR, calls step functions, emits events."""
from __future__ import annotations

import builtins
import copy
import io
import sys
import threading
import time
import traceback
import uuid
from typing import TYPE_CHECKING, Any, Callable

from pydantic import ValidationError

from .checker import (AssignIR, ExprIR, BUILTINS, BreakIR, CallIR, ContinueIR, DeclIR, FlowIR, ForIR, IfIR, LogIR,
                      ReturnIR, is_tuple_outputs)
from . import dsl
from .dsl import _local
from .diagnostics import CocoRuntimeError
from .env import EnvView
from .events import Event, RunResult, now_iso
from .console import format_args, log_arg, table_payload
from .serialize import to_jsonable, type_name

if TYPE_CHECKING:  # pragma: no cover
    from .program import Program

SAFE_BUILTINS = {name: getattr(builtins, name) for name in BUILTINS}
LOG_LEVELS = ("debug", "info", "warning", "error")


# ---------------------------------------------------------------- print() capture


class _StdoutRouter(io.TextIOBase):
    """Sends print() output from inside a step to that step's log; everything else passes through."""

    def __init__(self, orig):
        self.orig = orig

    def write(self, s: str) -> int:
        ctx = getattr(_local, "ctx", None)
        if ctx is None or getattr(_local, "emitting", False):
            return self.orig.write(s)
        ctx._buffer += s
        while "\n" in ctx._buffer:
            line, ctx._buffer = ctx._buffer.split("\n", 1)
            ctx.log(line)
        return len(s)

    def flush(self) -> None:
        self.orig.flush()

    def __getattr__(self, name: str):
        return getattr(self.orig, name)


def _install_print_capture() -> None:
    if not isinstance(sys.stdout, _StdoutRouter):
        sys.stdout = _StdoutRouter(sys.stdout)


# ---------------------------------------------------------------- the running step
class StepContext:
    """Where code is running: what `log`, `env` and `context` use. One per step call (step=None in flows)."""

    def __init__(self, execution: "Execution", flow: str, step: str | None, path: str):
        self._ex = execution
        self._flow = flow
        self._step = step
        self._path = path
        self._buffer = ""

    @property
    def run_id(self) -> str:
        return self._ex.run_id

    @property
    def env(self) -> EnvView:
        """The run environment (read-only): `env.api_url` or `env["api_url"]`."""
        return self._ex.env

    @property
    def flow(self) -> str | None:
        return self._flow

    @property
    def step(self) -> str | None:
        return self._step

    @property
    def path(self) -> str:
        return self._path

    # -- console-style logging: any number of values of any type, like console.log
    def log(self, *values: Any, level: str = "info") -> None:
        """Log any values: `log("total", n, {"a": 1}, True)`. Objects keep their structure."""
        if level not in LOG_LEVELS:
            raise ValueError(f"log level must be one of {', '.join(LOG_LEVELS)}")
        args = [log_arg(v) for v in values]
        self._ex.emit("log", self._path, self._step, level=level, message=format_args(args), args=args)

    def debug(self, *values: Any) -> None:
        self.log(*values, level="debug")

    def info(self, *values: Any) -> None:
        self.log(*values, level="info")

    def warn(self, *values: Any) -> None:
        self.log(*values, level="warning")

    warning = warn

    def error(self, *values: Any) -> None:
        self.log(*values, level="error")

    def log_table(self, data: Any, columns: list[str] | None = None) -> None:
        """Log rows as a table, like console.table: a list of objects, lists or values, or a dict of rows.

        `columns` picks and orders the columns. At most 1000 rows are kept.
        """
        table = table_payload(data, columns)
        self._ex.emit("log.table", self._path, self._step, level="info",
                      message=f"table: {table['row_count']} row(s) x {len(table['columns']) - 1} column(s)",
                      **table)

    def _flush(self) -> None:
        if self._buffer:
            line, self._buffer = self._buffer, ""
            self.log(line)

    def __repr__(self) -> str:
        return f"<StepContext {self._path}>"


# ---------------------------------------------------------------- control flow signals
class _Cancelled(Exception):
    def __init__(self, path: str):
        self.path = path


class _Failure(Exception):
    def __init__(self, error: dict[str, Any], path: str):
        super().__init__(error.get("message"))
        self.error = error
        self.path = path


class _Return(Exception):
    def __init__(self, values: dict[str, Any]):
        self.values = values


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


def _err(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **extra}


def _validation_message(e: ValidationError) -> str:
    parts = []
    for item in e.errors()[:5]:
        loc = ".".join(str(x) for x in item.get("loc", ()))
        parts.append(f"{loc + ': ' if loc else ''}{item.get('msg')}")
    return "; ".join(parts)


# ---------------------------------------------------------------- execution
class Execution:
    def __init__(self, program: "Program", run_id: str | None, on_event: Callable[[Event], Any] | None,
                 cancel: threading.Event | None, collect: bool, env: dict[str, Any] | None = None,
                 env_name: str | None = None):
        self.program = program
        if env is not None and not isinstance(env, dict):
            raise TypeError("env must be a JSON object (dict)")
        self.env = EnvView(env or {})
        self.env_name = env_name
        self._globals: dict[int, dict[str, Any]] = {}
        self.run_id = run_id or "r_" + uuid.uuid4().hex[:12]
        self.on_event = on_event
        self.cancel = cancel or threading.Event()
        self.collect = collect
        self.events: list[Event] = []
        self._seq = 0
        self._lock = threading.Lock()

    def globals_for(self, prog: "Program") -> dict[str, Any]:
        g = self._globals.get(id(prog))
        if g is None:
            g = {**prog.constants, "__builtins__": SAFE_BUILTINS, "env": self.env, "context": dsl.context}
            self._globals[id(prog)] = g
        return g

    # -- events
    def emit(self, type_: str, path: str | None = None, step: str | None = None, **payload: Any) -> Event:
        with self._lock:
            self._seq += 1
            ev = Event(self.run_id, self._seq, type_, now_iso(), path, step, payload)
            if self.collect:
                self.events.append(ev)
        if self.on_event is not None:
            prev = getattr(_local, "emitting", False)
            _local.emitting = True
            try:
                self.on_event(ev)
            finally:
                _local.emitting = prev
        return ev

    # -- entry point
    def run(self, flow_name: str, inputs: dict[str, Any] | None) -> RunResult:
        _install_print_capture()
        t0 = time.perf_counter()
        prog = self.program
        started: dict[str, Any] = {"flow": flow_name, "input": to_jsonable(inputs or {})}
        if self.env_name is not None or len(self.env):
            started["env"] = self.env_name  # the name only: values may hold secrets
        self.emit("flow.started", flow_name, None, **started)
        status, output, error = "failed", None, None
        try:
            ir = prog.flow_irs.get(flow_name)
            if ir is None:
                raise _Failure(_err("unknown_flow", f"no flow named `{flow_name}` in workflow "
                                                    f"`{prog.name}`"), flow_name)
            args = self._bind_inputs(prog, ir, inputs or {}, flow_name)
            values = self._exec_flow(prog, ir, args, flow_name, flow_name)
            output = {k: to_jsonable(v) for k, v in values.items()}
            status = "succeeded"
            self.emit("flow.completed", flow_name, None, output=output, duration_ms=self._ms(t0))
        except _Cancelled as c:
            status = "cancelled"
            error = _err("cancelled", "run was cancelled")
            self.emit("flow.cancelled", c.path, None, at_path=c.path, duration_ms=self._ms(t0))
        except _Failure as f:
            error = f.error
            self.emit("flow.failed", f.path, None, error=f.error, failed_path=f.path, duration_ms=self._ms(t0))
        except Exception as e:  # interpreter bug: still report it as a failed run
            error = _err("internal_error", f"{type(e).__name__}: {e}", traceback=traceback.format_exc())
            self.emit("flow.failed", flow_name, None, error=error, failed_path=flow_name,
                      duration_ms=self._ms(t0))
        return RunResult(self.run_id, flow_name, status, output, error, self._ms(t0), self.events)

    @staticmethod
    def _ms(t0: float) -> int:
        return int((time.perf_counter() - t0) * 1000)

    def _bind_inputs(self, prog: "Program", ir: FlowIR, inputs: dict[str, Any], path: str) -> dict[str, Any]:
        if not isinstance(inputs, dict):
            raise _Failure(_err("invalid_input", "flow input must be an object"), path)
        names = [p.name for p in ir.params]
        unknown = [k for k in inputs if k not in names]
        missing = [p.name for p in ir.params if p.name not in inputs and not p.has_default]
        if unknown or missing:
            msg = []
            if missing:
                msg.append("missing input(s): " + ", ".join(missing))
            if unknown:
                msg.append("unknown input(s): " + ", ".join(unknown))
            raise _Failure(_err("invalid_input", "; ".join(msg)), path)
        return self._typed_args(prog, ir, inputs, path, f"flow `{ir.name}`")

    def _typed_args(self, prog: "Program", ir: FlowIR, given: dict[str, Any], path: str,
                    what: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for p in ir.params:
            if p.name in given:
                out[p.name] = self._validate(prog, p.type, given[p.name], f"input `{p.name}` of {what}", path,
                                             "invalid_input")
            else:
                out[p.name] = copy.deepcopy(p.default)
        return out

    def _validate(self, prog: "Program", t, value: Any, what: str, path: str, code: str) -> Any:
        try:
            return prog.adapter(t).validate_python(value)
        except ValidationError as e:
            raise _Failure(_err(code, f"{what} is not a valid {t}: {_validation_message(e)}"), path)

    # -- flows
    def _exec_flow(self, prog: "Program", ir: FlowIR, args: dict[str, Any], path: str,
                   label: str) -> dict[str, Any]:
        frame: dict[str, Any] = dict(args)
        for name in ir.decls:
            frame.setdefault(name, None)
        prev = getattr(_local, "ctx", None)
        _local.ctx = StepContext(self, label, None, path)
        try:
            self._block(prog, ir.body, frame, path)
        except _Return as r:
            return {o.name: self._validate(prog, o.type, r.values.get(o.name), f"output `{o.name}` of flow "
                                           f"`{label}`", path, "invalid_output") for o in ir.outputs}
        finally:
            _local.ctx = prev
        if ir.outputs:
            raise _Failure(_err("no_return", f"flow `{label}` ended without `return`"), path)
        return {}

    def _eval(self, prog: "Program", code, frame: dict[str, Any], path: str, line: int) -> Any:
        try:
            return eval(code, self.globals_for(prog), frame)
        except Exception as e:
            raise _Failure(_err("expression_error", f"line {line}: {type(e).__name__}: {e}", line=line), path)

    def _block(self, prog: "Program", body: list, frame: dict[str, Any], path: str) -> None:
        for st in body:
            if self.cancel.is_set():
                raise _Cancelled(path)
            if isinstance(st, DeclIR):
                if st.value is not None:
                    frame[st.names[0][0]] = self._eval(prog, st.value, frame, path, st.line)
            elif isinstance(st, ExprIR):
                self._eval(prog, st.expr, frame, path, st.line)
            elif isinstance(st, AssignIR):
                frame[st.target] = self._eval(prog, st.expr, frame, path, st.line)
            elif isinstance(st, CallIR):
                self._call(prog, st, frame, path)
            elif isinstance(st, LogIR):
                self._log(prog, st, frame, path)
            elif isinstance(st, IfIR):
                for arm, cond, body in st.branches:
                    if cond is None or self._eval(prog, cond, frame, path, st.line):
                        self.emit("branch.taken", f"{path}/{st.id}", None, arm=arm, line=st.line)
                        self._block(prog, body, frame, f"{path}/{st.id}.{arm}")
                        break
            elif isinstance(st, ForIR):
                self._for(prog, st, frame, path)
            elif isinstance(st, BreakIR):
                raise _Break()
            elif isinstance(st, ContinueIR):
                raise _Continue()
            elif isinstance(st, ReturnIR):
                raise _Return({name: self._eval(prog, v, frame, path, st.line) for name, v in st.values})

    def _for(self, prog: "Program", st: ForIR, frame: dict[str, Any], path: str) -> None:
        if st.range_args is not None:
            rargs = [self._eval(prog, a, frame, path, st.line) for a in st.range_args]
            try:
                items: Any = range(*rargs)
            except TypeError as e:
                raise _Failure(_err("expression_error", f"line {st.line}: range(): {e}"), path)
        else:
            items = self._eval(prog, st.iterable, frame, path, st.line)
        try:
            iterator = iter(items)
        except TypeError as e:
            raise _Failure(_err("expression_error", f"line {st.line}: {e}", line=st.line), path)
        for i, item in enumerate(iterator):
            if self.cancel.is_set():
                raise _Cancelled(f"{path}/{st.id}[{i}]")
            if isinstance(st.var, list):
                try:
                    parts = list(item)
                except TypeError:
                    parts = [item]
                if len(parts) != len(st.var):
                    raise _Failure(_err("expression_error", f"line {st.line}: cannot unpack {len(parts)} "
                                                            f"value(s) into {', '.join(st.var)}", line=st.line), path)
                frame.update(zip(st.var, parts))
            else:
                frame[st.var] = item
            self.emit("loop.iteration", f"{path}/{st.id}", None, index=i, value=to_jsonable(item))
            try:
                self._block(prog, st.body, frame, f"{path}/{st.id}[{i}]")
            except _Continue:
                continue
            except _Break:
                break

    def _log(self, prog: "Program", st: LogIR, frame: dict[str, Any], path: str) -> None:
        args = [self._eval(prog, a, frame, path, st.line) for a in st.args]
        kwargs = {k: self._eval(prog, v, frame, path, st.line) for k, v in st.kwargs.items()}
        ctx = StepContext(self, path.split("/")[0], None, path)
        prev = getattr(_local, "ctx", None)
        _local.ctx = ctx
        try:
            if st.func == "print":
                sep = kwargs.get("sep", " ")
                ctx.log(sep.join(str(a) for a in args))
            else:
                getattr(dsl, st.func)(*args, **kwargs)
        except Exception as e:
            raise _Failure(_err("expression_error", f"line {st.line}: {st.func}(): {type(e).__name__}: {e}",
                                line=st.line), path)
        finally:
            _local.ctx = prev

    def _call(self, prog: "Program", st: CallIR, frame: dict[str, Any], path: str) -> None:
        callee = st.callee
        target_prog = prog.imports[callee.package] if callee.package else prog
        names = [p.name for p in callee.params]
        given = {n: self._eval(prog, a, frame, path, st.line) for n, a in zip(names, st.args)}
        for k, code in st.kwargs.items():
            given[k] = self._eval(prog, code, frame, path, st.line)
        cpath = f"{path}/{callee.label}"
        if callee.kind == "step":
            raw, values = self._call_step(target_prog, callee.name, callee.label, given, cpath, path)
        else:
            values = self._call_subflow(target_prog, callee.name, callee.label, given, cpath)
            raw = None
        if not st.targets:
            return
        outs = callee.outputs
        if outs is None:  # a step without `->`: plain Python assignment of whatever it returned
            if len(st.targets) == 1:
                frame[st.targets[0]] = raw
                return
            try:
                parts = list(raw)
            except TypeError:
                parts = [raw]
            if len(parts) != len(st.targets):
                raise _Failure(_err("invalid_output", f"line {st.line}: `{callee.label}` returned {len(parts)} "
                                                      f"value(s), expected {len(st.targets)}"), cpath)
            frame.update(zip(st.targets, parts))
            return
        if len(st.targets) == 1:
            if len(outs) == 1:
                frame[st.targets[0]] = values[outs[0].name]
            elif is_tuple_outputs(outs):
                frame[st.targets[0]] = [values[o.name] for o in outs]
            else:
                frame[st.targets[0]] = dict(values)
            return
        for t, o in zip(st.targets, outs):
            frame[t] = values[o.name]

    def _call_subflow(self, prog: "Program", name: str, label: str, given: dict[str, Any],
                      path: str) -> dict[str, Any]:
        ir = prog.flow_irs[name]
        t0 = time.perf_counter()
        self.emit("subflow.started", path, None, flow=label, input={k: to_jsonable(v) for k, v in given.items()})
        args = self._typed_args(prog, ir, given, path, f"flow `{label}`")
        values = self._exec_flow(prog, ir, args, path, label)
        self.emit("subflow.completed", path, None, flow=label,
                  output={k: to_jsonable(v) for k, v in values.items()}, duration_ms=self._ms(t0))
        return values

    def _call_step(self, prog: "Program", name: str, label: str, given: dict[str, Any], path: str,
                   flow_path: str) -> tuple[Any, dict[str, Any]]:
        sdef = prog.module.steps_by_name[name]
        func = prog.step_funcs[name]
        flow_name = flow_path.split("/")[0]
        self.emit("step.started", path, label, input={k: to_jsonable(v) for k, v in given.items()})
        t0 = time.perf_counter()

        def fail(error: dict[str, Any]) -> _Failure:
            self.emit("step.failed", path, label, error=error, duration_ms=self._ms(t0))
            return _Failure(error, path)

        kwargs: dict[str, Any] = {}
        for p in sdef.params:
            if p.name not in given:
                continue  # the step's own Python default applies
            try:
                kwargs[p.name] = prog.adapter(p.type).validate_python(given[p.name])
            except ValidationError as e:
                raise fail(_err("invalid_input", f"input `{p.name}` of step `{label}` is not a valid "
                                                 f"{p.type}: {_validation_message(e)}"))
        ctx = StepContext(self, flow_name, label, path)
        prev = getattr(_local, "ctx", None)
        _local.ctx = ctx
        try:
            result = func(**kwargs)
        except Exception as e:
            ctx._flush()
            raise fail(_err("step_error", f"{type(e).__name__}: {e}", exception=type(e).__name__,
                            traceback=_step_traceback(e, prog.filename)))
        finally:
            _local.ctx = prev
        ctx._flush()

        outs = sdef.outputs
        if outs is None:
            self.emit("step.completed", path, label,
                      output={} if result is None else {"result": to_jsonable(result)}, duration_ms=self._ms(t0))
            return result, {"result": result}
        n = len(outs)
        names = [o.name for o in outs]
        if n == 0:
            values: list[Any] = []
        elif n == 1 and not (names[0] != "result" and isinstance(result, dict) and names[0] in result):
            values = [result]
        elif isinstance(result, dict) and not is_tuple_outputs(outs):
            missing = [k for k in names if k not in result]
            if missing:
                raise fail(_err("invalid_output", f"step `{label}` must return a dict with "
                                                  f"{', '.join(names)}; missing {', '.join(missing)}"))
            values = [result[k] for k in names]
        elif isinstance(result, (tuple, list)) and len(result) == n:
            values = list(result)
        else:
            got = f"{len(result)} values" if isinstance(result, (tuple, list)) else type_name(result)
            shape = f"a dict with {', '.join(names)}" if not is_tuple_outputs(outs) else f"{n} values"
            raise fail(_err("invalid_output", f"step `{label}` must return {shape}, got {got}"))
        out: dict[str, Any] = {}
        for o, v in zip(outs, values):
            try:
                out[o.name] = prog.adapter(o.type).validate_python(v)
            except ValidationError as e:
                raise fail(_err("invalid_output", f"output `{o.name}` of step `{label}` is not a valid "
                                                  f"{o.type}: {_validation_message(e)}"))
        self.emit("step.completed", path, label, output={k: to_jsonable(v) for k, v in out.items()},
                  duration_ms=self._ms(t0))
        return result, out


def _step_traceback(e: BaseException, filename: str) -> str:
    """Traceback starting at the step's own code, without interpreter frames."""
    tb = e.__traceback__
    while tb is not None and tb.tb_frame.f_code.co_filename != filename:
        tb = tb.tb_next
    if tb is None:
        tb = e.__traceback__
    return "".join(traceback.format_exception(type(e), e, tb))
