"""Server settings, read from COCO_* environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _default_ui_dir() -> str | None:
    """The built UI: the copy shipped inside this package (`coco_server/ui`, kept in git), or else a local
    build in the repo (`ui/.output/public`)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, "ui"),
                 os.path.normpath(os.path.join(here, "..", "..", "ui", ".output", "public"))):
        if os.path.isfile(os.path.join(path, "index.html")):
            return path
    return None


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: os.environ.get(
        "COCO_DB", os.path.join(os.path.expanduser("~"), ".coco", "coco.db")))
    host: str = field(default_factory=lambda: os.environ.get("COCO_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _int("COCO_PORT", 8765))
    max_runs: int = field(default_factory=lambda: _int("COCO_MAX_RUNS", 4))
    api_token: str | None = field(default_factory=lambda: os.environ.get("COCO_API_TOKEN") or None)
    event_retention_days: int = field(default_factory=lambda: _int("COCO_EVENT_RETENTION_DAYS", 90))
    ui_dir: str | None = field(default_factory=lambda: os.environ.get("COCO_UI_DIR") or _default_ui_dir())
    wait_timeout_s: float = 30.0
    sse_heartbeat_s: float = 15.0
