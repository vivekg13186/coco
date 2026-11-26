import asyncio
import json
import textwrap
import threading
from pathlib import Path

import coco
from coco.cli import main as cli_main

EXAMPLES = Path(__file__).parent.parent.parent / "examples"


def compile_src(src: str) -> coco.Program:
    return coco.compile(textwrap.dedent(src), "test.coco")


def onboard_input():
    return json.loads((EXAMPLES / "onboard.input.json").read_text())


def test_onboard_end_to_end():
    prog = coco.compile_file(str(EXAMPLES / "onboard.coco"))
    res = prog.run("onboard", onboard_input())
    assert res.ok, res.error
    assert res.output == {"processed": 2, "avg_age": 103 / 3}
    types = [e.type for e in res.events]
    assert types[0] == "flow.started" and types[-1] == "flow.completed"
    assert [e.seq for e in res.events] == list(range(1, len(res.events) + 1))
    # console-style logs keep each value with its structure
    resp = next(e for e in res.events if e.type == "log" and e.payload["message"].startswith("api response"))
    assert resp.payload["args"] == [{"type": "string", "value": "api response:"},
                                    {"type": "object", "value": {"age": 33, "source": "https://api.example"}}]
    warn = next(e for e in res.events if e.type == "log" and e.payload["level"] == "warning")
    assert warn.payload["message"] == "age changed for Grace 0 -> 35"
    assert [a["type"] for a in warn.payload["args"]] == ["string", "string", "number", "string", "number"]
    table = next(e for e in res.events if e.type == "log.table")
    assert table.payload["columns"] == ["(index)", "name", "age"]
    assert table.payload["rows"] == [[0, "Ada", 33], [1, "Grace", 35], [2, "Linus", 35]]
    # print() in a step becomes a log event on that step
    mail = [e for e in res.events if e.type == "log" and e.step == "send_mail"]
    assert [m.payload["message"] for m in mail] == ["mail to Grace: Welcome, Grace", "mail to Linus: Welcome, Linus"]
    # sub-flow events are nested
    assert any(e.type == "subflow.started" and e.path.endswith("if#0.then/notify_member") for e in res.events)


def test_onboard_event_sequence_snapshot():
    prog = coco.compile_file(str(EXAMPLES / "onboard.coco"))
    res = prog.run("onboard", onboard_input())
    seq = [(e.type, e.path) for e in res.events][:7]
    assert seq == [
        ("flow.started", "onboard"),
        ("loop.iteration", "onboard/for#0"),
        ("step.started", "onboard/for#0[0]/enrich"),
        ("log", "onboard/for#0[0]/enrich"),
        ("log", "onboard/for#0[0]/enrich"),
        ("step.completed", "onboard/for#0[0]/enrich"),
        ("loop.iteration", "onboard/for#0"),
    ]
    assert [e.type for e in res.events][-5:] == ["step.started", "log.table", "step.completed", "branch.taken",
                                                 "flow.completed"]
    assert len(res.events) == 35


def test_step_exception_fails_flow_with_traceback_line():
    prog = compile_src("""
    package p
    step boom(int a)(int b):
        self.log("about to fail")
        return 10 // a
    flow f(int a)(int b):
        int x
        x = boom(a)
        return x
    """)
    res = prog.run("f", {"a": 0})
    assert res.status == "failed"
    types = [e.type for e in res.events]
    assert types == ["flow.started", "step.started", "log", "step.failed", "flow.failed"]
    err = res.events[3].payload["error"]
    assert err["code"] == "step_error" and "ZeroDivisionError" in err["message"]
    assert 'test.coco", line 5' in err["traceback"]


def test_invalid_input_and_coercion():
    prog = compile_src("""
    package p
    flow f(int a, float b)(float c):
        return a + b
    """)
    assert prog.run("f", {"a": "3", "b": 1}).output == {"c": 4.0}
    bad = prog.run("f", {"a": "x", "b": 1})
    assert bad.status == "failed" and bad.error["code"] == "invalid_input"
    missing = prog.run("f", {"a": 1})
    assert "missing input(s): b" in missing.error["message"]


def test_step_must_return_declared_outputs():
    prog = compile_src("""
    package p
    step s()(int a, int b):
        return 1
    flow f()(int a):
        int x, y
        (x, y) = s()
        return x
    """)
    res = prog.run("f", {})
    assert res.error["code"] == "invalid_output"
    assert [e.type for e in res.events][-2:] == ["step.failed", "flow.failed"]


def test_expression_error_is_reported():
    prog = compile_src("""
    package p
    flow f(int a)(int b):
        int x
        x = 10 // a
        return x
    """)
    res = prog.run("f", {"a": 0})
    assert res.error["code"] == "expression_error" and "line 5" in res.error["message"]


def test_classes_validate_and_serialize():
    prog = compile_src("""
    package p
    class Person:
        string name
        int age = 0
    class Team:
        string title
        Person members[]
    step oldest(Team t)(Person p):
        return max(t.members, key=lambda m: m.age)
    flow f(Team t)(Person p):
        Person o
        o = oldest(t)
        return o
    """)
    res = prog.run("f", {"t": {"title": "x", "members": [{"name": "a", "age": 3}, {"name": "b", "age": 9}]}})
    assert res.output == {"p": {"name": "b", "age": 9}}
    bad = prog.run("f", {"t": {"title": "x", "members": [{"age": 3}]}})
    assert bad.error["code"] == "invalid_input" and "name" in bad.error["message"]


def test_cancel_before_start():
    prog = compile_src("""
    package p
    step s()():
        pass
    flow f()():
        s()
    """)
    ev = threading.Event()
    ev.set()
    res = prog.run("f", {}, cancel=ev)
    assert res.status == "cancelled"
    assert [e.type for e in res.events] == ["flow.started", "flow.cancelled"]


def test_cancel_mid_loop():
    prog = compile_src("""
    package p
    step tick(int i)():
        if i == 2:
            self.log("cancel please")
    flow f()():
        for i in range(10):
            tick(i)
    """)
    ev = threading.Event()

    def on_event(e):
        if e.type == "log":
            ev.set()

    res = prog.run("f", {}, on_event=on_event, cancel=ev)
    assert res.status == "cancelled"
    assert sum(1 for e in res.events if e.type == "step.completed") == 3


def test_stream_yields_events_in_order():
    prog = coco.compile_file(str(EXAMPLES / "hello.coco"))

    async def collect():
        return [e async for e in prog.stream("main", {"name": "Vivek"})]

    events = asyncio.run(collect())
    assert [e.type for e in events] == ["flow.started", "step.started", "log", "step.completed",
                                        "flow.completed"]
    assert events[-1].payload["output"] == {"message": "Hello, Vivek!, Hello, Vivek!"}


def test_parallel_runs_keep_print_output_apart():
    prog = compile_src("""
    package p
    step say(string who)():
        for i in range(50):
            print(who, i)
    flow f(string who)():
        say(who)
    """)
    results = {}

    def go(who):
        results[who] = prog.run("f", {"who": who})

    threads = [threading.Thread(target=go, args=(w,)) for w in ("a", "b", "c")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for who, res in results.items():
        logs = [e.payload["message"] for e in res.events if e.type == "log"]
        assert logs == [f"{who} {i}" for i in range(50)]


def test_self_identity_and_log_levels():
    prog = compile_src("""
    package p
    step s()(string id):
        self.log("careful", level="warning")
        return self.flow + ":" + self.step + ":" + self.path
    flow f()(string id):
        string x
        x = s()
        return x
    """)
    res = prog.run("f", {}, run_id="r_test")
    assert res.run_id == "r_test"
    assert res.output == {"id": "f:s:f/s"}
    assert res.events[2].payload == {"level": "warning", "message": "careful",
                                     "args": [{"type": "string", "value": "careful"}]}


def test_cli_check_and_run(capsys, tmp_path):
    assert cli_main(["check", str(EXAMPLES / "onboard.coco")]) == 0
    bad = tmp_path / "bad.coco"
    bad.write_text("package p\nflow f()(int a):\n    pass\n")
    assert cli_main(["check", str(bad)]) == 1
    capsys.readouterr()
    assert cli_main(["run", str(EXAMPLES / "hello.coco"), "main", "-s", "name=Ada", "-e", "json"]) == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.strip().splitlines()]
    assert lines[-1]["type"] == "flow.completed"


def test_console_style_logging():
    prog = compile_src("""
    package p
    class Item:
        string sku
        int qty = 1
    step s()():
        self.log("mixed", 1, 2.5, True, None, [1, "a"], {"k": {"n": 1}}, Item(sku="x"))
        self.debug("d")
        self.info("i")
        self.error("boom", {"code": 7})
        self.log_object("legacy", {"a": 1})
        self.log_value("legacy value", 3)
        self.log_table([{"a": 1, "b": "x"}, {"a": 2, "c": True}])
        self.log_table([[1, 2], [3, 4, 5]])
        self.log_table(["red", "green"])
        self.log_table({"ada": {"age": 33}, "bob": {"age": 40}}, columns=["age"])
        self.log_table([Item(sku="a"), Item(sku="b", qty=3)])
    flow f()():
        s()
    """)
    res = prog.run("f", {})
    assert res.ok, res.error
    logs = [e.payload for e in res.events if e.type == "log"]
    mixed = logs[0]
    assert [a["type"] for a in mixed["args"]] == ["string", "number", "number", "boolean", "null", "array", "object", "object"]
    assert mixed["args"][7] == {"type": "object", "value": {"sku": "x", "qty": 1}, "class": "Item"}
    assert mixed["message"] == 'mixed 1 2.5 True None [1, "a"] {"k": {"n": 1}} Item {"sku": "x", "qty": 1}'
    assert [p["level"] for p in logs[1:4]] == ["debug", "info", "error"]
    assert logs[4]["message"] == 'legacy: {"a": 1}' and logs[5]["message"] == "legacy value = 3"

    tables = [e.payload for e in res.events if e.type == "log.table"]
    assert tables[0]["columns"] == ["(index)", "a", "b", "c"]
    assert tables[0]["rows"] == [[0, 1, "x", None], [1, 2, None, True]]
    assert tables[1]["columns"] == ["(index)", "0", "1", "2"] and tables[1]["rows"][0] == [0, 1, 2, None]
    assert tables[2]["columns"] == ["(index)", "Values"] and tables[2]["rows"] == [[0, "red"], [1, "green"]]
    assert tables[3]["columns"] == ["(index)", "age"] and tables[3]["rows"] == [["ada", 33], ["bob", 40]]
    assert tables[4]["rows"] == [[0, "a", 1], [1, "b", 3]]
    assert all(t["row_count"] == len(t["rows"]) and not t["truncated"] for t in tables)


def test_log_table_limits_and_errors():
    prog = compile_src("""
    package p
    step big()():
        self.log_table([{"i": i} for i in range(1500)])
    step bad()():
        self.log_table(42)
    flow f()():
        big()
    flow g()():
        bad()
    """)
    t = next(e.payload for e in prog.run("f", {}).events if e.type == "log.table")
    assert t["row_count"] == 1500 and len(t["rows"]) == 1000 and t["truncated"]
    res = prog.run("g", {})
    assert res.status == "failed" and "log_table needs a list or a dict" in res.error["message"]
