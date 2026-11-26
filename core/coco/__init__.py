"""Coco: a small workflow language for solo developers.

    import coco
    program = coco.compile(source)          # raises coco.CocoCompileError
    result = program.run("flow", {...}, on_event=print)
"""
from .env import EnvView
from .diagnostics import CocoCompileError, CocoRuntimeError, Diagnostic
from .events import EVENT_TYPES, Event, RunResult
from .program import Program, check, compile, compile_file, directory_loader, symbols
from .runtime import StepContext

__version__ = "0.1.0"

__all__ = [
    "compile", "compile_file", "check", "symbols", "directory_loader",
    "Program", "StepContext", "EnvView", "Event", "RunResult", "EVENT_TYPES",
    "Diagnostic", "CocoCompileError", "CocoRuntimeError",
]
