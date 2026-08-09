"""download-reranker CLI: pre-fetch the CrossEncoder reranker model weights."""
from __future__ import annotations

import sys

import click

from .main import main


@main.command(name="download-reranker")
@click.option("--model", "model_name", default=None,
              help="CrossEncoder model id (default: BAAI/bge-reranker-base, the "
                   "natural pair for the bge-m3 embedder; or $CAIRN_RERANK_MODEL "
                   "if set).")
def download_reranker(model_name):
    """Download the reranker model and enable reranking.

    Fetches the CrossEncoder weights into the local HuggingFace cache (default:
    BAAI/bge-reranker-base, ~1.1GB), then writes a persistent enable marker so
    reranking is on for subsequent queries — no need to export CAIRN_RERANK=1.
    (Set CAIRN_RERANK=0 to force it back off.)

    Needs the optional [semantic] extra (it provides sentence-transformers,
    the same dependency as local embeddings):

        pip install 'cairn-intel[semantic]'

    Examples:

        cairn download-reranker                       # bge-reranker-base + enable
        cairn download-reranker --model cross-encoder/ms-marco-MiniLM-L-6-v2

    At query time, if the configured model is missing/evicted from the cache,
    reranking falls back to the hybrid (vector + BM25 + RRF) order rather than
    failing — re-run this command to re-fetch.
    """
    from ..graph.reranker import (
        current_rerank_model, download_reranker_model, install_hint,
        reranker_available, set_rerank_enabled_persistently,
    )

    if not reranker_available():
        click.echo(install_hint(), err=True)
        sys.exit(2)

    resolved = model_name or current_rerank_model()
    click.echo(f"Reranker model: {resolved}")
    ok = download_reranker_model(resolved)
    if ok:
        # Persistently enable reranking for subsequent processes. A CLI process
        # can't export an env var into its parent shell, so we write a marker
        # file that rerank_enabled() honors as if CAIRN_RERANK=1 were set.
        set_rerank_enabled_persistently()
        click.echo(
            "Reranking enabled for subsequent queries. "
            "(Set CAIRN_RERANK=0 to turn it back off.)"
        )
    sys.exit(0 if ok else 1)
