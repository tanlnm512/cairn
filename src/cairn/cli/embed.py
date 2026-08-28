"""Embed CLI: semantic embedding index + semantic search."""
from __future__ import annotations

import click
import json
import sys

from .main import DEFAULT_DB_PATH, get_db, main, queries


def _exit_backend_unavailable() -> None:
    """Report an unavailable embedding backend and exit 1 (D-003: loud).

    Server-family backends never fall back to hash (FR-002), so a failed
    probe exits with a server-specific remediation (base URL, the /v1/models
    check it must pass, `cairn doctor`) instead of the sentence-transformers
    install hint every other backend keeps. Exit code is 1 either way.
    """
    from . import display
    from cairn.graph import embeddings as emb

    if emb._backend_name() not in emb._SERVER_FAMILY:
        display.error("Semantic dependencies unavailable")
        display.dim(emb.install_hint())
        display.dim("Run `cairn embed --install-deps` to auto-install.")
        sys.exit(1)
    display.error("Embedding server unavailable")
    try:
        base = emb._server_base_url().rstrip("/")
    except RuntimeError as exc:
        # Bare 'server' without CAIRN_EMBED_BASE_URL: the message names the
        # missing knob or the presets, which is itself the remediation.
        display.dim(str(exc))
        display.dim("Run `cairn doctor` for embedding backend diagnostics.")
        sys.exit(1)
    display.dim(
        f"Availability probe failed: GET {base}/models must return 200 and "
        f"list model '{emb._server_model()}'."
    )
    display.dim(
        f"Start the embedding server at {base} (verify with "
        f"`curl {base}/models`), then re-run; `cairn doctor` checks probe, "
        "parity, and latency."
    )
    sys.exit(1)


def _exit_not_adoptable(requested, state) -> None:
    """Exit 1 naming what the ladder found (FR-012/D-003: degrade loudly)."""
    from . import display

    if requested:
        display.error(
            f"Server model '{requested}' is not a parity-verified candidate "
            "for the stored corpus"
        )
    else:
        display.error("No parity-verified server model candidate to adopt")
    if state is None:
        display.dim(
            "The embedding server is healthy (the configured model is "
            "served); there is no degraded state to adopt from."
        )
    elif state.rung == 1:
        display.dim(
            f"The ladder's parity scan adopted '{state.adopted_model}' instead."
        )
    elif state.rung == 2:
        display.dim(
            "The ladder fell back to the local model, not a server model: "
            f"{state.detail}"
        )
    else:
        display.dim(f"Ladder verdict: {state.reason}; {state.detail}")
    sys.exit(1)


def _resolve_adopted_model(conn, requested):
    """Resolve --adopt-server-model into the FR-012 alias binding.

    Returns (adopted_model_id, stored_stamp) with the session pins set so the
    embed below reads and writes the STORED stamp while requests route to the
    adopted id — permanence of the alias binding, never a corpus restamp. A
    bare flag reuses the ladder's active rung-1 adoption, else forces one
    evaluation against the server; an explicit MODEL_ID is verified the same
    honest way: it must be the candidate the ladder's parity scan proves
    (D-009: only a passing parity gate switches producers).
    """
    from . import display
    from cairn.graph import embed_ladder as ladder

    # The binding needs a stored corpus to latch onto, so check that BEFORE
    # evaluating the ladder: with no rows there is nothing to verify parity
    # against (the ladder would refuse on its own verdict), and the empty
    # table deserves its specific remediation, not the ladder's.
    row = conn.execute(
        "SELECT model, COUNT(*) AS n FROM embeddings GROUP BY model "
        "ORDER BY COUNT(*) DESC, model LIMIT 1"
    ).fetchone()
    if row is None:
        display.error(
            "--adopt-server-model: the database holds no stored embeddings "
            "to bind the adoption to"
        )
        sys.exit(1)
    stored_stamp = row["model"]

    if requested:
        state = ladder.evaluate_ladder(conn=conn, force=True)
        if state is None:
            # Healthy server, or the effective backend left the server arm;
            # surface any cached verdict for the "why".
            state = ladder.ladder_state()
        if state is None or state.rung != 1 or state.adopted_model != requested:
            _exit_not_adoptable(requested, state)
        adopted = requested
    else:
        state = ladder.ladder_state()
        if not (state and state.active and state.rung == 1 and state.adopted_model):
            forced = ladder.evaluate_ladder(conn=conn, force=True)
            state = forced if forced is not None else ladder.ladder_state()
        if not (state and state.rung == 1 and state.adopted_model):
            _exit_not_adoptable(requested, state)
        adopted = state.adopted_model

    ladder.set_session_stamp(stored_stamp)
    ladder.set_session_server_model(adopted)
    # Make the dominant-stamp choice visible: which stored stamp the alias
    # binding latched onto (most rows wins) and how many rows back it.
    display.dim(
        f"binding alias to stored stamp '{stored_stamp}' ({row['n']} rows)"
    )
    return adopted, stored_stamp


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
@click.option(
    "--multivector",
    is_flag=True,
    help="Also embed name-only and docstring-only vectors (embeddings_mv, FR-005). "
    "Off by default: default builds store one vector per symbol, byte-identical "
    "to before.",
)
@click.option(
    "--adopt-server-model",
    "adopt_server_model",
    is_flag=False,
    flag_value="",
    default=None,
    metavar="[MODEL_ID]",
    help="Adopt a parity-verified server model (FR-012): the stored corpus "
    "keeps its stamp while this embed runs through the adopted model. "
    "Omit MODEL_ID to use the ladder's verified rung-1 adoption, "
    "re-evaluated against the server when none is active.",
)
def embed(
    db,
    batch_size,
    limit,
    no_reap,
    build_index,
    install_deps,
    download_model,
    multivector,
    adopt_server_model,
):
    """Build the semantic embedding index over the symbol corpus."""
    from . import display
    from cairn.graph import embeddings as emb

    if install_deps:
        # --install-deps: install semantic dependencies and exit.
        if not emb.ensure_semantic_deps(auto_install=True):
            display.error("Semantic dependencies unavailable")
            display.dim(emb.install_hint())
            sys.exit(1)
        display.success("Semantic dependencies installed.")
        display.dim("Run `cairn embed` to build the embedding index.")
        sys.exit(0)

    if download_model:
        # The availability check imports the semantic stack in-process; on a
        # first run (or right after --install-deps) that import alone takes
        # 30s+. Say something BEFORE it so the window isn't dead silence.
        display.dim("Loading the semantic backend (first import can take a minute)...")
        if not emb.embeddings_available():
            _exit_backend_unavailable()
        if not emb.download_model():
            display.error("Model download failed")
            sys.exit(1)
        display.success("Model download complete.")
        sys.exit(0)

    if adopt_server_model is not None:
        # The ladder, not the availability probe, is the evaluator here: the
        # configured model is typically already missing from /v1/models (the
        # case adoption exists for), so a failed probe must not exit early.
        if emb._backend_name() not in emb._SERVER_FAMILY:
            display.error(
                "--adopt-server-model requires a server-family embedding "
                "backend (server, omlx, or ollama)"
            )
            sys.exit(1)
    elif not emb.embeddings_available():
        _exit_backend_unavailable()

    # Warn when silently falling back to the hash backend. is_hash_fallback()
    # is True only when the backend is the *default* local but
    # sentence-transformers isn't installed (a silent fallback), not when the
    # user explicitly set CAIRN_EMBED_BACKEND=hash.
    if emb.is_hash_fallback():
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
            "This downloads torch + sentence-transformers into "
            "~/.cairn/lib/cp<version> (survives reinstalls). "
            "Then re-run: cairn embed"
        )
        display.dim(
            "Or set CAIRN_EMBED_BACKEND=hash explicitly to silence this warning."
        )

    conn = get_db(db)
    try:
        adopted = stored_stamp = None
        if adopt_server_model is not None:
            adopted, stored_stamp = _resolve_adopted_model(conn, adopt_server_model)

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

        from cairn.graph import ann_index as ann

        build_ann = ann.ann_backend_enabled()

        # One progress bar for the whole `cairn embed` run -- embedding and
        # (if enabled) the ANN rebuild -- retargeting its description/total for
        # the second phase.
        with display.progress_bar(description="Embedding", total=None, unit="symbols") as bar:
            bar_state["bar"] = bar
            bar_state["task"] = bar._cg_task_id
            summary = emb.embed_all(
                conn,
                batch_size=batch_size,
                limit=limit,
                progress=progress,
                reap_orphans=not no_reap,
                multivector=multivector,
            )
            bar_state["bar"] = None

            if build_ann:
                task_id = bar._cg_task_id
                # Reset the task directly so the ANN phase renders as a fresh
                # indeterminate bar.
                bar.tasks[task_id].total = None
                bar.update(task_id, description="ANN index", completed=0)
                idx_summary = ann.rebuild_index(conn, emb.current_model())
                # --multivector: also rebuild the FR-005 mv index (D-007 --
                # its own vecmv_<model> vec0 table over embeddings_mv). Flag
                # off: this call is absent and the flow is byte-identical to
                # the pre-FR-005 single-index build.
                mv_idx_summary = (
                    ann.rebuild_index(conn, emb.current_model(), source="embeddings_mv")
                    if multivector
                    else None
                )
        elapsed = time.time() - t0

        after = emb.embed_count(conn)
        kv_pairs=[
            ("embedded", f"{summary['embedded']:,}"),
            ("skipped", f"{summary['skipped']:,}"),
            ("reaped", f"{summary.get('reaped', 0):,}"),
            ("model", summary["model"]),
            ("vectors", f"{before:,} -> {after:,}"),
        ]
        if multivector:
            kv_pairs.append(("mv vectors", f"{summary.get('mv_embedded', 0):,}"))
        display.summary_panel(
            title=f"Embedded {summary['embedded']:,} symbols in {elapsed:.1f}s",
            kv_pairs=kv_pairs,
        )

        if build_ann:
            if idx_summary.get("skipped"):
                display.warning(f"ANN index not built: {idx_summary['skipped']}")
            else:
                display.success(
                    f"ANN index rebuilt: {idx_summary['indexed']:,} vectors, "
                    f"dim={idx_summary['dim']}, model='{idx_summary['model']}'"
                )
            if multivector and mv_idx_summary is not None:
                if mv_idx_summary.get("skipped"):
                    display.warning(
                        f"MV ANN index not built: {mv_idx_summary['skipped']}"
                    )
                else:
                    display.success(
                        f"MV ANN index rebuilt: {mv_idx_summary['indexed']:,} vectors, "
                        f"dim={mv_idx_summary['dim']}, model='{mv_idx_summary['model']}'"
                    )

        if adopted is not None:
            # FR-012: permanence persists the alias binding — the corpus keeps
            # its stamp. ~/.cairn/config.json (the FR-010 substrate, landed) is
            # the durable home for the pin; the env export remains for
            # env-only setups.
            display.success(
                f"Adopted server model '{adopted}': this corpus keeps its "
                f"stamp '{stored_stamp}' (no re-embed, no restamp)."
            )
            display.dim(
                "To keep the binding across processes, set it in the config "
                "file ~/.cairn/config.json (dashboard Settings or a direct "
                "edit): CAIRN_EMBED_MODEL_STAMP="
                f"{stored_stamp} alongside CAIRN_EMBED_SERVER_MODEL={adopted}."
            )
            display.dim("Env-only setups can pin the same stamp by exporting:")
            display.dim(f"  export CAIRN_EMBED_MODEL_STAMP={stored_stamp}")

        # Persist an 'embed' build_runs row (best-effort; record_build_run
        # swallows all errors). Only the symbol/skipped counts are meaningful
        # for an embedding pass -- repos/files/edges/resolution/phase_timings
        # stay NULL (no scan/parse/resolve phases run here). t0 was captured
        # at the start of the embed phase.
        from ..graph.builder import record_build_run
        record_build_run(
            db,
            "embed",
            started_at=t0,
            duration_s=elapsed,
            symbols=summary["embedded"],
            skipped=summary["skipped"],
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
        _exit_backend_unavailable()

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
        sys.exit(1)
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


