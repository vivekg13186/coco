"""The API a workflow file uses:

    from coco import step, flow, log, env

    @step
    def greet(name: str) -> str:
        log("greeting", name)
        return f"Hello {name}"

    @flow
    def hello(name: str = "world") -> str:
        message = greet(name)
        return message

`log`, `info`, `warn`, `error`, `debug`, `log_table`, `env` and `context` work inside any step (and in
flows); they find the running step themselves. Outside a run, logging prints and `env` is empty.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ConfigDict

from .console import format_args, log_arg, table_payload
from .env import EnvView

F = TypeVar("F", bound=Callable[..., Any])

# the step (or flow) running on this thread; set by the runtime
_local = threading.local()
_EMPTY_ENV = EnvView({})


def _current():
    return getattr(_local, "ctx", None)


# ---------------------------------------------------------------- decorators
def _mark(kind: str, fn: Any = None, **_options: Any):
    def wrap(f: F) -> F:
        try:
            f._coco_kind = kind  # type: ignore[attr-defined]
        except AttributeError:  # pragma: no cover
            pass
        return f

    if fn is not None and callable(fn):
        return wrap(fn)
    return wrap


def step(fn: Any = None, **options: Any):
    """Mark a function as a step: `@step` or `@step(...)`. A step is plain Python; its type hints
    describe its inputs and `->` describes its outputs."""
    return _mark("step", fn, **options)


def flow(fn: Any = None, **options: Any):
    """Mark a function as a flow: `@flow` or `@flow(...)`. Flows call steps and other flows, and may
    only use assignments, if/elif/else, for loops, logging and return."""
    return _mark("flow", fn, **options)


class Model(BaseModel):
    """Base class for data passed between steps. Classes without a base get it automatically."""
    model_config = ConfigDict(protected_namespaces=(), arbitrary_types_allowed=True)


# ---------------------------------------------------------------- logging, console style
def log(*values: Any, level: str = "info") -> None:
    """Log any values, like console.log: `log("total", n, {"a": 1}, True)`. Objects keep their structure."""
    ctx = _current()
    if ctx is None:
        print(format_args([log_arg(v) for v in values]))
        return
    ctx.log(*values, level=level)


def debug(*values: Any) -> None:
    log(*values, level="debug")


def info(*values: Any) -> None:
    log(*values, level="info")


def warn(*values: Any) -> None:
    log(*values, level="warning")


warning = warn


def error(*values: Any) -> None:
    log(*values, level="error")


def log_table(data: Any, columns: list[str] | None = None) -> None:
    """Log rows as a table, like console.table: a list of objects, lists or values, or a dict of rows.
    `columns` picks and orders the columns. At most 1000 rows are kept."""
    ctx = _current()
    if ctx is None:
        t = table_payload(data, columns)
        print(" | ".join(t["columns"]))
        for row in t["rows"]:
            print(" | ".join("" if v is None else str(v) for v in row))
        return
    ctx.log_table(data, columns)


# ---------------------------------------------------------------- env and context
class _EnvProxy:
    """`env`: the run environment (read-only JSON). `env.api_url`, `env["api_url"]`, `env.get("k", d)`."""

    @staticmethod
    def _view() -> EnvView:
        ctx = _current()
        return ctx.env if ctx is not None else _EMPTY_ENV

    def __getattr__(self, key: str) -> Any:
        if key.startswith("__"):
            raise AttributeError(key)
        return getattr(self._view(), key)

    def __getitem__(self, key: str) -> Any:
        return self._view()[key]

    def __contains__(self, key: object) -> bool:
        return key in self._view()

    def __iter__(self):
        return iter(self._view())

    def __len__(self) -> int:
        return len(self._view())

    def get(self, key: str, default: Any = None) -> Any:
        return self._view().get(key, default)

    def keys(self):
        return self._view().keys()

    def items(self):
        return self._view().items()

    def to_dict(self) -> dict[str, Any]:
        return self._view().to_dict()

    def __setattr__(self, key: str, value: Any) -> None:
        raise AttributeError("env is read-only")

    def __repr__(self) -> str:
        return repr(self._view())


class _Context:
    """`context`: where the code is running. `context.run_id`, `.flow`, `.step`, `.path`."""

    def _get(self, name: str) -> Any:
        ctx = _current()
        return getattr(ctx, name) if ctx is not None else None

    @property
    def run_id(self) -> str | None:
        return self._get("run_id")

    @property
    def flow(self) -> str | None:
        return self._get("flow")

    @property
    def step(self) -> str | None:
        return self._get("step")

    @property
    def path(self) -> str | None:
        return self._get("path")

    def __repr__(self) -> str:
        return f"<coco context run_id={self.run_id} path={self.path}>"


env = _EnvProxy()
context = _Context()

API: dict[str, Any] = {
    "step": step, "flow": flow, "Model": Model, "log": log, "info": info, "warn": warn, "warning": warning,
    "error": error, "debug": debug, "log_table": log_table, "env": env, "context": context,
}

__all__ = list(API)
