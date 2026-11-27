import json
import textwrap
from pathlib import Path

import pytest

import coco
from coco.cli import main as cli_main

EXAMPLES = Path(__file__).parent.parent.parent / "examples"
DEV = json.loads((EXAMPLES / "dev.env.json").read_text())


def compile_src(src: str) -> coco.Program:
    return coco.compile("from coco import step, flow, env\n" + textwrap.dedent(src), "test.coco")


def test_env_in_flows_and_steps():
    prog = coco.compile_file(str(EXAMPLES / "env_demo.coco"))
    res = prog.run("main", {"path": "/users"}, env=DEV, env_name="dev")
    assert res.ok, res.error
    assert res.output == {"url": "https://dev.example.com/users?verbose=1", "retries": 5}
    started = res.events[0]
    assert started.payload["env"] == "dev"            # the name, never the values
    assert "api" not in json.dumps(started.payload)
    logged = [e for e in res.events if e.type == "log"][1]
    assert logged.payload["args"][0] == {"type": "string", "value": "api settings:"}
    assert logged.payload["args"][1] == {"type": "object", "value": DEV["api"], "class": "EnvView"}


def test_env_defaults_and_missing_keys():
    prog = coco.compile_file(str(EXAMPLES / "env_demo.coco"))
    no_retries = {"user": "a", "api": {"base_url": "http://x", "verbose": False}}
    res = prog.run("main", {"path": "/p"}, env=no_retries)
    assert res.output == {"url": "http://x/p", "retries": 3}
    res = prog.run("main", {"path": "/p"}, env={"user": "a"})
    assert res.status == "failed" and res.error["code"] == "step_error"
    assert "env has no key `api`" in res.error["message"]
    res = prog.run("main", {"path": "/p"})  # no environment at all
    assert res.status == "failed" and "has no key" in res.error["message"]


def test_missing_key_in_flow_is_an_expression_error():
    prog = compile_src("""
    @flow
    def f() -> str:
        return env.region.name
    """)
    res = prog.run("f", {}, env={"region": {}})
    assert res.error["code"] == "expression_error" and "env.region has no key `name`" in res.error["message"]


def test_env_is_read_only_and_reserved_in_flows():
    prog = compile_src("""
    @step
    def s() -> str:
        env.x = 1
        return "never"
    @flow
    def f() -> str:
        r = s()
        return r
    """)
    res = prog.run("f", {}, env={"x": 0})
    assert res.status == "failed" and "read-only" in res.error["message"]
    errs = [d.code for d in coco.check(textwrap.dedent("""
    from coco import flow
    @flow
    def f():
        env = 1
    """)) if d.severity == "error"]
    assert errs == ["E201"]


def test_env_values_pass_into_steps_and_items():
    prog = compile_src("""
    @step
    def keys(o: dict, k: str) -> {"n": int, "v": str}:
        return {"n": len(o), "v": k}
    @flow
    def f() -> {"n": int, "v": str}:
        a, b = keys(env.db, env["db"]["host"])
        return {"n": a, "v": b}
    """)
    res = prog.run("f", {}, env={"db": {"host": "localhost", "port": 5432}})
    assert res.ok, res.error
    assert res.output == {"n": 2, "v": "localhost"}


def test_env_must_be_an_object():
    prog = compile_src("@flow\ndef f():\n    pass\n")
    with pytest.raises(TypeError):
        prog.run("f", {}, env=[1, 2])  # type: ignore[arg-type]


def test_cli_env_file(capsys):
    code = cli_main(["run", str(EXAMPLES / "env_demo.coco"), "main", "-s", "path=/x",
                     "--env", str(EXAMPLES / "dev.env.json"), "-e", "json"])
    assert code == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.strip().splitlines()]
    assert lines[0]["payload"]["env"] == "dev.env"
    assert lines[-1]["payload"]["output"]["retries"] == 5
