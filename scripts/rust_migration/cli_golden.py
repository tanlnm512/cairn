#!/usr/bin/env python3
"""C-4 CLI golden gate: differential python-vs-rust runs of the fixed machine-contract command set (3a)."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(REPO_ROOT / "src"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reexec_repo_venv() -> None:
    """Re-exec under the repo venv python when this interpreter lacks the project deps."""
    try:
        import cairn.cli  # noqa: F401
    except ModuleNotFoundError:
        venv_python = REPO_ROOT / ".venv" / "bin" / "python"
        if (venv_python.exists()
                and os.path.realpath(sys.executable) != os.path.realpath(venv_python)):
            os.execv(str(venv_python),
                     [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_reexec_repo_venv()

import golden_record as gr  # reuse: path scrub rules + text normalizer
import parity  # reuse: cairn_cli(), CARGO_BIN, RUST_WORKSPACE, load_pinned_store

DB_NAME = parity.DB_NAME
RUST_BIN = parity.RUST_WORKSPACE / "target" / "release" / "cairn"
CMD_TIMEOUT_S = 120.0
# C-4 machine contracts: stdout-pure JSON modes + exit codes, over the seeded scratch store.
# One command per knowledge-family group: knowledge, workflow, memory, compass, wiki.
COMMANDS = [
    ["config", "--json"],
    ["config", "--db"],
    ["config", "--mcp-config"],
    ["config", "--list"],
    ["def", "--json", "resolve_store"],
    ["callers", "--json", "resolve_store"],
    ["callees", "--json", "search_symbols"],
    ["search", "--json", "impact"],
    ["impact", "--json", "search_symbols"],
    ["deps", "--json", "cairn"],
    ["tree", "cairn"],
    ["pack", "--json", "--task", "parse dispatch", "--budget", "512"],
    ["knowledge", "list", "--json"],
    ["knowledge", "search", "--json", "golden capture"],
    ["knowledge", "islands", "--json"],
    ["knowledge", "workflow", "trace", "--json", "golden capture workflow"],
    ["memory", "list"],
    ["compass", "list"],
    ["wiki", "search", "golden capture"],
]
DIFF_PRINT_CHARS = 300


def _note(msg: str) -> None:
    """Progress goes to stderr: stdout is the verdict channel."""
    print(f"[cli-golden] {msg}", file=sys.stderr, flush=True)


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def cli_env(scratch: Path, workspace: Path, cargo_path: bool) -> dict:
    """Child env wiring the scratch store; the rust leg's PATH gets the cargo
    bin dir prepended because the caller's PATH may predate rustup."""
    env = dict(os.environ)
    for key in ("CAIRN_READ_ONLY", "CAIRN_MCP_API_KEY", "PYTEST_CURRENT_TEST",
                "CAIRN_DB", "CAIRN_KNOWLEDGE", "CAIRN_WORKSPACE", "CAIRN_HOME"):
        env.pop(key, None)
    env.update(gr.ENV_PINS)
    # Pack costs differ when the python leg resolves the exact tokenizer from a
    # locally cached model; the Rust leg is heuristic-only, so pin the probe to
    # a never-cached name and differential-run the heuristic contract on both.
    env["CAIRN_EMBED_LOCAL_MODEL"] = "cairn-golden-no-such-model"
    # Python-side logging (ann-index degrade warnings, rich handler) is noise
    # this gate does not contract on; the C-4 surface is stdout + exit codes.
    env["CAIRN_LOG_LEVEL"] = "ERROR"
    env["CAIRN_HOME"] = str(scratch / "home")
    env["CAIRN_DB"] = str(scratch / DB_NAME)
    env["CAIRN_KNOWLEDGE"] = str(scratch / ".knowledge")
    env["CAIRN_WORKSPACE"] = str(workspace)
    env["NO_COLOR"] = "1"
    if cargo_path:
        env["PATH"] = os.pathsep.join([str(parity.CARGO_BIN),
                                       env.get("PATH", os.defpath)])
    return env


def prepare_store(scratch: Path) -> None:
    """Scratch materialization of the pinned self store (db, seeded bundle, corpus)."""
    parity.load_pinned_store("self", scratch)


def ensure_rust_binary() -> Path:
    """Build (cargo, incremental) the release cairn binary."""
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(parity.CARGO_BIN), env.get("PATH", os.defpath)])
    subprocess.run(
        ["cargo", "build", "--release", "-p", "cairn-cli"],
        cwd=str(parity.RUST_WORKSPACE), check=True, env=env,
    )
    return RUST_BIN


def run_side(exe: list[str], argv: list[str], env: dict) -> tuple[int, str, str]:
    proc = subprocess.run(
        [*exe, *argv],
        capture_output=True, text=True, env=env, timeout=CMD_TIMEOUT_S,
        cwd=str(REPO_ROOT),
    )
    return proc.returncode, proc.stdout, proc.stderr


def normalize(text: str, rules: list) -> str:
    """Colors stripped, then the golden path/time scrub rules applied."""
    return gr.normalize_text(ANSI_RE.sub("", text), rules)


def first_diff(argv: list[str], py: tuple, rs: tuple, rules: list) -> str | None:
    if py[0] != rs[0]:
        return f"exit {py[0]} != {rs[0]}"
    for stream, p, r in zip(("stdout", "stderr"), py[1:], rs[1:]):
        pn, rn = normalize(p, rules), normalize(r, rules)
        if pn != rn:
            plines, rlines = pn.splitlines(), rn.splitlines()
            for i, (a, b) in enumerate(zip(plines, rlines)):
                if a != b:
                    return f"{stream} line {i}: {a!r} != {b!r}"
            side = "python" if len(plines) > len(rlines) else "rust"
            extra = plines[len(rlines):] if side == "python" else rlines[len(plines):]
            return f"{stream}: {side} has {abs(len(plines) - len(rlines))} extra line(s): {extra[0]!r}"
    return None


def _shrink(diff: str) -> str:
    return diff if len(diff) <= DIFF_PRINT_CHARS else diff[:DIFF_PRINT_CHARS] + "…"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rust-bin", default=None,
                        help=f"Rust binary override (default: build {RUST_BIN.relative_to(REPO_ROOT)}). "
                             "The repo venv `cairn` self-checks the harness machinery.")
    parser.add_argument("--keep-scratch", action="store_true",
                        help="Keep the scratch store for inspection.")
    args = parser.parse_args(argv)

    scratch = Path(tempfile.mkdtemp(prefix="cairn-rust-cli-golden-"))
    _note(f"scratch {scratch}")
    workspace = scratch / "corpus"
    try:
        prepare_store(scratch)
        rules = gr.scrub_rules(scratch, scratch / "home", workspace)
        py_env = cli_env(scratch, workspace, cargo_path=False)
        rust_bin = Path(args.rust_bin) if args.rust_bin else ensure_rust_binary()
        rust_env = cli_env(scratch, workspace, cargo_path=True)
        _note(f"rust binary: {rust_bin}")

        failures = []
        for cmd in COMMANDS:
            py = run_side(parity.cairn_cli(), cmd, py_env)
            rs = run_side([str(rust_bin)], cmd, rust_env)
            diff = first_diff(cmd, py, rs, rules)
            label = " ".join(cmd)
            if diff:
                failures.append(label)
                print(f"FAIL {label}: {_shrink(diff)}")
            else:
                print(f"PASS {label}")
        total = len(COMMANDS)
        print(f"{total - len(failures)}/{total} commands golden-equal")
        return 0 if not failures else 1
    finally:
        if args.keep_scratch:
            _note(f"scratch kept: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
