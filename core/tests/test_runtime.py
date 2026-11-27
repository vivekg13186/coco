import asyncio
import json
import textwrap
import threading
from pathlib import Path

import pytest

import coco
from coco.cli import main as cli_main

EXAMPLES = Path(__file__).parent.parent.parent / "examples"
HEAD = "from coco import step, flow, log, info, warn, error, debug, log_table, env, context\n"


def compile_src(src: str) -> coco.Program:
    return coco.compile(HEAD + textwrap.dedent(src), "test.coco", name="test")


def onboard_input():
    return json.loads((EXAMPLES / "onboard.input.json").read_text())


def test_onboard_end_to_end():
    prog = coco.compile_file(str(EXAMPLES / "onboard.coco"))
    assert prog.name == "onboard"
    res = prog.run("onboard", onboard_input())
    assert res.ok, res.error
    assert res.output == {"processed": 2, "avg_age": pytest.approx(34.333, abs=0.01)}
    mail = [e for e in res.events if e.type == "log" and e.step == "send_mail"]
    assert [m.payload["message"] for m in mail] == ["mail to Grace: Welcome, Grace", "mail to Linus: Welcome, Linus"]
    assert any(e.type == "subflow.started" and e.path.endswith("if#0.then/notify_member") for e in res.events)
    enrich = next(e for e in res.events if e.type == "step.completed" and e.step == "enrich")
    assert enrich.payload["output"] == {"person": {"name": "Ada", "age": 33}, "changed": False}


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


def test_defaults_for_flows_and_steps():
    prog = compile_src("""
    @step
    def greet(name: str, punct: str = "!") -> str:
        return "hi " + name + punct
    @flow
    def f(name: str = "world", loud: bool = False) -> str:
        if loud:
            m = greet(name, punct="!!!")
        else:
            m = greet(name)
        return m
    """)
    assert prog.run("f", {}).output == {"result": "hi world!"}
    assert prog.run("f", {"name": "ada", "loud": True}).output == {"result": "hi ada!!!"}
    assert prog.flows["f"]["inputs"][0] == {"name": "name", "type": "str", "default": "world", "optional": True}


def test_output_shapes():
    prog = compile_src("""
    @step
    def named() -> {"a": int, "b": str}:
        return {"a": 1, "b": "x"}
    @step
    def named_tuple() -> {"a": int, "b": str}:
        return 2, "y"
    @step
    def pair() -> tuple[int, int]:
        return 3, 4
    @step
    def raw(x):
        return [x, x]
    @step
    def nothing() -> None:
        return 99
    @flow
    def f():
        a, b = named()
        whole = named()
        c, d = named_tuple()
        p = pair()
        e, g = raw(5)
        r = raw(6)
        nothing()
        return {"a": a, "b": b, "whole": whole, "c": c, "d": d, "p": p, "e": e, "r": r}
    """)
    res = prog.run("f", {})
    assert res.ok, res.error
    assert res.output == {"a": 1, "b": "x", "whole": {"a": 1, "b": "x"}, "c": 2, "d": "y", "p": [3, 4],
                          "e": 5, "r": [6, 6]}
    done = [e.payload["output"] for e in res.events if e.type == "step.completed"]
    assert done[3] == {"result1": 3, "result2": 4} and done[4] == {"result": [5, 5]} and done[-1] == {}


def test_step_must_return_declared_outputs():
    prog = compile_src("""
    @step
    def s() -> {"a": int, "b": int}:
        return {"a": 1}
    @step
    def t() -> int:
        return "nope"
    @flow
    def f():
        x, y = s()
    @flow
    def g():
        x = t()
    """)
    res = prog.run("f", {})
    assert res.error["code"] == "invalid_output" and "missing b" in res.error["message"]
    res = prog.run("g", {})
    assert res.error["code"] == "invalid_output" and "is not a valid int" in res.error["message"]


def test_step_exception_fails_flow_with_traceback_line():
    prog = compile_src("""
    @step
    def boom(x: int) -> int:
        y = x + 1
        raise ValueError("bad " + str(y))
    @flow
    def f(x: int) -> int:
        r = boom(x)
        return r
    """)
    res = prog.run("f", {"x": 1})
    assert res.status == "failed"
    assert res.error["code"] == "step_error" and res.error["message"] == "ValueError: bad 2"
    assert 'line 6, in boom' in res.error["traceback"]
    assert res.events[-2].type == "step.failed"


def test_invalid_input_and_coercion():
    prog = compile_src("""
    @flow
    def f(n: int, ratio: float, names: list[str], maybe: str | None = None) -> float:
        return n * ratio
    """)
    assert prog.run("f", {"n": "3", "ratio": 2, "names": []}).output == {"result": 6.0}
    res = prog.run("f", {"n": "x", "ratio": 1, "names": []})
    assert res.error["code"] == "invalid_input" and "input `n`" in res.error["message"]
    res = prog.run("f", {"ratio": 1, "extra": 1})
    assert "missing input(s): n, names" in res.error["message"] and "unknown input(s): extra" in res.error["message"]


def test_any_types_accept_anything():
    prog = compile_src("""
    import datetime
    @step
    def s(x, when: datetime.date = None) -> object:
        return {"x": x}
    @flow
    def f(x) -> dict:
        r = s(x)
        return r
    """)
    assert prog.run("f", {"x": [1, {"a": None}]}).output == {"result": {"x": [1, {"a": None}]}}


def test_expression_error_is_reported():
    prog = compile_src("""
    @flow
    def f(items: list[int]) -> int:
        return items[5]
    """)
    res = prog.run("f", {"items": [1]})
    assert res.error["code"] == "expression_error" and "IndexError" in res.error["message"]


def test_classes_validate_and_serialize():
    prog = compile_src("""
    class Address:
        city: str
    class Person:
        name: str
        age: int = 0
        address: Address | None = None
    import coco
    class Plain(coco.Model):
        v: int
    @step
    def older(p: Person) -> Person:
        p.age += 1
        return p
    @flow
    def f(p: Person) -> {"p": Person, "city": str}:
        q = older(p)
        return {"p": q, "city": q.address.city}
    """)
    res = prog.run("f", {"p": {"name": "ada", "age": "40", "address": {"city": "Delft"}}})
    assert res.ok, res.error
    assert res.output == {"p": {"name": "ada", "age": 41, "address": {"city": "Delft"}}, "city": "Delft"}
    res = prog.run("f", {"p": {"age": 1}})
    assert res.error["code"] == "invalid_input" and "name" in res.error["message"]


def test_helpers_and_constants_in_steps_and_flows():
    prog = compile_src("""
    RATE = 2
    def double(x):
        return x * RATE
    @step
    def s(x: int) -> int:
        return double(x)
    @flow
    def f(x: int) -> int:
        y = s(x)
        return y + RATE
    """)
    assert prog.run("f", {"x": 5}).output == {"result": 12}


def test_loops_break_continue_and_unpack():
    prog = compile_src("""
    @flow
    def f(items: dict) -> list:
        keep = []
        for k, v in sorted(items.items()):
            if v < 0:
                continue
            if v > 100:
                break
            keep = keep + [k]
        return keep
    """)
    assert prog.run("f", {"items": {"a": 1, "b": -1, "c": 5, "d": 500, "e": 2}}).output == {"result": ["a", "c"]}


def test_flow_level_logging():
    prog = compile_src("""
    @flow
    def f(n: int):
        log("n is", n)
        warn("careful")
        print("printed", n)
        log_table([{"a": n}])
    """)
    res = prog.run("f", {"n": 3})
    assert res.ok, res.error
    logs = [(e.type, e.step, e.path, e.payload.get("level"), e.payload["message"]) for e in res.events
            if e.type.startswith("log")]
    assert logs[:3] == [("log", None, "f", "info", "n is 3"), ("log", None, "f", "warning", "careful"),
                        ("log", None, "f", "info", "printed 3")]
    assert logs[3][0] == "log.table"


def test_cancel_before_start():
    prog = coco.compile_file(str(EXAMPLES / "onboard.coco"))
    ev = threading.Event()
    ev.set()
    res = prog.run("onboard", onboard_input(), cancel=ev)
    assert res.status == "cancelled"


def test_cancel_mid_loop():
    prog = compile_src("""
    import time
    @step
    def slow(i: int) -> None:
        time.sleep(0.02)
    @flow
    def f():
        for i in range(100):
            slow(i)
    """)
    cancel = threading.Event()
    seen = []

    def on_event(e):
        seen.append(e)
        if e.type == "step.completed" and len([x for x in seen if x.type == "step.completed"]) == 3:
            cancel.set()

    res = prog.run("f", {}, on_event=on_event, cancel=cancel)
    assert res.status == "cancelled"
    assert res.events[-1].payload["at_path"] == "f/for#0[3]"


def test_stream_yields_events_in_order():
    prog = coco.compile_file(str(EXAMPLES / "hello.coco"))

    async def collect():
        return [e async for e in prog.stream("main", {"name": "Vivek"})]

    events = asyncio.run(collect())
    assert [e.type for e in events] == ["flow.started", "step.started", "log", "step.completed",
                                        "flow.completed"]
    assert events[-1].payload["output"] == {"result": "Hello, Vivek!, Hello, Vivek!"}


def test_parallel_runs_keep_print_output_apart():
    prog = compile_src("""
    @step
    def say(who: str) -> None:
        for i in range(50):
            print(who, i)
    @flow
    def f(who: str):
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


def test_context_and_log_levels():
    prog = compile_src("""
    @step
    def s() -> str:
        log("careful", level="warning")
        return context.flow + ":" + context.step + ":" + context.path + ":" + context.run_id
    @flow
    def f() -> str:
        x = s()
        return x
    """)
    res = prog.run("f", {}, run_id="r_test")
    assert res.output == {"result": "f:s:f/s:r_test"}
    assert res.events[2].payload == {"level": "warning", "message": "careful",
                                     "args": [{"type": "string", "value": "careful"}]}


def test_api_outside_a_run(capsys):
    coco.log("plain", 1, {"a": True})
    coco.log_table([{"a": 1}])
    assert coco.env.get("x", 5) == 5 and coco.context.run_id is None
    out = capsys.readouterr().out
    assert 'plain 1 {"a": true}' in out and "(index) | a" in out


def test_steps_are_plain_python_functions():
    prog = compile_src("""
    @step
    def add(a: int, b: int = 1) -> int:
        return a + b
    """)
    assert prog.step_funcs["add"](2) == 3


def test_load_failure_is_a_compile_error():
    with pytest.raises(coco.CocoCompileError) as e:
        compile_src("""
        class A:
            x: int = 1 // 0
        """)
    assert e.value.diagnostics[0].code == "E234"


def test_workflow_imports_run():
    shared = HEAD + textwrap.dedent("""
    class Item:
        sku: str
        price: float
    @step
    def total(items: list[Item]) -> float:
        return sum(i.price for i in items)
    @flow
    def checkout(items: list[Item]) -> {"total": float}:
        t = total(items)
        return {"total": t}
    """)
    prog = coco.compile(HEAD + textwrap.dedent("""
    import shared
    @step
    def make(n: int) -> list[shared.Item]:
        return [shared.Item(sku=str(i), price=2.5) for i in range(n)]
    @flow
    def main(n: int) -> float:
        items = make(n)
        t = shared.checkout(items)
        return t
    """), "main.coco", {"shared": shared}.get)
    res = prog.run("main", {"n": 4})
    assert res.ok, res.error
    assert res.output == {"result": 10.0}
    assert any(e.type == "subflow.started" and e.payload["flow"] == "shared.checkout" for e in res.events)


def test_cli_check_and_run(capsys, tmp_path):
    assert cli_main(["check", str(EXAMPLES / "onboard.coco")]) == 0
    bad = tmp_path / "bad.coco"
    bad.write_text("from coco import flow\n@flow\ndef f() -> int:\n    pass\n")
    assert cli_main(["check", str(bad)]) == 1
    capsys.readouterr()
    assert cli_main(["run", str(EXAMPLES / "hello.coco"), "main", "-s", "name=Ada", "-e", "json"]) == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.strip().splitlines()]
    assert lines[-1]["type"] == "flow.completed"


def test_console_style_logging():
    prog = compile_src("""
    class Item:
        sku: str
        qty: int = 1
    @step
    def s() -> None:
        log("mixed", 1, 2.5, True, None, [1, "a"], {"k": {"n": 1}}, Item(sku="x"))
        debug("d")
        info("i")
        error("boom", {"code": 7})
        log_table([{"a": 1, "b": "x"}, {"a": 2, "c": True}])
        log_table([[1, 2], [3, 4, 5]])
        log_table(["red", "green"])
        log_table({"ada": {"age": 33}, "bob": {"age": 40}}, columns=["age"])
        log_table([Item(sku="a"), Item(sku="b", qty=3)])
    @flow
    def f():
        s()
    """)
    res = prog.run("f", {})
    assert res.ok, res.error
    logs = [e.payload for e in res.events if e.type == "log"]
    mixed = logs[0]
    assert [a["type"] for a in mixed["args"]] == ["string", "number", "number", "boolean", "null", "array", "object",
                                                 "object"]
    assert mixed["args"][7] == {"type": "object", "value": {"sku": "x", "qty": 1}, "class": "Item"}
    assert [p["level"] for p in logs[1:4]] == ["debug", "info", "error"]

    tables = [e.payload for e in res.events if e.type == "log.table"]
    assert tables[0]["columns"] == ["(index)", "a", "b", "c"]
    assert tables[0]["rows"] == [[0, 1, "x", None], [1, 2, None, True]]
    assert tables[1]["columns"] == ["(index)", "0", "1", "2"] and tables[1]["rows"][0] == [0, 1, 2, None]
    assert tables[2]["columns"] == ["(index)", "Values"] and tables[2]["rows"] == [[0, "red"], [1, "green"]]
    assert tables[3]["columns"] == ["(index)", "age"] and tables[3]["rows"] == [["ada", 33], ["bob", 40]]
    assert tables[4]["rows"] == [[0, "a", 1], [1, "b", 3]]


def test_log_table_limits_and_errors():
    prog = compile_src("""
    @step
    def big() -> None:
        log_table([{"i": i} for i in range(1500)])
    @step
    def bad() -> None:
        log_table(42)
    @flow
    def f():
        big()
    @flow
    def g():
        bad()
    """)
    t = next(e.payload for e in prog.run("f", {}).events if e.type == "log.table")
    assert t["row_count"] == 1500 and len(t["rows"]) == 1000 and t["truncated"]
    res = prog.run("g", {})
    assert res.status == "failed" and "log_table needs a list or a dict" in res.error["message"]


def test_methods_on_values_in_flows():
    prog = compile_src("""
    import json
    @flow
    def f(name: str, tags: list[str]) -> dict:
        tags.append(name.upper())
        d = {"n": name.strip().title(), "tags": ",".join(tags), "k": {"a": 1}.get("a")}
        return d
    """)
    assert prog.run("f", {"name": " ada ", "tags": ["x"]}).output == {
        "result": {"n": "Ada", "tags": "x, ADA ", "k": 1}}
    errs = [d.code for d in coco.check(HEAD + "import json\n@flow\ndef f(x: int):\n    s = json.dumps(x)\n"
                                       "    json.loads(s)\n") if d.severity == "error"]
    assert errs == ["E227", "E209"]
