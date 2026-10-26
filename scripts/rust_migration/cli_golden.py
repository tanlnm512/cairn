#!/usr/bin/env python3
"""C-4 CLI golden gate: replay the fixed machine-contract command set against the frozen fixtures under rust/golden/cli."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(Path(__file__).resolve().parent),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import golden_record as gr  # reuse: path scrub rules + text normalizer
import parity  # reuse: CARGO_BIN, RUST_WORKSPACE, load_pinned_store

DB_NAME = parity.DB_NAME
RUST_BIN = parity.RUST_WORKSPACE / "target" / "release" / "cairn"
CMD_TIMEOUT_S = 120.0
SCHEMA = "cairn-rust-golden-cli/1"
FIXTURE_DIR = REPO_ROOT / "rust" / "golden" / "cli"
CASES_FILE = FIXTURE_DIR / "cases.json"
# Frozen OKF concepts the seed hooks write; one file per concept id.
SEEDS_DIR = FIXTURE_DIR / "seeds"
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
    # Task-queue family: read-only cases share the seeded bundle; mutating
    # cases re-seed their task before each leg via RESEED.
    ["task", "list"],
    ["task", "list", "--status", "pending"],
    ["task", "list", "--status", "failed"],
    ["task", "show", "golden-catalog"],
    ["task", "show", "golden-done"],
    ["task", "show", "no-such-task"],
    ["task", "claim", "golden-claim"],
    ["task", "claim", "golden-claim", "--as", "alice"],
    ["task", "claim", "no-such-task"],
    ["task", "complete", "golden-complete", "--result",
     '[{"module": "src/cairn/llm", "title": "Task queue"}]'],
    ["task", "complete", "golden-revise", "--result",
     "See `no/such/file.py`."],
    ["task", "drop", "golden-drop"],
    # Wiki family: read-only cases share the seeded manifest/pages; the
    # mutating cases (generate, generate --llm, retry, enrich) re-seed via
    # RESEED, keyed by each command's first three argv words.
    ["wiki", "generate"],
    ["wiki", "generate", "--dry-run"],
    ["wiki", "generate", "--llm"],
    ["wiki", "generate", "--llm", "--dry-run"],
    ["wiki", "status"],
    ["wiki", "status", "--repo", "cairn"],
    ["wiki", "retry"],
    ["wiki", "enrich", "--all"],
    # Lifecycle/ops family (4c-2): read-only over the seeded store — the
    # metrics legs render populated tables/kv sections via _seed_telemetry;
    # the mutating arms run dry/dead-pinned below.
    ["status"],
    ["version"],
    ["metrics"],
    ["metrics", "--json"],
    ["metrics", "--builds", "--json"],
    ["metrics", "--quality"],
    ["metrics", "--contention"],
    ["metrics", "--tasks"],
    ["sync"],
    ["download-reranker"],
    # Graph-analytics family (4c-3): read-only legs over the pinned store.
    # The mutating legs (dataflow build, embed) run last so the lookup legs
    # below keep the pinned rows and timestamps.
    ["grep", "--json", "DEFAULT_DB_PATH"],
    ["grep", "def resolve_store"],
    ["grep", "-i", "--json", "resolve_store"],
    ["grep", "--fixed", "--max-hits", "5", "self.session"],
    ["map"],
    ["map", "--json"],
    ["map", "--max-clusters", "3", "--max-hubs", "2"],
    ["rationale"],
    ["rationale", "--json"],
    ["rationale", "--symbol", "type_scoped"],
    ["rationale", "--file", "test_resolver", "--json"],
    ["semantic", "--json", "resolve the store path"],
    ["semantic", "--json", "--include-callers", "resolve the store path"],
    ["semantic", "zzzqqqxyzzy no such token"],
    ["viz"],
    ["viz", "--symbol", "resolve_store"],
    ["viz", "--embed", "--symbol", "resolve_store"],
    ["viz", "--scope", "deps"],
    ["viz", "--scope", "repo", "--repo", "corpus"],
    ["viz", "--scope", "module", "--module", "src/cairn/graph", "--format", "dot"],
    ["viz", "--scope", "impact", "--symbol", "resolve_store", "--depth", "2",
     "--format", "json"],
    ["blast"],
    ["blast", "--format", "markdown"],
    ["blast", "--format", "mermaid"],
    ["blast", "--format", "json"],
    ["blast", "--base", "origin/main"],
    ["dataflow", "lookup", "A"],
    ["dataflow", "lookup", "--json", "Ae"],
    ["dataflow", "lookup", "no_such_symbol"],
    ["dataflow", "build", "--max-symbols", "3"],
    ["embed", "--limit", "5"],
    # Knowledge-layer family (4c-4a): validate/verify read-only legs share
    # the seeded bundle; rebuild prints recomputed counts (idempotent), so
    # no reseed; embed/ingest re-seed via RESEED keyed below. The scratch-
    # pathed legs (ingest, tarball export) join in main().
    ["validate"],
    ["validate-paths"],
    ["verify", "compass/golden-capture"],
    ["verify", "compass/golden-pass"],
    ["verify", "no/such/concept"],
    ["knowledge", "rebuild"],
    ["knowledge", "embed"],
    ["skill", "generate", "src/cairn/graph"],
    ["skill", "generate", "rust/crates/cairn-build/src/config.rs"],
    # Workflow-sync + memory-lifecycle family (4c-4b): every leg mutates or
    # reads seeded state restored per-leg via RESEED (the memory tree, the
    # workflow concepts, and the memory_embeddings rows).
    ["knowledge", "workflow", "sync"],
    ["knowledge", "workflow", "sync", "x", "--all"],
    ["knowledge", "workflow", "sync", "--dry-run", "--all"],
    ["knowledge", "workflow", "sync", "--dry-run", "golden capture workflow"],
    ["knowledge", "workflow", "sync", "--dry-run", "no-such-workflow"],
    ["knowledge", "workflow", "sync", "--all"],
    ["knowledge", "workflow", "sync", "golden sync workflow"],
    ["knowledge", "workflow", "sync", "golden capture workflow"],
    ["knowledge", "workflow", "sync", "no-such-workflow"],
    ["memory", "reflect"],
    ["memory", "evolve", "memory/tribal/golden-evolve-me",
     "--body", "Revised body citing `resolve_store`."],
    ["memory", "evolve", "no/such/memory"],
    ["memory", "capture", "--session-transcript",
     '[{"role": "user", "content": "learned to run the golden capture"}]'],
    ["memory", "capture", "--session-transcript-stdin"],
    ["memory", "embed"],
    ["memory", "batch-critic"],
    ["memory", "consolidate"],
    # Compass family (4c-4c): generate/gaps/flow/flow-gaps. The 3+-word
    # legs re-seed the pinned compass tree via RESEED so coverage state
    # resets after the writing legs (flow legs also queue tasks under
    # _tasks/, which no later leg reads); the read-only 2-word "compass
    # gaps" relies on the preceding --use-llm leg leaving compass/ as
    # seeded. The mid-path module leg pins the `%/{module}/%` match arm.
    ["compass", "generate", "--dry-run", "src/cairn/graph"],
    ["compass", "generate", "--dry-run", "cairn/graph"],
    ["compass", "generate", "src/cairn/graph"],
    ["compass", "generate", "--use-llm", "--dry-run", "src/cairn/graph"],
    ["compass", "generate", "--use-llm", "src/cairn/graph"],
    ["compass", "gaps"],
    ["compass", "flow", "--dry-run", "build_pack"],
    ["compass", "flow", "build_pack"],
    ["compass", "flow", "--as-workflow", "--max-steps", "5", "build_pack"],
    ["compass", "flow", "main"],
    ["compass", "flow", "NoSuchSymbol"],
    ["compass", "flow", "--use-llm", "--dry-run", "build_pack"],
    ["compass", "flow", "--use-llm", "build_pack"],
    ["compass", "flow-gaps", "--min-edges", "15"],
    ["compass", "flow-gaps", "--generate", "--dry-run", "--min-edges", "15",
     "--limit", "2"],
    ["compass", "flow-gaps", "--generate", "--limit", "1", "--min-edges", "25"],
    # Cross-layer family (4c-5): ask/context are read-only over the seeded
    # bundle; the all-repos legs re-seed a one-store registry via RESEED.
    ["ask", "--json", "how does resolve_store work"],
    ["ask", "where is resolve_store defined"],
    ["ask", "why did we choose resolve_store"],
    ["ask", ""],
    ["ask", "--all-repos", "resolve_store"],
    ["ask", "--all-repos", "--json", "resolve_store"],
    ["context", "src/cairn/paths.py"],
    ["context", "rust/crates/cairn-build/src/builder.rs"],
    # Review family (4c-5): usage-error arms need no seed; the --base and
    # --pre-submit legs re-seed the feature-branch corpus via RESEED, and the
    # --capture-event legs restore the memory tree per leg.
    ["review"],
    ["review", "--gate"],
    ["review", "--base", "x", "--pre-submit"],
    ["review", "--base", "main"],
    ["review", "--base", "main", "--format", "markdown"],
    ["review", "--base", "origin/main"],
    ["review", "--pre-submit"],
    ["review", "--pre-submit", "--gate"],
]
# The stdin-fed capture leg reads its transcript from stdin (ARG_MAX-free path).
STDIN_OVERRIDES: dict[tuple[str, ...], str] = {
    ("memory", "capture", "--session-transcript-stdin"):
        '[{"role": "user", "content": "learned to run the golden capture"}]',
}
DIFF_PRINT_CHARS = 300
# Report renders runtime identity fields (python/platform/sqlite versions) that
# are environment values, not contract: normalize their values, keep the shape.
_REPORT_ENV_FIELD_RE = re.compile(
    r"(?m)^(\s*\"?(?:python|platform|sqlite|db_schema_user_version)\"?:).*$"
)
EXTRA_SCRUB_RULES = [
    ("report_env_fields", _REPORT_ENV_FIELD_RE, r"\1 <ENV>"),
    # Plain-text progress bars carry wall-clock seconds; the shape is contract.
    ("progress_elapsed", re.compile(r"%  \d+s"), "% <S>"),
    # The embed summary titles carry the run's elapsed seconds.
    ("embed_elapsed", re.compile(r"(Embedded [\d,]+ (?:symbols|docs) in )\d+\.\d+s"), r"\1<S>s"),
    # viz --embed stamps the generation date (UTC-day equality is the shape).
    ("viz_embed_date", re.compile(r"(Generated by `cairn viz` on )\d{4}-\d{2}-\d{2}"), r"\1<D>"),
    # Registered-store directories are keyed on the scratch workspace path.
    ("store_key", re.compile(r"(?<=/)[0-9a-f]{16}\b"), "<STORE_KEY>"),
    # The daemon check probes whatever is live on the machine's default port.
    ("report_daemon", re.compile(r"(?m)^(\s*PASS daemon: ).*$"), r"\1<DAEMON>"),
]
# `upgrade` legs pin a dead loopback proxy so the PyPI probe fails
# deterministically (offline and networked machines alike).
DEAD_PROXY = "http://127.0.0.1:9"
ENV_OVERRIDES: dict[tuple[str, ...], dict[str, str]] = {
    ("upgrade", "--check"): {
        "https_proxy": DEAD_PROXY, "HTTPS_PROXY": DEAD_PROXY,
    },
    # The embed leg pins the ANN backend off: with it on, the progress bar
    # force-draws the ANN description change AND stop() draws again after the
    # rebuild, and those indeterminate segments carry elapsed seconds with no
    # `%` token for the progress_elapsed scrub to normalize.
    ("embed", "--limit"): {
        "CAIRN_ANN_BACKEND": "off",
    },
}


def _note(msg: str) -> None:
    """Progress goes to stderr: stdout is the verdict channel."""
    print(f"[cli-golden] {msg}", file=sys.stderr, flush=True)


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def cli_env(scratch: Path, workspace: Path) -> dict:
    """Child env wiring the scratch store; PATH gets the cargo bin dir
    prepended because the caller's PATH may predate rustup."""
    env = dict(os.environ)
    for key in ("CAIRN_READ_ONLY", "CAIRN_MCP_API_KEY", "PYTEST_CURRENT_TEST",
                "CAIRN_DB", "CAIRN_KNOWLEDGE", "CAIRN_WORKSPACE", "CAIRN_HOME"):
        env.pop(key, None)
    env.update(gr.ENV_PINS)
    # Pack costs are contracted on the heuristic tokenizer: pin the local-model
    # probe to a never-cached name so no cached tokenizer can take over.
    env["CAIRN_EMBED_LOCAL_MODEL"] = "cairn-golden-no-such-model"
    # Logging (ann-index degrade warnings) is noise this gate does not
    # contract on; the C-4 surface is stdout + exit codes.
    env["CAIRN_LOG_LEVEL"] = "ERROR"
    # Self-recorded `cli:cairn <cmd>` tool_metrics rows carry per-leg timings.
    env["CAIRN_TELEMETRY"] = "off"
    # memory capture must take the deterministic queue path, not whatever
    # agent CLI the caller's machine happens to have on PATH.
    env["CAIRN_LLM_BACKEND"] = ""
    # Agent-config probes (report, status) read $HOME; a scratch home makes
    # every machine look like a bare runner.
    userhome = scratch / "userhome"
    userhome.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(userhome)
    env["CAIRN_HOME"] = str(scratch / "home")
    env["CAIRN_DB"] = str(scratch / DB_NAME)
    env["CAIRN_KNOWLEDGE"] = str(scratch / ".knowledge")
    env["CAIRN_WORKSPACE"] = str(workspace)
    env["NO_COLOR"] = "1"
    env["PATH"] = os.pathsep.join([str(parity.CARGO_BIN),
                                   env.get("PATH", os.defpath)])
    return env


def _write_seed(scratch: Path, concept_id: str) -> None:
    """Materialize one frozen concept into the scratch bundle."""
    src = SEEDS_DIR / f"{concept_id}.md"
    if not src.is_file():
        raise SystemExit(f"missing frozen seed {src}; the fixtures are frozen — restore it from history")
    dst = scratch / ".knowledge" / f"{concept_id}.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def prepare_store(scratch: Path) -> None:
    """Scratch materialization of the pinned self store (db, seeded bundle, corpus)."""
    parity.load_pinned_store("self", scratch)
    for task_id in ("golden-catalog", "golden-claim", "golden-complete",
                    "golden-revise", "golden-drop", "golden-done"):
        _seed_task(scratch, task_id)
    _seed_wiki(scratch)
    _seed_telemetry(scratch)
    _seed_knowledge_extras(scratch)
    _seed_memory_extras(scratch)
    _seed_workflow_sync(scratch)


def _seed_telemetry(scratch: Path) -> None:
    """Fixed tool_metrics + events rows so the metrics legs render their
    populated tables and kv sections, not just their empty arms."""
    import json
    import sqlite3

    conn = sqlite3.connect(str(scratch / DB_NAME))
    try:
        for tool, invoked_at, duration, status in [
            ("golden_probe", 1.0, 12.5, "ok"),
            ("golden_probe", 2.0, 30.25, "error"),
        ]:
            conn.execute(
                "INSERT INTO tool_metrics (tool_name, invoked_at, duration_ms, status) "
                "VALUES (?, ?, ?, ?)",
                (tool, invoked_at, duration, status),
            )
        events = [
            (100.0, "lock_contention", {"site": "build_lock"}),
            (101.0, "lock_contention", {"site": "build_lock"}),
            (102.0, "lock_contention", {"site": "query_lock"}),
            (103.0, "semantic_backend", {"backend": "hash"}),
            (104.0, "semantic_backend", {"backend": "hash"}),
            (105.0, "semantic_backend", {"backend": "hash"}),
            (106.0, "empty_result", {"query_kind": "semantic_search"}),
            (107.0, "truncate_result", {"tool": "pack"}),
            (108.0, "task_lifecycle", {"event": "queued", "task_kind": "wiki-page"}),
            (109.0, "task_lifecycle", {"event": "queued", "task_kind": "wiki-page"}),
            (110.0, "task_lifecycle", {"event": "completed", "task_kind": "wiki-page"}),
        ]
        for ts, name, attrs in events:
            conn.execute(
                "INSERT INTO events (ts, name, session_id, attrs) VALUES (?, ?, ?, ?)",
                (ts, name, "golden", json.dumps(attrs)),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_wiki(scratch: Path) -> None:
    """Reset leg-created wiki state, then seed one promoted, one failed, one
    dropped wiki page (manifest + tasks + promoted content) plus a
    Notes-bearing report the deterministic generator must splice."""
    knowledge = scratch / ".knowledge"
    shutil.rmtree(knowledge / "reports", ignore_errors=True)
    tasks_dir = knowledge / "_tasks"
    if tasks_dir.is_dir():
        for entry in tasks_dir.iterdir():
            if not entry.name.startswith("golden"):
                entry.unlink(missing_ok=True)
    for task_id in ("golden-page", "golden-failed", "golden-wdrop"):
        _seed_task(scratch, task_id)
    _write_seed(scratch, "wiki/pages/cairn/overview")
    _write_seed(scratch, "reports/architecture/corpus")

    def row(page_id: str, task_id: str) -> dict:
        return {
            "page_id": page_id,
            "title": page_id,
            "description": f"Page {page_id}.",
            "module": "src/cairn/llm",
            "source": "code",
            "seeds": {"files": [], "symbols": [], "docs": []},
            "input_hash": f"seeded-{page_id}",
            "task_id": task_id,
            "queue_attempts": 1,
        }

    manifest = {
        "schema": "cairn-wiki-manifest-3",
        "pages": {
            "cairn/overview": row("overview", "golden-page"),
            "cairn/failed-page": row("failed-page", "golden-failed"),
            "cairn/dropped-page": row("dropped-page", "golden-wdrop"),
        },
    }
    manifest_dir = knowledge / "_wiki"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _seed_knowledge_extras(scratch: Path) -> None:
    """Seed the 4c-4a legs' fixtures: a critic-passing compass concept, a
    fed-docs directory (spec + numbered ADR pair + draft skip), and reset
    knowledge_embeddings so each embed leg starts from the same stale set."""
    import sqlite3

    _write_seed(scratch, "compass/golden-pass")
    fed = scratch / "fed"
    shutil.rmtree(fed, ignore_errors=True)
    fed.mkdir(parents=True, exist_ok=True)
    (fed / "001-auth-design.md").write_text(
        "# Auth token design\n\nRotating token scheme for the API.\n\n"
        "**Status:** accepted\n", encoding="utf-8")
    (fed / "002-auth-design.md").write_text(
        "# Auth token rotation\n\nSupersedes 001 with a shorter lifetime.\n\n"
        "**Status:** accepted\n", encoding="utf-8")
    (fed / "003-note.md").write_text(
        "# Draft note\n\nUnreviewed scratch thinking.\n\n"
        "**Status:** draft\n", encoding="utf-8")
    conn = sqlite3.connect(str(scratch / DB_NAME))
    try:
        conn.execute("DELETE FROM knowledge_embeddings")
        conn.commit()
    finally:
        conn.close()


def _restore_pinned(scratch: Path) -> None:
    """Drop every store artifact, then re-materialize the seeded scratch."""
    shutil.rmtree(scratch / ".knowledge", ignore_errors=True)
    shutil.rmtree(scratch / "corpus", ignore_errors=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(scratch / DB_NAME) + suffix).unlink(missing_ok=True)
    prepare_store(scratch)


def _seed_broken_concept(scratch: Path) -> None:
    """A frontmatter-less concept so the validate leg renders its error arm."""
    _write_seed(scratch, "reports/broken-no-type")


def _seed_memory_extras(scratch: Path) -> None:
    """Seed the 4c-4b memory legs' fixtures: a same-title raw pair (consolidate),
    a tribal memory to evolve, a superseded pair sharing a live symbol ref
    (reflect's contested verdict), and a ref-baseline drop (tentative)."""
    for concept_id in (
        "memory/raw/golden-raw-pair-one",
        "memory/raw/golden-raw-pair-two",
        "memory/tribal/golden-evolve-me",
        "memory/tribal/golden-reflect-old",
        "memory/tribal/golden-reflect-new",
        "memory/tribal/golden-reflect-tentative",
    ):
        _write_seed(scratch, concept_id)


def _seed_memory_reset(scratch: Path) -> None:
    """Restore the pinned memory tree plus the 4c-4b seeds, and reset
    memory_embeddings to exactly one orphan row (the embed leg's reap arm)."""
    import sqlite3

    mem = scratch / ".knowledge" / "memory"
    shutil.rmtree(mem, ignore_errors=True)
    shutil.copytree(parity.PINNED_STORES / "self" / "bundle" / "memory", mem)
    _seed_memory_extras(scratch)
    conn = sqlite3.connect(str(scratch / DB_NAME))
    try:
        conn.execute("DELETE FROM memory_embeddings")
        conn.execute(
            "INSERT INTO memory_embeddings "
            "(doc_id, chunk_index, model, dim, vec, chunk, embedded_at) "
            "VALUES ('memory/tribal/orphaned-golden', 0, 'hash-256-v1', 1, "
            "X'3F800000', 'orphan', '2026-01-01T00:00:00Z')"
        )
        conn.commit()
    finally:
        conn.close()


def _seed_workflow_sync(scratch: Path) -> None:
    """Restore the pinned workflow and (re)write the seeded sync target: a
    workflow whose resource re-traces but whose steps anchor at gone refs."""
    pinned = parity.PINNED_STORES / "self" / "bundle" / "knowledge" / "workflow"
    shutil.copy2(pinned / "golden-capture-workflow.md",
                 scratch / ".knowledge" / "knowledge" / "workflow"
                 / "golden-capture-workflow.md")
    _write_seed(scratch, "knowledge/workflow/golden-sync-workflow")


def prepare_hooks_workspace(scratch: Path) -> Path:
    """Scratch workspace for the hooks legs: repo alpha (clean), repo beta
    (foreign post-commit), and an empty dir for the no-repos guidance path."""
    ws = scratch / "hooks-ws"
    for repo in ("alpha", "beta"):
        (ws / repo / ".git" / "hooks").mkdir(parents=True, exist_ok=True)
    (ws / "beta" / ".git" / "hooks" / "post-commit").write_text(
        "#!/bin/sh\necho user\n", encoding="utf-8")
    (ws / "empty").mkdir(parents=True, exist_ok=True)
    return ws


_HOOK_SCRIPTS = {
    "post-commit": """#!/bin/bash
# Auto-generated by cairn (cairn hooks install)
# Runs an incremental graph update over the committed range after every commit.
# cairn resolves the central store for this workspace from cwd (ancestor walk);
# when the store home is not the default, the export line above pins it for
# this script.
prev=$(git rev-parse -q --verify 'HEAD@{{1}}')
if [ -n "$prev" ]; then
  range="${{prev}}..HEAD"
else
  range="$(git hash-object -t tree /dev/null)..HEAD"
fi
cairn update --repo "{repo}" --diff-ref "$range" > /dev/null 2>&1 &
cairn validate-paths --mark > /dev/null 2>&1 &
""",
    "post-checkout": """#!/bin/bash
# Auto-generated by cairn (cairn hooks install)
# Runs an incremental graph update over the checked-out range on branch
# switches. cairn resolves the central store for this workspace from cwd
# (ancestor walk); when the store home is not the default, the export line
# above pins it for this script.
if [ "$3" = "1" ]; then
  cairn update --repo "{repo}" --diff-ref "$1..$2" > /dev/null 2>&1 &
fi
""",
}


def _reset_hooks_workspace(scratch: Path, installed: bool) -> None:
    """Restore the hooks workspace to its pre-install or post-install state."""
    ws = scratch / "hooks-ws"
    for repo in ("alpha", "beta"):
        for name in ("post-commit", "post-checkout"):
            (ws / repo / ".git" / "hooks" / name).unlink(missing_ok=True)
    (ws / "beta" / ".git" / "hooks" / "post-commit").write_text(
        "#!/bin/sh\necho user\n", encoding="utf-8")
    if not installed:
        return
    # The managed-hook shape the uninstall leg keys on; a foreign post-commit
    # is left untouched, so beta only gets post-checkout.
    for repo in ("alpha", "beta"):
        for name, template in _HOOK_SCRIPTS.items():
            path = ws / repo / ".git" / "hooks" / name
            if path.exists():
                continue
            path.write_text(template.format(repo=repo), encoding="utf-8")
            path.chmod(0o755)


def _seed_compass_reset(scratch: Path) -> None:
    """Restore the pinned compass tree so each compass leg starts from the
    same coverage state."""
    compass = scratch / ".knowledge" / "compass"
    shutil.rmtree(compass, ignore_errors=True)
    shutil.copytree(parity.PINNED_STORES / "self" / "bundle" / "compass", compass)


def _seed_task(scratch: Path, task_id: str) -> None:
    """(Re)write a frozen fixed-id Task concept, its result when one is frozen,
    and drop its claim marker so each golden leg starts from the same queue state."""
    _write_seed(scratch, f"_tasks/{task_id}")
    marker = scratch / ".knowledge" / "_tasks" / f"{task_id}.claim"
    marker.unlink(missing_ok=True)
    if (SEEDS_DIR / "_tasks" / f"{task_id}.result.md").is_file():
        _write_seed(scratch, f"_tasks/{task_id}.result")


def _seed_sync_drift(scratch: Path) -> None:
    """Re-materialize the pinned store with one drifted corpus file."""
    import sqlite3

    workspace = scratch / "corpus"
    shutil.rmtree(workspace, ignore_errors=True)
    shutil.rmtree(scratch / ".knowledge", ignore_errors=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(scratch / DB_NAME) + suffix).unlink(missing_ok=True)
    parity.load_pinned_store("self", scratch)
    (workspace / ".git").write_text("gitdir: pinned-parity-corpus\n", encoding="utf-8")
    conn = sqlite3.connect(str(scratch / DB_NAME))
    try:
        rel = conn.execute(
            "SELECT path FROM files WHERE path LIKE '%.py' ORDER BY path LIMIT 1"
        ).fetchone()[0]
    finally:
        conn.close()
    target = workspace / rel
    target.write_text(target.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")


def _seed_blast_drift(scratch: Path) -> None:
    """Re-materialize the pinned store+corpus (knowledge untouched) as a real
    git repo carrying one deletion and one rename, so the blast legs exercise
    seed selection, radius, deleted_files and unindexed_files instead of the
    no-git empty arm."""
    import subprocess

    workspace = scratch / "corpus"
    shutil.rmtree(workspace, ignore_errors=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(scratch / DB_NAME) + suffix).unlink(missing_ok=True)
    pinned = parity.PINNED_STORES / "self"
    shutil.copy2(pinned / DB_NAME, scratch / DB_NAME)
    shutil.copytree(pinned / "corpus", workspace)

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args],
            cwd=str(workspace), check=True, capture_output=True, text=True,
        )

    git("init", "-q")
    git("config", "user.email", "golden@example.com")
    git("config", "user.name", "golden")
    git("add", "-A")
    git("commit", "-qm", "base")
    (workspace / "src/cairn/agent_install/clients/omp.py").unlink()
    git("mv", ".github/scripts/bench_compare.py",
        ".github/scripts/bench_compare_renamed.py")


def _seed_review_drift(scratch: Path) -> None:
    """Review legs' store+corpus: a real git repo whose HEAD is one commit
    ahead of main carrying a deletion, a rename, and an edit, so the pack and
    pre-submit legs diff against a non-empty merge base."""
    import subprocess

    workspace = scratch / "corpus"
    shutil.rmtree(workspace, ignore_errors=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(scratch / DB_NAME) + suffix).unlink(missing_ok=True)
    pinned = parity.PINNED_STORES / "self"
    shutil.copy2(pinned / DB_NAME, scratch / DB_NAME)
    shutil.copytree(pinned / "corpus", workspace)

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args],
            cwd=str(workspace), check=True, capture_output=True, text=True,
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "golden@example.com")
    git("config", "user.name", "golden")
    git("add", "-A")
    git("commit", "-qm", "base")
    (workspace / "src/cairn/agent_install/clients/omp.py").unlink()
    git("mv", ".github/scripts/bench_compare.py",
        ".github/scripts/bench_compare_renamed.py")
    target = workspace / "src/cairn/paths.py"
    target.write_text(
        target.read_text(encoding="utf-8") + "\n# golden edit\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "feature")


def _seed_registry(scratch: Path) -> None:
    """Register the corpus workspace in the scratch home with a copy of the
    current store + bundle so the all-repos legs iterate one reachable store."""
    import hashlib
    import json

    # Registration stores the resolved path (cairn init parity): iter_stores
    # re-resolves the key when deriving the store dir.
    ws = (scratch / "corpus").resolve()
    key = hashlib.sha256(str(ws).encode()).hexdigest()[:16]
    home = scratch / "home" / key
    home.mkdir(parents=True, exist_ok=True)
    shutil.copy2(scratch / DB_NAME, home / ".kg")
    if not (home / ".knowledge").is_dir():
        shutil.copytree(scratch / ".knowledge", home / ".knowledge")
    (scratch / "home" / "workspaces.json").write_text(
        json.dumps({str(ws): key}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def _prepare_review_events(scratch: Path) -> Path:
    """Event payloads for the review capture legs: a resolved pattern-marker
    comment, an unresolved comment, and a payload failing field validation."""
    import json

    events = scratch / "events"
    events.mkdir(parents=True, exist_ok=True)
    (events / "resolved.json").write_text(json.dumps({
        "resolved": True, "file": "src/cairn/paths.py", "line": 10,
        "body": "Use resolve_store for the store paths cairn:pattern",
        "thread_url": "https://github.com/example/cairn/pull/42",
    }), encoding="utf-8")
    (events / "open.json").write_text(json.dumps({
        "resolved": False, "file": "src/cairn/paths.py", "line": None,
        "body": "Still open", "thread_url": "https://github.com/example/cairn/pull/43",
    }), encoding="utf-8")
    (events / "bad-payload.json").write_text(json.dumps({
        "resolved": "yes", "file": "src/cairn/paths.py", "line": None,
        "body": "bogus", "thread_url": "",
    }), encoding="utf-8")
    return events


# Mutating task cases: the seeded task is restored before each leg.
RESEED = {
    ("task", "claim", "golden-claim"):
        lambda s: _seed_task(s, "golden-claim"),
    ("task", "complete", "golden-complete"):
        lambda s: _seed_task(s, "golden-complete"),
    ("task", "complete", "golden-revise"):
        lambda s: _seed_task(s, "golden-revise"),
    ("task", "drop", "golden-drop"):
        lambda s: _seed_task(s, "golden-drop"),
    ("wiki", "generate"): _seed_wiki,
    ("wiki", "generate", "--llm"): _seed_wiki,
    ("wiki", "retry"): _seed_wiki,
    ("wiki", "enrich", "--all"): _seed_wiki,
    ("hooks", "install", "--workspace"):
        lambda s: _reset_hooks_workspace(s, installed=False),
    ("hooks", "uninstall", "--workspace"):
        lambda s: _reset_hooks_workspace(s, installed=True),
    # Mutating sync case: a fresh store + git marker + one drifted indexed
    # file before each leg, so both legs reindex exactly that file.
    ("sync", "--workspace"):
        _seed_sync_drift,
    # Mutating blast cases: a fresh store over a real git repo with one
    # deletion + one rename before each leg (seeds, radius, deleted_files,
    # unindexed_files).
    ("blast",):
        _seed_blast_drift,
    ("blast", "--format"):
        _seed_blast_drift,
    ("blast", "--base"):
        _seed_blast_drift,
    # Mutating knowledge-layer cases: the embed leg starts from the same
    # stale doc set; rebuild's doc-link queueing and the approved ingest's
    # writes persist across legs, so both restore the seeded scratch first.
    # The validate leg seeds a frontmatter-less concept for its error arm
    # (persisting through the read-only legs until rebuild restores).
    ("validate",):
        _seed_broken_concept,
    ("knowledge", "embed"):
        lambda s: _seed_knowledge_extras(s),
    ("knowledge", "rebuild"):
        _restore_pinned,
    ("knowledge", "ingest"):
        _restore_pinned,
    # Workflow-sync + memory-lifecycle cases (4c-4b): restore the workflow
    # pair and the memory tree before each leg.
    ("knowledge", "workflow", "sync"):
        _seed_workflow_sync,
    ("memory", "reflect"):
        _seed_memory_reset,
    ("memory", "evolve"):
        _seed_memory_reset,
    ("memory", "capture"):
        _seed_memory_reset,
    ("memory", "embed"):
        _seed_memory_reset,
    ("memory", "batch-critic"):
        _seed_memory_reset,
    ("memory", "consolidate"):
        _seed_memory_reset,
    # Compass cases (4c-4c): coverage state resets before every leg.
    ("compass", "generate"):
        _seed_compass_reset,
    ("compass", "flow"):
        _seed_compass_reset,
    ("compass", "flow-gaps"):
        _seed_compass_reset,
    # Cross-layer cases (4c-5): the all-repos legs iterate a one-store
    # registry; the review legs re-seed the feature-branch corpus, and the
    # capture legs restore the memory tree per leg.
    ("ask", "--all-repos"):
        _seed_registry,
    ("review", "--base"):
        _seed_review_drift,
    ("review", "--pre-submit"):
        _seed_review_drift,
    ("review", "--capture-event"):
        _seed_memory_reset,
}


def ensure_rust_binary() -> Path:
    """Build (cargo, incremental) the release cairn binary."""
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(parity.CARGO_BIN), env.get("PATH", os.defpath)])
    subprocess.run(
        ["cargo", "build", "--release", "-p", "cairn-cli"],
        cwd=str(parity.RUST_WORKSPACE), check=True, env=env,
    )
    return RUST_BIN


def run_side(exe: list[str], argv: list[str], env: dict,
             cwd: Path = REPO_ROOT, stdin: str | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(
        [*exe, *argv],
        capture_output=True, text=True, env=env, timeout=CMD_TIMEOUT_S,
        cwd=str(cwd), input=stdin,
    )
    return proc.returncode, proc.stdout, proc.stderr


def normalize(text: str, rules: list) -> str:
    """Colors stripped, then the golden path/time scrub rules applied."""
    return gr.normalize_text(ANSI_RE.sub("", text), rules)


def case_label(argv: list[str], rules: list) -> str:
    """Scratch-independent identity of one case: its argv with paths scrubbed."""
    return " ".join(normalize(arg, rules) for arg in argv)


def first_diff(expected: dict, actual: tuple, rules: list) -> str | None:
    """First divergence between a frozen case and a live (exit, stdout, stderr) run."""
    if expected["exit"] != actual[0]:
        return f"exit {expected['exit']} != {actual[0]}"
    for stream, raw in zip(("stdout", "stderr"), actual[1:]):
        want, got = expected[stream], normalize(raw, rules)
        if want != got:
            wlines, glines = want.splitlines(), got.splitlines()
            for i, (a, b) in enumerate(zip(wlines, glines)):
                if a != b:
                    return f"{stream} line {i}: {a!r} != {b!r}"
            side = "fixture" if len(wlines) > len(glines) else "rust"
            extra = wlines[len(glines):] if side == "fixture" else glines[len(wlines):]
            return f"{stream}: {side} has {abs(len(wlines) - len(glines))} extra line(s): {extra[0]!r}"
    return None


def _shrink(diff: str) -> str:
    return diff if len(diff) <= DIFF_PRINT_CHARS else diff[:DIFF_PRINT_CHARS] + "\u2026"


def golden_commands(scratch: Path) -> list[list[str]]:
    """The full ordered case list: the fixed COMMANDS plus the scratch-pathed legs."""
    export_dir = scratch / "export"
    events = _prepare_review_events(scratch)
    hooks_ws = prepare_hooks_workspace(scratch)
    empty_knowledge = scratch / "empty-knowledge"
    empty_knowledge.mkdir(parents=True, exist_ok=True)
    return COMMANDS + [
        ["hooks", "install", "--workspace", str(hooks_ws)],
        ["hooks", "uninstall", "--workspace", str(hooks_ws)],
        ["hooks", "install", "--workspace", str(hooks_ws / "empty")],
        ["wiki", "export", "--dir", str(export_dir), "--force"],
        # Empty knowledge dir: every memory tier is zero, so the tier
        # averages must render the zero guard, not a NaN.
        ["status", "--knowledge", str(empty_knowledge)],
        # Report runs from the scratch dir so the repo-dev quality gates
        # render their no-source-root arm instead of the live checkout.
        ["report", "--db", str(scratch / "missing.kg")],
        ["uninstall", "--dry-run", "--graph-only"],
        ["upgrade", "--check"],
        ["sync", "--workspace", str(scratch / "corpus")],
        # Knowledge-layer scratch-pathed legs (4c-4a): the fed directory
        # and tarball target live under scratch. The ingest legs run last
        # because their reseed restores the pinned store (dropping every
        # seed above); the tarball export follows them.
        ["knowledge", "ingest", "--dir", str(scratch / "fed")],
        ["knowledge", "ingest", "--dir", str(scratch / "fed"), "--ingest"],
        ["knowledge", "export", "--out", str(scratch / "export" / "bundle.tar.gz")],
        # Review capture legs (4c-5): resolved-pattern, unresolved, and a
        # malformed payload; each mutates (or reads) the seeded memory tree.
        ["review", "--capture-event", str(events / "resolved.json")],
        ["review", "--capture-event", str(events / "open.json")],
        ["review", "--capture-event", str(events / "bad-payload.json")],
        # Communities family: seeded Louvain over the scratch store's structural
        # edges; runs last because it mutates the communities tables the map
        # legs above read.
        ["communities", "--top-k", "2"],
    ]


# Per-command cwd overrides, keyed by the leading argv word.
CWD_OVERRIDES = {"report": "scratch"}


def run_case(cmd: list[str], exe: list[str], env: dict, scratch: Path) -> tuple[int, str, str]:
    """Seed, then run one case through `exe` with its env/stdin/cwd overrides."""
    # Keys may stop before a positional path: try the 3-word key the
    # task/wiki cases use, then the 2-word key (`sync --workspace`).
    hook = RESEED.get(tuple(cmd[:3])) or (
        RESEED.get(tuple(cmd[:2])) if len(cmd) > 2 else None
    )
    if hook is not None:
        hook(scratch)
    overrides = ENV_OVERRIDES.get(tuple(cmd[:2]), {})
    stdin_data = STDIN_OVERRIDES.get(tuple(cmd[:3]))
    cwd = scratch if CWD_OVERRIDES.get(cmd[0]) == "scratch" else REPO_ROOT
    return run_side(exe, cmd, {**env, **overrides}, cwd, stdin=stdin_data)


def load_cases() -> list[dict]:
    if not CASES_FILE.is_file():
        raise SystemExit(f"missing frozen cases {CASES_FILE}; the fixtures are frozen — restore them from history")
    data = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise SystemExit(f"{CASES_FILE}: schema {data.get('schema')!r} != {SCHEMA!r}")
    return data["cases"]


def _reference_label(exe: list[str]) -> str:
    """The recorded executable, repo-relative when it lives in the checkout."""
    parts = []
    for part in exe:
        try:
            parts.append(str(Path(part).resolve().relative_to(REPO_ROOT)))
        except ValueError:
            parts.append(part)
    return " ".join(parts)


def record(commands: list[list[str]], exe: list[str], env: dict, scratch: Path,
           rules: list, only: str | None = None) -> None:
    """Capture every case from `exe` into the frozen cases file; `only` re-freezes
    just the cases whose label starts with it and keeps the rest."""
    frozen = {case["label"]: case for case in load_cases()} if only else {}
    cases = []
    refreshed = []
    for cmd in commands:
        label = case_label(cmd, rules)
        if only and not label.startswith(only):
            cases.append(frozen[label])
            continue
        code, out, err = run_case(cmd, exe, env, scratch)
        cases.append({
            "label": label,
            "exit": code,
            "stdout": normalize(out, rules),
            "stderr": normalize(err, rules),
        })
        refreshed.append(label)
        print(f"RECORDED {label} (exit {code})")
    capture = {
        "reference": _reference_label(exe),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "env_pins": gr.ENV_PINS,
    }
    if only:
        previous = json.loads(CASES_FILE.read_text(encoding="utf-8"))["capture"]
        capture = {**previous, "refreshed": {**previous.get("refreshed", {}), **{
            label: {"reference": _reference_label(exe), "captured_at": capture["captured_at"]}
            for label in refreshed
        }}}
    payload = {
        "schema": SCHEMA,
        "capture": capture,
        "normalization": [name for name, _p, _r in rules],
        "cases": cases,
    }
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    CASES_FILE.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n",
                          encoding="utf-8")
    _note(f"recorded {len(cases)} cases into {CASES_FILE}")


def replay(commands: list[list[str]], exe: list[str], env: dict, scratch: Path,
           rules: list) -> int:
    """Run every case through the rust binary and diff it against the frozen fixture."""
    cases = load_cases()
    labels = [case_label(cmd, rules) for cmd in commands]
    frozen = [case["label"] for case in cases]
    if labels != frozen:
        for i, (live, want) in enumerate(zip(labels, frozen)):
            if live != want:
                raise SystemExit(f"case {i}: live {live!r} != frozen {want!r}; re-record the fixtures")
        raise SystemExit(f"{len(labels)} live cases vs {len(frozen)} frozen; re-record the fixtures")
    failures = []
    for cmd, case in zip(commands, cases):
        result = run_case(cmd, exe, env, scratch)
        diff = first_diff(case, result, rules)
        if diff:
            failures.append(case["label"])
            print(f"FAIL {case['label']}: {_shrink(diff)}")
        else:
            print(f"PASS {case['label']}")
    total = len(commands)
    print(f"{total - len(failures)}/{total} commands golden-equal")
    return 0 if not failures else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rust-bin", default=None,
                        help=f"Rust binary override (default: build {RUST_BIN.relative_to(REPO_ROOT)}).")
    parser.add_argument("--record", action="store_true",
                        help="Re-freeze the fixtures from the rust binary "
                             "instead of replaying against them.")
    parser.add_argument("--only", default=None, metavar="LABEL",
                        help="With --record: re-freeze only the cases whose label starts "
                             "with LABEL (an intentional divergence), keeping the rest.")
    parser.add_argument("--keep-scratch", action="store_true",
                        help="Keep the scratch store for inspection.")
    args = parser.parse_args(argv)

    scratch = Path(tempfile.mkdtemp(prefix="cairn-rust-cli-golden-"))
    _note(f"scratch {scratch}")
    workspace = scratch / "corpus"
    try:
        prepare_store(scratch)
        rules = gr.scrub_rules(scratch, scratch / "home", workspace) + EXTRA_SCRUB_RULES
        commands = golden_commands(scratch)
        exe = [str(Path(args.rust_bin).resolve() if args.rust_bin else ensure_rust_binary())]
        env = cli_env(scratch, workspace)
        _note(f"binary: {' '.join(exe)}")
        if args.record:
            record(commands, exe, env, scratch, rules, only=args.only)
            return 0
        return replay(commands, exe, env, scratch, rules)
    finally:
        if args.keep_scratch:
            _note(f"scratch kept: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
