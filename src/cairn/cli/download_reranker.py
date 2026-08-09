"""download-reranker CLI: pre-fetch the CrossEncoder reranker model weights."""
from __future__ import annotations

import sys

import click

from .main import main


@main.command(name="download-reranker")
@click.option("--model", "model_name", default=None,
              help="CrossEncoder model id (default: $CAIRN_RERANK_MODEL or "
                   "cross-encoder/ms-marco-MiniLM-L-6-v2).")
def download_reranker(model_name):
    """Pre-download the reranker model weights into the local HuggingFace cache.

    Reranking is off by default (CAIRN_RERANK); this command lets you fetch
    the weights ahead of time so the first reranked query isn't blocked on a
    download. Resolves the model in this order: --model flag, else
    CAIRN_RERANK_MODEL, else the built-in default. Does NOT require
    CAIRN_RERANK=1 — pre-fetching shouldn't depend on the feature being on.

    Needs the optional [semantic] extra (it provides sentence-transformers,
    the same dependency as local embeddings):

        pip install 'cairn-intel[semantic]'

    Example (BAAI reranker, the natural pair for the bge-m3 embedder):

        cairn download-reranker --model BAAI/bge-reranker-base

    Then enable reranking at query time:

        export CAIRN_RERANK=1
        export CAIRN_RERANK_MODEL=BAAI/bge-reranker-base
    """
    from ..graph.reranker import (
        current_rerank_model, download_reranker_model, install_hint,
        reranker_available,
    )

    if not reranker_available():
        click.echo(install_hint(), err=True)
        sys.exit(2)

    resolved = model_name or current_rerank_model()
    click.echo(f"Reranker model: {resolved}")
    ok = download_reranker_model(resolved)
    sys.exit(0 if ok else 1)
