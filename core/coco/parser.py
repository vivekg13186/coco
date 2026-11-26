"""Parser for .coco files.

Coco is line- and indentation-based, so the top level is parsed line by line:
`package`, `import`, `from`, `class`, `step`, `flow`. Step bodies are captured
verbatim as Python. Flow bodies are Python-shaped, so after rewriting Coco
declarations (`int d, f`) into marker calls they are parsed with Python's own
`ast` module, which gives exact line and column numbers for free.
"""
from __future__ import annotations

import ast
import keyword
import re
from dataclasses import dataclass

from .diagnostics import Diagnostic
from .model import ClassDef, Field, FlowDef, Import, Package, Param, StepDef, TypeRef

IDENT = r"[A-Za-z_]\w*"
DOTTED = rf"{IDENT}(?:\.{IDENT})*"

RE_PACKAGE = re.compile(rf"^package\s+({IDENT})$")
RE_IMPORT = re.compile(rf"^import\s+({DOTTED})(?:\s+as\s+({IDENT}))?$")
RE_FROM = re.compile(rf"^from\s+({DOTTED})\s+import\s+({IDENT}(?:\s*,\s*{IDENT})*)$")
RE_CLASS = re.compile(rf"^class\s+({IDENT})\s*:$")
RE_CALLABLE = re.compile(rf"^(step|flow)\s+({IDENT})\s*\((.*?)\)\s*(?:\((.*?)\))?\s*:$")
RE_PARAM = re.compile(rf"^({DOTTED})\s+({IDENT})\s*(\[\])?$")
RE_FIELD = re.compile(rf"^({DOTTED})\s+({IDENT})\s*(\[\])?\s*(?:=\s*(.+))?$")
RE_DECL = re.compile(
    rf"^(\s*)({DOTTED})\s+({IDENT}\s*(?:\[\])?(?:\s*,\s*{IDENT}\s*(?:\[\])?)*)\s*(?:=\s*(.+))?$"
)

DECL_MARKER = "__coco_decl__"
_NOT_TYPES = set(keyword.kwlist) | set(getattr(keyword, "softkwlist", []))


@dataclass
class _Line:
    no: int
    text: str      # leading tabs expanded to 4 spaces
    indent: int
    blank: bool    # empty or comment-only


def strip_comment(s: str) -> str:
    """Remove a trailing # comment, respecting quoted strings."""
    quote = None
    i = 0
    while i < len(s):
        c = s[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#":
            return s[:i].rstrip()
        i += 1
    return s.rstrip()


def _split_lines(source: str, diags: list[Diagnostic]) -> list[_Line]:
    out: list[_Line] = []
    first_tab = first_space = None
    for no, raw in enumerate(source.splitlines(), 1):
        lead = raw[: len(raw) - len(raw.lstrip(" \t"))]
        stripped = raw.strip()
        blank = stripped == "" or stripped.startswith("#")
        if not blank and lead:
            if "\t" in lead and first_tab is None:
                first_tab = no
            if " " in lead and first_space is None:
                first_space = no
        expanded = lead.replace("\t", "    ")
        out.append(_Line(no, expanded + raw[len(lead):], len(expanded), blank))
    if first_tab and first_space:
        diags.append(Diagnostic(max(first_tab, first_space), 1,
                                "tabs and spaces are both used for indentation in this file",
                                "warning", "W001"))
    return out


def _parse_type(text: str) -> TypeRef:
    return TypeRef(text)


def _parse_params(text: str | None, line: int, diags: list[Diagnostic]) -> list[Param]:
    params: list[Param] = []
    if not text or not text.strip():
        return params
    for part in text.split(","):
        p = part.strip()
        m = RE_PARAM.match(p)
        if not m:
            diags.append(Diagnostic(line, 1, f"bad parameter `{p}`; expected `type name` or `type name[]`",
                                    code="E101"))
            continue
        params.append(Param(TypeRef(m.group(1), bool(m.group(3))), m.group(2), line))
    return params


def _body_text(body: list[_Line]) -> tuple[str, int, int] | None:
    """Dedent a captured block. Returns (text, first_line, removed_indent)."""
    while body and body[-1].blank:
        body = body[:-1]
    real = [ln for ln in body if not ln.blank]
    if not real:
        return None
    ind = min(ln.indent for ln in real)
    parts = []
    for ln in body:
        if ln.blank:
            parts.append("")  # blank or comment-only: no code, keep the line so numbers stay aligned
        else:
            parts.append(ln.text[ind:])
    return "\n".join(parts), body[0].no, ind


def _rewrite_decls(text: str, first_line: int, col: int, diags: list[Diagnostic]) -> str:
    """Turn `int d, f` / `Person ps[]` / `int n = 0` into marker calls Python can parse."""
    out = []
    for i, line in enumerate(text.split("\n")):
        code = strip_comment(line)
        m = RE_DECL.match(code)
        if m and m.group(2).split(".")[0] not in _NOT_TYPES and m.group(3).split(",")[0].strip() not in _NOT_TYPES:
            indent, type_name, names, init = m.groups()
            name_list = [n.replace(" ", "") for n in names.split(",")]
            args = ", ".join(repr(n) for n in name_list)
            new = f"{indent}{DECL_MARKER}({type_name!r}, {args})"
            if init is not None:
                if len(name_list) != 1 or name_list[0].endswith("[]"):
                    diags.append(Diagnostic(first_line + i, col + len(indent) + 1,
                                            "an initial value is only allowed when declaring one variable",
                                            code="E102"))
                else:
                    new += f"; {name_list[0]} = {init}"
            out.append(new)
        else:
            out.append(line)
    return "\n".join(out)


def parse(source: str, filename: str = "<coco>") -> tuple[Package, list[Diagnostic]]:
    diags: list[Diagnostic] = []
    lines = _split_lines(source, diags)
    pkg = Package(name=None, line=0, filename=filename, source=source)
    i, n = 0, len(lines)
    while i < n:
        ln = lines[i]
        if ln.blank:
            i += 1
            continue
        if ln.indent > 0:
            diags.append(Diagnostic(ln.no, ln.indent + 1, "unexpected indentation", code="E100"))
            i += 1
            continue
        header = strip_comment(ln.text).strip()
        j = i + 1
        body: list[_Line] = []
        while j < n and (lines[j].blank or lines[j].indent > 0):
            body.append(lines[j])
            j += 1
        has_body = any(not b.blank for b in body)
        word = header.split()[0] if header.split() else ""
        no = ln.no

        if word in ("package", "import", "from"):
            if has_body:
                first = next(b for b in body if not b.blank)
                diags.append(Diagnostic(first.no, first.indent + 1, "unexpected indented block", code="E100"))
            if word == "package":
                m = RE_PACKAGE.match(header)
                if not m:
                    diags.append(Diagnostic(no, 1, "expected `package <name>`", code="E103"))
                elif pkg.name is not None:
                    diags.append(Diagnostic(no, 1, "package is declared twice", code="E104"))
                else:
                    pkg.name, pkg.line = m.group(1), no
            elif word == "import":
                m = RE_IMPORT.match(header)
                if not m:
                    diags.append(Diagnostic(no, 1, "expected `import module [as name]`", code="E103"))
                else:
                    pkg.imports.append(Import(m.group(1), m.group(2), [], False, no))
            else:
                m = RE_FROM.match(header)
                if not m:
                    diags.append(Diagnostic(no, 1, "expected `from module import name, ...`", code="E103"))
                else:
                    names = [x.strip() for x in m.group(2).split(",")]
                    pkg.imports.append(Import(m.group(1), None, names, True, no))

        elif word == "class":
            m = RE_CLASS.match(header)
            if not m:
                diags.append(Diagnostic(no, 1, "expected `class Name:`", code="E103"))
            else:
                fields: list[Field] = []
                for b in body:
                    if b.blank:
                        continue
                    text = strip_comment(b.text).strip()
                    fm = RE_FIELD.match(text)
                    if not fm:
                        diags.append(Diagnostic(b.no, b.indent + 1,
                                                f"bad field `{text}`; expected `type name [= default]`",
                                                code="E105"))
                        continue
                    f = Field(TypeRef(fm.group(1), bool(fm.group(3))), fm.group(2), b.no)
                    if fm.group(4) is not None:
                        try:
                            f.default = ast.literal_eval(fm.group(4))
                            f.has_default = True
                        except Exception:
                            diags.append(Diagnostic(b.no, b.indent + 1,
                                                    f"default for `{f.name}` must be a literal value",
                                                    code="E106"))
                    fields.append(f)
                if not has_body:
                    diags.append(Diagnostic(no, 1, f"class `{m.group(1)}` has no fields", "warning", "W002"))
                pkg.classes.append(ClassDef(m.group(1), fields, no))

        elif word in ("step", "flow"):
            m = RE_CALLABLE.match(header)
            if not m:
                diags.append(Diagnostic(no, 1, f"expected `{word} name(inputs)(outputs):`", code="E103"))
            else:
                kind, name, ins, outs = m.groups()
                params = _parse_params(ins, no, diags)
                outputs = _parse_params(outs, no, diags)
                bt = _body_text(body)
                if bt is None:
                    diags.append(Diagnostic(no, 1, f"{kind} `{name}` has an empty body", code="E107"))
                    text, bline, bcol = "pass", no + 1, 4
                else:
                    text, bline, bcol = bt
                if kind == "step":
                    pkg.steps.append(StepDef(name, params, outputs, text, bline, bcol, no))
                else:
                    flow = FlowDef(name, params, outputs, text, bline, bcol, no)
                    rewritten = _rewrite_decls(text, bline, bcol, diags)
                    try:
                        tree = ast.parse(rewritten, filename=filename)
                        ast.increment_lineno(tree, bline - 1)
                        flow.tree = tree
                    except SyntaxError as e:
                        diags.append(Diagnostic((e.lineno or 1) + bline - 1, (e.offset or 0) + bcol,
                                                f"syntax error in flow `{name}`: {e.msg}", code="E108"))
                    pkg.flows.append(flow)
        else:
            diags.append(Diagnostic(no, 1, f"unexpected `{word}`; expected package, import, class, step or flow",
                                    code="E103"))
        i = j

    if pkg.name is None:
        diags.append(Diagnostic(1, 1, "missing `package <name>` declaration", code="E109"))
    return pkg, diags
