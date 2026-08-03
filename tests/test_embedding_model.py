import os
import tempfile
from pathlib import Path

import pytest

from codegraph.graph.embeddings import current_model, purge_stale_models
from codegraph.graph.schema import get_db


def _semantic_extra_installed() -> bool:
    """True iff sentence-transformers (the [semantic] extra) is importable.

    `current_model()` only returns the configured local model name when the
    local backend is actually usable -- without sentence_transformers the
    backend falls back to 'hash' and the model stamp becomes 'hash-256-v1',
    so the env-var-fallback assertions below can't hold. Skip in the default
    (extra-free) install; run with `pip install 'cg-intel[semantic]'` to exercise.
    """
    try:
        import sentence_transformers  # noqa: F401

        return True
    except ImportError:
        return False


# Skip the whole module when the [semantic] extra isn't installed: both
# test_knowledge_model_falls_back_to_code_model (needs the local backend to
# honor CODEGRAPH_EMBED_LOCAL_MODEL) and the model-name assertions depend on it.
# (The other two tests in this file -- purge_stale_models, fp16_flag -- don't
# need it, so the skip is applied per-test below rather than via pytestmark.)
_NEEDS_SEMANTIC = pytest.mark.skipif(
    not _semantic_extra_installed(),
    reason="requires the [semantic] extra (sentence-transformers); install with pip install 'cg-intel[semantic]'",
)


@_NEEDS_SEMANTIC
def test_knowledge_model_falls_back_to_code_model(monkeypatch):
    # monkeypatch.delenv accepts raising=False so a missing key is fine.
    monkeypatch.setenv("CODEGRAPH_EMBED_LOCAL_MODEL", "BAAI/bge-m3")
    monkeypatch.delenv("CODEGRAPH_EMBED_KNOWLEDGE_MODEL", raising=False)

    assert current_model("code") == "BAAI/bge-m3"
    assert current_model("knowledge") == "BAAI/bge-m3"

    monkeypatch.setenv("CODEGRAPH_EMBED_KNOWLEDGE_MODEL", "some-other-model")
    assert current_model("knowledge") == "some-other-model"
    assert current_model("code") == "BAAI/bge-m3"


def test_purge_stale_models_removes_only_other_models():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        conn = get_db(db_path)

        conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'r1', '/p1')")
        conn.execute("INSERT INTO files (id, path, repo_id, hash, line_count, language) VALUES ('f1', 'a.py', 'r1', 'h', 5, 'python')")
        conn.execute("INSERT INTO symbols (id, file_id, name, kind) VALUES ('s1', 'f1', 'sym1', 'function')")

        # Insert active model row and stale model row
        conn.execute("INSERT INTO embeddings (symbol_id, model, vec, dim, chunk) VALUES ('s1', 'BAAI/bge-m3', X'00', 1024, 'c1')")
        conn.execute("INSERT INTO embeddings (symbol_id, model, vec, dim, chunk) VALUES ('s1', 'old-model-v1', X'00', 384, 'c1')")
        conn.commit()

        purged_count = purge_stale_models(conn, active_model="BAAI/bge-m3")
        assert purged_count == 1

        remaining = conn.execute("SELECT model FROM embeddings").fetchall()
        assert len(remaining) == 1
        assert remaining[0][0] == "BAAI/bge-m3"


def test_fp16_flag_env(monkeypatch):
    monkeypatch.setenv("CODEGRAPH_EMBED_FP16", "1")
    assert os.environ.get("CODEGRAPH_EMBED_FP16") == "1"
