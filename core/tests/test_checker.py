import textwrap
from pathlib import Path

import pytest

import coco

HERE = Path(__file__).parent


def errors(src: str, **kw):
    return [d for d in coco.check(textwrap.dedent(src), **kw) if d.severity == "error"]


def codes(src: str, **kw):
    return [d.code for d in errors(src, **kw)]


def test_coco_md_sample_golden():
    src = (HERE / "golden" / "coco_md_sample.coco").read_text()
    got = [(d.line, d.code) for d in coco.check(src, "coco.md")]
    assert got == [
        (4, "E230"),   # import asdsd: not found
        (9, "E205"),   # field `a` declared twice
        (9, "E203"),   # unknown type Person
        (12, "E206"),  # step1 body is not Python
        (15, "E206"),  # step2 body is not Python
        (17, "E208"),  # flow1 declares an output but never returns
        (19, "E220"),  # step1(a, b): 2 of 3 arguments
        (19, "E221"),  # (d, f) = ...: step1 returns 1 value
        (21, "E212"),  # m not declared
        (22, "E220"),  # step2(asd): 1 of 3 arguments
        (22, "E212"),  # asd not declared
    ]


def test_examples_are_clean():
    for path in (HERE.parent.parent / "examples").glob("*.coco"):
        assert coco.check(path.read_text(), str(path)) == [], path


def test_missing_package():
    assert codes("step a()():\n    pass\n") == ["E109"]


def test_flow_statement_restrictions():
    src = """
    package p
    flow f(int a)():
        while a > 0:
            a = a - 1
    """
    assert codes(src) == ["E218"]


def test_flow_cannot_call_python():
    src = """
    package p
    import math
    flow f(float a)(float b):
        float x
        x = math.sqrt(a)
        return x
    """
    assert codes(src) == ["E227"]


def test_step_call_inside_expression_is_rejected():
    src = """
    package p
    step s(int a)(int b):
        return a
    flow f(int a)(int b):
        int x
        x = s(a) + 1
        return x
    """
    assert codes(src) == ["E226"]


def test_assignment_needs_declaration_and_type():
    src = """
    package p
    flow f(int a)(int b):
        string s
        y = a + 1
        s = a + 1
        return a
    """
    assert codes(src) == ["E212", "E214"]


def test_arithmetic_and_aug_assign_ok():
    src = """
    package p
    flow f(int a, float r)(float b):
        float x
        int n = 3
        x = a * r + n / 2
        n += 1
        x = x - n
        return x
    """
    assert codes(src) == []


def test_recursion_is_rejected():
    src = """
    package p
    flow a()():
        b()
    flow b()():
        a()
    """
    assert "E207" in codes(src)


def test_self_is_reserved():
    src = """
    package p
    step s(int self)():
        pass
    flow f()():
        int self
    """
    assert codes(src) == ["E201", "E201"]


def test_return_paths():
    src = """
    package p
    flow f(int a)(int b):
        if a > 0:
            return 1
        else:
            return 2
    flow g(int a)(int b):
        if a > 0:
            return 1
    """
    assert [(d.code) for d in errors(src)] == ["E208"]


def test_class_field_types_and_attribute_access():
    src = """
    package p
    class Person:
        string name
        int age = 0
    flow f(Person p)(string n):
        string s
        s = p.nickname
        return p.name
    """
    assert codes(src) == ["E225"]


def test_for_each_over_array_infers_element_type():
    src = """
    package p
    class Person:
        string name
    step hello(Person p)(string msg):
        return "hi " + p.name
    flow f(Person people[])(int n):
        int count = 0
        string m
        for p in people:
            m = hello(p)
            count += 1
        return count
    """
    assert codes(src) == []


def test_keyword_arguments():
    src = """
    package p
    step s(int a, int b)(int c):
        return a + b
    flow f()(int c):
        int x
        x = s(b=2, a=1)
        x = s(1, c=3)
        return x
    """
    assert codes(src) == ["E220"]


def test_symbols_for_editor():
    src = textwrap.dedent("""
    package p
    class Person:
        string name
    step s(Person p)(string n):
        return p.name
    flow f(Person p)(string n):
        string x
        x = s(p)
        return x
    """)
    sym = coco.symbols(src)
    assert sym["package"] == "p"
    assert sym["classes"][0]["fields"] == [{"name": "name", "type": "string"}]
    assert sym["steps"][0]["inputs"] == [{"name": "p", "type": "Person"}]
    assert "log(*values)" in sym["self"] and "log_table(data, columns=None)" in sym["self"]


def test_coco_package_import():
    shared = textwrap.dedent("""
    package shared
    class Item:
        string sku
    step price(Item it)(float p):
        return 9.5
    """)
    main = textwrap.dedent("""
    package main
    import shared
    flow f(shared.Item it)(float p):
        float x
        x = shared.price(it)
        return x
    """)
    loader = {"shared": shared}.get
    assert coco.check(main, loader=loader) == []
    prog = coco.compile(main, loader=loader)
    res = prog.run("f", {"it": {"sku": "A1"}})
    assert res.ok and res.output == {"p": 9.5}


def test_compile_raises_with_diagnostics():
    with pytest.raises(coco.CocoCompileError) as ei:
        coco.compile("package p\nflow f()(int a):\n    pass\n")
    assert [d.code for d in ei.value.diagnostics] == ["E208"]


def test_json_literals_are_flagged_with_a_fix():
    src = textwrap.dedent("""
    package p
    import json
    step s(string name)(string m):
        data = [{"active": true, "x": null}, {"active": false}]
        text = "true and null inside a string are fine"
        return name
    step own()(int v):
        true = 1
        return true
    flow f(string n)(string m):
        string x
        x = s(n)
        if true:
            x = x + "!"
        return x
    """)
    found = [(d.line, d.col, d.end_col, d.code, d.message) for d in errors(src)]
    assert found == [
        (5, 24, 28, "E235", "`true` is JSON, not Python: use `True`"),
        (5, 35, 39, "E235", "`null` is JSON, not Python: use `None`"),
        (5, 53, 58, "E235", "`false` is JSON, not Python: use `False`"),
        (14, 8, 12, "E235", "`true` is JSON, not Python: use `True`"),
    ]


def test_json_literals_fixed_run_fine():
    src = "package p\nstep s()(int n):\n    return len([{\"a\": True, \"b\": None}])\nflow f()(int n):\n    int x\n    x = s()\n    return x\n"
    assert coco.compile(src).run("f", {}).output == {"n": 1}
