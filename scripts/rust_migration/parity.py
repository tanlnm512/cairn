#!/usr/bin/env python3
"""Parity runner replaying the MCP goldens against the RUST daemon (1e)."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(REPO_ROOT / "src"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import baseline  # reuse: env pins, corpus staging, build/embed, free port
import golden_record as gr  # reuse: golden client, query script, normalizer, fixture IO
from cairn.mcp_server import lifecycle  # reuse: healthz probe

SCHEMA = gr.SCHEMA
DEFAULT_OUT_DIR = REPO_ROOT / "rust" / "golden" / "mcp"
CORPORA = {
    "yarl": REPO_ROOT / "benchmarks" / "datasource" / "t2" / "yarl",
    "attrs": REPO_ROOT / "benchmarks" / "datasource" / "ds2" / "second-corpus" / "attrs-26.1.0",
}
RUST_WORKSPACE = REPO_ROOT / "rust"
CARGO_BIN = Path.home() / ".cargo" / "bin"
SERVE_EXAMPLE = "serve_search"
SERVE_ADDR_RE = re.compile(r"http://127\.0\.0\.1:(\d+)/mcp")
HEALTHZ_TIMEOUT_S = 180.0
DB_NAME = "store.kg"
DIFF_PRINT_CHARS = 300


def _note(msg: str) -> None:
    """Progress goes to stderr: stdout is the verdict channel."""
    print(f"[parity] {msg}", file=sys.stderr, flush=True)


# --- store wiring ------------------------------------------------------------


def cairn_cli() -> list[str]:
    """The repo venv's `cairn` when present, else PATH."""
    venv = REPO_ROOT / ".venv" / "bin" / "cairn"
    return [str(venv)] if venv.exists() else ["cairn"]


def cairn_config() -> dict:
    """Live store wiring via the CLI machine contract (`config --db` + `--json`)."""
    db = subprocess.run(
        cairn_cli() + ["config", "--db"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    cfg = json.loads(subprocess.run(
        cairn_cli() + ["config", "--json"],
        capture_output=True, text=True, check=True,
    ).stdout)
    cfg["db"] = db
    return cfg


def snapshot_db(live_db: Path, target: Path) -> None:
    """Consistent read-only sqlite backup of a live store into the scratch db."""
    src = sqlite3.connect(f"file:{live_db}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


@contextmanager
def build_env(scratch: Path, workspace: Path):
    """Pin the build/embed env to the scratch store, then restore."""
    keys = (*gr.ENV_PINS, "CAIRN_HOME", "CAIRN_DB", "CAIRN_KNOWLEDGE",
            "CAIRN_WORKSPACE")
    saved = {k: os.environ.get(k) for k in keys}
    os.environ.update(gr.ENV_PINS)
    os.environ["CAIRN_HOME"] = str(scratch / "home")
    os.environ["CAIRN_DB"] = str(scratch / DB_NAME)
    os.environ["CAIRN_KNOWLEDGE"] = str(scratch / ".knowledge")
    os.environ["CAIRN_WORKSPACE"] = str(workspace)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def prepare_store(store: str, scratch: Path, cfg: dict) -> dict:
    """Materialize the store to serve: live snapshot (self) or fresh corpus build.

    Fixed scratch layout: <scratch>/store.kg + <scratch>/.knowledge + <scratch>/home.
    """
    (scratch / "home").mkdir(parents=True, exist_ok=True)
    db = scratch / DB_NAME
    knowledge = scratch / ".knowledge"
    if store == "self":
        workspace = Path(cfg["workspace"])
        _note(f"snapshotting live self-store {cfg['db']} ...")
        snapshot_db(Path(cfg["db"]), db)
        seeded = gr.seed_bundle(Path(cfg["knowledge"]), knowledge)
    else:
        workspace = baseline.stage_corpus(CORPORA[store], scratch)
        with build_env(scratch, workspace):
            _note(f"building {store} corpus ({workspace}) ...")
            built = baseline.run_build(workspace, db)
            _note(f"build {built['seconds']}s; embedding (hash) ...")
            baseline.run_embed(db)
        seeded = gr.seed_bundle(Path(cfg["knowledge"]), knowledge)
    gr.seed_concepts(knowledge)
    return {"workspace": workspace, "seeded": seeded}


# --- daemon + client ---------------------------------------------------------


class RustClient(gr.GoldenClient):
    """GoldenClient tolerating rmcp SSE framing (empty keepalive data frames)."""

    @staticmethod
    def _parse_messages(raw: str) -> list[dict]:
        raw = raw.strip()
        if raw.startswith("{"):
            return [json.loads(raw)]
        return [
            json.loads(payload)
            for line in raw.splitlines()
            if line.startswith("data:")
            for payload in (line[len("data:") :].strip(),)
            if payload
        ]


def daemon_env(scratch: Path, workspace: Path, api_key: str,
               cargo_path: bool = False) -> dict:
    """Child env for either daemon: pins, scratch store wiring, keyed."""
    env = dict(os.environ)
    for key in ("CAIRN_READ_ONLY", "CAIRN_MCP_API_KEY", "PYTEST_CURRENT_TEST",
                "CAIRN_DB", "CAIRN_KNOWLEDGE", "CAIRN_WORKSPACE", "CAIRN_HOME"):
        env.pop(key, None)
    env.update(gr.ENV_PINS)
    env["CAIRN_HOME"] = str(scratch / "home")
    env["CAIRN_DB"] = str(scratch / DB_NAME)
    env["CAIRN_KNOWLEDGE"] = str(scratch / ".knowledge")
    env["CAIRN_WORKSPACE"] = str(workspace)
    env["CAIRN_MCP_API_KEY"] = api_key
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    if cargo_path:
        env["PATH"] = os.pathsep.join([str(CARGO_BIN),
                                       env.get("PATH", os.defpath)])
    return env


class RustDaemon:
    """Keyed `serve_search` example child serving the ported tool surface."""

    def __init__(self, exe: Path, db: Path, workspace: Path, env: dict):
        self.proc = subprocess.Popen(
            [str(exe), str(db)],
            cwd=str(workspace),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.port: int | None = None
        self.url: str | None = None
        self._output: list[str] = []
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()

    def _read_output(self) -> None:
        for line in self.proc.stdout:
            self._output.append(line)
            m = SERVE_ADDR_RE.search(line)
            if m and self.port is None:
                self.port = int(m.group(1))
                self.url = lifecycle.mcp_url(self.port)

    def wait_ready(self) -> bool:
        deadline = time.monotonic() + HEALTHZ_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                return False
            if self.port is not None and lifecycle.healthz_responds(
                self.port, lifecycle.DEFAULT_HOST, timeout=2.0
            ):
                return True
            time.sleep(0.25)
        return False

    def diagnostics(self) -> str:
        return "".join(self._output)[-2000:]

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)


def ensure_serve_binary() -> Path:
    """Build (cargo, incremental) and return the example binary; PATH gets the
    cargo bin dir prepended because the caller's PATH may predate rustup."""
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(CARGO_BIN), env.get("PATH", os.defpath)])
    subprocess.run(
        ["cargo", "build", "--release", "-p", "cairn-daemon",
         "--example", SERVE_EXAMPLE],
        cwd=str(RUST_WORKSPACE), check=True, env=env,
    )
    return RUST_WORKSPACE / "target" / "release" / "examples" / SERVE_EXAMPLE


# --- volatility overrides (burn-down classifications) ------------------------

# Cross-build BM25/query tie order is volatile; rank-order parity for these
# tools is owned by the dedicated rust/golden/{search,nav,flow}/ fixtures, so
# the MCP golden sorts lines.
SORT_LINES_TOOLS = {"search_symbols", "find_definition", "path"}

# Cross-build ambiguous-dispatch candidate order is volatile inside the line;
# the hop itself stays visible via the masked marker.
DISPATCH_LIST_RE = re.compile(r"could dispatch to: .*")


def extended_rules(rules: list) -> list:
    """Append parity-owned mask rules to the recorder's scrub rules."""
    return rules + [("dispatch_candidates", DISPATCH_LIST_RE,
                     "could dispatch to: <CANDIDATES>")]


def apply_volatility(entries: list[dict]) -> None:
    """Apply burn-down normalization flags on top of the recorder's script."""
    for entry in entries:
        if entry["tool"] in SORT_LINES_TOOLS:
            entry["sort_lines"] = True


# --- capture -----------------------------------------------------------------


def replay_entries(client: gr.GoldenClient, entries: list[dict]) -> dict:
    """Re-run the recorded script; unlike gr.drive this tolerates a partial
    rust catalog so unported tools surface as per-tool FAILs, not an abort."""
    captures = {}
    for entry in entries:
        args = dict(entry["args"])
        try:
            if "setup" in entry:
                setup = entry["setup"]
                ok, text = client.call(setup["tool"], setup["args"])
                if ok:
                    raise RuntimeError(f"setup {setup['tool']} failed: {text}")
                m = re.search(setup["id_regex"], text)
                if not m:
                    raise RuntimeError(
                        f"setup {setup['tool']} returned no concept id: {text!r}"
                    )
                args["doc_id"] = m.group(1)
            response = client.capture(entry["tool"], args)
        except RuntimeError as exc:
            response = {"isError": True,
                        "content": [{"type": "text",
                                     "text": f"parity replay failed: {exc}"}],
                        "structuredContent": None}
        captures[entry["tool"]] = {"args": args, "response": response}
        _note(f"  {entry['tool']}: replayed isError={response['isError']}")
    return captures


def attach_fixture(cap: dict, entry: dict, rules: list, meta: dict) -> None:
    """Attach the normalized fixture block (the recorder's fixture shape)."""
    sort_lines = bool(entry.get("sort_lines"))
    mask_lists = bool(entry.get("mask_list_lines"))
    cap["fixture"] = {
        "schema": SCHEMA,
        "tool": entry["tool"],
        "arguments": gr.normalize_obj(cap["args"], rules),
        "capture": {**meta, "captured_at": datetime.now(timezone.utc).isoformat()},
        "normalization": {
            "rules": [name for name, _p, _r in rules],
            "sort_lines": sort_lines,
            "mask_list_lines": mask_lists,
        },
        "response": gr.normalize_response(cap["response"], rules,
                                          sort_lines, mask_lists),
    }


def entry_map(entries: list[dict]) -> dict:
    return {e["tool"]: e for e in entries}


# --- verify ------------------------------------------------------------------


def verify_store(out_dir: Path, captures: dict, entries: list[dict],
                 dump_dir: Path | None = None) -> tuple[list, list]:
    """Per-tool golden diff; returns (passed tools, (tool, first-diff) failures)."""
    by_tool = entry_map(entries)
    passed, failures = [], []
    for tool in sorted(by_tool):
        path = out_dir / f"{tool}.json"
        if not path.exists():
            failures.append((tool, f"missing fixture {path}"))
            continue
        golden = json.loads(path.read_text(encoding="utf-8"))
        actual = captures[tool]["fixture"]
        diff = gr._diff_path(gr.compare_scope(golden), gr.compare_scope(actual))
        if diff:
            failures.append((tool, diff))
            if dump_dir:
                _dump_diff(dump_dir, tool, golden, actual)
        else:
            passed.append(tool)
    for path in sorted(out_dir.glob("*.json")):
        if path.stem not in by_tool:
            failures.append((path.stem, "fixture without a script entry"))
    return passed, failures


def _dump_diff(dump_dir: Path, tool: str, golden: dict, actual: dict) -> None:
    """Write the failing pair's first-content texts for burn-down classification."""
    dump_dir.mkdir(parents=True, exist_ok=True)
    for side, fixture in (("golden", golden), ("rust", actual)):
        text = "\n".join(
            block.get("text", "") for block in fixture["response"].get("content") or []
        )
        (dump_dir / f"{tool}.{side}.txt").write_text(text, encoding="utf-8")


def _shrink(diff: str) -> str:
    return diff if len(diff) <= DIFF_PRINT_CHARS else diff[:DIFF_PRINT_CHARS] + "…"


# --- modes -------------------------------------------------------------------


def record(store: str, out_dir: Path, scratch: Path, cfg: dict) -> int:
    """Refresh the goldens from the python daemon over the wired store."""
    prep = prepare_store(store, scratch, cfg)
    workspace: Path = prep["workspace"]
    db = scratch / DB_NAME
    knowledge = scratch / ".knowledge"
    rules = extended_rules(gr.scrub_rules(scratch, scratch / "home", workspace))
    meta = {
        "impl": "python",
        "harness": "scripts/rust_migration/parity.py",
        "corpus": gr.normalize_obj(baseline.corpus_identity(workspace), rules),
        "store": gr.store_counts(db),
        "env_pins": dict(gr.ENV_PINS),
        "knowledge_bundle_seeded_files": prep["seeded"],
    }
    targets = gr.refine_targets(
        db, baseline.choose_targets(db, workspace, knowledge)
    )
    entries = gr.query_script(targets, gr.workflow_ref(knowledge))
    apply_volatility(entries)
    api_key = secrets.token_urlsafe(24)
    port = baseline._free_port()
    daemon = gr.GoldenDaemon(daemon_env(scratch, workspace, api_key), port, api_key)
    try:
        if not daemon.wait_ready():
            out = daemon.proc.stdout.read() if daemon.proc.stdout else ""
            raise RuntimeError(
                f"python daemon never became healthy on :{port}: {out[-2000:]}"
            )
        _note(f"python daemon up on :{port} (keyed); capturing ...")
        captures = gr.drive(gr.GoldenClient(daemon.url, api_key), entries)
    finally:
        daemon.stop()
    by_tool = entry_map(entries)
    for tool, cap in captures.items():
        attach_fixture(cap, by_tool[tool], rules, meta)
    written = gr.write_fixtures(out_dir, captures)
    _note(f"wrote {len(written)} fixture(s) under {out_dir}")
    print(json.dumps({"recorded": len(written), "out_dir": str(out_dir),
                      "store": meta["store"]}, indent=2))
    return 0


def replay(out_dir: Path, scratch: Path, cfg: dict, store: str,
           dump_dir: Path | None = None) -> int:
    """Replay the goldens against the rust daemon and print per-tool verdicts."""
    prep = prepare_store(store, scratch, cfg)
    workspace: Path = prep["workspace"]
    db = scratch / DB_NAME
    knowledge = scratch / ".knowledge"
    rules = extended_rules(gr.scrub_rules(scratch, scratch / "home", workspace))
    targets = gr.refine_targets(
        db, baseline.choose_targets(db, workspace, knowledge)
    )
    entries = gr.query_script(targets, gr.workflow_ref(knowledge))
    apply_volatility(entries)
    api_key = secrets.token_urlsafe(24)
    exe = ensure_serve_binary()
    daemon = RustDaemon(exe, db, workspace,
                        daemon_env(scratch, workspace, api_key, cargo_path=True))
    try:
        if not daemon.wait_ready():
            raise RuntimeError(
                f"rust daemon never became healthy: {daemon.diagnostics()}"
            )
        _note(f"rust daemon up on :{daemon.port} (keyed); replaying ...")
        client = RustClient(daemon.url, api_key)
        catalog = set(client.start())
        _note(f"rust catalog: {len(catalog)} tools "
              f"(missing: {sorted({e['tool'] for e in entries} - catalog)})")
        captures = replay_entries(client, entries)
    finally:
        daemon.stop()
    by_tool = entry_map(entries)
    for tool, cap in captures.items():
        attach_fixture(cap, by_tool[tool], rules, meta={})
    passed, failures = verify_store(out_dir, captures, entries, dump_dir)
    for tool in sorted(by_tool):
        diff = next((d for t, d in failures if t == tool), None)
        if diff:
            print(f"FAIL {tool}: {_shrink(diff)}")
        else:
            print(f"PASS {tool}")
    total = len(by_tool)
    print(f"{store}: {len(passed)}/{total} tools golden-equal against {out_dir}")
    return 0 if not failures else 1


# --- entry point -------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, choices=["self", *CORPORA],
                        help="self: snapshot the live `cairn config --db` store; "
                             "yarl/attrs: fresh corpus build into a tmp store.")
    parser.add_argument("--record", action="store_true",
                        help="Refresh goldens from the python daemon (same normalizer).")
    parser.add_argument("--out-dir", default=None,
                        help=f"Fixture directory (default: {DEFAULT_OUT_DIR} for self,"
                             f" {DEFAULT_OUT_DIR}/<store> for corpora).")
    parser.add_argument("--keep-scratch", action="store_true",
                        help="Keep the scratch store for inspection.")
    parser.add_argument("--dump-diffs", default=None,
                        help="Directory for failing tools' golden/rust text dumps.")
    args = parser.parse_args(argv)

    out_dir = (Path(args.out_dir) if args.out_dir else
               DEFAULT_OUT_DIR if args.store == "self"
               else DEFAULT_OUT_DIR / args.store)
    cfg = cairn_config()
    scratch = Path(tempfile.mkdtemp(prefix=f"cairn-rust-parity-{args.store}-"))
    _note(f"scratch {scratch}")
    try:
        if args.record:
            return record(args.store, out_dir, scratch, cfg)
        return replay(out_dir, scratch, cfg, args.store,
                      Path(args.dump_diffs) if args.dump_diffs else None)
    finally:
        if args.keep_scratch:
            _note(f"scratch kept: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
