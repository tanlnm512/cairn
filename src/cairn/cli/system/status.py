"""Status and evaluation command implementations."""
from __future__ import annotations

import click
import json
from pathlib import Path

from ..main import DEFAULT_DB_PATH, DEFAULT_KNOWLEDGE_PATH, get_db, main, queries
from .._helpers import _shorten


def _pending_sync_rows(conn):
    """Return unindexed edits, or nothing when the table is unavailable."""
    try:
        return conn.execute(
            "SELECT path, repo_id, changed_at FROM pending_sync ORDER BY changed_at DESC"
        ).fetchall()
    except Exception:
        return []


def _parse_errors(conn):
    """Return the newest parse errors and their total, or an empty result."""
    try:
        total = conn.execute("SELECT COUNT(*) FROM parse_errors").fetchone()[0]
        rows = conn.execute(
            "SELECT file_path, error_message FROM parse_errors "
            "ORDER BY timestamp DESC LIMIT 5"
        ).fetchall()
    except Exception:
        return 0, []
    return total, rows


def _display_memory(mem):
    from .. import display

    for tier, info in mem.items():
        display.kv(f"  {tier}", f"{info['count']:>4} (avg {info['avg_score']:.2f})")


def _display_pending(rows):
    from .. import display

    if not rows:
        return
    display.warning(f"Pending sync: {len(rows)} files")
    for row in rows[:20]:
        display.dim(f"  {_shorten(row['path'])}")
    if len(rows) > 20:
        display.dim(f"  ... and {len(rows) - 20} more")


def _display_parse_errors(total, rows):
    from .. import display

    if not total:
        return
    display.warning(f"Parse errors: {total}")
    for row in rows:
        message = row["error_message"] or ""
        if len(message) > 100:
            message = message[:100] + "..."
        display.dim(f"  {_shorten(row['file_path'])} — {message}")
    if total > len(rows):
        display.dim(f"  ... and {total - len(rows)} more")


# --------------------------------------------------------------------------
# cairn status
# --------------------------------------------------------------------------
@main.command()
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--knowledge", default=DEFAULT_KNOWLEDGE_PATH, help="Knowledge directory path.")
def status(db, knowledge):
    """System status and health across all layers."""
    from ...memory.promotion import memory_stats as mstats
    from ...okf.bundle import OKFBundle

    conn = get_db(db)
    try:
        stats = queries.get_stats(conn)
        bundle = OKFBundle(knowledge)
        compass_n = len(bundle.list_concepts(prefix="compass/"))
        wiki_n = len(bundle.list_concepts(prefix="wiki/"))
        mem = mstats(bundle)
        pending_rows = _pending_sync_rows(conn)
        parse_err_total, parse_err_rows = _parse_errors(conn)
    finally:
        conn.close()

    from .. import display
    display.kv(
        "graph",
        f"{stats['repos']} repos · {stats['symbols']:,} symbols · "
        f"{stats['edges']:,} edges",
    )
    display.kv("compass", f"{compass_n} files")
    display.kv("wiki", f"{wiki_n} articles")
    display.kv("memory", "")
    _display_memory(mem)
    _display_pending(pending_rows)
    _display_parse_errors(parse_err_total, parse_err_rows)


# --------------------------------------------------------------------------
# cairn eval
# --------------------------------------------------------------------------
@main.command(name="eval")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--knowledge", default=DEFAULT_KNOWLEDGE_PATH, help="Knowledge directory path.")
@click.option("--corpus", type=click.Choice(["L1", "L4", "L5", "all"]), default="all", help="Corpus filter.")
@click.option("--queries", "queries_path", default=None,
              help="Path to eval queries.yaml OR a ground-truth directory "
                   "(queries.jsonl + expectations.tsv); default: bundled tests/eval/queries.yaml.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def eval_cmd(db, knowledge, corpus, queries_path, as_json):
    """Run retrieval evaluation harness across L1/L5 corpora."""
    from ...eval import run_evaluation

    qpath = Path(queries_path) if queries_path else None
    conn = get_db(db)
    try:
        report = run_evaluation(conn, bundle_root=knowledge, queries_path=qpath, corpus_filter=corpus)
    except ValueError as exc:
        raise click.ClickException(f"invalid eval dataset: {exc}") from exc
    finally:
        conn.close()

    if as_json:
        click.echo(json.dumps(report, indent=2))
        return

    from .. import display
    rows = []
    for c_key in ["L1", "L4", "L5"]:
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
