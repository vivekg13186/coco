# coco-core

Parser, checker and runtime for the Coco workflow language (milestone 1 of the design).

## Install and try

```bash
cd ~/Developer/coco
python3 -m venv .venv && source .venv/bin/activate
pip install -e "core[dev]"

coco check examples/onboard.coco
coco run examples/onboard.coco onboard -i examples/onboard.input.json
coco run examples/hello.coco main -s name=Vivek
coco run examples/hello.coco main -s name=Vivek -e json   # one JSON event per line
pytest core
```

## Logging in steps

Steps log like the JavaScript console. Every call becomes an event in the run history, in order.

```python
self.log("loaded", len(orders), "orders; first:", orders[0])   # any number of values of any type
self.debug(...); self.info(...); self.warn(...); self.error(...) # same, at that level
self.log_table(orders)                                           # rows as a table, like console.table
self.log_table({"ada": {"age": 33}, "bob": {"age": 40}}, columns=["age"])
print("plain text")                                              # becomes a log line too
```

- `log` events keep each value with its type (`args`), so the UI shows objects as expandable trees;
  `message` is the values joined as text.
- `log_table` takes a list of objects, lists or plain values, a dict of rows, or a pandas DataFrame. Each row
  gets an `(index)` cell; `columns` picks and orders columns; at most 1000 rows are kept.
- `error(...)` only logs: raise an exception to fail the step.
- `log_value` and `log_object` from the first version still work (they map to `log`) but are no longer
  documented. Try `coco run examples/logging_demo.coco main -s n=12`.

## Environments

A run can get an environment: a JSON object of settings (URLs, flags, credentials for local use).

```python
step build_url(string path)(string url):
    return self.env.api.base_url + path          # in a step: self.env

flow main(string path)(string url, int retries):
    string u
    int r
    u = build_url(path)
    r = env.get("retries", 3)                    # in a flow: env.key, env["key"], env.get(key, default)
    if env.api.verbose:
        u = u + "?verbose=1"
    return u, r
```

`env` is read-only, nested objects work with dots or brackets, and a missing key fails with
`env.api has no key `x``. `env` and `self` are reserved names. Run with
`coco run examples/env_demo.coco main -s path=/users --env examples/dev.env.json`, or
`program.run(flow, inputs, env={...}, env_name="dev")`. The `flow.started` event records the environment's
name, never its values.

## Library API

```python
import coco

program = coco.compile(source, "onboard.coco")   # raises coco.CocoCompileError(.diagnostics)
diags   = coco.check(source)                      # list[Diagnostic], never raises, never imports
symbols = coco.symbols(source)                    # classes, steps, flows, self members (for the editor)

result = program.run("onboard", inputs, on_event=print,  # RunResult: status, output, error, events
                     env={"api_url": "..."}, env_name="dev")
async for event in program.stream("onboard", inputs):    # same events, from a worker thread
    ...
```

`program.run` never raises for a failing flow: check `result.status` (`succeeded`, `failed`, `cancelled`)
and `result.error`. Pass `cancel=threading.Event()` and set it to stop at the next statement.

## How it is built

| Module | Job |
| --- | --- |
| `parser.py` | Line/indentation parser for `package`, `import`, `class`, `step`, `flow`. Step bodies are kept as Python text. Flow bodies have their declarations (`int d, f`) rewritten to marker calls and are then parsed with Python's `ast`, which gives exact line/column numbers. |
| `checker.py` | Symbol resolution, type checks, call arity/unpacking, flow-expression rules, return paths, sub-flow cycle check. Produces the flow IR with precompiled expressions. |
| `program.py` | `compile` / `check` / `symbols`; lowers classes to Pydantic models and steps to Python functions `def step(self, ...)` whose line numbers match the `.coco` file. |
| `runtime.py` | Interpreter for the flow IR, `StepContext` (`self`), `print()` capture per thread, cancellation, events. |
| `events.py`, `serialize.py` | Event and result types; JSON-safe conversion with a 64 KB cap per value. |
| `cli.py` | `coco check`, `coco run`. |

Differences from the design doc: the parser is hand-written plus Python's `ast` instead of a Lark grammar (no
extra dependency, exact positions), the CLI uses `argparse` instead of Typer, and mixed tabs/spaces is a
warning rather than an error.

## Diagnostic codes

| Code | Meaning |
| --- | --- |
| E100–E109 | Syntax: indentation, headers, parameters, fields, empty bodies, flow syntax, missing `package` |
| E201–E208 | Reserved names, duplicates, unknown types, duplicate class fields, step Python syntax, recursion, missing `return` |
| E209–E228 | Flow rules: non-step calls, undeclared names, assignment/type mismatches, arity (E220), unpacking (E221), disallowed expressions |
| E230–E234 | Imports: module not found, import cycles, errors in an imported Coco package, failing Python import |
| W001–W002 | Mixed tabs and spaces, class without fields |
