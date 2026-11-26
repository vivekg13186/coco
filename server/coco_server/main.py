"""`coco-server` command: start the engine with uvicorn."""
from __future__ import annotations

import argparse

import uvicorn

from .api import create_app
from .settings import Settings


def main(argv: list[str] | None = None) -> None:
    s = Settings()
    ap = argparse.ArgumentParser(prog="coco-server", description="Coco workflow engine")
    ap.add_argument("--host", default=s.host)
    ap.add_argument("--port", type=int, default=s.port)
    ap.add_argument("--db", default=s.db_path, help="SQLite file (default ~/.coco/coco.db or $COCO_DB)")
    ap.add_argument("--max-runs", type=int, default=s.max_runs)
    args = ap.parse_args(argv)
    s.host, s.port, s.db_path, s.max_runs = args.host, args.port, args.db, args.max_runs
    ui = f"UI: http://{s.host}:{s.port}/" if s.ui_dir else "UI not built (npm run build in ui/)"
    print(f"coco engine on http://{s.host}:{s.port}  (db: {s.db_path}, API docs: /docs, {ui})")
    uvicorn.run(create_app(s), host=s.host, port=s.port, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()
