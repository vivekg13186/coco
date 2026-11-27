"""Coco: workflows for solo developers, written as plain Python.

In a workflow file:

    from coco import step, flow, log, env

    @step
    def greet(name: str) -> str:
        log("hi", name)
        return f"Hello {name}"

    @flow
    def hello(name: str = "world") -> str:
        message = greet(name)
        return message

From Python:

    import coco
    program = coco.compile(source, name="hello")   # raises coco.CocoCompileError
    result = program.run("hello", {"name": "Ada"}, on_event=print)
"""
from .env import EnvView
from .diagnostics import CocoCompileError, CocoRuntimeError, Diagnostic
from .events import EVENT_TYPES, Event, RunResult
from .dsl import Model, context, debug, env, error, flow, info, log, log_table, step, warn, warning
from .program import Program, check, compile, compile_file, directory_loader, symbols, workflow_name
from .runtime import StepContext

__version__ = "0.1.0"

__all__ = [
    "step", "flow", "Model", "log", "info", "warn", "warning", "error", "debug", "log_table", "env", "context",
    "workflow_name", "compile", "compile_file", "check", "symbols", "directory_loader",
    "Program", "StepContext", "EnvView", "Event", "RunResult", "EVENT_TYPES",
    "Diagnostic", "CocoCompileError", "CocoRuntimeError",
]
