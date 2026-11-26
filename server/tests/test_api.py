import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coco_server import Settings, create_app
from coco_server.db import Database, dumps, now_iso

EXAMPLES = Path(__file__).parent.parent.parent / "examples"
API = "/api/v1"

SLOW = """
package slow

step tick(int i)(int out):
    import time
    time.sleep(0.05)
    self.log_value("i", i)
    return i

flow count(int n)(int total):
    int t = 0
    int x
    for i in range(n):
        x = tick(i)
        t = t + x
    return t
"""


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=str(tmp_path / "coco.db"), max_runs=2, api_token=None, wait_timeout_s=10)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def create(client, source, **kw):
    r = client.post(f"{API}/workflows", json={"source": source, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def wait_done(client, run_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"{API}/runs/{run_id}").json()
        if r["status"] in ("succeeded", "failed", "cancelled"):
            return r
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def sse_events(text):
    out = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n") if not line.startswith(":") and ": " in line)
        if "data" in fields:
            out.append((fields.get("event"), json.loads(fields["data"])))
    return out


def test_health(client):
    r = client.get(f"{API}/health").json()
    assert r["ok"] is True and r["max_runs"] == 2


def test_workflow_crud_and_versions(client):
    src = (EXAMPLES / "onboard.coco").read_text()
    wf = create(client, src, description="team onboarding")
    assert wf["name"] == "onboard"
    v = wf["current_version"]
    assert v["version"] == 1 and v["is_valid"] and [f["name"] for f in v["flows"]] == ["notify_member", "onboard"]
    assert v["symbols"]["classes"][0]["name"] == "Person"

    dup = client.post(f"{API}/workflows", json={"source": src})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "duplicate_name"

    same = client.put(f"{API}/workflows/{wf['id']}", json={"source": src}).json()
    assert same["current_version"]["version"] == 1  # unchanged source -> no new version

    v2 = client.put(f"{API}/workflows/onboard", json={"source": src + "\n# v2\n", "description": "d2"}).json()
    assert v2["current_version"]["version"] == 2 and v2["description"] == "d2"

    versions = client.get(f"{API}/workflows/{wf['id']}/versions").json()["items"]
    assert [x["version"] for x in versions] == [2, 1] and versions[0]["is_current"]
    old = client.get(f"{API}/workflows/{wf['id']}/versions/1").json()
    assert old["source"] == src and not old["is_current"]

    listed = client.get(f"{API}/workflows", params={"q": "onb"}).json()
    assert listed["total"] == 1 and "source" not in listed["items"][0]["current_version"]

    assert client.delete(f"{API}/workflows/{wf['id']}").status_code == 204
    assert client.get(f"{API}/workflows/{wf['id']}").json()["error"]["code"] == "not_found"


def test_missing_package_and_error_shape(client):
    r = client.post(f"{API}/workflows", json={"source": "flow f()():\n    pass\n"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "missing_package"
    r = client.post(f"{API}/workflows", json={"nope": 1})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert client.get(f"{API}/nothing").json()["error"]["code"] == "not_found"


def test_validate_reports_diagnostics(client):
    src = (Path(__file__).parent.parent.parent / "core" / "tests" / "golden" / "coco_md_sample.coco").read_text()
    r = client.post(f"{API}/validate", json={"source": src}).json()
    assert r["is_valid"] is False and r["package"] == "maind"
    assert {d["code"] for d in r["diagnostics"]} >= {"E205", "E220", "E221", "E212"}
    assert r["symbols"]["flows"][0]["name"] == "flow1"


def test_invalid_workflow_is_saved_but_cannot_run(client):
    wf = create(client, "package broken\nflow f()(int a):\n    pass\n")
    assert wf["current_version"]["is_valid"] is False
    r = client.post(f"{API}/workflows/broken/flows/f/runs", json={})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_workflow"


def test_run_with_wait_and_events(client):
    create(client, (EXAMPLES / "onboard.coco").read_text())
    inp = json.loads((EXAMPLES / "onboard.input.json").read_text())
    r = client.post(f"{API}/workflows/onboard/flows/onboard/runs", json={"input": inp, "wait": True})
    assert r.status_code == 201
    run = r.json()
    assert run["status"] == "succeeded" and run["output"]["processed"] == 2
    assert run["trigger"] == "manual" and run["workflow_name"] == "onboard" and run["version"] == 1
    assert run["event_count"] == 35

    ev = client.get(f"{API}/runs/{run['id']}/events").json()
    assert [e["seq"] for e in ev["items"]] == list(range(1, 36)) and not ev["has_more"]
    page = client.get(f"{API}/runs/{run['id']}/events", params={"after_seq": 30, "limit": 2}).json()
    assert [e["seq"] for e in page["items"]] == [31, 32] and page["has_more"]
    table = client.get(f"{API}/runs/{run['id']}/events", params={"types": "log.table"}).json()["items"]
    assert len(table) == 1 and table[0]["payload"]["rows"][0] == [0, "Ada", 33]
    logs = client.get(f"{API}/runs/{run['id']}/events", params={"types": "log"}).json()["items"]
    assert logs[1]["payload"]["args"][1]["value"]["age"] == 33

    assert client.post(f"{API}/workflows/onboard/flows/nope/runs", json={}).json()["error"]["code"] == "unknown_flow"


def test_bad_input_fails_the_run(client):
    create(client, (EXAMPLES / "hello.coco").read_text())
    run = client.post(f"{API}/workflows/hello/flows/main/runs", json={"input": {"who": 1}, "wait": True}).json()
    assert run["status"] == "failed" and run["error"]["code"] == "invalid_input"


def test_stream_replays_finished_run(client):
    create(client, (EXAMPLES / "hello.coco").read_text())
    run = client.post(f"{API}/workflows/hello/flows/main/runs",
                      json={"input": {"name": "Ada"}, "wait": True}).json()
    text = client.get(f"{API}/runs/{run['id']}/stream").text
    evs = sse_events(text)
    assert [t for t, _ in evs] == ["flow.started", "step.started", "log", "step.completed",
                                   "flow.completed", "end"]
    assert evs[-1][1]["status"] == "succeeded"
    # resume after a given event
    evs2 = sse_events(client.get(f"{API}/runs/{run['id']}/stream", params={"after_seq": 3}).text)
    assert [t for t, _ in evs2] == ["step.completed", "flow.completed", "end"]


def test_stream_live_run_gets_every_event_once(client):
    create(client, SLOW)
    run = client.post(f"{API}/workflows/slow/flows/count/runs", json={"input": {"n": 6}}).json()
    assert run["status"] in ("queued", "running")
    with client.stream("GET", f"{API}/runs/{run['id']}/stream") as r:
        text = "".join(r.iter_text())
    evs = sse_events(text)
    seqs = [d["seq"] for t, d in evs if t != "end"]
    assert seqs == list(range(1, len(seqs) + 1))
    assert evs[-1][0] == "end" and evs[-1][1]["status"] == "succeeded" and evs[-1][1]["output"] == {"total": 15}
    stored = client.get(f"{API}/runs/{run['id']}/events").json()["items"]
    assert [e["seq"] for e in stored] == seqs


def test_cancel_and_rerun(client):
    create(client, SLOW)
    run = client.post(f"{API}/workflows/slow/flows/count/runs", json={"input": {"n": 100}}).json()
    time.sleep(0.2)
    assert client.post(f"{API}/runs/{run['id']}/cancel").json()["cancel_requested"] is True
    done = wait_done(client, run["id"])
    assert done["status"] == "cancelled"
    again = client.post(f"{API}/runs/{run['id']}/cancel")
    assert again.status_code == 409

    small = client.post(f"{API}/workflows/slow/flows/count/runs", json={"input": {"n": 2}, "wait": True}).json()
    re = client.post(f"{API}/runs/{small['id']}/rerun").json()
    assert re["trigger"] == "rerun" and re["version_id"] == small["version_id"]
    assert wait_done(client, re["id"])["output"] == {"total": 1}


def test_run_list_filters_and_delete(client):
    create(client, (EXAMPLES / "hello.coco").read_text())
    ok = client.post(f"{API}/workflows/hello/flows/main/runs", json={"input": {"name": "a"}, "wait": True}).json()
    bad = client.post(f"{API}/workflows/hello/flows/main/runs", json={"input": {}, "wait": True}).json()
    assert client.get(f"{API}/runs").json()["total"] == 2
    failed = client.get(f"{API}/runs", params={"status": "failed"}).json()
    assert [r["id"] for r in failed["items"]] == [bad["id"]]
    assert "input" not in failed["items"][0]
    by_wf = client.get(f"{API}/runs", params={"workflow_id": "hello", "flow": "main"}).json()
    assert by_wf["total"] == 2
    assert client.delete(f"{API}/runs/{ok['id']}").status_code == 204
    assert client.get(f"{API}/runs/{ok['id']}").status_code == 404


def test_delete_workflow_with_active_run_is_refused(client):
    create(client, SLOW)
    run = client.post(f"{API}/workflows/slow/flows/count/runs", json={"input": {"n": 40}}).json()
    r = client.delete(f"{API}/workflows/slow")
    assert r.status_code == 409 and r.json()["error"]["code"] == "runs_active"
    client.post(f"{API}/runs/{run['id']}/cancel")
    wait_done(client, run["id"])


def test_import_another_workflow(client):
    create(client, "package shared\nclass Item:\n    string sku\nstep price(Item it)(float p):\n    return 9.5\n")
    main = ("package shop\nimport shared\nflow total(shared.Item it, int n)(float t):\n    float p\n"
            "    p = shared.price(it)\n    return p * n\n")
    wf = create(client, main)
    assert wf["current_version"]["is_valid"], wf["current_version"]["diagnostics"]
    run = client.post(f"{API}/workflows/shop/flows/total/runs",
                      json={"input": {"it": {"sku": "A"}, "n": 2}, "wait": True}).json()
    assert run["status"] == "succeeded" and run["output"] == {"t": 19.0}


def test_api_token(tmp_path):
    s = Settings(db_path=str(tmp_path / "t.db"), api_token="secret")
    with TestClient(create_app(s)) as c:
        assert c.get(f"{API}/workflows").status_code == 401
        assert c.get(f"{API}/workflows", headers={"Authorization": "Bearer secret"}).status_code == 200
        assert c.get(f"{API}/workflows", params={"token": "secret"}).status_code == 200
        assert c.get(f"{API}/health").status_code == 200


def test_startup_marks_interrupted_runs_failed(tmp_path):
    s = Settings(db_path=str(tmp_path / "r.db"))
    with TestClient(create_app(s)) as c:
        wf = create(c, (EXAMPLES / "hello.coco").read_text())
    db = Database(s.db_path)
    with db.tx() as conn:
        conn.execute("INSERT INTO run (id, workflow_id, version_id, flow_name, status, trigger, input, queued_at) "
                     "VALUES (?,?,?,?,?,?,?,?)", ("r_stuck", wf["id"], wf["current_version"]["id"], "main",
                                                  "running", "manual", dumps({}), now_iso()))
    db.close()
    with TestClient(create_app(s)) as c:
        r = c.get(f"{API}/runs/r_stuck").json()
        assert r["status"] == "failed" and r["error"]["code"] == "interrupted"


def test_serves_ui_and_json_404_for_api(tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html><title>Coco</title>")
    s = Settings(db_path=str(tmp_path / "u.db"), ui_dir=str(ui))
    with TestClient(create_app(s)) as c:
        assert "<title>Coco</title>" in c.get("/").text
        r = c.get(f"{API}/no/such/route")
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    s2 = Settings(db_path=str(tmp_path / "v.db"), ui_dir=None)
    with TestClient(create_app(s2)) as c:
        assert "Build the UI" in c.get("/").json()["message"]
