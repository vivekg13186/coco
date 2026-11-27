"""Command line: `coco check file.coco` and `coco run file.coco flow --input in.json` (the workflow is named after the file)."""
from __future__ import annotations

import argparse
import json
import os
import sys

from .diagnostics import CocoCompileError
from .program import check, compile_file, directory_loader


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def cmd_check(args: argparse.Namespace) -> int:
    status = 0
    for path in args.files:
        src = _read(path)
        diags = check(src, path, directory_loader(os.path.dirname(os.path.abspath(path))))
        errors = [d for d in diags if d.severity == "error"]
        for d in diags:
            print(f"{path}:{d}")
        if errors:
            status = 1
        elif not args.quiet:
            warn = len(diags) - len(errors)
            print(f"{path}: ok" + (f" ({warn} warning(s))" if warn else ""))
    return status


def _parse_value(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _cell(v) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def _ascii_table(columns: list[str], rows: list[list], limit: int = 20) -> str:
    shown = [[_cell(c)[:40] for c in r] for r in rows[:limit]]
    widths = [max([len(c)] + [len(r[i]) for r in shown]) for i, c in enumerate(columns)]
    line = "+".join("-" * (w + 2) for w in widths)
    out = [" | ".join(c.ljust(w) for c, w in zip(columns, widths)), line]
    out += [" | ".join(c.ljust(w) for c, w in zip(r, widths)) for r in shown]
    if len(rows) > limit:
        out.append(f"... {len(rows) - limit} more row(s)")
    return "\n".join("      " + x for x in out)


def cmd_run(args: argparse.Namespace) -> int:
    try:
        program = compile_file(args.file)
    except CocoCompileError as e:
        for d in e.diagnostics:
            print(f"{args.file}:{d}", file=sys.stderr)
        return 2
    inputs: dict = {}
    if args.input:
        inputs = json.loads(sys.stdin.read() if args.input == "-" else _read(args.input))
    for item in args.set or []:
        key, _, value = item.partition("=")
        inputs[key] = _parse_value(value)

    def on_event(ev) -> None:
        if args.events == "json":
            print(json.dumps(ev.to_dict(), ensure_ascii=False))
        elif args.events == "text":
            detail = ""
            p = ev.payload
            if ev.type == "log":
                detail = (f"[{p['level']}] " if p.get("level") != "info" else "") + p["message"]
            elif ev.type == "log.table":
                detail = p["message"] + "\n" + _ascii_table(p["columns"], p["rows"])
            elif ev.type in ("step.completed", "subflow.completed"):
                detail = f"{json.dumps(p.get('output'), ensure_ascii=False)}  {p.get('duration_ms')} ms"
            elif ev.type == "branch.taken":
                detail = p["arm"]
            elif ev.type == "loop.iteration":
                detail = f"[{p['index']}]"
            elif ev.type in ("step.failed", "flow.failed"):
                detail = p["error"]["message"]
            print(f"{ev}  {detail}".rstrip())

    env = None
    env_name = None
    if args.env:
        env = json.loads(_read(args.env))
        if not isinstance(env, dict):
            print(f"{args.env}: the environment must be a JSON object", file=sys.stderr)
            return 2
        env_name = os.path.splitext(os.path.basename(args.env))[0]
    result = program.run(args.flow, inputs, on_event=on_event, collect_events=False, env=env, env_name=env_name)
    if args.events != "json":
        print()
        if result.ok:
            print(json.dumps(result.output, indent=2, ensure_ascii=False))
        else:
            print(f"{result.status}: {result.error['message'] if result.error else ''}", file=sys.stderr)
            tb = (result.error or {}).get("traceback")
            if tb:
                print(tb, file=sys.stderr)
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="coco", description="Coco workflow language")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="report errors and warnings")
    c.add_argument("files", nargs="+")
    c.add_argument("-q", "--quiet", action="store_true", help="only print problems")
    c.set_defaults(func=cmd_check)

    r = sub.add_parser("run", help="run a flow")
    r.add_argument("file")
    r.add_argument("flow")
    r.add_argument("-i", "--input", help="JSON file with the flow input object ('-' for stdin)")
    r.add_argument("-s", "--set", action="append", metavar="NAME=VALUE",
                   help="set one input; VALUE is parsed as JSON when possible")
    r.add_argument("-e", "--events", choices=["text", "json", "none"], default="text")
    r.add_argument("--env", metavar="FILE", help="JSON file with the run environment (`env` in flows and steps)")
    r.set_defaults(func=cmd_run)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
