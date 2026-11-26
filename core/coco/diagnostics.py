"""Diagnostics reported by the Coco compiler."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Diagnostic:
    line: int
    col: int
    message: str
    severity: str = "error"  # "error" | "warning"
    code: str = "E000"
    end_col: int | None = None  # exclusive end column on the same line, when the problem is one token

    def to_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        return f"{self.line}:{self.col}: {self.severity} {self.code}: {self.message}"


class CocoCompileError(Exception):
    """Raised by coco.compile() when the source has errors."""

    def __init__(self, diagnostics: list[Diagnostic], filename: str = "<coco>"):
        self.diagnostics = diagnostics
        self.filename = filename
        errors = [d for d in diagnostics if d.severity == "error"]
        lines = "\n".join(f"{filename}:{d}" for d in errors)
        super().__init__(f"{len(errors)} error(s)\n{lines}")


class CocoRuntimeError(Exception):
    """A failure while running a flow (bad input, bad step output, expression error)."""

    def __init__(self, message: str, code: str = "runtime_error", path: str | None = None):
        super().__init__(message)
        self.code = code
        self.path = path
