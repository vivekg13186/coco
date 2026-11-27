# coco-server

The Coco engine (milestone 2): workflow storage with versions, a thread-pool run worker, persisted events,
live Server-Sent Events, and the REST API under `/api/v1`. Built on `coco-core`.

## Run it

```bash
cd ~/Developer/coco
source .venv/bin/activate
pip install -e "core[dev]" -e "server[dev]"

coco-server                         # http://127.0.0.1:8765, db at ~/.coco/coco.db, API docs at /docs
                                    # serves the web UI at / (the build committed in coco_server/ui)
coco-server --db ./coco.db --port 8765 --max-runs 4
pytest core server
```

Settings come from flags or environment variables: `COCO_DB`, `COCO_HOST`, `COCO_PORT`, `COCO_MAX_RUNS`
(default 4), `COCO_API_TOKEN` (off by default; when set, send `Authorization: Bearer <token>` or `?token=`),
`COCO_EVENT_RETENTION_DAYS` (default 90), `COCO_UI_DIR` (default: the built UI shipped in `coco_server/ui`, refreshed by `npm run build` in `ui/`).

## Try it with curl

```bash
B=http://127.0.0.1:8765/api/v1
jq -n --rawfile s examples/onboard.coco '{source: $s}' | curl -s -X POST $B/workflows -H 'content-type: application/json' -d @-
jq -n --slurpfile i examples/onboard.input.json '{input: $i[0]}' \
  | curl -s -X POST $B/workflows/onboard/flows/onboard/runs -H 'content-type: application/json' -d @-
curl -N $B/runs/<run id>/stream          # every event in order, then `event: end`
```

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness, active runs (no token needed) |
| GET / POST | `/workflows` | List (`?q=&page=&size=`) / create from `{source, description}` |
| GET / PUT / DELETE | `/workflows/{id or name}` | Read with source and symbols / save (new version only when the source changes) / delete |
| GET | `/workflows/{wf}/versions`, `/versions/{n}` | Version history and old sources |
| GET | `/workflows/{wf}/flows` | Flow signatures of the current version |
| POST | `/validate` | `{source}` → diagnostics, flows, symbols; stores nothing (editor live check) |
| POST | `/workflows/{wf}/flows/{flow}/runs` | `{input, version?, wait?, env?}`; `wait` blocks up to 30 s; `env` omitted = default environment, `""` = none |
| GET | `/runs` | Filters: `workflow_id`, `flow`, `status` (comma list), `trigger`, `env`, `since`, `until`, `q` |
| GET | `/runs/{id}` | Run with input, output, error, timings |
| GET | `/runs/{id}/events` | `?after_seq=&limit=&types=log,log.object` |
| GET | `/runs/{id}/stream` | SSE: stored events, then live ones, then `event: end`; `?after_seq=` to resume |
| POST | `/runs/{id}/cancel`, `/runs/{id}/rerun` | Cancel at the next statement / same version, flow and input |
| DELETE | `/runs/{id}` | Delete a finished run and its events |
| GET / POST | `/notes` | List (`?q=` searches title and text; newest first, without bodies) / create `{title, body}` |
| GET / PUT / DELETE | `/notes/{id}` | Read / update (title and/or body, up to 1 MB) / delete |
| GET / POST | `/environments` | List / create `{name, data, description, is_default}`; `data` must be a JSON object |
| GET / PUT / DELETE | `/environments/{id or name}` | Read / update (rename, values, default) / delete |

Every error is `{"error": {"code", "message", "details"?}}`.

## How it works

- **Workflows and versions:** the package name is the workflow name. Saving changed source adds an
  immutable version, valid or not; only valid versions can run. `import other` resolves to the current
  version of the workflow named `other`.
- **Runs:** `POST .../runs` stores a `queued` run and submits it to a `ThreadPoolExecutor`
  (`COCO_MAX_RUNS`). Compiled programs are cached per version and the cache is cleared on any save.
- **Events:** each event is published to the in-memory hub at once and written to SQLite in batches
  (50 events or 200 ms). An SSE client gets the backlog and the live queue under one lock, so it never
  misses or repeats an event; finished runs replay from SQLite.
- **Environments:** named JSON objects (`environment` table). A run resolves its environment when it
  starts (named, default, or none), records `env_id` and `env_name`, and hands the values to the flow as
  `env`. Rerun uses the same environment with its current values. At most one environment is the default.
  Values are stored unencrypted in SQLite; events carry only the name.
- **Restarts:** on startup, runs left `queued` or `running` are marked `failed` with code `interrupted`.
  A daily task deletes events of runs that finished more than `COCO_EVENT_RETENTION_DAYS` ago.

Differences from the design: plain `sqlite3` with numbered migrations (`PRAGMA user_version`) instead of
SQLModel + Alembic, to keep the engine dependency-light. Migration 2 adds environments and migration 3 adds notes; an existing
database is upgraded automatically on start. The `schedule` table is created now; the
scheduler itself is milestone 4.
