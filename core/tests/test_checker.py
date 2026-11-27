import textwrap
from pathlib import Path

import pytest

import coco

EXAMPLES = Path(__file__).parent.parent.parent / "examples"

HEAD = "from coco import step, flow, log\n"


def diags(src: str, loader=None):
    return coco.check(HEAD + textwrap.dedent(src), "t.coco", loader)


def codes(src: str, loader=None, severity="error"):
    return [d.code for d in diags(src, loader) if d.severity == severity]


def test_examples_are_clean():
    for f in EXAMPLES.glob("*.coco"):
        loader = coco.directory_loader(str(EXAMPLES))
        assert coco.check(f.read_text(), str(f), loader) == [], f.name


def test_file_must_be_valid_python():
    assert codes("def f(:\n    pass\n") == ["E100"]


def test_top_level_code_is_rejected():
    assert codes("""
    print("hi")
    X = load()
    for i in range(3):
        pass
    """) == ["E110", "E110", "E110"]
    assert codes("""
    '''docs'''
    import json
    LIMIT = 10
    NAMES = ["a", "b"]
    def helper(x):
        return x
    """) == []


def test_decorators_any_form_and_arguments_ignored():
    src = """
    import coco
    @step(name="ignored")
    def a() -> int:
        return 1
    @coco.step
    def b(x: int) -> int:
        return x
    @flow(name="also ignored")
    def f() -> int:
        x = a()
        y = b(x)
        return y
    """
    assert diags(src) == []
    assert [s["name"] for s in coco.symbols(HEAD + textwrap.dedent(src))["steps"]] == ["a", "b"]


def test_outputs_from_return_hint():
    sym = coco.symbols(HEAD + textwrap.dedent("""
    @step
    def none() -> None: pass
    @step
    def one() -> int: return 1
    @step
    def named() -> {"total": int, "ok": bool}: return {"total": 1, "ok": True}
    @step
    def pair() -> tuple[int, str]: return 1, "a"
    @step
    def unknown(): return 1
    """))
    outs = {s["name"]: s["outputs"] for s in sym["steps"]}
    assert outs["none"] == []
    assert outs["one"] == [{"name": "result", "type": "int"}]
    assert outs["named"] == [{"name": "total", "type": "int"}, {"name": "ok", "type": "bool"}]
    assert outs["pair"] == [{"name": "result1", "type": "int"}, {"name": "result2", "type": "str"}]
    assert outs["unknown"] is None


def test_lenient_types():
    sym = coco.symbols(HEAD + textwrap.dedent("""
    from typing import Optional
    import datetime
    class P:
        name: str
        tags: list[str]
        meta: dict[str, int]
        when: datetime.date
        nick: Optional[str] = None
        alt: int | None = None
    """))
    fields = {f["name"]: f["type"] for f in sym["classes"][0]["fields"]}
    assert fields == {"name": "str", "tags": "list[str]", "meta": "dict", "when": "Any", "nick": "str | None",
                      "alt": "int | None"}


def test_flow_inferred_outputs():
    sym = coco.symbols(HEAD + textwrap.dedent("""
    @flow
    def a(): return {"x": 1, "y": 2}
    @flow
    def b(): return 1, 2
    @flow
    def c(): return 3
    @flow
    def d(): pass
    """))
    outs = {f["name"]: [o["name"] for o in f["outputs"]] for f in sym["flows"]}
    assert outs == {"a": ["x", "y"], "b": ["result1", "result2"], "c": ["result"], "d": []}


def test_flow_statement_restrictions():
    assert codes("""
    @flow
    def f(n: int):
        while n:
            pass
        with open("x") as fh:
            pass
        try:
            pass
        except Exception:
            pass
        import os
    """) == ["E218"] * 4


def test_flow_cannot_call_python():
    assert codes("""
    import json
    def helper(x):
        return x
    @flow
    def f(n: int):
        a = helper(n)
        b = json.dumps(n)
        open("x")
        c = len(str(n))
    """) == ["E227", "E227", "E209"]
    msgs = [d.message for d in diags("""
    def helper(x):
        return x
    @flow
    def f(n: int):
        helper(n)
    """)]
    assert "add `@step`" in msgs[0]


def test_step_call_inside_expression_is_rejected():
    assert codes("""
    @step
    def s(x: int) -> int:
        return x
    @flow
    def f(n: int) -> int:
        y = s(n) + 1
        return y
    """) == ["E226"]


def test_implicit_and_annotated_declarations():
    assert codes("""
    @flow
    def f(n: int) -> int:
        total: int = 0
        count = 0
        for i in range(n):
            total += i
            count = count + 1
        avg = total / max(count, 1)
        return total
    """) == []
    assert codes("""
    @flow
    def f() -> int:
        x += 1
        return y
    """) == ["E212", "E212"]


def test_type_mismatch_is_a_warning_only():
    src = """
    @step
    def s(x: int) -> str:
        return str(x)
    @flow
    def f() -> int:
        a: str = 5
        b = s("text")
        return b
    """
    assert codes(src) == []
    assert codes(src, severity="warning") == ["E214", "E214", "E214"]


def test_arity_and_defaults_and_keywords():
    assert codes("""
    @step
    def s(a: int, b: str = "x", *, c: bool = False) -> int:
        return a
    @flow
    def f() -> int:
        r1 = s(1)
        r2 = s(1, "y", c=True)
        r3 = s(a=1, c=True)
        return r1
    """) == []
    assert codes("""
    @step
    def s(a: int, b: str = "x") -> int:
        return a
    @flow
    def f():
        r1 = s()
        r2 = s(1, "y", 3)
        r3 = s(1, zz=2)
        r4 = s(1, a=2)
    """) == ["E220"] * 4


def test_unpacking():
    assert codes("""
    @step
    def two() -> {"a": int, "b": int}:
        return {"a": 1, "b": 2}
    @step
    def nothing() -> None:
        pass
    @step
    def unknown():
        return 1, 2, 3
    @flow
    def f():
        a, b = two()
        both = two()
        x, y, z = unknown()
        q, w, e = two()
        n = nothing()
    """) == ["E221", "E221"]


def test_returns():
    assert codes("""
    @flow
    def f(n: int) -> {"a": int, "b": int}:
        if n > 0:
            return {"b": 2, "a": 1}
        return n, n
    """) == []
    assert codes("""
    @flow
    def f(n: int) -> {"a": int, "b": int}:
        if n > 0:
            return {"a": 1}
        return n
    @flow
    def g(n: int) -> int:
        if n:
            return 1
    """) == ["E217", "E217", "E208"]


def test_break_continue_and_tuple_loops():
    assert codes("""
    @flow
    def f(items: list[str]) -> int:
        n = 0
        for i, s in enumerate(items):
            if s == "skip":
                continue
            if s == "stop":
                break
            n += 1
        return n
    """) == []
    assert codes("""
    @flow
    def f():
        break
    """) == ["E215"]


def test_recursion_is_rejected():
    assert codes("""
    @flow
    def a():
        b()
    @flow
    def b():
        a()
    """) == ["E207"]


def test_reserved_names_in_flows():
    assert codes("""
    @flow
    def f(env: int):
        log = 1
        context = 2
    """) == ["E201", "E201", "E201"]


def test_env_and_context_and_constants_in_flows():
    assert codes("""
    LIMIT = 5
    @flow
    def f() -> int:
        url = env.api.base_url + "/x"
        retries = env.get("retries", 3)
        rid = context.run_id
        n = LIMIT * 2
        log("url", url, n)
        return n
    """) == []


def test_class_attribute_access():
    src = """
    class P:
        name: str
        age: int = 0
    @flow
    def f(p: P) -> str:
        n = p.name
        a = p.nope
        return n
    """
    assert codes(src) == []
    assert codes(src, severity="warning") == ["E225"]


def test_flow_param_defaults_must_be_literals():
    assert codes("""
    @flow
    def f(n: int = 1, names: list = ["a"], x=len("a")):
        pass
    """) == ["E113"]


def test_unsupported_signatures():
    assert codes("""
    @step
    def s(*args, **kw):
        pass
    @step
    async def a():
        pass
    @step
    @flow
    def both():
        pass
    """) == ["E112", "E114", "E116"]


def test_json_literals_are_flagged_with_a_fix():
    ds = [d for d in diags("""
    @step
    def s() -> dict:
        return {"ok": true, "v": null}
    @flow
    def f():
        ok = false
    """) if d.code == "E235"]
    assert [(d.line, d.col, d.end_col) for d in ds] == [(5, 19, 23), (5, 30, 34), (8, 10, 15)]
    assert "use `True`" in ds[0].message
    # names the file defines itself are fine
    assert codes("""
    true = True
    @step
    def s() -> bool:
        return true
    """) == []


def test_workflow_imports():
    shared = HEAD + textwrap.dedent("""
    class Item:
        sku: str
    @step
    def price(i: Item) -> float:
        return 9.5
    @flow
    def total(items: list[Item]) -> float:
        return 1.0
    """)
    loader = {"shared": shared}.get
    assert codes("""
    import shared
    @flow
    def f(i: shared.Item) -> float:
        p = shared.price(i)
        t = shared.total([i])
        return p
    """, loader) == []
    assert codes("""
    from shared import price
    import nope_not_a_module
    """, loader) == ["E236", "E230"]
    sym = coco.symbols(HEAD + "import shared\nimport json\n", loader=loader)
    assert [i["kind"] for i in sym["imports"]] == ["coco", "python"]


def test_symbols_for_editor():
    sym = coco.symbols(HEAD + "@flow\ndef main(name: str = 'x') -> str:\n    return name\n", name="hello")
    assert sym["workflow"] == "hello"
    assert sym["flows"][0]["inputs"] == [{"name": "name", "type": "str", "default": "x", "optional": True}]
    assert "log(*values)" in sym["globals"]


def test_compile_raises_with_diagnostics():
    with pytest.raises(coco.CocoCompileError) as e:
        coco.compile(HEAD + "@flow\ndef f():\n    while True:\n        pass\n")
    assert e.value.diagnostics[0].code == "E218"


def test_workflow_name_from_file():
    assert coco.workflow_name("/x/my-flow.coco") == "my_flow"
    assert coco.workflow_name("2go.py") == "w_2go"
