"""Core CLI: init, config, build, stats, checkpoint."""
from __future__ import annotations

import click
import json
import os
from pathlib import Path

from .main import DEFAULT_DB_PATH, builder, get_db, main, queries, scanner_mod
from ._helpers import _human_bytes

@main.command()
@click.option("--workspace", "ws_arg", default=None, help="Workspace root (default: cwd).")
@click.option("--from-legacy", "legacy_dir", default=None,
              help="Migrate .kg + .knowledge from this cairn dir (e.g. ./cairn).")
@click.option("--no-build", is_flag=True, help="Register without building the graph.")
@click.option("--import-docs", is_flag=True,
              help="Auto-discover and ingest docs/**/*.md as knowledge.")
def init(ws_arg, legacy_dir, no_build, import_docs):
    """Register this workspace with cairn's central store.

    Creates ~/.cairn/<key>/.kg and .knowledge/ for this workspace and
    records cwd -> key in the registry, so subsequent `cairn` commands (and the
    MCP server) find the right store from anywhere inside the workspace.

    If a legacy cairn/.kg exists under the workspace, it is migrated into
    the central store (moved, not copied) so you don't rebuild the graph.
    """
    import shutil
    import time

    from . import display
    from ..paths import register_workspace, resolve_workspace

    ws = Path(ws_arg).resolve() if ws_arg else resolve_workspace()
    store = register_workspace(ws)

    with display.rail("Initializing cairn") as r:
        r.step("Initialized in", str(ws))
        r.step("Store", str(store.home))
        r.detail(".kg", str(store.db))
        r.detail(".knowledge", str(store.knowledge))

        # Migrate legacy cairn/.kg + .knowledge if present.
        legacy = Path(legacy_dir).resolve() if legacy_dir else ws / "cairn"
        legacy_db = legacy / ".kg"
        legacy_kn = legacy / ".knowledge"
        migrated = []
        if legacy_db.exists() and not store.db.exists():
            size_kb = legacy_db.stat().st_size // 1024
            store.db.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy_db), str(store.db))
            migrated.append(f".kg ({size_kb} KB)")
        if legacy_kn.exists() and legacy_kn.is_dir():
            # Merge: copy legacy knowledge tree into the store. Skip .DS_Store
            # and files that already exist at the destination. Only count as a
            # migration if at least one real file was copied.
            copied = 0
            for item in legacy_kn.rglob("*"):
                if item.is_dir() or item.name == ".DS_Store":
                    continue
                rel = item.relative_to(legacy_kn)
                dst = store.knowledge / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists():
                    shutil.copy2(str(item), str(dst))
                    copied += 1
            if copied:
                migrated.append(f".knowledge/ ({copied} files)")
        if migrated:
            r.step(f"Migrated from {legacy}", ", ".join(migrated))

        if not no_build and not store.db.exists():
            # Scan completes before the first progress event fires, so open the
            # sub-step before calling build_graph and settle it on `scan`.
            r.start("Scanning files")
            phase_state = {"scan_done": False, "resolve_started": False}

            def on_progress(phase, **kw):
                if phase == "scan":
                    files = kw.get("files", 0)
                    r.tick(f"{files:,} found")
                    r.finish(f"{files:,} found")
                    r.start("Parsing code")
                    phase_state["scan_done"] = True
                elif phase == "parse_progress":
                    total = kw.get("total", 0) or 1
                    pct = 50 * kw.get("done", 0) // total
                    r.tick(f"{pct}%")
                elif phase == "insert_progress":
                    total = kw.get("total", 0) or 1
                    pct = 50 + 50 * kw.get("done", 0) // total
                    r.tick(f"{pct}%")
                elif phase == "resolve_start":
                    if not phase_state["resolve_started"]:
                        r.finish("done")
                        r.start("Resolving refs")
                        phase_state["resolve_started"] = True
                    r.tick(kw.get("repo", ""))
                elif phase == "persist":
                    r.finish("done")
                    r.start("Persisting graph")

            t0 = time.time()
            # verbose stays False: the rail replaces the per-file print() noise,
            # and a stray writer would corrupt the live region. `cairn build -v`
            # remains the way to get per-file detail.
            summary = builder.build_graph(
                workspace=str(ws), db_path=str(store.db), verbose=False,
                progress=on_progress,
            )
            elapsed = time.time() - t0
            # build_graph emits persist as its last event but never closes the
            # sub-step; settle it before the closing lines.
            r.finish("done")
            if not summary.get("files"):
                r.warn("No source files found")
            else:
                r.step(f"Indexed {summary['files']:,} files")
                r.stat(
                    f"{summary['symbols']:,} nodes, {summary['edges']:,} edges "
                    f"in {elapsed:.1f}s"
                )
        elif store.db.exists():
            r.warn("Graph already present. Use `cairn build` to rebuild.")

        # Auto-discover and ingest docs/**/*.md as knowledge.
        if import_docs:
            from cairn.knowledge.store import import_directory
            from cairn.okf.bundle import OKFBundle

            docs_dir = ws / "docs"
            if docs_dir.is_dir():
                store.ensure()
                bundle = OKFBundle(str(store.knowledge))
                imported = import_directory(bundle, str(docs_dir), doc_type="spec")
                r.step(f"Imported {len(imported)} doc(s) from docs/")
            else:
                r.warn("No docs/ directory found; skipping --import-docs")

    # Trailing hint prints after the rail closes so `└ Done` stays the last
    # rail line.
    display.dim("`cairn config` to verify; `cairn serve` for the MCP server.")


# --------------------------------------------------------------------------
# cairn config — show resolved paths and registry.
# --------------------------------------------------------------------------
@main.command()
@click.option("--list", "list_all", is_flag=True, help="List all registered workspaces.")
@click.option("--mcp-config", is_flag=True, help="Print a path-free .mcp.json snippet for agents.")
def config(list_all, mcp_config):
    """Show resolved store paths for this workspace (or all registered ones)."""

    from ..paths import REGISTRY_FILE, resolve_store, resolve_workspace

    if mcp_config:
        snippet = {
            "mcpServers": {
                "cairn": {"command": "cairn", "args": ["serve"]}
            }
        }
        click.echo(json.dumps(snippet, indent=2))
        return

    if list_all:
        from ..paths import _load_registry

        reg = _load_registry()
        if not reg:
            click.echo("No workspaces registered. Run `cairn init` in a workspace root.")
            click.echo(f"Registry would live at: {REGISTRY_FILE}")
            return
        click.echo(f"Registry: {REGISTRY_FILE}")
        for ws_path, key in sorted(reg.items()):
            mark = " <- cwd context" if ws_path == str(resolve_workspace()) else ""
            click.echo(f"  {key}  {ws_path}{mark}")
        return

    store = resolve_store()
    click.echo(f"workspace:  {store.workspace}")
    click.echo(f"store:      {store.home}")
    click.echo(f"  .kg:         {store.db}{' (exists)' if store.db.exists() else ' (missing)'}")
    click.echo(f"  .knowledge:  {store.knowledge}")
    click.echo(f"home:       {store.home.parent}  (override with CAIRN_HOME)")

    # Show the resolved embedding backend + model so the user can see, right
    # after install, whether they're getting real embeddings (bge-m3) or the
    # dep-free hash fallback. This is the earliest visibility point — it
    # surfaces the state before the user ever runs `cairn embed`.
    from ..graph.embeddings import _effective_backend, is_hash_fallback, current_model

    eff = _effective_backend()
    model = current_model()
    if is_hash_fallback():
        # Silent fallback: user expects bge-m3 but will get hash.
        click.echo(f"embed:      {model}  ⚠ fallback (sentence-transformers not installed)")
        click.echo("            for real embeddings: uv tool install 'cairn-intel[semantic]' --force")
    elif eff == "hash":
        click.echo(f"embed:      {model}  (CAIRN_EMBED_BACKEND=hash)")
    else:
        click.echo(f"embed:      {model}  (backend: {eff})")

    # Show the resolved cross-repo namespace map (env / cairn.json / default).
    from ..graph.cross_repo import _load_namespaces
    from ..graph.config import load_config

    cfg = load_config(store.workspace)
    source = "CAIRN_REPO_NAMESPACES" if os.environ.get("CAIRN_REPO_NAMESPACES") else (
        str(cfg.source) if cfg.source and cfg.repo_namespaces else "built-in default"
    )
    namespaces = _load_namespaces()
    click.echo(f"repo_namespaces ({source}):")
    if namespaces:
        for ns, owner in sorted(namespaces.items()):
            click.echo(f"  {ns} -> {owner}")
    else:
        click.echo("  (empty — cross_repo_deps will find no cross-repo links)")

    click.echo("")
    click.echo("MCP config for an agent (path-free):")
    click.echo('  cairn config --mcp-config')


# --------------------------------------------------------------------------
# cairn build
# --------------------------------------------------------------------------
@main.command()
@click.option("--repo", "repo", default=None, help="Only build this repo.")
@click.option("--workspace", default=scanner_mod.DEFAULT_WORKSPACE, help="Workspace root.")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("-v", "--verbose", is_flag=True,
              help="Verbose per-file detail (parse errors, skip reasons).")
@click.option("--staging", is_flag=True, help="Build to temp DB and atomic-swap for zero downtime.")
def build(repo, workspace, db, verbose, staging):
    """Build (or rebuild) the code graph."""
    from . import display

    target_db = db + ".tmp" if staging else db

    import time
    t0 = time.time()

    # One rail across the whole flow. animate=not verbose: the -v path turns
    # on builder._log's raw print(), which would corrupt a live region.
    with display.rail("Building graph", animate=not verbose) as r:
        r.start("Scanning files")
        phase_state = {"scan_done": False, "resolve_started": False}

        def on_progress(phase, **kw):
            if phase == "scan":
                files = kw.get("files", 0)
                r.tick(f"{files:,} found")
                r.finish(f"{files:,} found")
                r.start("Parsing code")
                phase_state["scan_done"] = True
            elif phase == "parse_progress":
                total = kw.get("total", 0) or 1
                pct = 50 * kw.get("done", 0) // total
                r.tick(f"{pct}%")
            elif phase == "insert_progress":
                total = kw.get("total", 0) or 1
                pct = 50 + 50 * kw.get("done", 0) // total
                r.tick(f"{pct}%")
            elif phase == "resolve_start":
                if not phase_state["resolve_started"]:
                    r.finish("done")
                    r.start("Resolving refs")
                    phase_state["resolve_started"] = True
                r.tick(kw.get("repo", ""))
            elif phase == "persist":
                r.finish("done")
                r.start("Persisting graph")

        try:
            summary = builder.build_graph(
                workspace=workspace, repo_filter=repo, db_path=target_db,
                verbose=verbose, progress=on_progress,
            )
        except Exception:
            # --staging writes to a temp DB; remove the half-built temp DB on
            # failure so a failed staging build doesn't leak. The rail's
            # context manager closes as "└ Failed" on the re-raise.
            if staging and os.path.exists(target_db):
                try:
                    os.remove(target_db)
                except OSError:
                    pass
            raise
        # build_graph's last event (persist) opens a sub-step it never closes;
        # settle it before the derived-index phases.
        r.finish("done")

        # Derived indexes: dataflow + transitive closure. Dataflow calls
        # impact_analysis per public symbol and can be slow on large workspaces,
        # so it gets its own animated sub-step. Transitive closure is pure SQL.
        df_count = tc_count = None
        df_error = None
        conn = None
        try:
            conn = get_db(target_db)
            from ..graph.dataflow import build_dataflow_index, build_transitive_closure

            # Count public symbols first so the sub-step ticks are meaningful.
            from ..graph.dataflow import _public_symbols
            pub_total = len(_public_symbols(conn))

            if pub_total > 0:
                r.start("Dataflow index")
                df_count = build_dataflow_index(
                    conn, progress=lambda done: r.tick(f"{done:,}/{pub_total:,}")
                )
                r.finish(f"{df_count:,} symbols")
                r.start("Transitive closure")
                tc_count = build_transitive_closure(conn)
                r.finish(f"{tc_count:,} edges")
            else:
                df_count = 0
                tc_count = build_transitive_closure(conn)

            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except Exception as e:
            df_error = str(e)
        finally:
            # Always close: a leaked connection can hold SQLite's writer lock
            # and lock out subsequent `cairn build`/`cairn embed`.
            if conn is not None:
                conn.close()

    elapsed = time.time() - t0

    if staging:
        os.replace(target_db, db)

    # Final summary panel (unchanged): repos/files/symbols/edges/imports +
    # resolution breakdown + staging note. The rail replaced the progress
    # rendering, not this detail table.
    kv_pairs = [
        ("repos", str(summary["repos"])),
        ("files", f"{summary['files']:,}"),
        ("symbols", f"{summary['symbols']:,}"),
        ("edges", f"{summary['edges']:,}"),
        ("imports", f"{summary['imports']:,}"),
    ]
    if summary.get("skipped"):
        kv_pairs.append(("skipped", f"{summary['skipped']:,}"))
    if df_count is not None:
        kv_pairs.append(("dataflow", f"{df_count:,} symbols"))
    if tc_count is not None:
        kv_pairs.append(("transitive", f"{tc_count:,} edges"))

    resolution = summary.get("resolution") or {}
    exact = resolution.get("exact", 0)
    ambig = resolution.get("ambiguous", 0)
    unres = resolution.get("unresolved", 0)
    total_edges = summary["edges"] or 1
    subtitle_parts = [
        f"edges resolved: {exact:,} exact ({100*exact//total_edges}%)",
        f"{ambig:,} ambiguous",
        f"{unres:,} unresolved",
    ]
    if staging:
        subtitle_parts.append(f"atomic swap → {db}")
    if df_error:
        subtitle_parts.append(f"dataflow skipped: {df_error}")

    display.summary_panel(
        title=f"Built graph in {elapsed:.1f}s",
        kv_pairs=kv_pairs,
        subtitle="  ·  ".join(subtitle_parts),
    )
    if verbose and summary.get("skipped"):
        display.dim(f"{summary['skipped']} files skipped (gitignored/default/config/size; see `cairn stats`)")


# --------------------------------------------------------------------------
# cairn stats
# --------------------------------------------------------------------------
@main.command(name="stats")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
def stats(db):
    """Show graph statistics."""
    from . import display

    conn = get_db(db)
    try:
        s = queries.get_stats(conn)
    finally:
        conn.close()

    display.kv("repos", s["repos"])
    display.kv("files", f"{s['files']:,}")
    display.kv("symbols", f"{s['symbols']:,}")
    display.kv("edges", f"{s['edges']:,} ({s['edges_resolved']:,} resolved)")
    display.kv("imports", f"{s['imports']:,}")
    if s.get("skipped_total"):
        display.kv("skipped", f"{s['skipped_total']:,} (not indexed)")

    if s["by_repo"]:
        display.print_table("By repo", ["repo", "count"],
                            [[r, c] for r, c in s["by_repo"].items()])
    if s["by_kind"]:
        display.print_table("By kind", ["kind", "count"],
                            [[k, c] for k, c in s["by_kind"].items()])
    if s.get("skipped_by_reason"):
        display.print_table("Skipped by reason", ["reason", "count"],
                            [[r, c] for r, c in s["skipped_by_reason"].items()])


@main.command()
@click.option("--db", default=None, help="SQLite DB path (default: central store for this workspace).")
def checkpoint(db):
    """Checkpoint the graph DB's WAL back into the main file (TRUNCATE).

    The cairn server runs in WAL mode. With multiple processes (or a
    long-lived daemon) the WAL file grows and can't be reclaimed until a
    checkpoint runs. This command forces TRUNCATE, shrinking the -wal file
    to zero. Safe to run any time; no data loss.

    Run this after `cairn serve stop` to reclaim space, or as recovery if the
    -wal file has grown large (check with `ls -la <store>/.kg*`).
    """
    from . import display

    import sqlite3

    from ..paths import resolve_store

    path = db or str(resolve_store().db)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA busy_timeout = 10000")
        before = Path(path + "-wal").stat().st_size if Path(path + "-wal").exists() else 0
        result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    finally:
        conn.close()
    after = Path(path + "-wal").stat().st_size if Path(path + "-wal").exists() else 0
    display.kv("checkpoint", f"busy={result[0]} log_frames={result[1]} checkpointed={result[2]}")
    display.kv("wal size", f"{_human_bytes(before)} -> {_human_bytes(after)}")

