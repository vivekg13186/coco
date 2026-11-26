"""The run environment: a read-only JSON object available as `env` in flows and `self.env` in steps."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Iterator


class EnvView(Mapping):
    """Read-only view of an environment object.

    Keys work as attributes (`env.api.base_url`) and as items (`env["api"]["base_url"]`);
    nested objects are views too. A missing key raises an error that names the key.
    """

    __slots__ = ("_data", "_path")

    def __init__(self, data: Mapping[str, Any] | None = None, path: str = "env"):
        object.__setattr__(self, "_data", dict(data or {}))
        object.__setattr__(self, "_path", path)

    def _wrap(self, key: str, value: Any) -> Any:
        if isinstance(value, Mapping) and not isinstance(value, EnvView):
            return EnvView(value, f"{self._path}.{key}")
        if isinstance(value, list):
            return [EnvView(v, f"{self._path}.{key}[{i}]") if isinstance(v, Mapping) else v
                    for i, v in enumerate(value)]
        return value

    def __getattr__(self, key: str) -> Any:
        if key.startswith("__"):
            raise AttributeError(key)
        try:
            return self._wrap(key, self._data[key])
        except KeyError:
            raise AttributeError(f"{self._path} has no key `{key}`") from None

    def __getitem__(self, key: str) -> Any:
        try:
            return self._wrap(key, self._data[key])
        except KeyError:
            raise KeyError(f"{self._path} has no key `{key}`") from None

    def __setattr__(self, key: str, value: Any) -> None:
        raise AttributeError("env is read-only")

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._wrap(key, self._data[key]) if key in self._data else default

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)

    def __repr__(self) -> str:
        return f"<{self._path} keys={list(self._data)}>"
