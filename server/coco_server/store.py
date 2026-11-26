"""Data access for workflows, versions, runs and events."""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
from typing import Any, Callable

import coco

from .db import Database, dumps, loads, new_id, now_iso
from .errors import ApiError, not_found

TERMINAL = ("succeeded", "failed", "cancelled")


def checksum(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- analysis
def make_loader(db: Database, exclude: str | None = None) -> Callable[[str], str | None]:
    """Resolve `import other` to the current source of the workflow named `other`."""

    def load(name: str) -> str | None:
        if name == exclude:
            return None
        row = db.one("SELECT v.source FROM workflow w JOIN workflow_version v ON v.id = w.current_version_id "
                     "WHERE w.name = ?", (name,))
        return row["source"] if row else None

    return load


def analyze(db: Database, source: str, own_name: str | None = None) -> dict[str, Any]:
    sym = coco.symbols(source)
    name = own_name or sym["package"] or "workflow"
    loader = make_loader(db, exclude=sym["package"])
    diags = coco.check(source, f"{name}.coco", loader)
    sym = coco.symbols(source, f"{name}.coco", loader)
    return {
        "package": sym["package"],
        "is_valid": not any(d.severity == "error" for d in diags),
        "diagnostics": [d.to_dict() for d in diags],
        "flows": sym["flows"],
        "symbols": sym,
    }


# ---------------------------------------------------------------- workflows
def _version_out(v: dict[str, Any] | None, with_source: bool = True) -> dict[str, Any] | None:
    if v is None:
        return None
    out = {
        "id": v["id"], "version": v["version"], "checksum": v["checksum"], "is_valid": bool(v["is_valid"]),
        "diagnostics": loads(v["diagnostics"]), "flows": loads(v["flows"]), "created_at": v["created_at"],
    }
    if with_source:
        out["source"] = v["source"]
        out["symbols"] = loads(v["symbols"])
    return out


def _workflow_out(db: Database, w: dict[str, Any], full: bool) -> dict[str, Any]:
    v = db.one("SELECT * FROM workflow_version WHERE id = ?", (w["current_version_id"],))
    last = db.one("SELECT id, status, flow_name, queued_at, finished_at FROM run WHERE workflow_id = ? "
                  "ORDER BY queued_at DESC LIMIT 1", (w["id"],))
    return {
        "id": w["id"], "name": w["name"], "description": w["description"],
        "created_at": w["created_at"], "updated_at": w["updated_at"],
        "current_version": _version_out(v, with_source=full),
        "last_run": last,
    }


def list_workflows(db: Database, q: str | None, page: int, size: int) -> dict[str, Any]:
    where, args = "", []
    if q:
        where, args = "WHERE name LIKE ? OR description LIKE ?", [f"%{q}%", f"%{q}%"]
    total = db.scalar(f"SELECT COUNT(*) FROM workflow {where}", args)
    rows = db.all(f"SELECT * FROM workflow {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                  args + [size, (page - 1) * size])
    return {"items": [_workflow_out(db, w, full=False) for w in rows], "total": total, "page": page, "size": size}


def get_workflow_row(db: Database, wf_id: str) -> dict[str, Any]:
    w = db.one("SELECT * FROM workflow WHERE id = ? OR name = ?", (wf_id, wf_id))
    if not w:
        raise not_found("workflow", wf_id)
    return w


def get_workflow(db: Database, wf_id: str) -> dict[str, Any]:
    return _workflow_out(db, get_workflow_row(db, wf_id), full=True)


def _insert_version(c, wf_id: str, number: int, source: str, info: dict[str, Any]) -> str:
    vid = new_id("v")
    c.execute("INSERT INTO workflow_version (id, workflow_id, version, source, checksum, is_valid, diagnostics, "
              "flows, symbols, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
              (vid, wf_id, number, source, checksum(source), int(info["is_valid"]), dumps(info["diagnostics"]),
               dumps(info["flows"]), dumps(info["symbols"]), now_iso()))
    return vid


def _require_package(info: dict[str, Any]) -> str:
    if not info["package"]:
        raise ApiError(422, "missing_package", "the source must start with `package <name>`; "
                                               "the package name is the workflow name",
                       details=info["diagnostics"])
    return info["package"]


def create_workflow(db: Database, source: str, description: str = "", name: str | None = None) -> dict[str, Any]:
    info = analyze(db, source)
    pkg = _require_package(info)
    if name and name != pkg:
        raise ApiError(422, "name_mismatch", f"workflow name `{name}` must match `package {pkg}`")
    if db.one("SELECT id FROM workflow WHERE name = ?", (pkg,)):
        raise ApiError(409, "duplicate_name", f"a workflow named `{pkg}` already exists")
    wf_id, ts = new_id("wf"), now_iso()
    with db.tx() as c:
        c.execute("INSERT INTO workflow (id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
                  (wf_id, pkg, description or "", ts, ts))
        vid = _insert_version(c, wf_id, 1, source, info)
        c.execute("UPDATE workflow SET current_version_id = ? WHERE id = ?", (vid, wf_id))
    return get_workflow(db, wf_id)


def save_workflow(db: Database, wf_id: str, source: str | None, description: str | None) -> dict[str, Any]:
    w = get_workflow_row(db, wf_id)
    cur = db.one("SELECT * FROM workflow_version WHERE id = ?", (w["current_version_id"],))
    with db.tx() as c:
        if source is not None and (cur is None or checksum(source) != cur["checksum"]):
            info = analyze(db, source, own_name=w["name"])
            pkg = _require_package(info)
            if pkg != w["name"]:
                other = c.execute("SELECT id FROM workflow WHERE name = ? AND id != ?", (pkg, w["id"])).fetchone()
                if other:
                    raise ApiError(409, "duplicate_name", f"a workflow named `{pkg}` already exists")
                c.execute("UPDATE workflow SET name = ? WHERE id = ?", (pkg, w["id"]))
            number = (c.execute("SELECT MAX(version) FROM workflow_version WHERE workflow_id = ?",
                                (w["id"],)).fetchone()[0] or 0) + 1
            vid = _insert_version(c, w["id"], number, source, info)
            c.execute("UPDATE workflow SET current_version_id = ?, updated_at = ? WHERE id = ?",
                      (vid, now_iso(), w["id"]))
        if description is not None and description != w["description"]:
            c.execute("UPDATE workflow SET description = ?, updated_at = ? WHERE id = ?",
                      (description, now_iso(), w["id"]))
    return get_workflow(db, w["id"])


def delete_workflow(db: Database, wf_id: str) -> None:
    w = get_workflow_row(db, wf_id)
    with db.tx() as c:
        c.execute("UPDATE workflow SET current_version_id = NULL WHERE id = ?", (w["id"],))
        c.execute("DELETE FROM workflow WHERE id = ?", (w["id"],))


def list_versions(db: Database, wf_id: str) -> list[dict[str, Any]]:
    w = get_workflow_row(db, wf_id)
    rows = db.all("SELECT * FROM workflow_version WHERE workflow_id = ? ORDER BY version DESC", (w["id"],))
    return [_version_out(v, with_source=False) | {"is_current": v["id"] == w["current_version_id"]} for v in rows]


def get_version(db: Database, wf_id: str, version: int | str) -> dict[str, Any]:
    w = get_workflow_row(db, wf_id)
    v = db.one("SELECT * FROM workflow_version WHERE workflow_id = ? AND (version = ? OR id = ?)",
               (w["id"], version, version))
    if not v:
        raise not_found("version", str(version))
    return _version_out(v) | {"workflow_id": w["id"], "is_current": v["id"] == w["current_version_id"]}


# ---------------------------------------------------------------- runs
def _run_out(r: dict[str, Any]) -> dict[str, Any]:
    out = dict(r)
    for k in ("input", "output", "error"):
        out[k] = loads(r.get(k))
    return out


RUN_LIST_SQL = ("SELECT r.*, w.name AS workflow_name, v.version AS version FROM run r "
                "JOIN workflow w ON w.id = r.workflow_id JOIN workflow_version v ON v.id = r.version_id")


def create_run(db: Database, wf_id: str, version_id: str, flow: str, inputs: dict[str, Any], trigger: str,
               schedule_id: str | None = None, env_id: str | None = None, env_name: str | None = None) -> str:
    run_id = new_id("r")
    with db.tx() as c:
        c.execute("INSERT INTO run (id, workflow_id, version_id, flow_name, status, trigger, schedule_id, input, "
                  "queued_at, env_id, env_name) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (run_id, wf_id, version_id, flow, "queued", trigger, schedule_id, dumps(inputs), now_iso(),
                   env_id, env_name))
    return run_id


def mark_running(db: Database, run_id: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE run SET status = 'running', started_at = ? WHERE id = ? AND status = 'queued'",
                  (now_iso(), run_id))


def finish_run(db: Database, run_id: str, status: str, output: Any, error: Any, duration_ms: int | None,
               event_count: int) -> None:
    with db.tx() as c:
        c.execute("UPDATE run SET status = ?, output = ?, error = ?, finished_at = ?, duration_ms = ?, "
                  "event_count = ?, started_at = COALESCE(started_at, ?) WHERE id = ?",
                  (status, dumps(output) if output is not None else None,
                   dumps(error) if error is not None else None, now_iso(), duration_ms, event_count, now_iso(),
                   run_id))


def insert_events(db: Database, run_id: str, events: list[dict[str, Any]]) -> None:
    if not events:
        return
    with db.tx() as c:
        c.executemany("INSERT OR IGNORE INTO run_event (run_id, seq, type, path, step_name, payload, ts) "
                      "VALUES (?,?,?,?,?,?,?)",
                      [(run_id, e["seq"], e["type"], e.get("path"), e.get("step"), dumps(e.get("payload") or {}),
                        e["ts"]) for e in events])


def get_run(db: Database, run_id: str) -> dict[str, Any]:
    r = db.one(RUN_LIST_SQL + " WHERE r.id = ?", (run_id,))
    if not r:
        raise not_found("run", run_id)
    return _run_out(r)


def list_runs(db: Database, *, workflow_id: str | None = None, flow: str | None = None,
              status: str | None = None, trigger: str | None = None, env: str | None = None,
              since: str | None = None,
              until: str | None = None, q: str | None = None, page: int = 1, size: int = 50) -> dict[str, Any]:
    conds, args = [], []
    if workflow_id:
        w = get_workflow_row(db, workflow_id)
        conds.append("r.workflow_id = ?")
        args.append(w["id"])
    for col, val in (("r.flow_name", flow), ("r.trigger", trigger), ("r.env_name", env)):
        if val:
            conds.append(f"{col} = ?")
            args.append(val)
    if status:
        sts = [s.strip() for s in status.split(",") if s.strip()]
        conds.append(f"r.status IN ({','.join('?' * len(sts))})")
        args.extend(sts)
    if since:
        conds.append("r.queued_at >= ?")
        args.append(since)
    if until:
        conds.append("r.queued_at < ?")
        args.append(until)
    if q:
        conds.append("(w.name LIKE ? OR r.flow_name LIKE ? OR r.id LIKE ?)")
        args.extend([f"%{q}%"] * 3)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    total = db.scalar("SELECT COUNT(*) FROM run r JOIN workflow w ON w.id = r.workflow_id" + where, args)
    rows = db.all(RUN_LIST_SQL + where + " ORDER BY r.queued_at DESC LIMIT ? OFFSET ?",
                  args + [size, (page - 1) * size])
    items = []
    for r in rows:
        out = _run_out(r)
        out.pop("input", None)
        out.pop("output", None)
        items.append(out)
    return {"items": items, "total": total, "page": page, "size": size}


def event_out(e: dict[str, Any], run_id: str) -> dict[str, Any]:
    return {"run_id": run_id, "seq": e["seq"], "type": e["type"], "ts": e["ts"], "path": e["path"],
            "step": e["step_name"], "payload": loads(e["payload"])}


def get_events(db: Database, run_id: str, after_seq: int = 0, limit: int = 500,
               types: list[str] | None = None) -> dict[str, Any]:
    get_run(db, run_id)
    conds, args = ["run_id = ?", "seq > ?"], [run_id, after_seq]
    if types:
        conds.append(f"type IN ({','.join('?' * len(types))})")
        args.extend(types)
    rows = db.all(f"SELECT * FROM run_event WHERE {' AND '.join(conds)} ORDER BY seq LIMIT ?", args + [limit + 1])
    more = len(rows) > limit
    rows = rows[:limit]
    return {"items": [event_out(e, run_id) for e in rows], "has_more": more,
            "next_after_seq": rows[-1]["seq"] if rows else after_seq}


def delete_run(db: Database, run_id: str) -> None:
    r = get_run(db, run_id)
    if r["status"] not in TERMINAL:
        raise ApiError(409, "run_active", "cancel the run before deleting it")
    with db.tx() as c:
        c.execute("DELETE FROM run WHERE id = ?", (run_id,))


def recover_interrupted(db: Database) -> int:
    """Runs left queued or running by a previous server process can never finish."""
    err = dumps({"code": "interrupted", "message": "the server stopped while this run was active"})
    with db.tx() as c:
        cur = c.execute("UPDATE run SET status = 'failed', error = ?, finished_at = ? "
                        "WHERE status IN ('queued', 'running')", (err, now_iso()))
        return cur.rowcount


def prune_events(db: Database, days: int) -> int:
    if days <= 0:
        return 0
    cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)).isoformat(timespec="milliseconds")
    cutoff = cutoff.replace("+00:00", "Z")
    with db.tx() as c:
        cur = c.execute("DELETE FROM run_event WHERE run_id IN (SELECT id FROM run WHERE finished_at < ?)",
                        (cutoff,))
        return cur.rowcount


# ---------------------------------------------------------------- environments
ENV_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


def _env_out(r: dict[str, Any]) -> dict[str, Any]:
    out = dict(r)
    out["data"] = loads(r["data"])
    out["is_default"] = bool(r["is_default"])
    out["keys"] = sorted(out["data"].keys()) if isinstance(out["data"], dict) else []
    return out


def _check_env(name: str | None, data: Any) -> None:
    if name is not None and not ENV_NAME_RE.match(name):
        raise ApiError(422, "invalid_name", "environment names use letters, digits, `_`, `-` and `.` (max 64)")
    if data is not None and not isinstance(data, dict):
        raise ApiError(422, "invalid_env", "an environment must be a JSON object, e.g. {\"api_url\": \"...\"}")


def list_envs(db: Database) -> list[dict[str, Any]]:
    return [_env_out(r) for r in db.all("SELECT * FROM environment ORDER BY is_default DESC, name")]


def get_env_row(db: Database, ident: str) -> dict[str, Any]:
    r = db.one("SELECT * FROM environment WHERE id = ? OR name = ?", (ident, ident))
    if not r:
        raise not_found("environment", ident)
    return r


def get_env(db: Database, ident: str) -> dict[str, Any]:
    return _env_out(get_env_row(db, ident))


def default_env(db: Database) -> dict[str, Any] | None:
    r = db.one("SELECT * FROM environment WHERE is_default = 1 LIMIT 1")
    return _env_out(r) if r else None


def create_env(db: Database, name: str, data: dict[str, Any] | None, description: str = "",
               is_default: bool = False) -> dict[str, Any]:
    _check_env(name, data)
    if db.one("SELECT id FROM environment WHERE name = ?", (name,)):
        raise ApiError(409, "duplicate_name", f"an environment named `{name}` already exists")
    env_id, ts = new_id("env"), now_iso()
    with db.tx() as c:
        if is_default:
            c.execute("UPDATE environment SET is_default = 0")
        c.execute("INSERT INTO environment (id, name, description, data, is_default, created_at, updated_at) "
                  "VALUES (?,?,?,?,?,?,?)", (env_id, name, description or "", dumps(data or {}), int(is_default), ts, ts))
    return get_env(db, env_id)


def update_env(db: Database, ident: str, *, name: str | None = None, data: dict[str, Any] | None = None,
               description: str | None = None, is_default: bool | None = None) -> dict[str, Any]:
    r = get_env_row(db, ident)
    _check_env(name, data)
    with db.tx() as c:
        if name is not None and name != r["name"]:
            if c.execute("SELECT id FROM environment WHERE name = ? AND id != ?", (name, r["id"])).fetchone():
                raise ApiError(409, "duplicate_name", f"an environment named `{name}` already exists")
            c.execute("UPDATE environment SET name = ? WHERE id = ?", (name, r["id"]))
        if data is not None:
            c.execute("UPDATE environment SET data = ? WHERE id = ?", (dumps(data), r["id"]))
        if description is not None:
            c.execute("UPDATE environment SET description = ? WHERE id = ?", (description, r["id"]))
        if is_default is not None:
            if is_default:
                c.execute("UPDATE environment SET is_default = 0")
            c.execute("UPDATE environment SET is_default = ? WHERE id = ?", (int(is_default), r["id"]))
        c.execute("UPDATE environment SET updated_at = ? WHERE id = ?", (now_iso(), r["id"]))
    return get_env(db, r["id"])


def delete_env(db: Database, ident: str) -> None:
    r = get_env_row(db, ident)
    with db.tx() as c:
        c.execute("DELETE FROM environment WHERE id = ?", (r["id"],))


# ---------------------------------------------------------------- notes (plain text)
MAX_NOTE_BYTES = 1_000_000


def _note_out(r: dict[str, Any], full: bool = True) -> dict[str, Any]:
    out = dict(r)
    body = r["body"]
    first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
    out["preview"] = first[:120]
    out["chars"] = len(body)
    if not full:
        out.pop("body")
    return out


def _check_note(body: str | None) -> None:
    if body is not None and len(body.encode("utf-8")) > MAX_NOTE_BYTES:
        raise ApiError(413, "note_too_large", "a note can hold at most 1 MB of text")


def list_notes(db: Database, q: str | None = None) -> list[dict[str, Any]]:
    if q:
        rows = db.all("SELECT * FROM note WHERE title LIKE ? OR body LIKE ? ORDER BY updated_at DESC",
                      (f"%{q}%", f"%{q}%"))
    else:
        rows = db.all("SELECT * FROM note ORDER BY updated_at DESC")
    return [_note_out(r, full=False) for r in rows]


def get_note(db: Database, note_id: str) -> dict[str, Any]:
    r = db.one("SELECT * FROM note WHERE id = ?", (note_id,))
    if not r:
        raise not_found("note", note_id)
    return _note_out(r)


def create_note(db: Database, title: str = "", body: str = "") -> dict[str, Any]:
    _check_note(body)
    note_id, ts = new_id("n"), now_iso()
    with db.tx() as c:
        c.execute("INSERT INTO note (id, title, body, created_at, updated_at) VALUES (?,?,?,?,?)",
                  (note_id, title, body, ts, ts))
    return get_note(db, note_id)


def update_note(db: Database, note_id: str, title: str | None = None, body: str | None = None) -> dict[str, Any]:
    get_note(db, note_id)
    _check_note(body)
    with db.tx() as c:
        if title is not None:
            c.execute("UPDATE note SET title = ? WHERE id = ?", (title, note_id))
        if body is not None:
            c.execute("UPDATE note SET body = ? WHERE id = ?", (body, note_id))
        c.execute("UPDATE note SET updated_at = ? WHERE id = ?", (now_iso(), note_id))
    return get_note(db, note_id)


def delete_note(db: Database, note_id: str) -> None:
    get_note(db, note_id)
    with db.tx() as c:
        c.execute("DELETE FROM note WHERE id = ?", (note_id,))
