from fastapi.testclient import TestClient

from coco_server import Settings, create_app
from coco_server.db import MIGRATIONS, Database

API = "/api/v1"


def test_notes_crud_and_search(tmp_path):
    with TestClient(create_app(Settings(db_path=str(tmp_path / "n.db"), ui_dir=None))) as c:
        assert c.get(f"{API}/notes").json() == {"items": []}
        a = c.post(f"{API}/notes", json={"title": "Ideas", "body": "\n  retry policy for steps\nsecond line"}).json()
        assert a["title"] == "Ideas" and a["preview"] == "retry policy for steps" and a["chars"] == 37
        b = c.post(f"{API}/notes", json={}).json()
        assert b["title"] == "" and b["body"] == ""

        listed = c.get(f"{API}/notes").json()["items"]
        assert [n["id"] for n in listed] == [b["id"], a["id"]]  # newest first
        assert "body" not in listed[0]

        upd = c.put(f"{API}/notes/{b['id']}", json={"body": "todo: scheduler"}).json()
        assert upd["body"] == "todo: scheduler" and upd["title"] == ""
        assert [n["id"] for n in c.get(f"{API}/notes", params={"q": "retry"}).json()["items"]] == [a["id"]]
        assert [n["id"] for n in c.get(f"{API}/notes", params={"q": "SCHED"}).json()["items"]] == [b["id"]]

        big = c.put(f"{API}/notes/{a['id']}", json={"body": "x" * 1_000_001})
        assert big.status_code == 413 and big.json()["error"]["code"] == "note_too_large"

        assert c.delete(f"{API}/notes/{a['id']}").status_code == 204
        assert c.get(f"{API}/notes/{a['id']}").status_code == 404
        assert c.put(f"{API}/notes/{a['id']}", json={"body": "x"}).status_code == 404


def test_v2_database_gets_notes(tmp_path):
    path = str(tmp_path / "old.db")
    db = Database(path)
    conn = db.conn()
    for script in MIGRATIONS[:2]:
        for stmt in [s.strip() for s in script.split(";") if s.strip()]:
            conn.execute(stmt)
    conn.execute("PRAGMA user_version=2")
    db.close()
    with TestClient(create_app(Settings(db_path=path, ui_dir=None))) as c:
        assert c.post(f"{API}/notes", json={"title": "t"}).status_code == 201
    assert Database(path).scalar("PRAGMA user_version") == 3
