"""Events emitted while a flow runs."""
from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass, field
from typing import Any

EVENT_TYPES = (
    "flow.started", "flow.completed", "flow.failed", "flow.cancelled",
    "step.started", "step.completed", "step.failed",
    "subflow.started", "subflow.completed",
    "branch.taken", "loop.iteration",
    "log", "log.table",
)


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class Event:
    run_id: str
    seq: int
    type: str
    ts: str
    path: str | None = None
    step: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def __str__(self) -> str:
        bits = [f"{self.seq:>4}", self.ts[11:23], f"{self.type:<17}", self.path or ""]
        return "  ".join(bits)


@dataclass
class RunResult:
    run_id: str
    flow: str
    status: str                   # "succeeded" | "failed" | "cancelled"
    output: dict[str, Any] | None  # JSON-safe output object
    error: dict[str, Any] | None
    duration_ms: int
    events: list[Event] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"
