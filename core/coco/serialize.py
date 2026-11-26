"""Turn any runtime value into JSON-safe data for events and results."""
from __future__ import annotations

import dataclasses
import datetime as _dt
import decimal
import enum
import json
import uuid
from collections.abc import Mapping
from typing import Any

MAX_BYTES = 64 * 1024


def _plain(v: Any, depth: int = 0) -> Any:
    if depth > 50:
        return repr(v)
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if v == v and v not in (float("inf"), float("-inf")) else repr(v)
    if hasattr(v, "model_dump") and callable(v.model_dump):  # pydantic model
        try:
            return v.model_dump(mode="json")
        except Exception:
            pass
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return {f.name: _plain(getattr(v, f.name), depth + 1) for f in dataclasses.fields(v)}
    if isinstance(v, (dict, Mapping)):
        return {str(k): _plain(x, depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [_plain(x, depth + 1) for x in v]
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, (decimal.Decimal, uuid.UUID)):
        return str(v)
    if isinstance(v, enum.Enum):
        return _plain(v.value, depth + 1)
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    tolist = getattr(v, "tolist", None)  # numpy arrays, pandas objects
    if callable(tolist):
        try:
            return _plain(tolist(), depth + 1)
        except Exception:
            pass
    to_dict = getattr(v, "to_dict", None)
    if callable(to_dict):
        try:
            return _plain(to_dict(), depth + 1)
        except Exception:
            pass
    return repr(v)


def to_jsonable(v: Any, max_bytes: int = MAX_BYTES) -> Any:
    """JSON-safe copy of v, truncated to max_bytes of JSON text."""
    data = _plain(v)
    text = json.dumps(data, ensure_ascii=False, default=repr)
    if len(text.encode("utf-8")) > max_bytes:
        return {"__truncated__": True, "bytes": len(text.encode("utf-8")), "preview": text[: max_bytes // 4]}
    return data


def type_name(v: Any) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "string"
    if v is None:
        return "none"
    if isinstance(v, (list, tuple)):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__
