# COCO

A simple workflow engine for developers who want to automate things. Workflows are written in a small,
Python-like language instead of a node-based canvas: node editors get cluttered and messy once a workflow
grows, while code stays readable, diffable and easy to review.

Steps are plain Python, so you can use any Python library you already know for the integration work.
Everything (workflows, versions, run history, environments, notes) is stored in a single SQLite file, which
is easy to back up or move to another machine.

## Features

* **Workflow language** with a web-based code editor: syntax highlighting, autocompletion, live error
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

You need **Python 3.11 or newer** and, to build the web UI, **Node.js 18 or newer**.

```bash
git clone <this repository> coco
cd coco

# 1. Python packages: the language and the engine, in a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e core -e server

# 2. Build the web UI once (and again after pulling changes to ui/)
cd ui
npm install
npm run build
cd ..

# 3. Start the engine
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

To try the examples, open the UI, go to **Workflows → Import .coco** and pick a file from `examples/`.

## Using the UI

| Tab | What it is for |
| --- | --- |
| **Workflows** | Your workflows. Create, import a `.coco` file, edit, run, duplicate or delete. |
| **Run History** | Every run with status, duration and environment. Filter, open, rerun or cancel. |
| **Schedulers** | Placeholder until the scheduler is available. |
| **Environments** | Named JSON settings. Mark one as the default, or pick one in the top bar. |
| **Notes** | Plain-text notes, saved automatically. |
| **Open items ▾** | The workflows and runs you have open. Switch between them, close with ×. |

In the editor, **Ctrl/Cmd+S** saves and **Run** asks for the flow's input (as a form or as JSON). A run opens
in its own viewer with four views: **Events** (everything that happened, in order), **Console** (your
logs), **Steps** (a tree of calls with timings) and **Input / Output**.

## DSL Examples

A `.coco` file has a `package` name (this is also the workflow's name), optional `import`s and `class`es,
**steps** (plain Python that does the work) and **flows** (which wire steps together).

### Defining simple workflow

```python
package greetings

class Person:
    string name
    int age = 0

step greet(Person p)(string message):
    return "Hello, " + p.name + "!"

step is_adult(Person p)(boolean adult):
    return p.age >= 18

flow main(Person p, int times)(string text):
    string m
    boolean adult
    m = greet(p)
    adult = is_adult(p)
    if adult:
        m = m + " Welcome back."
    for i in range(1, times, 1):
        m = m + " Hello again!"
    return m
```

Run `main` with this input:

```json
{ "p": { "name": "Ada", "age": 36 }, "times": 2 }
```

How it fits together:

- **Types**: `string`, `boolean`, `int`, `float`, `object` (a dict), `array` (a list), your own classes, and
  arrays such as `Person people[]`. Inputs and outputs are checked against them.
- **Steps**: `step name(inputs)(outputs):` followed by Python. `return a, b` returns several outputs, in order.
- **Flows**: `flow name(inputs)(outputs):` can only do these things:
  - declare variables (`string m`, `int n = 0`)
  - call steps and other flows (`x = step(a)`, `(x, y) = step(a)`)
  - assign simple expressions (`count = count + 1`)
  - use `if` / `elif` / `else`, `for i in range(...)` and `for p in people`
  - `return`

  Anything else belongs in a step.
- **Sub-flows**: a flow can call another flow like a step; flows cannot call themselves.
- Python spells values `True`, `False` and `None` (not `true` / `false` / `null`). The editor flags the JSON
  spellings and offers a fix.

### How to use environments

Create an environment in the **Environments** tab, for example `dev`:

```json
{ "user": "vivek", "retries": 5, "api": { "base_url": "https://dev.example.com", "verbose": true } }
```

Then read it with `env` in flows and `self.env` in steps:

```python
package env_demo

step build_url(string path)(string url):
    return self.env.api.base_url + path         # in a step: self.env

flow main(string path)(string url, int retries):
    string u
    int r
    u = build_url(path)
    r = env.get("retries", 3)                   # in a flow: env.key, env["key"], env.get(key, default)
    if env.api.verbose:
        u = u + "?verbose=1"
    return u, r
```

- Pick the environment for new runs in the top bar (or per run in the Run dialog). The default environment
  is used when you don't pick one.
- Values are read-only during a run, and a missing key stops the run with a clear message.
- Run events record the environment's **name**, never its values. Values are stored unencrypted in the
  SQLite file, so keep real production secrets elsewhere.

### How to use log statement

```python
package orders

step check(object order)(boolean ok):
    self.log("checking order", order["id"], order)       # any number of values, like console.log
    if order["total"] > 1000:
        self.warn("large order:", order["total"])        # also: self.info, self.error, self.debug
    self.log_table(order["lines"])                       # a list of rows, shown as a table
    print("print() works too")                           # becomes a log line
    return order["total"] > 0

flow main(object order)(boolean ok):
    boolean r
    r = check(order)
    return r
```

Input:

```json
{ "order": { "id": "A-17", "total": 1250.5, "lines": [ { "sku": "X1", "qty": 2 }, { "sku": "Y9", "qty": 1 } ] } }
```

Logs appear in the run viewer's **Console** view, in order. Objects can be expanded like in the browser
console. Tables (`log_table` takes a list of objects, lists or values, a dict of rows or a pandas
DataFrame) can be filtered by text and sorted by clicking a column header. `self.error(...)` only logs; to
fail a step, raise an exception.

### How to use python function and library

Imports go at the top of the file and are available in every step:

```python
package weather_report
import json
import statistics
from datetime import date

step summarize(string raw)(object report):
    readings = json.loads(raw)                       # standard library
    temps = [r["temp"] for r in readings]
    report = {
        "day": date.today().isoformat(),
        "avg": round(statistics.mean(temps), 1),
        "max": max(temps),
    }
    self.log_table(readings)
    return report

flow main(string raw)(object report):
    object r
    r = summarize(raw)
    return r
```

Input:

```json
{ "raw": "[{\"city\": \"Amsterdam\", \"temp\": 7.5}, {\"city\": \"Utrecht\", \"temp\": 8.1}]" }
```

To use a package from PyPI, install it into the same virtual environment as the engine, then restart
`coco-server`:

```bash
source .venv/bin/activate
pip install requests
```

```python
import requests

step fetch(string url)(object data):
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()
```

You can also import another Coco workflow by its package name (`import shared`) and call its steps and
flows as `shared.step_name(...)`.

## Command line

The `coco` command checks and runs `.coco` files without the engine, which is handy while writing them:

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
| `core/` | The language: parser, checker, runtime and the `coco` command ([details](core/README.md)) |
| `server/` | The engine: REST API, SQLite storage, run worker and `coco-server` ([details](server/README.md)) |
| `ui/` | The web UI: Nuxt, PrimeVue and the Monaco editor ([details](ui/README.md)) |
| `examples/` | Example workflows, an input file and an environment file |

Run the tests with `pip install -e "core[dev]" -e "server[dev]"`, then `pytest core server` and, for the UI,
`cd ui && npm test`.

## License

MIT License — see [LICENSE](LICENSE).
