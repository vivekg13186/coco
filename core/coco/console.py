"""Console-style logging helpers: structured log arguments and table normalisation."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Iterable

from .serialize import to_jsonable

MAX_TABLE_ROWS = 1000
INDEX_COLUMN = "(index)"
VALUES_COLUMN = "Values"


def arg_kind(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, (list, tuple, set, frozenset)):
        return "array"
    return "object"


def log_arg(v: Any) -> dict[str, Any]:
    """One console.log argument as JSON: its kind, value and (for objects) class name."""
    kind = arg_kind(v)
    out: dict[str, Any] = {"type": kind, "value": to_jsonable(v)}
    if kind == "object":
        if not isinstance(out["value"], (dict, list)):
            out["type"] = "string"  # an object with no JSON form: shown as its repr
        cls = type(v).__name__
        if cls not in ("dict",):
            out["class"] = cls
    return out


def format_arg(a: dict[str, Any]) -> str:
    """Text form of one argument, like console.log: strings bare, everything else as compact JSON."""
    v = a["value"]
    if a["type"] == "string":
        return v if isinstance(v, str) else str(v)
    if a["type"] == "null":
        return "None"
    if a["type"] == "boolean":
        return "True" if v else "False"
    text = json.dumps(v, ensure_ascii=False, separators=(", ", ": "))
    if a.get("class") and a["type"] == "object":
        text = f"{a['class']} {text}"
    return text


def format_args(args: list[dict[str, Any]], max_len: int = 2000) -> str:
    text = " ".join(format_arg(a) for a in args)
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _records(data: Any) -> Any:
    """pandas DataFrames and similar become a list of row dicts."""
    to_dict = getattr(data, "to_dict", None)
    if callable(to_dict) and hasattr(data, "columns"):
        try:
            return to_dict(orient="records")
        except TypeError:
            pass
    return data


def table_payload(data: Any, columns: Iterable[str] | None = None) -> dict[str, Any]:
    """Normalise data for log_table, like console.table.

    Accepts a list (or tuple) of objects, lists or plain values, or a dict of rows. Each row gets an
    `(index)` cell; objects contribute one column per key, lists one column per position, plain values a
    `Values` column. `columns` restricts and orders the columns.
    """
    data = _records(data)
    if isinstance(data, Mapping):
        items = list(data.items())
    elif isinstance(data, (list, tuple)):
        items = list(enumerate(data))
    else:
        raise TypeError(f"log_table needs a list or a dict of rows, got {type(data).__name__}")

    total = len(items)
    items = items[:MAX_TABLE_ROWS]
    cols: list[str] = []
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []

    def add_col(c: str) -> None:
        if c not in seen:
            seen.add(c)
            cols.append(c)

    for key, item in items:
        plain = to_jsonable(item)
        row: dict[str, Any] = {}
        if isinstance(plain, dict):
            for k, v in plain.items():
                row[str(k)] = v
                add_col(str(k))
        elif isinstance(plain, list):
            for i, v in enumerate(plain):
                row[str(i)] = v
                add_col(str(i))
        else:
            row[VALUES_COLUMN] = plain
            add_col(VALUES_COLUMN)
        rows.append({INDEX_COLUMN: to_jsonable(key), **row})

    if columns is not None:
        cols = [str(c) for c in columns]
    if VALUES_COLUMN in cols:  # like console.table, Values goes last
        cols = [c for c in cols if c != VALUES_COLUMN] + [VALUES_COLUMN]
    columns_out = [INDEX_COLUMN] + cols
    return {
        "columns": columns_out,
        "rows": [[r.get(c) for c in columns_out] for r in rows],
        "row_count": total,
        "truncated": total > MAX_TABLE_ROWS,
    }
