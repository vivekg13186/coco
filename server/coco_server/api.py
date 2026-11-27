"""FastAPI application: REST API under /api/v1 and the SSE event stream."""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

import coco

from . import store
from .db import Database
from .errors import ApiError
from .settings import Settings
from .worker import EventHub, RunManager

PREFIX = "/api/v1"


# ---------------------------------------------------------------- request bodies
class WorkflowCreate(BaseModel):
    name: str
    source: str
    description: str = ""


class WorkflowUpdate(BaseModel):
    source: str | None = None
    description: str | None = None
    name: str | None = None  # rename


class ValidateIn(BaseModel):
    source: str
    name: str | None = None


class RunCreate(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None
    wait: bool = False
    env: str | None = None  # environment name or id; omitted = the default environment, "" = none


class NoteIn(BaseModel):
    title: str | None = None
    body: str | None = None


class EnvCreate(BaseModel):
    name: str
    data: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    is_default: bool = False


class EnvUpdate(BaseModel):
    name: str | None = None
    data: dict[str, Any] | None = None
    description: str | None = None
    is_default: bool | None = None


# ---------------------------------------------------------------- app
class Engine:
    """Everything the routes need, created once per app."""

    def __init__(self, settings: Settings, db: Database | None = None):
        self.settings = settings
        self.db = db or Database(settings.db_path)
        self.hub = EventHub()
        self.runs = RunManager(self.db, settings, self.hub)
        self.recovered = 0

    def startup(self) -> None:
        self.db.migrate()
        self.recovered = store.recover_interrupted(self.db)

    def shutdown(self) -> None:
        self.runs.shutdown()
        self.db.close()


async def _retention_loop(engine: Engine) -> None:
    while True:
        try:
            await run_in_threadpool(store.prune_events, engine.db, engine.settings.event_retention_days)
        except Exception:
            pass
        await asyncio.sleep(24 * 3600)


def create_app(settings: Settings | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = Engine(settings, db)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine.startup()
        task = asyncio.create_task(_retention_loop(engine))
        try:
            yield
        finally:
            task.cancel()
            await run_in_threadpool(engine.shutdown)

    app = FastAPI(title="Coco engine", version="0.1.0", lifespan=lifespan,
                  openapi_url=f"{PREFIX}/openapi.json", docs_url="/docs")
    app.state.engine = engine

    # -- errors: one shape everywhere
    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError):
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_req: Request, exc: RequestValidationError):
        details = [{"loc": list(e.get("loc", ())), "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse({"error": {"code": "invalid_request", "message": "request body or parameters are "
                                       "invalid", "details": details}}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_req: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed", 401: "unauthorized"}.get(exc.status_code, "http_error")
        return JSONResponse({"error": {"code": code, "message": str(exc.detail)}}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _internal_error(_req: Request, exc: Exception):
        return JSONResponse({"error": {"code": "internal_error", "message": f"{type(exc).__name__}: {exc}"}},
                            status_code=500)

    # -- optional bearer token (?token= is accepted too, because EventSource cannot set headers)
    async def auth(request: Request) -> None:
        token = settings.api_token
        if not token:
            return
        header = request.headers.get("authorization", "")
        given = header[7:] if header.lower().startswith("bearer ") else request.query_params.get("token")
        if given != token:
            raise ApiError(401, "unauthorized", "missing or wrong API token")

    api = APIRouter(prefix=PREFIX, dependencies=[Depends(auth)])
    db = engine.db
    runs = engine.runs

    def page_args(page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=500)) -> tuple[int, int]:
        return page, size

    # -- health
    @app.get(f"{PREFIX}/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": "0.1.0", "coco_core": coco.__version__, "active_runs": runs.active_count(),
                "max_runs": settings.max_runs}

    # -- workflows
    @api.get("/workflows")
    def list_workflows(q: str | None = None, pg: tuple[int, int] = Depends(page_args)):
        return store.list_workflows(db, q, *pg)

    @api.post("/workflows", status_code=201)
    def create_workflow(body: WorkflowCreate):
        out = store.create_workflow(db, body.source, body.description, body.name)
        runs.invalidate()
        return out

    @api.get("/workflows/{wf}")
    def get_workflow(wf: str):
        return store.get_workflow(db, wf)

    @api.put("/workflows/{wf}")
    def update_workflow(wf: str, body: WorkflowUpdate):
        out = store.save_workflow(db, wf, body.source, body.description, body.name)
        runs.invalidate()
        return out

    @api.delete("/workflows/{wf}", status_code=204)
    def delete_workflow(wf: str):
        w = store.get_workflow_row(db, wf)
        active = db.scalar("SELECT COUNT(*) FROM run WHERE workflow_id = ? AND status IN ('queued','running')",
                           (w["id"],))
        if active:
            raise ApiError(409, "runs_active", f"{active} run(s) of this workflow are still active")
        store.delete_workflow(db, wf)
        runs.invalidate()
        return Response(status_code=204)

    @api.get("/workflows/{wf}/versions")
    def list_versions(wf: str):
        return {"items": store.list_versions(db, wf)}

    @api.get("/workflows/{wf}/versions/{version}")
    def get_version(wf: str, version: str):
        return store.get_version(db, wf, int(version) if version.isdigit() else version)

    @api.get("/workflows/{wf}/flows")
    def list_flows(wf: str):
        w = store.get_workflow(db, wf)
        return {"items": (w["current_version"] or {}).get("flows", [])}

    # -- notes (plain text)
    @api.get("/notes")
    def list_notes(q: str | None = None):
        return {"items": store.list_notes(db, q)}

    @api.post("/notes", status_code=201)
    def create_note(body: NoteIn):
        return store.create_note(db, body.title or "", body.body or "")

    @api.get("/notes/{note_id}")
    def get_note(note_id: str):
        return store.get_note(db, note_id)

    @api.put("/notes/{note_id}")
    def update_note(note_id: str, body: NoteIn):
        return store.update_note(db, note_id, body.title, body.body)

    @api.delete("/notes/{note_id}", status_code=204)
    def delete_note(note_id: str):
        store.delete_note(db, note_id)
        return Response(status_code=204)

    # -- environments
    @api.get("/environments")
    def list_envs():
        return {"items": store.list_envs(db)}

    @api.post("/environments", status_code=201)
    def create_env(body: EnvCreate):
        return store.create_env(db, body.name, body.data, body.description, body.is_default)

    @api.get("/environments/{env}")
    def get_env(env: str):
        return store.get_env(db, env)

    @api.put("/environments/{env}")
    def update_env(env: str, body: EnvUpdate):
        return store.update_env(db, env, name=body.name, data=body.data, description=body.description,
                                is_default=body.is_default)

    @api.delete("/environments/{env}", status_code=204)
    def delete_env(env: str):
        store.delete_env(db, env)
        return Response(status_code=204)

    @api.post("/validate")
    def validate(body: ValidateIn):
        return store.analyze(db, body.source, body.name)

    # -- runs
    @api.post("/workflows/{wf}/flows/{flow}/runs", status_code=201)
    async def start_run(wf: str, flow: str, body: RunCreate):
        run_id = await run_in_threadpool(runs.start, wf, flow, body.input, version=body.version, env=body.env)
        if body.wait:
            fut = runs.future(run_id)
            if fut is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(fut)), settings.wait_timeout_s)
                except asyncio.TimeoutError:
                    pass
        out = await run_in_threadpool(store.get_run, db, run_id)
        return out | {"stream_url": f"{PREFIX}/runs/{run_id}/stream"}

    @api.get("/runs")
    def list_runs(workflow_id: str | None = None, flow: str | None = None, status: str | None = None,
                  trigger: str | None = None, env: str | None = None, since: str | None = None,
                  until: str | None = None, q: str | None = None, pg: tuple[int, int] = Depends(page_args)):
        return store.list_runs(db, workflow_id=workflow_id, flow=flow, status=status, trigger=trigger, env=env,
                               since=since, until=until, q=q, page=pg[0], size=pg[1])

    @api.get("/runs/{run_id}")
    def get_run(run_id: str):
        return store.get_run(db, run_id)

    @api.get("/runs/{run_id}/events")
    def get_events(run_id: str, after_seq: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=5000),
                   types: str | None = None):
        return store.get_events(db, run_id, after_seq, limit,
                                [t for t in types.split(",") if t] if types else None)

    @api.post("/runs/{run_id}/cancel")
    def cancel_run(run_id: str):
        return runs.cancel(run_id)

    @api.post("/runs/{run_id}/rerun", status_code=201)
    def rerun(run_id: str):
        r = store.get_run(db, run_id)
        env = ""
        if r.get("env_id"):
            if not db.one("SELECT id FROM environment WHERE id = ?", (r["env_id"],)):
                raise ApiError(422, "env_not_found", f"environment `{r['env_name']}` used by this run was deleted")
            env = r["env_id"]
        new_id = runs.start(r["workflow_id"], r["flow_name"], r["input"], version_id=r["version_id"],
                            trigger="rerun", env=env)
        return store.get_run(db, new_id) | {"stream_url": f"{PREFIX}/runs/{new_id}/stream"}

    @api.delete("/runs/{run_id}", status_code=204)
    def delete_run(run_id: str):
        store.delete_run(db, run_id)
        return Response(status_code=204)

    @api.get("/runs/{run_id}/stream")
    async def stream(run_id: str, request: Request, after_seq: int = Query(0, ge=0)):
        await run_in_threadpool(store.get_run, db, run_id)  # 404 if unknown
        loop = asyncio.get_running_loop()
        sub = engine.hub.subscribe(run_id, loop)

        def sse(ev: dict[str, Any]) -> str:
            return f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"

        async def gen():
            last = after_seq
            try:
                if sub is not None:
                    backlog, q = sub
                    for ev in backlog:
                        if ev["seq"] > last:
                            last = ev["seq"]
                            yield sse(ev)
                    while True:
                        try:
                            ev = await asyncio.wait_for(q.get(), settings.sse_heartbeat_s)
                        except asyncio.TimeoutError:
                            if await request.is_disconnected():
                                return
                            yield ": keep-alive\n\n"
                            continue
                        if ev is None:
                            break
                        if ev["seq"] > last:
                            last = ev["seq"]
                            yield sse(ev)
                else:
                    while True:
                        page = await run_in_threadpool(store.get_events, db, run_id, last, 1000)
                        for ev in page["items"]:
                            last = ev["seq"]
                            yield sse(ev)
                        if not page["has_more"]:
                            break
                final = await run_in_threadpool(store.get_run, db, run_id)
                end = {"id": run_id, "status": final["status"], "output": final["output"], "error": final["error"],
                       "duration_ms": final["duration_ms"]}
                yield f"event: end\ndata: {json.dumps(end, ensure_ascii=False)}\n\n"
            finally:
                if sub is not None:
                    engine.hub.unsubscribe(run_id, sub[1])

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    app.include_router(api)

    @app.api_route(PREFIX + "/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def api_not_found(rest: str):
        raise ApiError(404, "not_found", f"no API route `{PREFIX}/{rest}`")

    # the built UI (nuxt generate) on the same port; API routes above take precedence
    if settings.ui_dir and os.path.isfile(os.path.join(settings.ui_dir, "index.html")):
        app.mount("/", StaticFiles(directory=settings.ui_dir, html=True), name="ui")
    else:
        @app.get("/", include_in_schema=False)
        def no_ui() -> dict[str, str]:
            return {"message": "Coco engine is running. Build the UI with `npm run build` in ui/, "
                               "or open /docs for the API."}

    return app
