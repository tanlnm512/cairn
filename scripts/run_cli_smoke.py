#!/usr/bin/env python3
"""Replay a checked-in CLI command list against a pinned fixture and DB."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


def load_commands(list_path: Path) -> list[str]:
    """Return the executable command lines, skipping blanks and # comments."""
    lines = list_path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def pinned_env(db_path: Path) -> dict[str, str]:
    """Return the child env: ambient CAIRN_* drift removed, DB and telemetry pinned."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("CAIRN_")}
    env["CAIRN_DB"] = str(db_path)
    env["CAIRN_TELEMETRY"] = "off"
    return env


def run_smoke(commands: list[str], *, fixture: Path, db_path: Path, as_json: bool) -> int:
    """Execute commands fail-fast in order; return 0 when all pass, else 1."""
    env = pinned_env(db_path)
    rows: list[dict[str, object]] = []
    for command in commands:
        if not as_json:
            print(f"::group::{command}", flush=True)
        proc = subprocess.run(
            shlex.split(command),
            cwd=str(fixture),
            env=env,
            capture_output=as_json,
            text=as_json,
        )
        row: dict[str, object] = {"command": command, "exit_code": proc.returncode}
        if proc.returncode != 0:
            if as_json:
                row["stdout"] = proc.stdout
                row["stderr"] = proc.stderr
            rows.append(row)
            if as_json:
                print(json.dumps({"ok": False, "failed": command, "commands": rows}, indent=2))
            else:
                print("::endgroup::", flush=True)
                print(
                    f"::error::smoke command failed (exit {proc.returncode}): {command}",
                    flush=True,
                )
            return 1
        rows.append(row)
        if not as_json:
            print("::endgroup::", flush=True)
    if as_json:
        print(json.dumps({"ok": True, "failed": None, "commands": rows}, indent=2))
    else:
        print(f"smoke: {len(rows)}/{len(rows)} commands passed", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("list", type=Path, help="command-list file, one command per line")
    parser.add_argument("--fixture", type=Path, required=True, help="cwd for every command")
    parser.add_argument("--db", type=Path, required=True, help="CAIRN_DB pinned for every command")
    parser.add_argument("--json", action="store_true", help="emit a command/result table as JSON")
    args = parser.parse_args(argv)

    if not args.list.is_file():
        print(f"error: command list not found: {args.list}", file=sys.stderr)
        return 2
    if not args.fixture.is_dir():
        print(f"error: fixture directory not found: {args.fixture}", file=sys.stderr)
        return 2
    commands = load_commands(args.list)
    if not commands:
        print(f"error: no commands found in {args.list}", file=sys.stderr)
        return 2
    return run_smoke(commands, fixture=args.fixture, db_path=args.db, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
