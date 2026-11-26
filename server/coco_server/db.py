"""SQLite access: one connection per thread, WAL mode, numbered migrations."""
from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE workflow (
      id                 TEXT PRIMARY KEY,
      name               TEXT NOT NULL UNIQUE,
      description        TEXT NOT NULL DEFAULT '',
      current_version_id TEXT REFERENCES workflow_version(id),
      created_at         TEXT NOT NULL,
      updated_at         TEXT NOT NULL
    );
    CREATE TABLE workflow_version (
      id            TEXT PRIMARY KEY,
      workflow_id   TEXT NOT NULL REFERENCES workflow(id) ON DELETE CASCADE,
      version       INTEGER NOT NULL,
      source        TEXT NOT NULL,
      checksum      TEXT NOT NULL,
      is_valid      INTEGER NOT NULL,
      diagnostics   TEXT NOT NULL,
      flows         TEXT NOT NULL,
      symbols       TEXT NOT NULL,
      created_at    TEXT NOT NULL,
      UNIQUE (workflow_id, version)
    );
    CREATE TABLE schedule (
      id            TEXT PRIMARY KEY,
      name          TEXT NOT NULL,
      workflow_id   TEXT NOT NULL REFERENCES workflow(id) ON DELETE CASCADE,
      flow_name     TEXT NOT NULL,
      cron          TEXT NOT NULL,
      timezone      TEXT NOT NULL DEFAULT 'Europe/Amsterdam',
      input         TEXT NOT NULL DEFAULT '{}',
      enabled       INTEGER NOT NULL DEFAULT 1,
      overlap       TEXT NOT NULL DEFAULT 'skip',
      misfire       TEXT NOT NULL DEFAULT 'run_once',
      next_run_at   TEXT,
      last_run_at   TEXT,
      last_run_id   TEXT,
      created_at    TEXT NOT NULL,
      updated_at    TEXT NOT NULL
    );
    CREATE TABLE run (
      id            TEXT PRIMARY KEY,
      workflow_id   TEXT NOT NULL REFERENCES workflow(id) ON DELETE CASCADE,
      version_id    TEXT NOT NULL REFERENCES workflow_version(id) ON DELETE CASCADE,
      flow_name     TEXT NOT NULL,
      status        TEXT NOT NULL,
      trigger       TEXT NOT NULL,
      schedule_id   TEXT REFERENCES schedule(id) ON DELETE SET NULL,
      input         TEXT NOT NULL,
      output        TEXT,
      error         TEXT,
      queued_at     TEXT NOT NULL,
      started_at    TEXT,
      finished_at   TEXT,
      duration_ms   INTEGER,
      event_count   INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX run_by_wf_time ON run(workflow_id, queued_at DESC);
    CREATE INDEX run_by_time    ON run(queued_at DESC);
    CREATE INDEX run_by_status  ON run(status);
    CREATE TABLE run_event (
      run_id     TEXT NOT NULL REFERENCES run(id) ON DELETE CASCADE,
      seq        INTEGER NOT NULL,
      type       TEXT NOT NULL,
      path       TEXT,
      step_name  TEXT,
      payload    TEXT NOT NULL,
      ts         TEXT NOT NULL,
      PRIMARY KEY (run_id, seq)
    ) WITHOUT ROWID;
    """,
    # 2: environments (named JSON objects a run can use as `env`)
    """
    CREATE TABLE environment (
      id           TEXT PRIMARY KEY,
      name         TEXT NOT NULL UNIQUE,
      description  TEXT NOT NULL DEFAULT '',
      data         TEXT NOT NULL DEFAULT '{}',
      is_default   INTEGER NOT NULL DEFAULT 0,
      created_at   TEXT NOT NULL,
      updated_at   TEXT NOT NULL
    );
    ALTER TABLE run ADD COLUMN env_id TEXT;
    ALTER TABLE run ADD COLUMN env_name TEXT;
    ALTER TABLE schedule ADD COLUMN env_id TEXT;
    CREATE INDEX run_by_env ON run(env_name)
    """,
    # 3: notes (plain text)
    """
    CREATE TABLE note (
      id          TEXT PRIMARY KEY,
      title       TEXT NOT NULL DEFAULT '',
      body        TEXT NOT NULL DEFAULT '',
      created_at  TEXT NOT NULL,
      updated_at  TEXT NOT NULL
    );
    CREATE INDEX note_by_time ON note(updated_at DESC)
    """,
]


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def loads(s: str | None) -> Any:
    return None if s is None else json.loads(s)


class Database:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._lock = threading.Lock()

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=30000")
            self._local.conn = c
            with self._lock:
                self._all.append(c)
        return c

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        c = self.conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")

    def all(self, sql: str, args: tuple | list = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn().execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple | list = ()) -> dict[str, Any] | None:
        r = self.conn().execute(sql, args).fetchone()
        return dict(r) if r else None

    def scalar(self, sql: str, args: tuple | list = ()) -> Any:
        r = self.conn().execute(sql, args).fetchone()
        return r[0] if r else None

    def migrate(self) -> int:
        c = self.conn()
        current = c.execute("PRAGMA user_version").fetchone()[0]
        for i, script in enumerate(MIGRATIONS[current:], start=current + 1):
            c.execute("BEGIN IMMEDIATE")
            try:
                for stmt in [s.strip() for s in script.split(";") if s.strip()]:
                    c.execute(stmt)
                c.execute(f"PRAGMA user_version={i}")
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
        return len(MIGRATIONS)

    def close(self) -> None:
        with self._lock:
            for c in self._all:
                try:
                    c.close()
                except Exception:
                    pass
            self._all.clear()
        self._local = threading.local()
