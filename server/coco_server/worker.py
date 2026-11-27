"""Run worker: executes runs on a thread pool, persists their events and publishes them live."""
from __future__ import annotations

import asyncio
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import coco

from . import store
from .db import Database
from .errors import ApiError, not_found
from .settings import Settings


class EventHub:
    """In-memory fan-out of events for runs that are still active.

    Subscribing returns every event the run has produced so far plus a queue for the rest, taken under one
    lock, so a live subscriber never misses or duplicates an event. Finished runs are replayed from SQLite.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live: dict[str, dict[str, Any]] = {}

    def open(self, run_id: str) -> None:
        with self._lock:
            self._live[run_id] = {"events": [], "subs": []}

    def publish(self, run_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            live = self._live.get(run_id)
            if live is None:
                return
            live["events"].append(event)
            subs = list(live["subs"])
        for loop, q in subs:
            loop.call_soon_threadsafe(q.put_nowait, event)

    def close(self, run_id: str) -> None:
        with self._lock:
            live = self._live.pop(run_id, None)
        if live:
            for loop, q in live["subs"]:
                loop.call_soon_threadsafe(q.put_nowait, None)

    def subscribe(self, run_id: str, loop: asyncio.AbstractEventLoop):
        with self._lock:
            live = self._live.get(run_id)
            if live is None:
                return None
            q: asyncio.Queue = asyncio.Queue()
            live["subs"].append((loop, q))
            return list(live["events"]), q

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        with self._lock:
            live = self._live.get(run_id)
            if live:
                live["subs"] = [s for s in live["subs"] if s[1] is not q]

    def is_live(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._live


class _EventWriter:
    """Batches event inserts: flush every 50 events or 200 ms, and at the end."""

    def __init__(self, db: Database, run_id: str):
        self.db, self.run_id = db, run_id
        self.buf: list[dict[str, Any]] = []
        self.count = 0
        self.last = time.monotonic()

    def add(self, ev: dict[str, Any]) -> None:
        self.buf.append(ev)
        self.count += 1
        if len(self.buf) >= 50 or time.monotonic() - self.last >= 0.2:
            self.flush()

    def flush(self) -> None:
        if self.buf:
            store.insert_events(self.db, self.run_id, self.buf)
            self.buf = []
        self.last = time.monotonic()


class RunManager:
    def __init__(self, db: Database, settings: Settings, hub: EventHub):
        self.db = db
        self.settings = settings
        self.hub = hub
        self.executor = ThreadPoolExecutor(max_workers=max(1, settings.max_runs), thread_name_prefix="coco-run")
        self._cancels: dict[str, threading.Event] = {}
        self._futures: dict[str, Future] = {}
        self._programs: dict[str, coco.Program] = {}
        self._lock = threading.Lock()

    # -- compile cache (cleared whenever any workflow changes, since imports may resolve differently)
    def invalidate(self) -> None:
        with self._lock:
            self._programs.clear()

    def program_for(self, version: dict[str, Any], name: str) -> coco.Program:
        with self._lock:
            prog = self._programs.get(version["id"])
        if prog is None:
            prog = coco.compile(version["source"], f"{name}.coco", store.make_loader(self.db, exclude=name),
                                name=name)
            with self._lock:
                self._programs[version["id"]] = prog
        return prog

    # -- starting
    def resolve_env(self, env: str | None) -> dict[str, Any] | None:
        """`None` means the default environment (if one is set); "" means no environment."""
        if env is None:
            return store.default_env(self.db)
        if env == "":
            return None
        try:
            return store.get_env(self.db, env)
        except ApiError:
            raise ApiError(404, "env_not_found", f"no environment named `{env}`") from None

    def start(self, workflow: str, flow: str, inputs: dict[str, Any] | None, *, version: int | None = None,
              version_id: str | None = None, trigger: str = "manual", schedule_id: str | None = None,
              env: str | None = None) -> str:
        w = store.get_workflow_row(self.db, workflow)
        if version_id:
            v = self.db.one("SELECT * FROM workflow_version WHERE id = ? AND workflow_id = ?", (version_id, w["id"]))
        elif version is not None:
            v = self.db.one("SELECT * FROM workflow_version WHERE workflow_id = ? AND version = ?", (w["id"], version))
        else:
            v = self.db.one("SELECT * FROM workflow_version WHERE id = ?", (w["current_version_id"],))
        if v is None:
            raise not_found("version", str(version_id or version))
        if not v["is_valid"]:
            raise ApiError(422, "invalid_workflow", f"version {v['version']} of `{w['name']}` has errors; "
                                                    f"fix them before running", details=store.loads(v["diagnostics"]))
        flows = [f["name"] for f in store.loads(v["flows"])]
        if flow not in flows:
            raise ApiError(404, "unknown_flow", f"`{w['name']}` has no flow `{flow}`", details={"flows": flows})
        if inputs is not None and not isinstance(inputs, dict):
            raise ApiError(422, "invalid_input", "input must be a JSON object")
        environment = self.resolve_env(env)
        run_id = store.create_run(self.db, w["id"], v["id"], flow, inputs or {}, trigger, schedule_id,
                                  environment["id"] if environment else None,
                                  environment["name"] if environment else None)
        cancel = threading.Event()
        with self._lock:
            self._cancels[run_id] = cancel
        self.hub.open(run_id)
        fut = self.executor.submit(self._execute, run_id, v, w["name"], flow, inputs or {}, cancel,
                                   environment["data"] if environment else None,
                                   environment["name"] if environment else None)
        with self._lock:
            self._futures[run_id] = fut
        fut.add_done_callback(lambda _f, rid=run_id: self._forget(rid))
        return run_id

    def _forget(self, run_id: str) -> None:
        with self._lock:
            self._cancels.pop(run_id, None)
            self._futures.pop(run_id, None)

    # -- executing (worker thread)
    def _execute(self, run_id: str, version: dict[str, Any], name: str, flow: str, inputs: dict[str, Any],
                 cancel: threading.Event, env: dict[str, Any] | None = None, env_name: str | None = None) -> None:
        writer = _EventWriter(self.db, run_id)
        try:
            if cancel.is_set():
                store.finish_run(self.db, run_id, "cancelled", None,
                                 {"code": "cancelled", "message": "cancelled before it started"}, 0, 0)
                return
            store.mark_running(self.db, run_id)
            try:
                program = self.program_for(version, name)
            except coco.CocoCompileError as e:
                store.finish_run(self.db, run_id, "failed", None,
                                 {"code": "compile_error", "message": str(e),
                                  "diagnostics": [d.to_dict() for d in e.diagnostics]}, 0, 0)
                return

            def on_event(ev: coco.Event) -> None:
                d = ev.to_dict()
                self.hub.publish(run_id, d)
                writer.add(d)

            result = program.run(flow, inputs, on_event=on_event, run_id=run_id, cancel=cancel,
                                 collect_events=False, env=env, env_name=env_name)
            writer.flush()
            store.finish_run(self.db, run_id, result.status, result.output, result.error, result.duration_ms,
                             writer.count)
        except Exception as e:  # never leave a run stuck in `running`
            try:
                writer.flush()
            except Exception:
                pass
            store.finish_run(self.db, run_id, "failed", None,
                             {"code": "internal_error", "message": f"{type(e).__name__}: {e}",
                              "traceback": traceback.format_exc()}, None, writer.count)
        finally:
            self.hub.close(run_id)

    # -- control
    def cancel(self, run_id: str) -> dict[str, Any]:
        r = store.get_run(self.db, run_id)
        if r["status"] in store.TERMINAL:
            raise ApiError(409, "run_finished", f"run is already {r['status']}")
        with self._lock:
            ev = self._cancels.get(run_id)
        if ev is None:
            raise ApiError(409, "run_not_active", "run is not active in this server process")
        ev.set()
        return {"id": run_id, "cancel_requested": True}

    def future(self, run_id: str) -> Future | None:
        with self._lock:
            return self._futures.get(run_id)

    def active_count(self) -> int:
        with self._lock:
            return len(self._futures)

    def shutdown(self, timeout: float = 5.0) -> None:
        with self._lock:
            cancels = list(self._cancels.values())
            futures = dict(self._futures)
        for ev in cancels:
            ev.set()
        deadline = time.monotonic() + timeout
        for fut in futures.values():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                fut.result(timeout=remaining)
            except Exception:
                pass
        self.executor.shutdown(wait=False, cancel_futures=True)
