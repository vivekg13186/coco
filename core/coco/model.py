"""Syntax tree for a parsed .coco file (top level). Flow bodies are kept as Python ast."""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

PRIMITIVES = ("string", "boolean", "int", "float", "object", "array")


@dataclass(frozen=True)
class TypeRef:
    name: str            # "int", "Person", "shared.Person"
    array: bool = False  # True for `Person ps[]`

    def element(self) -> "TypeRef":
        return TypeRef(self.name, False)

    def __str__(self) -> str:
        return f"{self.name}[]" if self.array else self.name


ANY = None  # inferred type unknown


@dataclass
class Param:
    type: TypeRef
    name: str
    line: int
    col: int = 0


@dataclass
class Import:
    module: str
    alias: str | None
    names: list[str]        # for `from x import a, b`
    is_from: bool
    line: int


@dataclass
class Field:
    type: TypeRef
    name: str
    line: int
    has_default: bool = False
    default: Any = None


@dataclass
class ClassDef:
    name: str
    fields: list[Field]
    line: int


@dataclass
class StepDef:
    name: str
    params: list[Param]
    outputs: list[Param]
    body: str           # dedented Python source
    body_line: int      # file line of the first body line
    body_col: int       # indentation removed from the body
    line: int


@dataclass
class FlowDef:
    name: str
    params: list[Param]
    outputs: list[Param]
    body: str
    body_line: int
    body_col: int
    line: int
    tree: ast.Module | None = None  # parsed body, line numbers already file-relative


@dataclass
class Package:
    name: str | None
    line: int
    filename: str
    source: str
    imports: list[Import] = field(default_factory=list)
    classes: list[ClassDef] = field(default_factory=list)
    steps: list[StepDef] = field(default_factory=list)
    flows: list[FlowDef] = field(default_factory=list)

    @property
    def classes_by_name(self) -> dict[str, ClassDef]:
        return {c.name: c for c in self.classes}

    @property
    def steps_by_name(self) -> dict[str, StepDef]:
        return {s.name: s for s in self.steps}

    @property
    def flows_by_name(self) -> dict[str, FlowDef]:
        return {f.name: f for f in self.flows}
