#!/usr/bin/env python3
"""2f embeddings-build harness: python-embed a fixture corpus, emit the golden + pre-embed store."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(REPO_ROOT / "src"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT_DIR = REPO_ROOT / "rust" / "golden" / "embed"
CORPUS_STORE = OUT_DIR / "embed-corpus.kg"
CORPUS_DIR = OUT_DIR / "corpus"
GOLDEN_JSON = OUT_DIR / "embed-golden.json"

# Same pins as the search harness: deterministic hash backend, no rerank.
ENV_PINS = {"CAIRN_EMBED_BACKEND": "hash", "CAIRN_RERANK": "0"}
_POP_KEYS = (
    "CAIRN_DB",
    "CAIRN_KNOWLEDGE",
    "CAIRN_ANN_BACKEND",
    "CAIRN_CHUNK_VARIANT",
    "CAIRN_EMBED_MODEL_STAMP",
    "CAIRN_EMBED_BASE_URL",
    "CAIRN_EMBED_SERVER_MODEL",
    "CAIRN_EMBED_SERVER_BATCH",
    "CAIRN_EMBED_TIMEOUT",
    "CAIRN_EMBED_API_KEY",
    "CAIRN_EMBED_LOCAL_MODEL",
    "CAIRN_FUSION",
)

ALPHA_SRC = '''"""Alpha utilities for tokenizing and joining words."""

import re


def tokenize(text):
    """Split text into lowercase alphanumeric tokens.

    Longer description of tokenize behavior that spans
    multiple lines for the docstring vector.
    """
    return re.findall(r"[a-z0-9]+", text.lower())


def join_tokens(tokens, separator=" "):
    """Join tokens with a separator."""
    return separator.join(tokens)


class Greeter:
    """Builds greeting strings with punctuation."""

    def greet(self, name):
        """Return a greeting for name."""
        return f"Hello, {name}!"
'''

BETA_SRC = '''"""Beta socket retry helpers."""

import socket


def connect_with_retry(host, port, retries=3):
    """Connect to host:port with bounded retries and exponential backoff."""
    for attempt in range(retries):
        try:
            return socket.create_connection((host, port))
        except OSError:
            if attempt == retries - 1:
                raise
    raise OSError("unreachable")


def parse_expression(expr):
    """Parse an arithmetic expression into a token list."""
    return [ch for ch in expr if not ch.isspace()]
'''

LONG_DOC_PREFIX = """Return a padded summary used to exercise chunk truncation.

"""
LONG_DOC_TAIL = (
    " The summary pads wording with unicode é André 中文 tokens and repeated "
    "padding words so the chunk crosses the char budget on every side."
)
LONG_DOC = LONG_DOC_PREFIX + LONG_DOC_TAIL * 60

GAMMA_SRC = f'''"""Gamma truncation fixture."""


def long_documentation():
    """{LONG_DOC}"""
    return None


def no_docstring_symbol(x, y):
    return x + y
'''

KNN_QUERIES = (
    "tokenize lowercase words",
    "socket connect retry backoff",
    "greeting message builder",
    "parse arithmetic expression tokens",
    "join list separator",
    "zzz_no_match_probe_queried",
)

KNN_K = 5


def stage_scratch(scratch: Path) -> tuple[Path, Path]:
    """Write the fixture corpus and a scratch home; returns (workspace, home)."""
    corpus = scratch / "corpus"
    (corpus / ".git").mkdir(parents=True)
    (corpus / "alpha.py").write_text(ALPHA_SRC, encoding="utf-8")
    (corpus / "beta.py").write_text(BETA_SRC, encoding="utf-8")
    (corpus / "gamma.py").write_text(GAMMA_SRC, encoding="utf-8")
    home = scratch / "home"
    home.mkdir()
    return corpus, home


def pin_env(corpus: Path, home: Path) -> dict[str, str | None]:
    """Apply the harness env; returns the saved values for restore."""
    saved = {key: os.environ.get(key) for key in (*ENV_PINS, "CAIRN_HOME", "CAIRN_WORKSPACE", *_POP_KEYS)}
    os.environ.update(ENV_PINS)
    os.environ["CAIRN_HOME"] = str(home)
    os.environ["CAIRN_WORKSPACE"] = str(corpus)
    for key in _POP_KEYS:
        os.environ.pop(key, None)
    return saved


def restore_env(saved: dict[str, str | None]) -> None:
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def insert_ghost_rows(conn: sqlite3.Connection, model: str) -> None:
    """Orphan rows the embed pass must reap on both implementations.

    The FK on ``embeddings.symbol_id`` blocks orphans in live writes, so the
    seeds go in with the pragma off (the reap path exists for stores that
    gained orphans before enforcement or across grafts).
    """
    blob = bytes(256 * 4)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO embeddings (symbol_id, model, dim, vec, chunk, content_hash, embedded_at) "
        "VALUES ('ghost-sym', ?, 256, ?, 'ghost chunk', 'ghost-hash', '2026-01-01T00:00:00+00:00')",
        (model, blob),
    )
    conn.execute(
        "INSERT INTO embeddings_mv (symbol_id, model, vector_kind, dim, vec, chunk, content_hash, embedded_at) "
        "VALUES ('ghost-sym', ?, 'name', 256, ?, 'ghost chunk', 'ghost-hash', '2026-01-01T00:00:00+00:00')",
        (model, blob),
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    # WAL checkpoint so the file copy below carries the ghost rows.
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def export_rows(conn: sqlite3.Connection) -> tuple[list, list, list]:
    embeddings = [
        [row[0], row[1], row[2], row[3].hex(), row[4], row[5]]
        for row in conn.execute(
            "SELECT symbol_id, model, dim, vec, chunk, content_hash "
            "FROM embeddings ORDER BY symbol_id"
        )
    ]
    mv = [
        [row[0], row[1], row[2], row[3], row[4].hex(), row[5], row[6]]
        for row in conn.execute(
            "SELECT symbol_id, model, vector_kind, dim, vec, chunk, content_hash "
            "FROM embeddings_mv ORDER BY symbol_id, vector_kind"
        )
    ]
    term_df = [
        [row[0], row[1], row[2]]
        for row in conn.execute(
            "SELECT token, symbol_df, n_symbols FROM term_df ORDER BY token"
        )
    ]
    return embeddings, mv, term_df


def knn_captures(conn, model: str) -> dict[str, list]:
    from cairn.graph import ann_index as ann
    from cairn.graph import embeddings as emb

    out: dict[str, list] = {}
    for query in KNN_QUERIES:
        blob, _dim = emb.embed_query(query)
        hits = ann.ann_query(conn, model, blob, KNN_K) or []
        out[query] = [[symbol_id, similarity] for symbol_id, similarity in hits]
    return out


def check_invariants(summary: dict, embeddings: list, mv: list, knn: dict) -> None:
    if summary["embedded"] <= 0 or summary["total"] <= 0:
        raise RuntimeError(f"empty embed run: {summary}")
    if summary["reaped"] != 2:
        raise RuntimeError(f"ghost rows not reaped once each: reaped={summary['reaped']}")
    if not any(len(row[4]) == 2048 for row in embeddings):
        raise RuntimeError("no chunk exercised the 2048-char truncation")
    kinds = {row[2] for row in mv}
    if kinds != {"name", "docstring"}:
        raise RuntimeError(f"expected both mv kinds, got {kinds}")
    if any(not hits for hits in knn.values()):
        raise RuntimeError("a KNN probe returned no rows")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    from cairn.graph import ann_index as ann
    from cairn.graph import embeddings as emb
    from cairn.graph.builder import build_graph
    from cairn.graph.schema import get_db

    scratch = Path(tempfile.mkdtemp(prefix="cairn-rust-2f-"))
    corpus, home = stage_scratch(scratch)
    saved = pin_env(corpus, home)
    db = scratch / "store.kg"
    try:
        emb.reset_backend_cache()
        build_summary = build_graph(workspace=str(corpus), db_path=str(db), verbose=False)
        print(f"build: symbols={build_summary.get('symbols')} files={build_summary.get('files')}")

        model = emb.current_model()
        conn = get_db(str(db))
        try:
            insert_ghost_rows(conn, model)

            # Freeze the pre-embed store + corpus for the Rust side.
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(db, CORPUS_STORE)
            if CORPUS_DIR.exists():
                shutil.rmtree(CORPUS_DIR)
            shutil.copytree(corpus, CORPUS_DIR)

            summary = emb.embed_all(conn, reap_orphans=True, multivector=True)
            print(f"embed: {summary}")
            base_idx = ann.rebuild_index(conn, model)
            mv_idx = ann.rebuild_index(conn, model, source="embeddings_mv")
            print(f"ann: base={base_idx} mv={mv_idx}")
            embeddings, mv, term_df = export_rows(conn)
            knn = knn_captures(conn, model)
            vec_counts = {
                ann._table_name(model): conn.execute(
                    f"SELECT COUNT(*) FROM {ann._table_name(model)}"
                ).fetchone()[0],
                ann._table_name(model, "embeddings_mv"): conn.execute(
                    f"SELECT COUNT(*) FROM {ann._table_name(model, 'embeddings_mv')}"
                ).fetchone()[0],
            }
        finally:
            conn.close()

        check_invariants(summary, embeddings, mv, knn)
        golden = {
            "model": model,
            "summary": {
                "embedded": summary["embedded"],
                "attempted": summary["attempted"],
                "failed_batches": summary.get("failed_batches", 0),
                "skipped": summary["skipped"],
                "total": summary["total"],
                "reaped": summary.get("reaped", 0),
                "mv_embedded": summary.get("mv_embedded"),
            },
            "vec_counts": vec_counts,
            "embeddings": embeddings,
            "embeddings_mv": mv,
            "term_df": term_df,
            "knn": knn,
        }
        GOLDEN_JSON.write_text(json.dumps(golden, indent=1) + "\n", encoding="utf-8")

        (OUT_DIR / "README.md").write_text(
            "# 2f embeddings-build fixtures\n"
            "\n"
            "- `embed-corpus.kg` -- pre-embed store over the `corpus/` fixture\n"
            "  (built by the Python build path; carries two orphan 'ghost-sym'\n"
            "  rows the embed pass must reap).\n"
            "- `embed-golden.json` -- the `cairn embed` result over that store\n"
            "  (hash backend): summary counts, per-row embeddings/embeddings_mv/\n"
            "  term_df content (embedded_at excluded), per-model vec0 row counts,\n"
            "  and sample vec0 KNN hits for the fixed probe queries.\n"
            "\n"
            "Regenerate with `.venv/bin/python scripts/rust_migration/embed_golden.py`.\n"
            "The Rust side is `crates/cairn-build/tests/embeddings_parity.rs`; it\n"
            "embeds a copy of `embed-corpus.kg` and must reproduce every exported\n"
            "value ((floats within 1e-6).\n",
            encoding="utf-8",
        )
        print(json.dumps({
            "golden": str(GOLDEN_JSON.relative_to(REPO_ROOT)),
            "embeddings": len(embeddings),
            "embeddings_mv": len(mv),
            "term_df": len(term_df),
            "vec_counts": vec_counts,
        }, indent=2))
        return 0
    finally:
        restore_env(saved)
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
