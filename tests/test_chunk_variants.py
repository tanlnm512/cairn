"""T013: field-dropout chunk variants + explicit recipe param (FR-002, D-004).

Pins four things:

* the existing A/B/C outputs byte-for-byte on a fixture symbol (adding the
  dropout variants must not move them),
* each new variant's shape (what it drops, what it keeps),
* the TC-008 identity floor across ALL selectable variants -- qualified name,
  file path, signature, and docstring appear in every chunk,
* the recipe param on ``embed_all``/``embed_symbols``: an explicit string
  beats ``CAIRN_CHUNK_VARIANT``, ``None`` falls back to it, and neither path
  ever mutates the environment (D-008 doctrine).
"""
from __future__ import annotations

import os

import pytest

from cairn.graph.embeddings import CHUNK_VARIANTS, chunk_for_symbol

# Apply the shared hash embedder to every test in this module.
pytestmark = pytest.mark.usefixtures("hash_backend")

_SIG = "def process(self, input_str: str) -> str:"


def _rich_row() -> dict:
    """One symbol with every chunk field populated."""
    return {
        "name": "process",
        "kind": "method",
        "qualified_name": "User.process",
        "docstring": "Process user data.",
        "parameters": '[{"name": "input_str", "type": "str"}]',
        "return_type": "str",
        "parent_scope": "User",
        "imports_summary": "import os",
        "body": "x = 1\nreturn f'processed_{input_str}'",
        "file_path": "src/user/Profile.py",
    }


# ---------------------------------------------------------------------------
# Existing variants: pinned byte-for-byte.
# ---------------------------------------------------------------------------


def test_existing_variants_pinned_byte_exact():
    row = _rich_row()
    assert chunk_for_symbol(row, signature=_SIG, variant="A") == (
        "File: src/user/Profile.py\n"
        "Enclosing Scope: User\n"
        "Imports: import os\n"
        "method User.process\n"
        "def process(self, input_str: str) -> str:\n"
        "Process user data."
    )
    assert chunk_for_symbol(row, signature=_SIG, variant="B") == (
        "File: src/user/Profile.py\n"
        "Enclosing Scope: User\n"
        "Imports: import os\n"
        "method User.process\n"
        "Signature: def process(self, input_str: str) -> str:\n"
        'Parameters: [{"name": "input_str", "type": "str"}]\n'
        "Return Type: str\n"
        "Docstring: Process user data."
    )
    assert chunk_for_symbol(row, signature=_SIG, variant="C") == (
        "File: src/user/Profile.py\n"
        "Enclosing Scope: User\n"
        "Imports: import os\n"
        "method User.process\n"
        "Signature: def process(self, input_str: str) -> str:\n"
        'Parameters: [{"name": "input_str", "type": "str"}]\n'
        "Return Type: str\n"
        "Docstring: Process user data.\n"
        "Body:\nx = 1\nreturn f'processed_{input_str}'"
    )


# ---------------------------------------------------------------------------
# New variants: shapes.
# ---------------------------------------------------------------------------


def test_b_no_scope_drops_enclosing_scope_and_imports_keeps_file():
    chunk = chunk_for_symbol(_rich_row(), signature=_SIG, variant="B_NO_SCOPE")
    assert "File: src/user/Profile.py" in chunk  # floor: file path stays
    assert "Enclosing Scope:" not in chunk
    assert "Imports:" not in chunk
    assert "Signature: def process" in chunk
    assert "Parameters: [{" in chunk
    assert "Return Type: str" in chunk
    assert "Docstring: Process user data." in chunk


def test_b_no_sig_drops_params_and_return_keeps_signature_floor():
    chunk = chunk_for_symbol(_rich_row(), signature=_SIG, variant="B_NO_SIG")
    assert "Parameters:" not in chunk
    assert "Return Type:" not in chunk
    # TC-008 floor: the signature itself is never dropped.
    assert "Signature: def process(self, input_str: str) -> str:" in chunk
    assert "Enclosing Scope: User" in chunk
    assert "Docstring: Process user data." in chunk


def test_b_identities_is_the_minimal_legal_variant():
    chunk = chunk_for_symbol(_rich_row(), signature=_SIG, variant="B_IDENTITIES")
    assert chunk == (
        "File: src/user/Profile.py\n"
        "method User.process\n"
        "Signature: def process(self, input_str: str) -> str:\n"
        "Docstring: Process user data."
    )


def test_c_trim_is_b_plus_half_budget_body():
    row = _rich_row()
    row["body"] = "x" * 4000  # far over the default 512*4 = 2048-char budget
    b_chunk = chunk_for_symbol(row, signature=_SIG, variant="B")
    chunk = chunk_for_symbol(row, signature=_SIG, variant="C_TRIM")
    # Exactly B + the body prefix capped at half the truncation budget.
    assert chunk == b_chunk + "\nBody:\n" + "x" * 1024


def test_variant_values_are_case_normalized_like_the_env_var():
    row = _rich_row()
    assert chunk_for_symbol(row, signature=_SIG, variant="b_identities") == (
        chunk_for_symbol(row, signature=_SIG, variant="B_IDENTITIES")
    )


# ---------------------------------------------------------------------------
# TC-008 identity floor: every selectable variant keeps the four fields.
# ---------------------------------------------------------------------------


def test_identity_floor_every_variant():
    row = _rich_row()
    for v in CHUNK_VARIANTS:
        chunk = chunk_for_symbol(row, signature=_SIG, variant=v)
        assert "User.process" in chunk, f"{v}: qualified name missing"
        assert "src/user/Profile.py" in chunk, f"{v}: file path missing"
        assert _SIG in chunk, f"{v}: signature missing"
        assert "Process user data." in chunk, f"{v}: docstring missing"


def test_chunk_variants_registry_is_complete():
    # The registry drives the floor test above; every documented variant must
    # be listed and each must produce distinct output on the fixture (the
    # body must exceed C_TRIM's half budget, else C and C_TRIM coincide).
    assert CHUNK_VARIANTS == (
        "A", "B", "C", "B_NO_SCOPE", "B_NO_SIG", "B_IDENTITIES", "C_TRIM",
    )
    row = _rich_row()
    row["body"] = "z" * 1500
    chunks = {
        chunk_for_symbol(row, signature=_SIG, variant=v) for v in CHUNK_VARIANTS
    }
    assert len(chunks) == len(CHUNK_VARIANTS)


# ---------------------------------------------------------------------------
# Recipe param threading through embed_all / embed_symbols.
# ---------------------------------------------------------------------------


def _seed_one_symbol(conn) -> None:
    """One fully-populated symbol; file path points nowhere on purpose so the
    disk-read signature step no-ops (chunk sections from DB columns still
    appear, which is what these tests assert on)."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r', 'r', '/tmp/r')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) "
        "VALUES (1, 'r', 'nowhere/UserRepo.py', 'python')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, "
        "line_start, line_end, parameters, return_type, parent_scope, "
        "imports_summary, body) "
        "VALUES (1, 1, 'process', 'method', 'User.process', 'Process user data.', "
        "1, 3, '[{\"name\": \"input_str\", \"type\": \"str\"}]', 'str', 'User', "
        "'import os', 'x = 1')"
    )
    conn.commit()


def _stored_chunk(conn) -> str:
    r = conn.execute(
        "SELECT chunk FROM embeddings WHERE symbol_id = '1'"
    ).fetchone()
    assert r is not None, "expected the seeded symbol to be embedded"
    return r["chunk"]


def test_embed_all_explicit_variant_beats_env_without_mutation(fresh_db, monkeypatch):
    from cairn.graph import embeddings as emb

    _seed_one_symbol(fresh_db)
    monkeypatch.setenv("CAIRN_CHUNK_VARIANT", "B")

    summary = emb.embed_all(fresh_db, variant="B_NO_SCOPE")

    assert summary["embedded"] == 1
    chunk = _stored_chunk(fresh_db)
    # The stored chunk is the spy: it was built with the explicit variant
    # (env's B would have kept both scope lines).
    assert "Enclosing Scope:" not in chunk
    assert "Imports:" not in chunk
    assert "Parameters: [{" in chunk
    assert "Docstring: Process user data." in chunk
    # D-008: the override never mutates the process environment.
    assert os.environ.get("CAIRN_CHUNK_VARIANT") == "B"


def test_embed_all_none_variant_falls_back_to_env(fresh_db, monkeypatch):
    from cairn.graph import embeddings as emb

    _seed_one_symbol(fresh_db)
    monkeypatch.setenv("CAIRN_CHUNK_VARIANT", "A")

    emb.embed_all(fresh_db)  # variant=None -> today's env resolution

    chunk = _stored_chunk(fresh_db)
    assert "Parameters:" not in chunk  # A drops them; B would keep them
    assert "Docstring: " not in chunk  # A's bare docstring, not B's label
    assert "Process user data." in chunk


def test_embed_symbols_explicit_variant_beats_env_without_mutation(fresh_db, monkeypatch):
    from cairn.graph import embeddings as emb

    _seed_one_symbol(fresh_db)
    monkeypatch.setenv("CAIRN_CHUNK_VARIANT", "A")

    summary = emb.embed_symbols(fresh_db, ["1"], variant="B_IDENTITIES")

    assert summary["embedded"] == 1
    chunk = _stored_chunk(fresh_db)
    assert "Enclosing Scope:" not in chunk
    assert "Imports:" not in chunk
    assert "Parameters:" not in chunk
    assert "Return Type:" not in chunk
    assert "Docstring: Process user data." in chunk  # floor intact
    assert os.environ.get("CAIRN_CHUNK_VARIANT") == "A"


def test_embed_all_variant_reembed_flips_content_hash(fresh_db, monkeypatch):
    """The staleness seam T014 drives: switching the recipe changes every
    chunk hash, so the same DB re-embeds under the new variant."""
    from cairn.graph import embeddings as emb

    _seed_one_symbol(fresh_db)
    monkeypatch.delenv("CAIRN_CHUNK_VARIANT", raising=False)

    first = emb.embed_all(fresh_db, variant="B")
    assert first["embedded"] == 1
    again_same = emb.embed_all(fresh_db, variant="B")
    assert again_same["embedded"] == 0  # content_hash match -> idempotent skip
    switched = emb.embed_all(fresh_db, variant="B_NO_SCOPE")
    assert switched["embedded"] == 1  # recipe change -> full re-embed
    assert "Imports:" not in _stored_chunk(fresh_db)
    # Explicit variant never leaks an env var (hermetic conftest cleared them).
    assert "CAIRN_CHUNK_VARIANT" not in os.environ
