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
    """Download the reranker model and enable reranking persistently (CAIRN_RERANK=0 forces it off).
    Needs the optional [semantic] extra: pip install 'cairn-intel[semantic]'."""
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
