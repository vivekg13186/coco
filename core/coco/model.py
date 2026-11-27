"""What a parsed workflow file contains. The file is real Python; flow bodies stay as Python `ast`."""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

# Kinds of type a TypeRef can name. Classes use their own name; `any` means unchecked.
PRIMITIVES = ("str", "int", "float", "bool", "dict", "list", "any", "none")


@dataclass(frozen=True)
class TypeRef:
    """A Python type hint, read leniently: anything unknown becomes `any`."""
    name: str                   # "str", "int", "float", "bool", "dict", "list", "any", or a class name
    array: bool = False         # list[T]: name is T
    nullable: bool = False      # Optional[T] / T | None

    def element(self) -> "TypeRef":
        return TypeRef(self.name, False, False)

    @property
    def is_any(self) -> bool:
        return self.name == "any" and not self.array

    def __str__(self) -> str:
        base = {"any": "Any"}.get(self.name, self.name)
        text = f"list[{base}]" if self.array else base
        return f"{text} | None" if self.nullable else text


ANY = TypeRef("any")


@dataclass
class Param:
    name: str
    type: TypeRef
    line: int
    has_default: bool = False
    default: Any = None          # literal default value (flows); steps keep Python's own default


@dataclass
class Import:
    module: str
    alias: str | None
    names: list[str]             # for `from x import a, b`
    is_from: bool
    line: int
    node: ast.stmt | None = None

    @property
    def bound(self) -> str:
        return self.alias or self.module.split(".")[0]


@dataclass
class Field:
    name: str
    type: TypeRef
    line: int
    has_default: bool = False


@dataclass
class ClassDef:
    name: str
    fields: list[Field]
    line: int


@dataclass
class StepDef:
    name: str
    params: list[Param]
    outputs: list[Param] | None  # None: no `->` hint, returns whatever it returns
    line: int
    node: ast.FunctionDef


@dataclass
class FlowDef:
    name: str
    params: list[Param]
    outputs: list[Param] | None
    line: int
    node: ast.FunctionDef


@dataclass
class Module:
    name: str                    # the workflow name (from the engine, or the file name)
    filename: str
    source: str
    tree: ast.Module | None = None
    imports: list[Import] = field(default_factory=list)
    classes: list[ClassDef] = field(default_factory=list)
    steps: list[StepDef] = field(default_factory=list)
    flows: list[FlowDef] = field(default_factory=list)
    helpers: list[str] = field(default_factory=list)      # plain functions usable inside steps
    constants: list[str] = field(default_factory=list)    # module-level names usable in steps and flows

    @property
    def classes_by_name(self) -> dict[str, ClassDef]:
        return {c.name: c for c in self.classes}

    @property
    def steps_by_name(self) -> dict[str, StepDef]:
        return {s.name: s for s in self.steps}

    @property
    def flows_by_name(self) -> dict[str, FlowDef]:
        return {f.name: f for f in self.flows}
