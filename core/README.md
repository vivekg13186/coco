# coco-core

Parser, checker and runtime for Coco workflows: plain Python files with `@step` and `@flow` functions.

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

## Writing a workflow

A workflow file is valid Python. `@step` functions do the work (any Python); `@flow` functions wire steps
together and are interpreted with a small, checked subset of Python. See the main README for the full rules.

```python
from coco import step, flow, log, env


@step
def build_url(path: str) -> str:
    log("building a url for", env.user)
    return env.api.base_url + path


@flow
def main(path: str = "/") -> {"url": str, "retries": int}:
    url = build_url(path)
    retries = env.get("retries", 3)
    if env.api.verbose:
        url = url + "?verbose=1"
    return {"url": url, "retries": retries}
```

Outputs come from `->`: `-> T` is one output `result`, `-> {"a": T, "b": U}` named outputs, `-> tuple[T, U]`
outputs `result1`, `result2`, `-> None` none, and no hint means "whatever the step returns". Types are
lenient like Python hints: unknown types are `Any`, and type mismatches in flows are warnings.

## Logging

Logging works like the JavaScript console, from steps and flows. Every call becomes an event in the run
history, in order.

```python
from coco import log, debug, info, warn, error, log_table, context

log("loaded", len(orders), "orders; first:", orders[0])   # any number of values of any type
debug(...); info(...); warn(...); error(...)             # same, at that level
log_table(orders)                                        # rows as a table, like console.table
log_table({"ada": {"age": 33}, "bob": {"age": 40}}, columns=["age"])
print("plain text")                                      # becomes a log line too
context.run_id, context.flow, context.step, context.path
```

- `log` events keep each value with its type (`args`), so the UI shows objects as expandable trees;
  `message` is the values joined as text.
- `log_table` takes a list of objects, lists or plain values, a dict of rows, or a pandas DataFrame. Each row
  gets an `(index)` cell; `columns` picks and orders columns; at most 1000 rows are kept.
- `error(...)` only logs: raise an exception to fail the step.
- The functions find the running step through a thread-local. Outside a run they print, and `env` is empty,
  so step functions can be called directly in tests. Try `coco run examples/logging_demo.coco main -s n=12`.

## Environments

A run can get an environment: a JSON object of settings, read as `env` in steps and flows (`env.key`,
`env["key"]`, `env.get(key, default)`). `env` is read-only, nested objects work with dots or brackets, and a
missing key fails with `env.api has no key `x``. Run with
`coco run examples/env_demo.coco main -s path=/users --env examples/dev.env.json`, or
`program.run(flow, inputs, env={...}, env_name="dev")`. The `flow.started` event records the environment's
name, never its values.

## Library API

```python
import coco

program = coco.compile(source, "onboard.coco", name="onboard")  # raises coco.CocoCompileError(.diagnostics)
program = coco.compile_file("examples/onboard.coco")            # name from the file name
diags   = coco.check(source)                      # list[Diagnostic], never raises, never runs the file
symbols = coco.symbols(source, name="onboard")    # classes, steps, flows, globals (for the editor)

result = program.run("onboard", inputs, on_event=print,  # RunResult: status, output, error, events
                     env={"api_url": "..."}, env_name="dev")
async for event in program.stream("onboard", inputs):    # same events, from a worker thread
    ...
program.step_funcs["enrich"](person, "src")              # steps are plain functions
```

`program.run` never raises for a failing flow: check `result.status` (`succeeded`, `failed`, `cancelled`)
and `result.error`. Pass `cancel=threading.Event()` and set it to stop at the next statement.

## How it is built

| Module | Job |
| --- | --- |
| `parser.py` | Parses the file with Python's `ast`, then picks out imports, classes, `@step` / `@flow` functions, helpers and constants. Reads type hints leniently and rejects top-level code that would run on load. |
| `checker.py` | Checks flows (allowed statements, calls, arity with defaults, unpacking, returns, recursion) and builds the flow IR with precompiled expressions. Type mismatches are warnings. Flags JSON `true`/`false`/`null` everywhere. |
| `program.py` | `compile` / `check` / `symbols`; executes the module once with the coco API injected, gives classes without a base the `coco.Model` (pydantic) base, and binds imported workflows. |
| `dsl.py` | The API workflow files import: `step`, `flow`, `Model`, `log`, `info`, `warn`, `error`, `debug`, `log_table`, `env`, `context`. |
| `runtime.py` | Interpreter for the flow IR, step calls, `print()` capture per thread, cancellation, events. |
| `events.py`, `serialize.py` | Event and result types; JSON-safe conversion with a 64 KB cap per value. |
| `cli.py` | `coco check`, `coco run` (the workflow is named after the file). |

## Diagnostic codes

| Code | Meaning |
| --- | --- |
| E100 | Python syntax error |
| E110–E116 | File structure: code at the top level, bad named-output hint, `*args`, non-literal flow default, async steps, relative imports, a function that is both step and flow |
| E201–E208 | Reserved names in flows, duplicates, recursion, missing `return` |
| E209–E228 | Flow rules: calls that aren't steps or flows, undefined names, arity (E220), unpacking (E221), disallowed statements and expressions; E214 / E225 are type warnings |
| E230–E236 | Imports: module not found, import cycles, errors in an imported workflow, failing load, `from workflow import ...` |
| E235 | JSON `true` / `false` / `null` instead of Python `True` / `False` / `None` |
