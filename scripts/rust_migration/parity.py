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


def _reexec_repo_venv() -> None:
    """Re-exec under the repo venv python when this interpreter lacks the project deps."""
    try:
        from cairn.mcp_server import lifecycle  # noqa: F401
    except ModuleNotFoundError:
        venv_python = REPO_ROOT / ".venv" / "bin" / "python"
        if (venv_python.exists()
                and os.path.realpath(sys.executable) != os.path.realpath(venv_python)):
            os.execv(str(venv_python),
                     [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_reexec_repo_venv()

import baseline  # reuse: env pins, corpus identity, build/embed, free port
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
# Checked-in store fixtures: the subset-cap corpus, the built store, the seeded
# bundle, and a manifest; served byte-identically to both parity sides.
PINNED_STORES = REPO_ROOT / "rust" / "golden" / "stores"
PIN_SUBSET_FILE_CAP = 100
PINNED_SIZE_CAP_BYTES = 25_000_000
PINNED_MANIFEST_SCHEMA = "cairn-rust-parity-pinned-store/1"
# Spike-query fixtures regenerated over the pinned store at record time.
SPIKE_TOOLS = ("search_symbols", "semantic_search")


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


def pin_db_bytes(db: Path) -> None:
    """Checkpoint the store into one sidecar-free DELETE-journal .kg."""
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.execute("VACUUM;")
        conn.commit()
    finally:
        conn.close()
    for suffix in ("-wal", "-shm", "-journal"):
        Path(str(db) + suffix).unlink(missing_ok=True)


def _git_marker_file(corpus: Path) -> None:
    """Replace the staged corpus's empty .git dir with a trackable .git file."""
    marker = corpus / ".git"
    if marker.is_dir():
        shutil.rmtree(marker)
    marker.write_text("gitdir: pinned-parity-corpus\n", encoding="utf-8")


def reproduce_pinned_corpus(store: str, scratch: Path) -> Path | None:
    """Stage the workspace from the checked-in pinned corpus, byte-exact."""
    pinned = PINNED_STORES / store
    corpus = pinned / "corpus"
    if pinned.exists() and not corpus.is_dir():
        raise SystemExit(
            f"REFUSING record: {pinned} has no corpus/ to reproduce; delete "
            f"the directory to re-pin from the live source deliberately"
        )
    if not corpus.is_dir():
        return None
    staged = scratch / "corpus"
    shutil.copytree(corpus, staged)
    _note(f"reproducing pinned corpus from {corpus}")
    return staged


def build_pinned_store(store: str, scratch: Path, cfg: dict) -> dict:
    """Build one store's pinned bytes into scratch and check them in.

    Fixed layout: <scratch>/store.kg + <scratch>/.knowledge + <scratch>/corpus.
    """
    (scratch / "home").mkdir(parents=True, exist_ok=True)
    db = scratch / DB_NAME
    knowledge = scratch / ".knowledge"
    workspace = reproduce_pinned_corpus(store, scratch)
    if workspace is None:
        import spike_golden  # reuse: the shared subset-cap corpus stager

        source = Path(cfg["workspace"]) if store == "self" else CORPORA[store]
        if spike_golden.indexable_rel_paths(source):
            workspace = spike_golden.stage_subset_corpus(
                source, scratch, cap=PIN_SUBSET_FILE_CAP)
        else:
            # Marker-less corpus: repo discovery needs the staged .git marker,
            # and these corpora sit far under the file cap whole.
            workspace = baseline.stage_corpus(source, scratch)
    _git_marker_file(workspace)
    with build_env(scratch, workspace):
        _note(f"building {store} pinned corpus ({workspace}) ...")
        built = baseline.run_build(workspace, db)
        _note(f"build {built['seconds']}s; embedding (hash) ...")
        baseline.run_embed(db)
    seeded = gr.seed_bundle(Path(cfg["knowledge"]), knowledge)
    gr.seed_concepts(knowledge)
    pin_db_bytes(db)
    _write_pinned_dir(store, scratch, workspace, seeded)
    return {"workspace": workspace, "seeded": seeded}


def _write_pinned_dir(store: str, scratch: Path, workspace: Path,
                      seeded: int) -> None:
    """Check the pinned bytes in under rust/golden/stores/<store>/."""
    pinned = PINNED_STORES / store
    if pinned.exists():
        shutil.rmtree(pinned)
    pinned.mkdir(parents=True)
    shutil.copy2(scratch / DB_NAME, pinned / DB_NAME)
    shutil.copytree(scratch / ".knowledge", pinned / "bundle")
    shutil.copytree(workspace, pinned / "corpus")
    corpus_files = [p for p in (pinned / "corpus").rglob("*") if p.is_file()]
    knowledge_files = [p for p in (pinned / "bundle").rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in pinned.rglob("*") if p.is_file())
    if total > PINNED_SIZE_CAP_BYTES:
        raise RuntimeError(
            f"pinned store {store} is {total} bytes, over the "
            f"{PINNED_SIZE_CAP_BYTES}-byte cap; lower PIN_SUBSET_FILE_CAP"
        )
    manifest = {
        "schema": PINNED_MANIFEST_SCHEMA,
        "store": store,
        "cap_files": PIN_SUBSET_FILE_CAP,
        "corpus_files": len(corpus_files),
        "corpus_bytes": sum(p.stat().st_size for p in corpus_files),
        "db_bytes": (pinned / DB_NAME).stat().st_size,
        "total_bytes": total,
        "store_counts": gr.store_counts(scratch / DB_NAME),
        "knowledge_files": len(knowledge_files),
        "knowledge_bundle_seeded_files": seeded,
        "env_pins": dict(gr.ENV_PINS),
    }
    (pinned / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _note(f"pinned store {pinned} ({total / 1e6:.1f} MB, "
          f"{manifest['corpus_files']} corpus files, cap {PIN_SUBSET_FILE_CAP})")


def load_pinned_store(store: str, scratch: Path) -> dict:
    """Materialize the checked-in pinned bytes into the scratch layout."""
    pinned = PINNED_STORES / store
    if not (pinned / DB_NAME).is_file():
        raise SystemExit(
            f"missing pinned store {pinned / DB_NAME}; "
            f"record it first: parity.py --store {store} --record"
        )
    (scratch / "home").mkdir(parents=True, exist_ok=True)
    shutil.copy2(pinned / DB_NAME, scratch / DB_NAME)
    shutil.copytree(pinned / "bundle", scratch / ".knowledge")
    shutil.copytree(pinned / "corpus", scratch / "corpus")
    for suffix in ("-wal", "-shm", "-journal"):
        Path(str(scratch / DB_NAME) + suffix).unlink(missing_ok=True)
    return {"workspace": scratch / "corpus"}


def refuse_live_store(db: Path, knowledge: Path) -> None:
    """Loudly refuse when replay would serve or write the live ~/.cairn store."""
    from cairn.paths import resolve_store

    live = resolve_store()
    pairs = [(db, live.db, "store db"), (knowledge, live.knowledge, "knowledge bundle")]
    for served, live_path, what in pairs:
        if os.path.realpath(served) == os.path.realpath(live_path):
            msg = (f"REFUSING replay: the served {what} {served} resolves to the "
                   f"live store {live_path}; replay must never touch it")
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
                f"{path.name}: no replay block; re-record with "
                f"parity.py --record so replay needs no store probing"
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


# --- spike-query regeneration -------------------------------------------------


def _spike_candidate_patterns(db: Path, probed: str) -> list[str]:
    """The probed pattern first, then deterministic store-derived prefixes."""
    candidates = [probed]
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        names = [r[0] for r in conn.execute(
            "SELECT DISTINCT name FROM symbols WHERE name != '' "
            "ORDER BY name LIMIT 20")]
    finally:
        conn.close()
    for name in names:
        for width in (4, 2, 1):
            prefix = name[:width] + "*"
            if prefix not in candidates:
                candidates.append(prefix)
    return candidates


def capture_spike_blocks(client: gr.GoldenClient, captures: dict, db: Path,
                         rules: list) -> dict:
    """Regenerate the search fixtures' spike_queries over the pinned store."""
    import spike_golden
    import semantic_golden

    blocks: dict = {}
    probed = captures["search_symbols"]["args"]["pattern"]
    for pattern in _spike_candidate_patterns(db, probed):
        response = client.capture("search_symbols", {"pattern": pattern})
        try:
            queries = spike_golden.spike_queries({
                "arguments": {"pattern": pattern},
                "response": response,
            })
        except (IndexError, RuntimeError):
            continue
        if pattern != probed:
            _note(f"search_symbols re-baselined to {pattern!r}")
            captures["search_symbols"] = {"args": {"pattern": pattern},
                                          "response": response}
        blocks["search_symbols"] = [
            {"pattern": q,
             "response": gr.normalize_response(
                 client.capture("search_symbols", {"pattern": q}),
                 rules, False, False)}
            for q in queries
        ]
        break
    else:
        raise RuntimeError(
            "no search_symbols pattern yields the >=5 spike query shapes"
        )
    _note(f"search_symbols spike queries: {queries}")

    cap = captures["semantic_search"]
    blocks["semantic_search"] = [
        {"query": query["query"], "limit": query["limit"],
         "response": gr.normalize_response(
             client.capture("semantic_search",
                            {"query": query["query"],
                             "limit": query["limit"]}),
             rules, False, False)}
        for query in semantic_golden.semantic_queries(
            {"arguments": dict(cap["args"])})
    ]
    return blocks


# --- capture -----------------------------------------------------------------


def attach_fixture(cap: dict, entry: dict, rules: list, meta: dict,
                   replay_store: str | None = None,
                   replay_workspace: str | None = None,
                   spike: list | None = None,
                   store_raw_response: bool = False) -> None:
    """Attach the normalized fixture block (the recorder's fixture shape)."""
    sort_lines = bool(entry.get("sort_lines"))
    mask_lists = bool(entry.get("mask_list_lines"))
    stored_sort = not store_raw_response and sort_lines
    block = gr.replay_block(entry, cap["args"], rules, seq=entry.get("seq"))
    fixture = {
        "schema": SCHEMA,
        "tool": entry["tool"],
        "arguments": gr.normalize_obj(cap["args"], rules),
        "replay": block,
        "capture": {**meta, "captured_at": datetime.now(timezone.utc).isoformat()},
        "normalization": {
            "rules": [name for name, _p, _r in rules],
            "sort_lines": sort_lines,
            "mask_list_lines": mask_lists,
        },
        "response": gr.normalize_response(cap["response"], rules,
                                          stored_sort, mask_lists),
    }
    if replay_store:
        fixture["replay_store"] = replay_store
    if replay_workspace:
        fixture["replay_workspace"] = replay_workspace
    if spike is not None:
        fixture["spike_queries"] = spike
    cap["fixture"] = fixture


def record(store: str, out_dir: Path, scratch: Path, cfg: dict) -> int:
    """Build the pinned store, capture the goldens from the python daemon over
    those exact bytes, and write the fixtures with their replay arguments."""
    build_pinned_store(store, scratch, cfg)
    workspace: Path = scratch / "corpus"
    db = scratch / DB_NAME
    knowledge = scratch / ".knowledge"
    rules = gr.scrub_rules(scratch, scratch / "home", workspace)
    meta = {
        "impl": "python",
        "harness": "scripts/rust_migration/parity.py",
        "corpus": gr.normalize_obj(baseline.corpus_identity(workspace), rules),
        "store": gr.store_counts(db),
        "env_pins": dict(gr.ENV_PINS),
    }
    targets = gr.refine_targets(
        db, baseline.choose_targets(db, workspace, knowledge)
    )
    entries = gr.query_script(targets, gr.workflow_ref(knowledge))
    for seq, entry in enumerate(entries):
        entry["seq"] = seq
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
        client = gr.GoldenClient(daemon.url, api_key)
        captures = gr.drive(client, entries)
        spikes = capture_spike_blocks(client, captures, db, rules)
    finally:
        daemon.stop()
    by_tool = entry_map(entries)
    pinned_rel = (PINNED_STORES / store).relative_to(REPO_ROOT)
    replay_store = str(pinned_rel / DB_NAME)
    replay_workspace = str(pinned_rel / "corpus")
    for tool, cap in captures.items():
        attach_fixture(
            cap, by_tool[tool], rules, meta,
            replay_store=replay_store,
            replay_workspace=replay_workspace,
            spike=spikes.get(tool),
            store_raw_response=tool == "search_symbols",
        )
    written = gr.write_fixtures(out_dir, captures)
    _note(f"wrote {len(written)} fixture(s) under {out_dir}")
    print(json.dumps({"recorded": len(written), "out_dir": str(out_dir),
                      "store": meta["store"]}, indent=2))
    return 0


# --- verify ------------------------------------------------------------------


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
            golden.get("response") or {}, [],
            bool(flags.get("sort_lines")), False,
        )}
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


def replay(out_dir: Path, scratch: Path, cfg: dict, store: str,
           dump_dir: Path | None = None) -> int:
    """Serve the pinned store bytes to the rust daemon and replay the fixtures."""
    del cfg  # replay reads no live wiring: fixtures + pinned bytes suffice
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
                        help="self/yarl/attrs: serve the checked-in pinned store "
                             f"under {PINNED_STORES.relative_to(REPO_ROOT)}.")
    parser.add_argument("--record", action="store_true",
                        help="Rebuild the pinned store and refresh the goldens "
                             "from the python daemon over those bytes.")
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
