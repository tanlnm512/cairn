"""Alias preflight gate tests (FR-005): the embed writers parity-check stored
rows under ``CAIRN_EMBED_MODEL_STAMP`` before the first INSERT.

Unit tests stub ``check_parity`` (verdict injection) or the server client;
two integration tests run the real sampler through a loopback stub server
(migration-with-alias keeps search correctness; a different model aborts).
"""
from __future__ import annotations

import http.server
import json
import math
import os
import struct
import threading
from unittest import mock

import pytest

from cairn.graph import embed_ladder
from cairn.graph import embeddings as emb

DIM = 8
ALIAS = "server/127.0.0.1:8000/bge-m3"
VEC = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]


@pytest.fixture(autouse=True)
def _server_env(monkeypatch):
    """Env-agnostic baseline: scrub embed config, pin the server family."""
    for name in list(os.environ):
        if name.startswith("CAIRN_EMBED_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
    monkeypatch.setenv("CAIRN_EMBED_BASE_URL", "http://127.0.0.1:9/v1")
    emb.reset_backend_cache()
    yield
    emb.reset_backend_cache()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _blob(vec):
    return struct.pack(f"<{len(vec)}f", *vec)


def _decode(blob):
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob[: n * 4]))


def _unit(vec):
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


def _orthogonal(vec):
    v = _unit(vec)
    e = [1.0] + [0.0] * (len(vec) - 1)
    dot = sum(a * b for a, b in zip(e, v))
    u = [a - dot * b for a, b in zip(e, v)]
    norm = math.sqrt(sum(x * x for x in u))
    if norm < 1e-9:
        e = [0.0, 1.0] + [0.0] * (len(vec) - 2)
        dot = sum(a * b for a, b in zip(e, v))
        u = [a - dot * b for a, b in zip(e, v)]
        norm = math.sqrt(sum(x * x for x in u))
    return [x / norm for x in u]


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb)


def _seed_symbol(conn, sid):
    conn.execute(
        "INSERT OR IGNORE INTO repos (id, name, path) VALUES ('r', 'r', '/tmp/r')"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (?, 'r', ?, 'py')",
        (f"f-{sid}", f"/repo/{sid}.py"),
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start) "
        "VALUES (?, ?, 'foo', 'foo', 'function', 1)",
        (sid, f"f-{sid}"),
    )
    conn.commit()


def _satisfy_alias_rows(conn, stamp, vec=None):
    """Insert rows under ``stamp`` whose content_hash matches the chunk
    embed_all computes, so every symbol counts as fresh. Returns
    {chunk_text: vec}."""
    vec = _unit(vec or VEC)
    rows = conn.execute(
        """SELECT s.id, s.name, s.qualified_name, s.kind, s.docstring,
                  s.line_start, s.parameters, s.return_type,
                  s.parent_scope, s.imports_summary, s.body,
                  f.path AS file_path, f.repo_id AS repo,
                  e.content_hash AS existing_hash
           FROM symbols s
           JOIN files f ON s.file_id = f.id
           LEFT JOIN embeddings e ON e.symbol_id = s.id AND e.model = ?
           WHERE s.kind IS NOT NULL
           ORDER BY s.id""",
        (stamp,),
    ).fetchall()
    signatures = emb._signature_lines_for_rows(rows)
    chunks = {}
    for r in rows:
        chunk = emb.chunk_for_symbol(r, signature=signatures.get(r["id"]))
        chunks[chunk] = vec
        conn.execute(
            "INSERT INTO embeddings "
            "(symbol_id, model, dim, vec, chunk, content_hash, embedded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, '2026-01-01T00:00:00+00:00')",
            (r["id"], stamp, len(vec), _blob(vec), chunk, emb._chunk_hash(chunk)),
        )
    conn.commit()
    return chunks


def _all_rows(conn):
    return [
        tuple(r)
        for r in conn.execute(
            "SELECT symbol_id, model, dim, vec, chunk, content_hash "
            "FROM embeddings ORDER BY symbol_id"
        )
    ]


def _echo_server_client(chunks):
    """Server-client stub serving each sampled chunk its stored vector."""
    calls = []

    def client(texts):
        calls.append(list(texts))
        return [_blob(chunks[t]) for t in texts], DIM

    client.calls = calls
    return client


# ---------------------------------------------------------------------------
# (a) alias + parity pass: zero re-embeds, rows resolve under the alias
# ---------------------------------------------------------------------------


def test_alias_pass_writes_zero_rows_and_rows_resolve(fresh_db, monkeypatch):
    _seed_symbol(fresh_db, "s1")
    _seed_symbol(fresh_db, "s2")
    chunks = _satisfy_alias_rows(fresh_db, ALIAS)
    monkeypatch.setenv("CAIRN_EMBED_MODEL_STAMP", ALIAS)
    client = _echo_server_client(chunks)
    monkeypatch.setattr(emb, "_embed_server", client)

    before = _all_rows(fresh_db)
    summary = emb.embed_all(fresh_db)

    assert summary["model"] == ALIAS
    assert summary["embedded"] == 0
    assert summary["skipped"] == 2
    assert _all_rows(fresh_db) == before, "pass must re-embed nothing"
    # the only embed traffic was the parity sample (real check_parity)
    assert len(client.calls) == 1
    assert sorted(client.calls[0]) == sorted(chunks)
    # stamp-driven machinery resolves the rows under the alias
    assert emb.embed_count(fresh_db) == 2


# ---------------------------------------------------------------------------
# (b) parity fail: hard abort BEFORE any INSERT, quoting the measured mean
# ---------------------------------------------------------------------------


def test_fail_aborts_embed_all_before_insert(fresh_db, monkeypatch):
    _seed_symbol(fresh_db, "s1")
    _satisfy_alias_rows(fresh_db, ALIAS)
    monkeypatch.setenv("CAIRN_EMBED_MODEL_STAMP", ALIAS)
    verdict = embed_ladder.ParityResult(
        2, 0.4321, True, False, "mean_cosine 0.4321 below gate 0.98"
    )
    probe = mock.Mock(return_value=verdict)
    monkeypatch.setattr(embed_ladder, "check_parity", probe)

    before = _all_rows(fresh_db)
    with pytest.raises(RuntimeError) as ei:
        emb.embed_all(fresh_db)

    assert "0.4321" in str(ei.value), "must quote the measured mean cosine"
    assert probe.call_count == 1
    assert _all_rows(fresh_db) == before, "abort must precede any INSERT"


def test_fail_aborts_each_writer(fresh_db, monkeypatch):
    _seed_symbol(fresh_db, "s1")
    _satisfy_alias_rows(fresh_db, ALIAS)
    monkeypatch.setenv("CAIRN_EMBED_MODEL_STAMP", ALIAS)
    verdict = embed_ladder.ParityResult(
        1, 0.1, True, False, "mean_cosine 0.1000 below gate 0.98"
    )
    probe = mock.Mock(return_value=verdict)
    monkeypatch.setattr(embed_ladder, "check_parity", probe)
    bundle = mock.Mock()

    before = _all_rows(fresh_db)
    with pytest.raises(RuntimeError):
        emb.embed_symbols(fresh_db, ["s1"])
    with pytest.raises(RuntimeError):
        emb.embed_knowledge(fresh_db, bundle)
    with pytest.raises(RuntimeError):
        emb.embed_memory_concepts(fresh_db, bundle, ["memory/tribal/x"])
    # once-per-process-per-stamp: the cached verdict serves every writer
    assert probe.call_count == 1
    assert _all_rows(fresh_db) == before


# ---------------------------------------------------------------------------
# (c) dim-mismatch verdict: message names both dims
# ---------------------------------------------------------------------------


def test_dim_mismatch_message_names_both_dims(fresh_db, monkeypatch):
    monkeypatch.setenv("CAIRN_EMBED_MODEL_STAMP", ALIAS)
    verdict = embed_ladder.ParityResult(
        2, None, False, False, "dim_mismatch stored=8 server=16"
    )
    monkeypatch.setattr(embed_ladder, "check_parity", mock.Mock(return_value=verdict))

    with pytest.raises(RuntimeError) as ei:
        emb.embed_all(fresh_db)

    assert "stored=8" in str(ei.value)
    assert "server=16" in str(ei.value)


# ---------------------------------------------------------------------------
# (d) no alias (or alias off the server family) -> gate is a no-op
# ---------------------------------------------------------------------------


def test_no_alias_gate_is_noop(fresh_db, monkeypatch):
    monkeypatch.delenv("CAIRN_EMBED_MODEL_STAMP", raising=False)
    probe = mock.Mock(side_effect=AssertionError("check_parity must not run"))
    monkeypatch.setattr(embed_ladder, "check_parity", probe)

    summary = emb.embed_all(fresh_db)

    assert summary["embedded"] == 0
    probe.assert_not_called()


def test_alias_ignored_off_server_family(fresh_db, monkeypatch):
    monkeypatch.setenv("CAIRN_EMBED_MODEL_STAMP", ALIAS)
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
    emb.reset_backend_cache()
    probe = mock.Mock(side_effect=AssertionError("check_parity must not run"))
    monkeypatch.setattr(embed_ladder, "check_parity", probe)

    summary = emb.embed_all(fresh_db)

    assert summary["embedded"] == 0
    probe.assert_not_called()


# ---------------------------------------------------------------------------
# (e) verdict cached per process per stamp; reset_backend_cache() clears it
# ---------------------------------------------------------------------------


def test_verdict_cached_and_reset_clears_it(fresh_db, monkeypatch):
    monkeypatch.setenv("CAIRN_EMBED_MODEL_STAMP", ALIAS)
    verdict = embed_ladder.ParityResult(0, None, True, True, "parity_ok")
    probe = mock.Mock(return_value=verdict)
    monkeypatch.setattr(embed_ladder, "check_parity", probe)

    emb.embed_all(fresh_db)
    emb.embed_all(fresh_db)
    emb.embed_symbols(fresh_db, ["missing"])
    assert probe.call_count == 1

    emb.reset_backend_cache()
    emb.embed_all(fresh_db)
    assert probe.call_count == 2


# ---------------------------------------------------------------------------
# (f) alias set, zero stored rows under it -> vacuous pass, writes under alias
# ---------------------------------------------------------------------------


def test_alias_with_zero_rows_vacuous_pass_writes_under_alias(fresh_db, monkeypatch):
    _seed_symbol(fresh_db, "s1")
    monkeypatch.setenv("CAIRN_EMBED_MODEL_STAMP", ALIAS)
    calls = []

    def client(texts):
        calls.append(list(texts))
        return [_blob(_unit(VEC)) for _ in texts], DIM

    monkeypatch.setattr(emb, "_embed_server", client)
    probe = mock.Mock(wraps=embed_ladder.check_parity)
    monkeypatch.setattr(embed_ladder, "check_parity", probe)

    summary = emb.embed_all(fresh_db)

    assert probe.call_count == 1
    assert summary["model"] == ALIAS
    assert summary["embedded"] == 1
    # the vacuous sampler never embedded anything; only the stale symbol did
    assert len(calls) == 1
    assert {r[0] for r in fresh_db.execute("SELECT model FROM embeddings")} == {ALIAS}


# ---------------------------------------------------------------------------
# Integration: real sampler + real server client over loopback HTTP
# ---------------------------------------------------------------------------


class _StubEmbedServer:
    """Loopback OpenAI-compatible /v1/embeddings stand-in on an ephemeral port."""

    def __init__(self, vec_for):
        self.requests = []
        outer = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                outer.requests.append(body)
                payload = json.dumps(
                    {
                        "data": [
                            {"index": i, "embedding": list(vec_for(t))}
                            for i, t in enumerate(body["input"])
                        ]
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(
            target=self._httpd.serve_forever, daemon=True, kwargs={"poll_interval": 0.05}
        ).start()

    @property
    def base_url(self):
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/v1"

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def stub_server():
    made = []

    def _make(vec_for):
        server = _StubEmbedServer(vec_for)
        made.append(server)
        return server

    yield _make
    for server in made:
        server.close()


def test_migration_with_alias_keeps_search_correct(fresh_db, monkeypatch, stub_server):
    _seed_symbol(fresh_db, "s1")
    _seed_symbol(fresh_db, "s2")
    chunks = _satisfy_alias_rows(fresh_db, ALIAS)
    server = stub_server(lambda text: chunks[text])
    monkeypatch.setenv("CAIRN_EMBED_MODEL_STAMP", ALIAS)
    monkeypatch.setenv("CAIRN_EMBED_BASE_URL", server.base_url)
    emb.reset_backend_cache()

    before = _all_rows(fresh_db)
    summary = emb.embed_all(fresh_db)

    assert summary["model"] == ALIAS
    assert summary["embedded"] == 0
    assert _all_rows(fresh_db) == before, "migration keeps the stored vectors"
    # the query leg rides the same server and lands on the stored vector space
    chunk0 = sorted(chunks)[0]
    blob, dim = emb.embed_query(chunk0)
    assert dim == DIM
    assert _cosine(_decode(blob), chunks[chunk0]) == pytest.approx(1.0)


def test_different_model_under_alias_aborts(fresh_db, monkeypatch, stub_server):
    _seed_symbol(fresh_db, "s1")
    _satisfy_alias_rows(fresh_db, ALIAS)
    other = _orthogonal(VEC)
    server = stub_server(lambda text: other)
    monkeypatch.setenv("CAIRN_EMBED_MODEL_STAMP", ALIAS)
    monkeypatch.setenv("CAIRN_EMBED_BASE_URL", server.base_url)
    emb.reset_backend_cache()

    before = _all_rows(fresh_db)
    with pytest.raises(RuntimeError) as ei:
        emb.embed_all(fresh_db)

    assert "0.0000" in str(ei.value)
    assert _all_rows(fresh_db) == before, "abort must write nothing"


def test_file_layer_stamp_triggers_parity_gate(fresh_db, monkeypatch, _server_env):
    """A stamp set only via config.json must arm the FR-005 gate exactly like
    the env var (file-layer aliases are the dashboard's persistence path)."""
    from cairn import paths as cairn_paths

    monkeypatch.delenv("CAIRN_EMBED_MODEL_STAMP", raising=False)
    emb.reset_backend_cache()
    stamp = "BAAI/bge-m3"
    conn = fresh_db
    _seed_symbol(conn, "s1")
    _satisfy_alias_rows(conn, stamp)

    gate_calls = []
    pass_verdict = embed_ladder.ParityResult(
        sampled=1, mean_cosine=1.0, dim_match=True, passed=True, reason="parity_ok"
    )

    def _spy_check(c, s, **k):
        gate_calls.append(s)
        return pass_verdict

    monkeypatch.setattr(embed_ladder, "check_parity", _spy_check)
    cairn_paths.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    cairn_paths.CONFIG_FILE.write_text(json.dumps({"CAIRN_EMBED_MODEL_STAMP": stamp}))
    cairn_paths.reset_config_cache()
    emb.reset_backend_cache()

    assert emb.current_model() == stamp
    emb.embed_all(conn)
    assert gate_calls == [stamp], "file-layer stamp must run the alias preflight"
