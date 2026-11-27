# COCO

A simple workflow engine for developers who want to automate things. Workflows are written as plain
Python files (`@step` and `@flow` functions) instead of a node-based canvas: node editors get cluttered and messy once a workflow
grows, while code stays readable, diffable and easy to review.

Steps are plain Python, so you can use any Python library you already know for the integration work.
Everything (workflows, versions, run history, environments, notes) is stored in a single SQLite file, which
is easy to back up or move to another machine.

## Features

* **Workflows in plain Python** with a web-based code editor: syntax highlighting, autocompletion, live error
  checking and one-click fixes.
* **Python inside steps**: use the standard library or any package you install.
* **Run history**: every run is recorded event by event, so you can inspect it at any time: inputs,
  outputs, each step, timings and errors.
* **Console-style logging**: log any values like `console.log`, see objects as expandable trees and tables
  you can filter and sort.
* **Environments**: keep settings such as URLs or flags in named environments (dev, prod, ...) and pick one
  when you run.
* **Versions**: every save keeps a version; you can open and restore older ones.
* **Notes**: a simple notepad for plain-text notes.
* **Live runs**: watch events arrive while a run is going, and cancel or rerun it.
* **One process, one port**: the engine serves the web UI and a REST API (with interactive docs).
* **Scheduler** for time-based runs: *coming soon* (the Schedulers tab is a placeholder for now).

## Install and Run

You need **Python 3.11 or newer**. The web UI is already built and included, so Node.js is only needed if
you want to change the UI (see [ui/README.md](ui/README.md)).

```bash
git clone <this repository> coco
cd coco

# 1. Install the language and the engine in a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e core -e server

# 2. Start the engine (it serves the web UI too)
coco-server
```

Open **http://127.0.0.1:8765/** in your browser. The API docs are at http://127.0.0.1:8765/docs.

`coco-server` only exists inside the virtual environment. In a new terminal, run
`source .venv/bin/activate` first, or start it with `.venv/bin/coco-server`.

Useful options (flags or environment variables):

| Option | Default | What it does |
| --- | --- | --- |
| `--db PATH` / `COCO_DB` | `~/.coco/coco.db` | The SQLite file with all your data |
| `--port N` / `COCO_PORT` | `8765` | Port for the UI and API |
| `--host H` / `COCO_HOST` | `127.0.0.1` | Bind address (local only by default) |
| `--max-runs N` / `COCO_MAX_RUNS` | `4` | How many runs can execute at the same time |
| `COCO_API_TOKEN` | off | When set, API calls need `Authorization: Bearer <token>` |

To try the examples, open the UI, go to **Workflows → Import file** and pick a file from `examples/`. The
workflow is named after the file.

## Using the UI

| Tab | What it is for |
| --- | --- |
| **Workflows** | Your workflows. Create, import a `.coco` or `.py` file, edit, rename, run, duplicate or delete. |
| **Run History** | Every run with status, duration and environment. Filter, open, rerun or cancel. |
| **Schedulers** | Placeholder until the scheduler is available. |
| **Environments** | Named JSON settings. Mark one as the default, or pick one in the top bar. |
| **Notes** | Plain-text notes, saved automatically. |
| **Open items ▾** | The workflows and runs you have open. Switch between them, close with ×. |

In the editor, **Ctrl/Cmd+S** saves and **Run** asks for the flow's input (as a form or as JSON). A run opens
in its own viewer with four views: **Events** (everything that happened, in order), **Console** (your
logs), **Steps** (a tree of calls with timings) and **Input / Output**.

## Writing workflows

A workflow is a plain Python file. You import a few helpers from `coco` and mark functions with two
decorators:

- `@step` marks a function that does the work. A step is ordinary Python, so it can use any library.
- `@flow` marks a function that wires steps together. Flows are deliberately restricted, which keeps them
  easy to read and lets the engine record every step, branch and loop iteration.

Because the file is valid Python, any Python editor highlights it, and the built-in editor adds
autocompletion and live checks. The workflow's name comes from the UI (or from the file name on the
command line), not from the code. Other workflows `import` it by that name.

### A simple workflow

```python
from coco import step, flow, log


class Person:
    name: str
    age: int = 0


@step
def greet(p: Person) -> str:
    log("greeting", p.name)
    return "Hello, " + p.name + "!"


@step
def is_adult(p: Person) -> bool:
    return p.age >= 18


@flow
def main(p: Person, times: int = 1) -> str:
    message = greet(p)
    adult = is_adult(p)
    if adult:
        message = message + " Welcome back."
    for i in range(times - 1):
        message = message + " Hello again!"
    return message
```

Run `main` with this input (`times` is optional because it has a default):

```json
{ "p": { "name": "Ada", "age": 36 }, "times": 2 }
```

How it fits together:

- **Inputs** are the function's parameters. Type hints work as in Python: `str`, `int`, `float`, `bool`,
  `dict`, `list[T]`, `T | None` (or `Optional[T]`), `Any`, and your own classes. Inputs are checked and
  converted when a run starts and when a step is called (`"3"` becomes `3` for an `int`). A parameter without
  a hint accepts anything, and a type the engine doesn't know is treated as `Any`. Defaults make an input
  optional.
- **Outputs** come from the `->` hint:

  | Hint | Outputs |
  | --- | --- |
  | `-> str` | one output, named `result` |
  | `-> {"total": int, "ok": bool}` | named outputs; return a dict with those keys (or a tuple in that order) |
  | `-> tuple[int, str]` | two outputs, `result1` and `result2` |
  | `-> None` | no outputs |
  | no hint | whatever the step returns |

  A flow without a hint gets its outputs from its `return`: `return {"url": u, "retries": r}` gives the
  outputs `url` and `retries`.
- **Classes** are data shapes: write fields with type hints, and Coco turns the class into a validated model
  (a pydantic model). Use them as input and output types.
- **Flows** can only do these things:
  - assign values (`count = 0`, `total: float = 0`, `count += 1`); a variable is created when first assigned
  - call steps and other flows as statements: `x = step(a)`, `a, b = step(x)`, `step(x)`
  - use simple expressions, builtins such as `len`, `min`, `round`, `sorted`, and methods on values
    (`name.upper()`, `items.get("k")`)
  - `if` / `elif` / `else`, `for i in range(...)`, `for item in items`, `for i, x in enumerate(items)`,
    `break` and `continue`
  - `log(...)`, `warn(...)`, `log_table(...)` and `print(...)`
  - read `env` and module constants
  - `return`

  Anything else (`while`, `try`, calling a library, creating objects) belongs in a step. The editor tells you
  when something isn't allowed in a flow.
- **Sub-flows**: a flow can call another flow like a step; flows cannot call themselves.
- **Helpers**: plain functions (without a decorator) and constants at the top of the file can be used by
  steps. A flow can't call a helper; add `@step` to it if a flow needs it.
- **Top-level code**: only imports, classes, functions and constants are allowed at the top level, so
  loading a workflow never runs anything by itself.
- Type mismatches in flows are warnings, not errors, just as in Python. Errors (an unknown step, wrong
  number of arguments, a `while` in a flow) stop a workflow from running until they are fixed.
- Python spells values `True`, `False` and `None` (not `true` / `false` / `null`). The editor flags the JSON
  spellings and offers a fix.

### How to use environments

Create an environment in the **Environments** tab, for example `dev`:

```json
{ "user": "vivek", "retries": 5, "api": { "base_url": "https://dev.example.com", "verbose": true } }
```

Then read it with `env`, in steps and in flows:

```python
from coco import step, flow, log, env


@step
def build_url(path: str) -> str:
    log("building a url for", env.user)
    return env.api.base_url + path


@flow
def main(path: str = "/") -> {"url": str, "retries": int}:
    url = build_url(path)
    retries = env.get("retries", 3)             # env.key, env["key"], env.get(key, default)
    if env.api.verbose:
        url = url + "?verbose=1"
    return {"url": url, "retries": retries}
```

- Pick the environment for new runs in the top bar (or per run in the Run dialog). The default environment
  is used when you don't pick one.
- Values are read-only during a run, and a missing key stops the run with a clear message.
- Run events record the environment's **name**, never its values. Values are stored unencrypted in the
  SQLite file, so keep real production secrets elsewhere.

### How to use log statements

```python
from coco import step, flow, log, warn, log_table


@step
def check(order: dict) -> bool:
    log("checking order", order["id"], order)       # any number of values, like console.log
    if order["total"] > 1000:
        warn("large order:", order["total"])        # also: info, error, debug
    log_table(order["lines"])                       # a list of rows, shown as a table
    print("print() works too")                      # becomes a log line
    return order["total"] > 0


@flow
def main(order: dict) -> bool:
    ok = check(order)
    log("result:", ok)                              # flows can log too
    return ok
```

Input:

```json
{ "order": { "id": "A-17", "total": 1250.5, "lines": [ { "sku": "X1", "qty": 2 }, { "sku": "Y9", "qty": 1 } ] } }
```

Logs appear in the run viewer's **Console** view, in order. Objects can be expanded like in the browser
console. Tables (`log_table` takes a list of objects, lists or values, a dict of rows or a pandas
DataFrame) can be filtered by text and sorted by clicking a column header. `error(...)` only logs; to fail a
step, raise an exception. `context.run_id`, `context.flow`, `context.step` and `context.path` tell a step
where it is running. Outside a run (for example when you call a step function in a test), `log` simply
prints.

### How to use Python functions and libraries

Imports go at the top of the file and are available in every step:

```python
import json
import statistics
from datetime import date

from coco import step, flow, log_table


@step
def summarize(raw: str) -> dict:
    readings = json.loads(raw)                       # standard library
    temps = [r["temp"] for r in readings]
    log_table(readings)
    return {
        "day": date.today().isoformat(),
        "avg": round(statistics.mean(temps), 1),
        "max": max(temps),
    }


@flow
def main(raw: str) -> dict:
    report = summarize(raw)
    return report
```

Input:

```json
{ "raw": "[{\"city\": \"Amsterdam\", \"temp\": 7.5}, {\"city\": \"Utrecht\", \"temp\": 8.1}]" }
```

[`requests`](https://requests.readthedocs.io) comes installed with Coco, so steps can call HTTP APIs
right away:

```python
import requests

from coco import step, env


@step
def fetch(path: str) -> dict:
    response = requests.get(env.api.base_url + path, timeout=10)
    response.raise_for_status()
    return response.json()
```

To use any other package from PyPI, install it into the same virtual environment as the engine, then
restart `coco-server`:

```bash
source .venv/bin/activate
pip install pandas
```

You can also import another workflow by its name (`import shared`), call its steps and flows from a flow
as `shared.step_name(...)`, and use its classes as `shared.ClassName`.

## Command line

The `coco` command checks and runs workflow files without the engine, which is handy while writing them
(the workflow is named after the file, and `import other` finds `other.coco` or `other.py` next to it):

```bash
coco check examples/onboard.coco
coco run examples/onboard.coco onboard -i examples/onboard.input.json
coco run examples/hello.coco main -s name=Vivek                 # set inputs one by one
coco run examples/env_demo.coco main -s path=/users --env examples/dev.env.json
coco run examples/hello.coco main -s name=Vivek -e json          # events as JSON lines
```

## REST API

Everything in the UI is also available over HTTP under `/api/v1`: workflows and versions, validation,
starting runs, run history, events and a live event stream (Server-Sent Events), environments and notes.
Browse and try it at **http://127.0.0.1:8765/docs**. Example:

```bash
curl -X POST http://127.0.0.1:8765/api/v1/workflows/hello/flows/main/runs \
     -H 'content-type: application/json' -d '{"input": {"name": "Ada"}, "wait": true}'
```

## Project layout

| Folder | Contents |
| --- | --- |
| `core/` | The workflow core: parser, checker, runtime, the `coco` API and command ([details](core/README.md)) |
| `server/` | The engine: REST API, SQLite storage, run worker and `coco-server` ([details](server/README.md)) |
| `ui/` | The web UI source: Nuxt, PrimeVue and the Monaco editor ([details](ui/README.md)) |
| `server/coco_server/ui/` | The built web UI, generated by `npm run build` in `ui/` and committed so nobody has to build it |
| `examples/` | Example workflows, an input file and an environment file |

Run the tests with `pip install -e "core[dev]" -e "server[dev]"`, then `pytest core server` and, for the UI,
`cd ui && npm test`.

## License

MIT License — see [LICENSE](LICENSE).
