"""Embed CLI: semantic embedding index + semantic search."""
from __future__ import annotations

import click
import json
import os
import sys

from .main import DEFAULT_DB_PATH, get_db, main, queries
from ._helpers import _human_bytes, _mods, _shorten  # noqa: F401

@main.command()
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--batch-size", default=64, type=int, help="Embedding batch size.")
@click.option("--limit", default=None, type=int, help="Max symbols to embed (debug).")
@click.option(
    "--no-reap",
    is_flag=True,
    help="Skip deleting embedding rows for symbols that no longer exist.",
)
@click.option(
    "--build-index",
    is_flag=True,
    help="Rebuild native ANN index after embedding.",
)
@click.option(
    "--install-deps",
    is_flag=True,
    help="Auto-install missing semantic dependencies (sentence-transformers).",
)
@click.option(
    "--download-model",
    is_flag=True,
    help="Pre-download model weights into local HuggingFace cache.",
)
def embed(db, batch_size, limit, no_reap, build_index, install_deps, download_model):
    """Build the semantic embedding index over the symbol corpus."""
    from . import display
    from cairn.graph import embeddings as emb

    if install_deps:
        # --install-deps: install semantic dependencies and exit. Don't fall
        # through to embedding — the user asked only to install deps (which
        # may involve downloading torch + sentence-transformers, ~hundreds of
        # MB). Run `cairn embed` separately to build the index.
        if not emb.ensure_semantic_deps(auto_install=True):
            display.error("Semantic dependencies unavailable")
            display.dim(emb.install_hint())
            sys.exit(1)
        display.success("Semantic dependencies installed.")
        display.dim("Run `cairn embed` to build the embedding index.")
        sys.exit(0)

    if not emb.embeddings_available():
        display.error("Semantic dependencies unavailable")
        display.dim(emb.install_hint())
        display.dim("Run `cairn embed --install-deps` to auto-install.")
        sys.exit(1)

    # Warn when silently falling back to the hash backend. The fallback is
    # intentional for `semantic_search` (graceful degradation), but `cairn embed`
    # is an explicit action where the user expects real model embeddings.
    # If CAIRN_EMBED_BACKEND is unset (default 'local') but sentence-
    # transformers isn't installed, _effective_backend() silently returns
    # 'hash' — surface that so the user knows the index won't carry real
    # semantic meaning, and tell them how to fix it.
    if emb._effective_backend() == "hash" and not os.environ.get("CAIRN_EMBED_BACKEND"):
        display.warning(
            "Using the hash embedder (dep-free) because sentence-transformers "
            "isn't installed. The index will work for token-overlap search but "
            "won't carry real semantic meaning."
        )
        display.dim(
            "For real embeddings (bge-m3 by default), run ONE TIME:"
        )
        display.dim("  cairn embed --install-deps")
        display.dim(
            "This downloads torch + sentence-transformers into ~/.cairn/lib "
            "(survives reinstalls). Then re-run: cairn embed"
        )
        display.dim(
            "Or set CAIRN_EMBED_BACKEND=hash explicitly to silence this warning."
        )

    if download_model:
        if not emb.download_model():
            display.error("Model download failed")
            sys.exit(1)
        display.success("Model download complete.")
        sys.exit(0)

    conn = get_db(db)
    try:
        sym_count = conn.execute("SELECT COUNT(*) AS c FROM symbols").fetchone()["c"]
        display.info(f"Embedding {sym_count:,} symbols with {emb.current_model()}")
        before = emb.embed_count(conn)

        import time
        t0 = time.time()

        # The progress callback from embed_all is (done, total). Wire it to
        # a determinate bar -- once the first callback fires we know the
        # total, so we can pin it then.
        bar_state = {"bar": None, "task": None}

        def progress(done, total):
            bar = bar_state["bar"]
            if bar is None:
                return
            task = bar_state["task"]
            # Set total on the first callback (embed_all doesn't call us
            # until it has the full corpus counted).
            if bar.tasks[task].total is None or bar.tasks[task].total != total:
                bar.update(task, total=total)
            bar.update(task, completed=done)

        with display.progress_bar(description="Embedding", total=None, unit="symbols") as bar:
            bar_state["bar"] = bar
            bar_state["task"] = bar._cg_task_id
            summary = emb.embed_all(
                conn, batch_size=batch_size, limit=limit, progress=progress, reap_orphans=not no_reap
            )
            bar_state["bar"] = None
        elapsed = time.time() - t0

        after = emb.embed_count(conn)
        display.summary_panel(
            title=f"Embedded {summary['embedded']:,} symbols in {elapsed:.1f}s",
            kv_pairs=[
                ("embedded", f"{summary['embedded']:,}"),
                ("skipped", f"{summary['skipped']:,}"),
                ("reaped", f"{summary.get('reaped', 0):,}"),
                ("model", summary["model"]),
                ("vectors", f"{before:,} -> {after:,}"),
            ],
        )

        from cairn.graph import ann_index as ann

        if ann.ann_backend_enabled():
            with display.progress_bar(description="ANN index", total=None, unit=""):
                idx_summary = ann.rebuild_index(conn, emb.current_model())
            if idx_summary.get("skipped"):
                display.warning(f"ANN index not built: {idx_summary['skipped']}")
            else:
                display.success(
                    f"ANN index rebuilt: {idx_summary['indexed']:,} vectors, "
                    f"dim={idx_summary['dim']}, model='{idx_summary['model']}'"
                )
    finally:
        conn.close()


@main.command()
@click.argument("query")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--limit", default=20, type=int, help="Max results.")
@click.option(
    "--threshold",
    default=0.3,
    type=float,
    help="Min cosine similarity (0..1). Lower = more recall, more noise.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
@click.option(
    "--include-callers",
    is_flag=True,
    help="Attach each hit's 1-hop callers/callees (precise-only, capped at 5 each).",
)
def semantic(query, db, limit, threshold, as_json, include_callers):
    """Semantic (concept) search: find code by meaning, not just words.

    Set CAIRN_RERANK=1 to add a cross-encoder rerank stage (widens the
    candidate pool, re-scores with a joint query/candidate model). Falls back
    to plain cosine ordering silently if the reranker isn't installed or
    fails to load -- check the 'reranked' field (or --json output) to see
    which path actually ran for a given call.
    """
    from cairn.graph import embeddings as emb

    if not emb.embeddings_available():
        from . import display
        display.error("Semantic backend unavailable")
        display.dim(emb.install_hint())
        sys.exit(1)

    conn = get_db(db)
    try:
        # Do NOT lazily embed during search — embed_all() writes thousands of
        # transactions and contends with the daemon's WAL lock. Embedding is a
        # build-time op (`cairn embed`); point the user there if not indexed.
        if emb.embed_count(conn) == 0:
            from . import display
            display.warning(
                f"Semantic index is empty under model '{emb.current_model()}'. "
                "Run `cairn embed` once to index, then retry."
            )
            sys.exit(1)
        rows = queries.semantic_search(
            conn, query, limit=limit, threshold=threshold, include_callers=include_callers
        )
    finally:
        conn.close()

    if as_json:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    from . import display
    if not rows:
        display.warning(f"No semantic matches for '{query}' (threshold {threshold})")
        return
    console_out = [f"{len(rows)} semantic match(es) for '{query}':"]
    for r in rows:
        short = (r["file_path"] or "").rsplit("/", 1)[-1]
        score_label = f"rerank {r['rerank_score']:.2f}" if r.get("reranked") else f"{r['score']:.2f}"
        console_out.append(
            f"  [{score_label}] {r['kind']} "
            f"{r['qualified_name'] or r['name']}  ({short})"
        )
    # Plain list output -- keep the click.echo path for parseable results
    # rather than the themed console, since semantic matches are often piped.
    click.echo("\n".join(console_out))


