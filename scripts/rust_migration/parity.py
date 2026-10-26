#!/usr/bin/env python3
"""Replay the frozen MCP goldens (rust/golden/mcp, over the pinned stores) against the RUST daemon."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(Path(__file__).resolve().parent),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import golden_record as gr  # reuse: golden client, normalizer, diff oracle

SCHEMA = gr.SCHEMA
DEFAULT_OUT_DIR = REPO_ROOT / "rust" / "golden" / "mcp"
RUST_WORKSPACE = REPO_ROOT / "rust"
CARGO_BIN = Path.home() / ".cargo" / "bin"
SERVE_EXAMPLE = "serve_search"
SERVE_ADDR_RE = re.compile(r"http://127\.0\.0\.1:(\d+)/mcp")
HEALTHZ_TIMEOUT_S = 180.0
DB_NAME = "store.kg"
DIFF_PRINT_CHARS = 300
# Checked-in store fixtures: the subset-cap corpus, the built store, the seeded
# bundle, and a manifest; served to the rust daemon byte-identically.
PINNED_STORES = REPO_ROOT / "rust" / "golden" / "stores"


def _note(msg: str) -> None:
    """Progress goes to stderr: stdout is the verdict channel."""
    print(f"[parity] {msg}", file=sys.stderr, flush=True)


# --- store wiring ------------------------------------------------------------


def load_pinned_store(store: str, scratch: Path) -> dict:
    """Materialize the checked-in pinned bytes into the scratch layout."""
    pinned = PINNED_STORES / store
    if not (pinned / DB_NAME).is_file():
        raise SystemExit(
            f"missing pinned store {pinned / DB_NAME}; the goldens are frozen — "
            f"restore them from history"
        )
    (scratch / "home").mkdir(parents=True, exist_ok=True)
    shutil.copy2(pinned / DB_NAME, scratch / DB_NAME)
    shutil.copytree(pinned / "bundle", scratch / ".knowledge")
    shutil.copytree(pinned / "corpus", scratch / "corpus")
    for suffix in ("-wal", "-shm", "-journal"):
        Path(str(scratch / DB_NAME) + suffix).unlink(missing_ok=True)
    return {"workspace": scratch / "corpus"}


def refuse_live_store(db: Path, knowledge: Path) -> None:
    """Loudly refuse when replay would serve or write the live store."""
    home = Path(os.environ.get("CAIRN_HOME", str(Path.home() / ".cairn")))
    overrides = {os.environ.get("CAIRN_DB"), os.environ.get("CAIRN_KNOWLEDGE")}
    live_paths = {Path(os.path.realpath(p)) for p in overrides if p}
    live_paths.add(Path(os.path.realpath(home)))
    for served in (db, knowledge):
        real = Path(os.path.realpath(served))
        for live in live_paths:
            if real == live or live in real.parents:
                msg = (f"REFUSING replay: the served path {served} resolves "
                       f"inside the live store {live}; replay must never touch it")
                print(msg)
                _note(msg)
                raise SystemExit(2)


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


def daemon_env(scratch: Path, workspace: Path, api_key: str) -> dict:
    """Child env for the daemon: pins, scratch store wiring, keyed, cargo on PATH."""
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
                self.url = f"http://127.0.0.1:{self.port}/mcp"

    def wait_ready(self) -> bool:
        deadline = time.monotonic() + HEALTHZ_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                return False
            if self.port is not None and healthz_responds(self.port):
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


def healthz_responds(port: int, host: str = "127.0.0.1",
                     timeout: float = 2.0) -> bool:
    """True when the daemon answers GET /healthz with 200 within ``timeout``."""
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", "/healthz")
        return conn.getresponse().status == 200
    except OSError:
        return False
    finally:
        conn.close()


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


def apply_volatility(entries: list[dict]) -> None:
    """Apply burn-down normalization flags on top of the recorder's script."""
    for entry in entries:
        if entry["tool"] in SORT_LINES_TOOLS:
            entry["sort_lines"] = True


# --- fixture replay blocks -----------------------------------------------------


def denormalize_obj(obj, store_home: Path, home: Path, workspace: Path):
    """Substitute scrub placeholders with this run's paths (inverse of capture)."""
    subs = [("<STORE_HOME>", str(store_home)), ("<CAIRN_HOME>", str(home)),
            ("<WORKSPACE>", str(workspace))]

    def _sub(text: str) -> str:
        for placeholder, value in subs:
            text = text.replace(placeholder, value)
        return text

    if isinstance(obj, str):
        return _sub(obj)
    if isinstance(obj, list):
        return [denormalize_obj(v, store_home, home, workspace) for v in obj]
    if isinstance(obj, dict):
        return {k: denormalize_obj(v, store_home, home, workspace)
                for k, v in obj.items()}
    return obj


def entries_from_fixtures(out_dir: Path, scratch: Path, workspace: Path) -> list[dict]:
    """The replay script read from fixture `replay` blocks; never store-probed."""
    paths = sorted(out_dir.glob("*.json"))
    if not paths:
        raise SystemExit(f"no fixtures under {out_dir}")
    entries = []
    for path in paths:
        fixture = json.loads(path.read_text(encoding="utf-8"))
        block = fixture.get("replay")
        if block is None:
            raise SystemExit(
                f"{path.name}: no replay block; fixtures predate the freeze — "
                f"restore the frozen fixtures from history"
            )
        entry = {
            "tool": fixture["tool"],
            "args": denormalize_obj(block.get("args") or {},
                                    scratch, scratch / "home", workspace),
            "seq": int(block.get("seq", 0)),
            "sort_lines": bool((fixture.get("normalization") or {})
                               .get("sort_lines")),
            "mask_list_lines": bool((fixture.get("normalization") or {})
                                    .get("mask_list_lines")),
        }
        setup = block.get("setup")
        if setup:
            entry["setup"] = {
                "tool": setup["tool"],
                "args": denormalize_obj(setup.get("args") or {},
                                        scratch, scratch / "home", workspace),
                "id_regex": setup["id_regex"],
            }
        entries.append(entry)
    entries.sort(key=lambda e: e["seq"])
    return entries


def entry_map(entries: list[dict]) -> dict:
    return {e["tool"]: e for e in entries}


# --- capture -----------------------------------------------------------------


def attach_fixture(cap: dict, entry: dict, rules: list) -> None:
    """Attach the normalized fixture block (the recorder's fixture shape)."""
    sort_lines = bool(entry.get("sort_lines"))
    mask_lists = bool(entry.get("mask_list_lines"))
    fixture = {
        "schema": SCHEMA,
        "tool": entry["tool"],
        "arguments": gr.normalize_obj(cap["args"], rules),
        "replay": gr.replay_block(entry, cap["args"], rules, seq=entry.get("seq")),
        "capture": {},
        "normalization": {
            "rules": [name for name, _p, _r in rules],
            "sort_lines": sort_lines,
            "mask_list_lines": mask_lists,
        },
        "response": gr.normalize_response(cap["response"], rules,
                                          sort_lines, mask_lists),
    }
    cap["fixture"] = fixture


# --- verify ------------------------------------------------------------------


def replay_entries(client: gr.GoldenClient, entries: list[dict]) -> dict:
    """Re-run the recorded script; tolerates a partial catalog so unported
    tools surface as per-tool FAILs, not an abort."""
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


def _stable_normalization(fixture: dict) -> dict:
    """Normalization block minus the `*_resolved` scrub-rule names: those
    rules exist only where scratch paths resolve differently (symlinked
    tmp dirs), so their presence in the recorded list is host-dependent."""
    block = dict(fixture.get("normalization") or {})
    rules = block.get("rules")
    if rules is not None:
        block["rules"] = [r for r in rules if not r.endswith("_resolved")]
    return block


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
        # Re-apply the fixture's own sort flag to the stored response: sibling
        # harnesses (spike_golden) rewrite responses raw, so line-order
        # normalization must live at verify time. Only the sort is re-applied —
        # it is idempotent; the list-line mask is not.
        flags = golden.get("normalization") or {}
        golden = {**golden, "response": gr.normalize_response(
            golden.get("response") or {},
            [],
            bool(flags.get("sort_lines")), False,
        ), "normalization": _stable_normalization(golden)}
        actual = {**captures[tool]["fixture"],
                  "normalization": _stable_normalization(captures[tool]["fixture"])}
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


def replay(out_dir: Path, scratch: Path, store: str,
           dump_dir: Path | None = None) -> int:
    """Serve the pinned store bytes to the rust daemon and replay the fixtures."""
    load_pinned_store(store, scratch)
    workspace: Path = scratch / "corpus"
    db = scratch / DB_NAME
    knowledge = scratch / ".knowledge"
    refuse_live_store(db, knowledge)
    rules = gr.scrub_rules(scratch, scratch / "home", workspace)
    entries = entries_from_fixtures(out_dir, scratch, workspace)
    apply_volatility(entries)
    api_key = secrets.token_urlsafe(24)
    exe = ensure_serve_binary()
    daemon = RustDaemon(exe, db, workspace, daemon_env(scratch, workspace, api_key))
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
        attach_fixture(cap, by_tool[tool], rules)
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
    parser.add_argument("--store", required=True, choices=["self", "yarl", "attrs"],
                        help="self/yarl/attrs: serve the checked-in pinned store "
                             f"under {PINNED_STORES.relative_to(REPO_ROOT)}.")
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
    scratch = Path(tempfile.mkdtemp(prefix=f"cairn-rust-parity-{args.store}-"))
    _note(f"scratch {scratch}")
    try:
        return replay(out_dir, scratch, args.store,
                      Path(args.dump_diffs) if args.dump_diffs else None)
    finally:
        if args.keep_scratch:
            _note(f"scratch kept: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
