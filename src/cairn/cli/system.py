"""System CLI: import-scip, metrics, status, eval, sync."""
from __future__ import annotations

import click
import json
from pathlib import Path

from .main import DEFAULT_DB_PATH, DEFAULT_KNOWLEDGE_PATH, get_db, main, queries, scanner_mod
from ._helpers import _shorten

@main.command()
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--tool", "tool_name", default=None, help="Filter by tool name.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def metrics(db, tool_name, as_json):
    """Report MCP tool invocation metrics (calls, avg latency, error rate)."""
    from . import display

    conn = get_db(db)
    try:
        where = "WHERE tool_name = ?" if tool_name else ""
        params = (tool_name,) if tool_name else ()
        rows = conn.execute(
            f"SELECT tool_name, COUNT(*) AS calls, "
            f"AVG(duration_ms) AS avg_ms, "
            f"SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors "
            f"FROM tool_metrics {where} "
            f"GROUP BY tool_name ORDER BY calls DESC",
            params,
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        display.info("No tool metrics recorded yet.")
        return
    if as_json:
        click.echo(json.dumps([dict(r) for r in rows], indent=2, default=str))
        return
    table_rows = []
    for r in rows:
        err_pct = r["errors"] / r["calls"] * 100 if r["calls"] else 0
        table_rows.append([
            r["tool_name"],
            f"{r['calls']:,}",
            f"{r['avg_ms']:.1f}",
            f"{r['errors']:,}",
            f"{err_pct:.1f}%",
        ])
    display.print_table(
        title=None,
        columns=["tool", "calls", "avg ms", "errors", "err %"],
        rows=table_rows,
    )


# --------------------------------------------------------------------------
# cairn status
# --------------------------------------------------------------------------
@main.command()
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--knowledge", default=DEFAULT_KNOWLEDGE_PATH, help="Knowledge directory path.")
def status(db, knowledge):
    """System status and health across all layers."""
    from ..memory.promotion import memory_stats as mstats
    from ..okf.bundle import OKFBundle

    conn = get_db(db)
    try:
        s = queries.get_stats(conn)
        bundle = OKFBundle(knowledge)
        compass_n = len(bundle.list_concepts(prefix="compass/"))
        wiki_n = len(bundle.list_concepts(prefix="wiki/"))
        mem = mstats(bundle)

        # Show pending sync files (unindexed edits in debounce window).
        try:
            pending_rows = conn.execute(
                "SELECT path, repo_id, changed_at FROM pending_sync ORDER BY changed_at DESC"
            ).fetchall()
        except Exception:
            pending_rows = []
    finally:
        conn.close()

    from . import display
    display.kv("graph", f"{s['repos']} repos · {s['symbols']:,} symbols · {s['edges']:,} edges")
    display.kv("compass", f"{compass_n} files")
    display.kv("wiki", f"{wiki_n} articles")
    display.kv("memory", "")
    for tier, info in mem.items():
        display.kv(f"  {tier}", f"{info['count']:>4} (avg {info['avg_score']:.2f})")

    if pending_rows:
        display.warning(f"Pending sync: {len(pending_rows)} files")
        for row in pending_rows[:20]:
            display.dim(f"  {_shorten(row['path'])}")
        if len(pending_rows) > 20:
            display.dim(f"  ... and {len(pending_rows) - 20} more")


# --------------------------------------------------------------------------
# cairn eval
# --------------------------------------------------------------------------
@main.command(name="eval")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--knowledge", default=DEFAULT_KNOWLEDGE_PATH, help="Knowledge directory path.")
@click.option("--corpus", type=click.Choice(["L1", "L5", "all"]), default="all", help="Corpus filter.")
@click.option("--queries", "queries_path", default=None,
              help="Path to eval queries.yaml (default: bundled tests/eval/queries.yaml).")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def eval_cmd(db, knowledge, corpus, queries_path, as_json):
    """Run retrieval evaluation harness across L1/L5 corpora."""
    from pathlib import Path

    from ..eval import run_evaluation

    qpath = Path(queries_path) if queries_path else None
    conn = get_db(db)
    try:
        report = run_evaluation(conn, bundle_root=knowledge, queries_path=qpath, corpus_filter=corpus)
    finally:
        conn.close()

    if as_json:
        click.echo(json.dumps(report, indent=2))
        return

    from . import display
    rows = []
    for c_key in ["L1", "L5"]:
        if corpus != "all" and c_key != corpus:
            continue
        data = report.get(c_key, {})
        rows.append([
            c_key,
            f"{data.get('count', 0):,}",
            f"{data.get('recall_at_10', 0.0):.4f}",
            f"{data.get('mrr', 0.0):.4f}",
        ])
    display.print_table(None, ["corpus", "samples", "recall@10", "mrr"], rows)


# --------------------------------------------------------------------------
# cairn sync (manual re-index escape hatch)
# --------------------------------------------------------------------------
@main.command()
@click.option("--workspace", default=scanner_mod.DEFAULT_WORKSPACE)
@click.option("--db", default=str(DEFAULT_DB_PATH))
def sync(workspace, db):
    """Manually re-index changed files (used when watcher is disabled or for scripting).

    Detects files changed since last index via size/mtime comparison and
    re-indexes them. Equivalent to what the watcher does automatically.
    """
    from ..graph import scanner as scanner_mod
    from ..graph.incremental import reindex_paths

    conn = get_db(db)
    try:
        changed: list[str] = []

        for repo_path in scanner_mod.discover_repos(workspace):
            repo_name = repo_path.name
            try:
                file_rows = conn.execute(
                    "SELECT path, size, mtime FROM files WHERE repo_id = ?",
                    (repo_name,),
                ).fetchall()
            except Exception:
                continue

            existing = set()
            for row in file_rows:
                existing.add(row["path"])
                # files.path is repo-relative; resolve to absolute via the
                # single chokepoint for stat.
                p = Path(scanner_mod.resolve_file_path(workspace, repo_name, row["path"]))
                try:
                    st = p.stat()
                except OSError:
                    changed.append(str(p))
                    continue
                if st.st_size != (row["size"] or 0):
                    changed.append(str(p))
                elif abs(st.st_mtime - (row["mtime"] or 0.0)) > 0.5:
                    changed.append(str(p))

            # Detect new source files. Storage is repo-relative; the scanner
            # yields absolute, so compare on the relative form.
            for src in scanner_mod.iter_source_files(repo_path):
                rel = str(src.relative_to(repo_path)) if str(src).startswith(str(repo_path)) else str(src)
                if rel not in existing and str(src) not in existing:
                    changed.append(str(src))

        if not changed:
            from . import display
            display.success("No changes detected. Graph is up to date.")
            return

        from . import display
        with display.progress_bar(description=f"Syncing {len(changed)} files", total=len(changed), unit="files") as bar:
            # reindex_paths doesn't expose per-file progress; show an indeterminate
            # bar that completes when it returns. For small N this is instant.
            result = reindex_paths(conn, workspace, changed)
            bar.update(bar._cg_task_id, completed=len(changed))
        # Refresh the dataflow index if any files were reindexed.
        if result["reindexed"]:
            try:
                from ..graph.dataflow import build_dataflow_index
                df_count = build_dataflow_index(conn)
                display.dim(f"  dataflow index: {df_count:,} symbols")
            except Exception:
                pass
        display.success(f"Synced: {result['reindexed']} reindexed, {result['deleted']} deleted")
        if result["errors"]:
            display.warning(f"{len(result['errors'])} errors")
            for e in result["errors"][:5]:
                display.dim(f"  {e}")
    finally:
        conn.close()


