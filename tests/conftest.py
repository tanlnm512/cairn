"""Shared pytest fixtures for the codegraph test suite.

Consolidates the duplicated setup that previously appeared per-test-file:

* ``fresh_db``  -- an in-memory SQLite connection with the full schema
  (``_apply_schema``) already applied, Row factory enabled, FKs ON. Each
  test gets its own isolated DB; nothing is shared between tests.

* ``hash_backend`` -- forces the dep-free hash embedder
  (``CODEGRAPH_EMBED_BACKEND=hash``) and resets the cached backend before
  and after the test, so semantic-stack tests don't need torch / a model
  download. Apply with ``@pytest.fixture(autouse=True)`` per-test, or just
  request the fixture by name where needed.

Tests that need specific symbol/file rows still seed them locally -- the
fixture only removes the boilerplate of creating the connection and running
schema setup, which was duplicated 6x.
"""
from __future__ import annotations

import sqlite3

import pytest

from codegraph.graph.schema import _apply_schema


@pytest.fixture
def fresh_db() -> sqlite3.Connection:
    """A fresh in-memory SQLite connection with the full graph schema applied.

    Row factory is set. Foreign keys are LEFT OFF -- this matches what every
    per-file fixture did before consolidation (``_apply_schema`` alone does
    not enable FK; only ``schema.get_db()`` does). Some tests delete parent
    rows that have child references (e.g. embeddings referencing a symbol
    they then DELETE) and rely on FK being off to assert reap behavior;
    turning it on here would silently break those.

    Callers that need FK on can set it themselves via
    ``conn.execute("PRAGMA foreign_keys = ON")``.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def hash_backend(monkeypatch):
    """Force the dep-free hash embedder and reset the cached backend.

    Use as ``autouse=True`` in a test module that exercises the semantic
    stack but doesn't want to depend on sentence-transformers/torch. The
    cache reset on entry AND exit is what makes consecutive tests see a
    consistent backend even if an earlier test changed the env var.
    """
    monkeypatch.setenv("CODEGRAPH_EMBED_BACKEND", "hash")
    from codegraph.graph import embeddings as emb

    emb.reset_backend_cache()
    yield
    emb.reset_backend_cache()
