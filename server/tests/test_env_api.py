from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coco_server import Settings, create_app
from coco_server.db import MIGRATIONS, Database

EXAMPLES = Path(__file__).parent.parent.parent / "examples"
API = "/api/v1"
DEV = {"user": "vivek", "retries": 5, "api": {"base_url": "https://dev.example.com", "verbose": True}}
PROD = {"user": "svc", "api": {"base_url": "https://api.example.com", "verbose": False}}


@pytest.fixture
def client(tmp_path):
    s = Settings(db_path=str(tmp_path / "coco.db"), ui_dir=None, wait_timeout_s=10)
    with TestClient(create_app(s)) as c:
        c.post(f"{API}/workflows", json={"source": (EXAMPLES / "env_demo.coco").read_text(), "name": "env_demo"})
        yield c


def run(client, **body):
    r = client.post(f"{API}/workflows/env_demo/flows/main/runs", json={"input": {"path": "/u"}, "wait": True, **body})
    return r


def test_environment_crud(client):
    r = client.post(f"{API}/environments", json={"name": "dev", "data": DEV, "description": "local"})
    assert r.status_code == 201
    dev = r.json()
    assert dev["name"] == "dev" and dev["keys"] == ["api", "retries", "user"] and not dev["is_default"]
    assert client.post(f"{API}/environments", json={"name": "dev", "data": {}}).status_code == 409
    bad = client.post(f"{API}/environments", json={"name": "has space", "data": {}})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_name"
    assert client.post(f"{API}/environments", json={"name": "x", "data": [1]}).status_code == 422

    prod = client.post(f"{API}/environments", json={"name": "prod", "data": PROD, "is_default": True}).json()
    assert prod["is_default"]
    # only one default at a time
    client.put(f"{API}/environments/dev", json={"is_default": True})
    envs = {e["name"]: e for e in client.get(f"{API}/environments").json()["items"]}
    assert envs["dev"]["is_default"] and not envs["prod"]["is_default"]
    assert list(envs) == ["dev", "prod"]  # default first

    upd = client.put(f"{API}/environments/{dev['id']}", json={"name": "local", "data": {"user": "me"}}).json()
    assert upd["name"] == "local" and upd["data"] == {"user": "me"}
    assert client.delete(f"{API}/environments/local").status_code == 204
    assert client.get(f"{API}/environments/local").status_code == 404


def test_run_with_named_default_and_no_env(client):
    client.post(f"{API}/environments", json={"name": "dev", "data": DEV})
    client.post(f"{API}/environments", json={"name": "prod", "data": PROD, "is_default": True})

    r = run(client, env="dev").json()
    assert r["status"] == "succeeded" and r["env_name"] == "dev"
    assert r["output"] == {"url": "https://dev.example.com/u?verbose=1", "retries": 5}
    started = client.get(f"{API}/runs/{r['id']}/events", params={"types": "flow.started"}).json()["items"][0]
    assert started["payload"]["env"] == "dev" and "base_url" not in str(started["payload"])

    d = run(client).json()  # no env given -> the default (prod)
    assert d["env_name"] == "prod" and d["output"] == {"url": "https://api.example.com/u", "retries": 3}

    none = run(client, env="").json()  # explicitly none
    assert none["env_name"] is None and none["status"] == "failed"

    missing = run(client, env="staging")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "env_not_found"

    by_env = client.get(f"{API}/runs", params={"env": "dev"}).json()
    assert [x["id"] for x in by_env["items"]] == [r["id"]]


def test_rerun_keeps_environment(client):
    env = client.post(f"{API}/environments", json={"name": "dev", "data": DEV}).json()
    first = run(client, env="dev").json()
    client.put(f"{API}/environments/dev", json={"data": {**DEV, "retries": 9}})
    again = client.post(f"{API}/runs/{first['id']}/rerun").json()
    done = client.get(f"{API}/runs/{again['id']}").json()
    for _ in range(100):
        if done["status"] in ("succeeded", "failed"):
            break
        done = client.get(f"{API}/runs/{again['id']}").json()
    assert done["env_name"] == "dev" and done["output"]["retries"] == 9  # current values of the same env
    client.delete(f"{API}/environments/{env['id']}")
    gone = client.post(f"{API}/runs/{first['id']}/rerun")
    assert gone.status_code == 422 and gone.json()["error"]["code"] == "env_not_found"


def test_existing_v1_database_is_upgraded(tmp_path):
    path = str(tmp_path / "old.db")
    db = Database(path)
    c = db.conn()
    for stmt in [s.strip() for s in MIGRATIONS[0].split(";") if s.strip()]:
        c.execute(stmt)
    c.execute("PRAGMA user_version=1")
    db.close()
    with TestClient(create_app(Settings(db_path=path, ui_dir=None))) as client:
        assert client.get(f"{API}/environments").json() == {"items": []}
    assert Database(path).scalar("PRAGMA user_version") == len(MIGRATIONS)
