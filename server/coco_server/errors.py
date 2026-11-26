"""One error shape for every API failure: {"error": {"code", "message", "details"}}."""
from __future__ import annotations

from typing import Any


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    def body(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            err["details"] = self.details
        return {"error": err}


def not_found(what: str, ident: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} `{ident}` not found")
