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
]
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
    # The embed summary title carries the run's elapsed seconds.
    ("embed_elapsed", re.compile(r"(Embedded [\d,]+ symbols in )\d+\.\d+s"), r"\1<S>s"),
    # viz --embed stamps the generation date (UTC-day equality is the shape).
    ("viz_embed_date", re.compile(r"(Generated by `cairn viz` on )\d{4}-\d{2}-\d{2}"), r"\1<D>"),
]
# `upgrade` legs pin a dead loopback proxy so the PyPI probe fails
# deterministically on both sides (offline and networked machines alike).
DEAD_PROXY = "http://127.0.0.1:9"
ENV_OVERRIDES: dict[tuple[str, ...], dict[str, str]] = {
    ("upgrade", "--check"): {
        "https_proxy": DEAD_PROXY, "HTTPS_PROXY": DEAD_PROXY,
    },
    # The embed leg pins the ANN backend off: with it on, python's bar
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
    # The python CLI self-records `cli:cairn <cmd>` tool_metrics rows at exit;
    # with per-leg timings they are nondeterministic, so gate the whole family.
    env["CAIRN_TELEMETRY"] = "off"
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
    _seed_task(scratch, "golden-catalog", "wiki-catalog", "cairn",
               {"repo": "cairn"})
    _seed_task(scratch, "golden-claim", "wiki-page", "cairn/overview",
               {"repo": "cairn"})
    _seed_task(scratch, "golden-complete", "wiki-catalog", "cairn",
               {"repo": "cairn"}, status="in-progress")
    _seed_task(scratch, "golden-revise", "compass-synthesize",
               "src/cairn/llm", {"key_files": ["src/graph/queries.py"]},
               status="in-progress")
    _seed_task(scratch, "golden-drop", "doc-link", "cairn",
               {"members": ["knowledge/a", "knowledge/b"]})
    _seed_task(scratch, "golden-done", "compass-synthesize",
               "src/cairn/llm", {"key_files": ["src/graph/queries.py"]},
               status="done", result="Compass body for src/cairn/llm.\n")
    _seed_wiki(scratch)
    _seed_telemetry(scratch)


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
    import json

    from cairn.okf.bundle import OKFBundle
    from cairn.okf.concept import OKFConcept

    knowledge = scratch / ".knowledge"
    shutil.rmtree(knowledge / "reports", ignore_errors=True)
    tasks_dir = knowledge / "_tasks"
    if tasks_dir.is_dir():
        for entry in tasks_dir.iterdir():
            if not entry.name.startswith("golden"):
                entry.unlink(missing_ok=True)
    _seed_task(scratch, "golden-page", "wiki-page", "cairn/overview",
               {"repo": "cairn"}, status="done", result="Overview body.\n",
               critic="passed")
    _seed_task(scratch, "golden-failed", "wiki-page", "cairn/failed-page",
               {"repo": "cairn"}, status="done", result="Rejected body.\n")
    _seed_task(scratch, "golden-wdrop", "wiki-page", "cairn/dropped-page",
               {"repo": "cairn"}, status="dropped")
    bundle = OKFBundle(str(knowledge))
    bundle.write_concept(OKFConcept(
        type="Wiki-Article", title="Wiki: overview",
        description="Wiki article for cairn/overview", resource="overview",
        tags=["cairn", "wiki"],
        concept_id="wiki/pages/cairn/overview", body="Promoted overview.\n",
    ))
    bundle.write_concept(OKFConcept(
        type="Architecture-Report", title="corpus Architecture",
        description="Graph-derived architectural overview of corpus",
        resource="corpus", tags=["corpus", "architecture"],
        concept_id="reports/architecture/corpus",
        body="Stale body.\n\n## Notes\nkept note\n",
    ))

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


def _reset_hooks_workspace(scratch: Path, installed: bool) -> None:
    """Restore the hooks workspace to its pre-install or post-install state."""
    from cairn.hooks.git_hooks import install_hooks

    ws = scratch / "hooks-ws"
    for repo in ("alpha", "beta"):
        for name in ("post-commit", "post-checkout"):
            (ws / repo / ".git" / "hooks" / name).unlink(missing_ok=True)
    (ws / "beta" / ".git" / "hooks" / "post-commit").write_text(
        "#!/bin/sh\necho user\n", encoding="utf-8")
    if installed:
        install_hooks(["alpha", "beta"], str(ws))


def _seed_task(scratch: Path, task_id: str, kind: str, resource: str,
               facts: dict, status: str = "pending",
               result: str | None = None,
               critic: str | None = None) -> None:
    """(Re)write a fixed-id Task concept (and drop its claim marker) so each
    golden leg starts from the same queue state."""
    from cairn.llm.tasks import Task, _task_to_concept
    from cairn.okf.bundle import OKFBundle
    from cairn.okf.concept import OKFConcept

    bundle = OKFBundle(str(scratch / ".knowledge"))
    bundle.write_concept(_task_to_concept(Task(
        id=task_id, task_kind=kind, resource=resource, facts=facts,
        status=status, attempt=1, created_at="2026-01-01T00:00:00Z",
    )))
    marker = scratch / ".knowledge" / "_tasks" / f"{task_id}.claim"
    marker.unlink(missing_ok=True)
    if result is not None:
        extensions = {"critic_status": critic} if critic else {}
        bundle.write_concept(OKFConcept(
            type="Task-Result", title=f"Result for {task_id}",
            resource=resource, concept_id=f"_tasks/{task_id}.result",
            body=result, extensions=extensions,
        ))


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


# Mutating task cases: the seeded task is restored before each leg.
RESEED = {
    ("task", "claim", "golden-claim"):
        lambda s: _seed_task(s, "golden-claim", "wiki-page", "cairn/overview",
                             {"repo": "cairn"}),
    ("task", "complete", "golden-complete"):
        lambda s: _seed_task(s, "golden-complete", "wiki-catalog", "cairn",
                             {"repo": "cairn"}, status="in-progress"),
    ("task", "complete", "golden-revise"):
        lambda s: _seed_task(s, "golden-revise", "compass-synthesize",
                             "src/cairn/llm",
                             {"key_files": ["src/graph/queries.py"]},
                             status="in-progress"),
    ("task", "drop", "golden-drop"):
        lambda s: _seed_task(s, "golden-drop", "doc-link", "cairn",
                             {"members": ["knowledge/a", "knowledge/b"]}),
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
             cwd: Path = REPO_ROOT) -> tuple[int, str, str]:
    proc = subprocess.run(
        [*exe, *argv],
        capture_output=True, text=True, env=env, timeout=CMD_TIMEOUT_S,
        cwd=str(cwd),
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

        export_dir = scratch / "export"
        hooks_ws = prepare_hooks_workspace(scratch)
        empty_knowledge = scratch / "empty-knowledge"
        empty_knowledge.mkdir(parents=True, exist_ok=True)
        commands = COMMANDS + [
            ["hooks", "install", "--workspace", str(hooks_ws)],
            ["hooks", "uninstall", "--workspace", str(hooks_ws)],
            ["hooks", "install", "--workspace", str(hooks_ws / "empty")],
            ["wiki", "export", "--dir", str(export_dir), "--force"],
            # Empty knowledge dir: every memory tier is zero, so the tier
            # averages must render the python zero guard, not a NaN.
            ["status", "--knowledge", str(empty_knowledge)],
            # Report runs from the scratch dir so the repo-dev quality gates
            # render their no-source-root arm instead of the live checkout.
            ["report", "--db", str(scratch / "missing.kg")],
            ["uninstall", "--dry-run", "--graph-only"],
            ["upgrade", "--check"],
            ["sync", "--workspace", str(workspace)],
        ]
        # Per-command cwd overrides, keyed by the leading argv word.
        CWD_OVERRIDES = {"report": scratch}
        failures = []
        for cmd in commands:
            # Keys may stop before a positional path: try the 3-word key the
            # task/wiki cases use, then the 2-word key (`sync --workspace`).
            hook = RESEED.get(tuple(cmd[:3])) or (
                RESEED.get(tuple(cmd[:2])) if len(cmd) > 2 else None
            )
            if hook is not None:
                hook(scratch)
            overrides = ENV_OVERRIDES.get(tuple(cmd[:2]), {})
            cwd = CWD_OVERRIDES.get(cmd[0], REPO_ROOT)
            py = run_side(parity.cairn_cli(), cmd, {**py_env, **overrides}, cwd)
            if hook is not None:
                hook(scratch)
            rs = run_side([str(rust_bin)], cmd, {**rust_env, **overrides}, cwd)
            diff = first_diff(cmd, py, rs, rules + EXTRA_SCRUB_RULES)
            label = " ".join(cmd)
            if diff:
                failures.append(label)
                print(f"FAIL {label}: {_shrink(diff)}")
            else:
                print(f"PASS {label}")
        total = len(commands)
        print(f"{total - len(failures)}/{total} commands golden-equal")
        return 0 if not failures else 1
    finally:
        if args.keep_scratch:
            _note(f"scratch kept: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
