"""CliRunner tests for `cairn embed --adopt-server-model` (FR-012).

The flag validates a parity-verified server candidate through the ladder and
then runs the embed under the ALIAS BINDING: rows keep the stored corpus stamp
(never restamped) while requests route to the adopted model id. The ladder's
HTTP seams are monkeypatched (same seams tests/test_embed_ladder.py uses); the
embed writer is stubbed to capture the stamp/routing a real embed_all would
have used (its model column is current_model(), its request id _server_model()).
"""
from __future__ import annotations

import math
import sqlite3
import struct
from urllib.parse import urlsplit

import pytest
from click.testing import CliRunner

from cairn.cli import main as cli_main
from cairn.graph import embed_ladder
from cairn.graph import embeddings as emb

DIM = 8
# Never contacted: every ladder HTTP seam is stubbed in these tests.
BASE = "http://127.0.0.1:9/v1"
STORED_VEC = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]


@pytest.fixture(autouse=True)
def _fresh_state():
    """Ladder verdicts and session adoptions are process-cached; every test
    gets a clean slate and leaves one behind."""
    emb.reset_backend_cache()
    yield
    emb.reset_backend_cache()


def _blob(vec):
    return struct.pack(f"<{len(vec)}f", *vec)


def _unit(vec):
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


def _server_env(monkeypatch, model="gone-model"):
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
    monkeypatch.setenv("CAIRN_EMBED_BASE_URL", BASE)
    monkeypatch.setenv("CAIRN_EMBED_SERVER_MODEL", model)
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")  # keep the ANN arm out
    emb.reset_backend_cache()


def _stamp_for(model):
    return f"server/{urlsplit(BASE).netloc}/{model}"


def _invoke(*args):
    return CliRunner().invoke(cli_main, ["embed", *args], catch_exceptions=False)


def _flat(result) -> str:
    # rich wraps at console width; collapse whitespace so substring
    # assertions never straddle a wrap boundary.
    return " ".join(result.output.split())


def _seed_rows(db_path, stamp, n=3, extra_stamp=None):
    """Seed stored embedding rows (FK off: only the model/stamp column
    matters to the alias binding, no symbol parents needed)."""
    from cairn.graph.schema import get_db as open_graph_db

    open_graph_db(db_path).close()  # create/migrate the schema first
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    rows = [(stamp, f"s{i}") for i in range(n)]
    if extra_stamp:
        rows.append((extra_stamp, "sx"))
    for model, symbol_id in rows:
        conn.execute(
            "INSERT INTO embeddings (symbol_id, model, dim, vec, chunk, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                symbol_id,
                model,
                DIM,
                _blob(STORED_VEC),
                f"chunk text for {symbol_id}",
                f"hash-{symbol_id}",
            ),
        )
    conn.commit()
    conn.close()


def _stamp_counts(db_path):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT model, COUNT(*) FROM embeddings GROUP BY model"
    ).fetchall()
    conn.close()
    return {model: count for model, count in rows}


def _stub_ladder(monkeypatch, candidates=("cand-a",), parity="pass"):
    """Deterministic ladder without HTTP: canned listing; every candidate
    embed returns the stored vectors (parity 1.0) or zero vectors (fail);
    rung 2 has no local fallback."""
    served = _unit(STORED_VEC) if parity == "pass" else [0.0] * DIM

    def fake_embed(texts, model_id):
        return [_blob(served)] * len(texts), DIM

    monkeypatch.setattr(embed_ladder, "_embed_with_model", fake_embed)
    monkeypatch.setattr(
        embed_ladder, "_fetch_model_listing", lambda: list(candidates)
    )
    monkeypatch.setattr(embed_ladder, "_sentence_transformers_available", lambda: False)


def _capture_embed_all(monkeypatch, captured):
    """Record the stamp/routing a real embed_all would have used."""
    def fake_embed_all(conn, **kwargs):
        captured["model"] = emb.current_model()
        captured["routed"] = emb._server_model()
        return {
            "model": captured["model"],
            "embedded": 0,
            "skipped": 0,
            "total": 0,
            "reaped": 0,
        }

    monkeypatch.setattr(emb, "embed_all", fake_embed_all)


def _forbid_embed_all(monkeypatch):
    def bomb(*args, **kwargs):
        raise AssertionError("embed_all must not run on a refused adoption")

    monkeypatch.setattr(emb, "embed_all", bomb)


# (a) explicit MODEL_ID + parity-passing candidate: embed runs under the
# stored stamp with the adopted id routed, and the permanence instruction
# (the exact export line) is printed. Also covers (f): no row is restamped.


def test_explicit_model_embeds_under_stored_stamp(monkeypatch, tmp_path):
    _server_env(monkeypatch)
    db_path = str(tmp_path / "db.sqlite")
    stamp = _stamp_for("gone-model")
    _seed_rows(db_path, stamp, n=3, extra_stamp="server/old-host/old")
    captured = {}
    _capture_embed_all(monkeypatch, captured)
    _stub_ladder(monkeypatch)

    result = _invoke("--db", db_path, "--adopt-server-model", "cand-a")

    assert result.exit_code == 0
    # alias binding: writes carry the STORED stamp, requests the adopted id
    assert captured["model"] == stamp
    assert captured["routed"] == "cand-a"
    out = _flat(result)
    assert f"export CAIRN_EMBED_MODEL_STAMP={stamp}" in out
    assert "cand-a" in out
    assert "config file" in out
    # (f) permanence act, not a restamp: every stored row keeps its stamp
    assert _stamp_counts(db_path) == {stamp: 3, "server/old-host/old": 1}


# (b) bare flag: no active adoption in-process -> the flag evaluates the
# ladder itself and adopts the parity-passing candidate.


def test_bare_flag_evaluates_and_adopts(monkeypatch, tmp_path):
    _server_env(monkeypatch)
    db_path = str(tmp_path / "db.sqlite")
    stamp = _stamp_for("gone-model")
    _seed_rows(db_path, stamp)
    captured = {}
    _capture_embed_all(monkeypatch, captured)
    _stub_ladder(monkeypatch)

    result = _invoke("--db", db_path, "--adopt-server-model")

    assert result.exit_code == 0
    assert captured["model"] == stamp
    assert captured["routed"] == "cand-a"
    assert f"export CAIRN_EMBED_MODEL_STAMP={stamp}" in _flat(result)
    assert _stamp_counts(db_path) == {stamp: 3}


# (b2) bare flag with an ACTIVE rung-1 adoption in-process: reused without a
# forced re-evaluation.


def test_bare_flag_reuses_active_rung1_adoption(monkeypatch, tmp_path):
    _server_env(monkeypatch)
    db_path = str(tmp_path / "db.sqlite")
    stamp = _stamp_for("gone-model")
    _seed_rows(db_path, stamp)
    captured = {}
    _capture_embed_all(monkeypatch, captured)
    _stub_ladder(monkeypatch)

    state = embed_ladder.evaluate_ladder(
        conn=sqlite3.connect(db_path), force=True
    )
    assert state.rung == 1 and state.adopted_model == "cand-a"

    forced = []
    real = embed_ladder.evaluate_ladder

    def spy(conn=None, force=False):
        forced.append(force)
        return real(conn=conn, force=force)

    monkeypatch.setattr(embed_ladder, "evaluate_ladder", spy)

    result = _invoke("--db", db_path, "--adopt-server-model")

    assert result.exit_code == 0
    assert forced == []  # active adoption reused; no re-evaluation
    assert captured["model"] == stamp
    assert captured["routed"] == "cand-a"


# (c) bare flag + ladder lands rung 3: exit 1 naming what the ladder found;
# the embed flow never runs.


def test_bare_flag_rung3_refuses_naming_the_verdict(monkeypatch, tmp_path):
    _server_env(monkeypatch)
    db_path = str(tmp_path / "db.sqlite")
    stamp = _stamp_for("gone-model")
    _seed_rows(db_path, stamp)
    _forbid_embed_all(monkeypatch)
    monkeypatch.setattr(embed_ladder, "_fetch_model_listing", lambda: [])
    monkeypatch.setattr(embed_ladder, "_sentence_transformers_available", lambda: False)

    result = _invoke("--db", db_path, "--adopt-server-model")

    assert result.exit_code == 1
    out = _flat(result)
    assert "No parity-verified server model candidate" in out
    assert "model_missing" in out  # the ladder's verdict
    assert _stamp_counts(db_path) == {stamp: 3}


# (d) explicit MODEL_ID that does not parity-pass: exit 1 naming why.


def test_explicit_model_that_fails_parity_refuses(monkeypatch, tmp_path):
    _server_env(monkeypatch)
    db_path = str(tmp_path / "db.sqlite")
    stamp = _stamp_for("gone-model")
    _seed_rows(db_path, stamp)
    _forbid_embed_all(monkeypatch)
    _stub_ladder(monkeypatch, parity="fail")

    result = _invoke("--db", db_path, "--adopt-server-model", "cand-a")

    assert result.exit_code == 1
    out = _flat(result)
    assert "cand-a" in out
    assert "not a parity-verified candidate" in out
    assert "parity_fail" in out  # the why
    assert _stamp_counts(db_path) == {stamp: 3}


# (d2) explicit MODEL_ID that is not the candidate the scan proved: the
# refusal names the model the ladder adopted instead.


def test_explicit_model_names_the_adopter_when_mismatched(monkeypatch, tmp_path):
    _server_env(monkeypatch)
    db_path = str(tmp_path / "db.sqlite")
    stamp = _stamp_for("gone-model")
    _seed_rows(db_path, stamp)
    _forbid_embed_all(monkeypatch)
    _stub_ladder(monkeypatch, candidates=("cand-a", "cand-b"))

    result = _invoke("--db", db_path, "--adopt-server-model", "cand-b")

    assert result.exit_code == 1
    out = _flat(result)
    assert "cand-b" in out
    assert "cand-a" in out  # the candidate parity actually proved
    assert "instead" in out


# (e) no server backend configured: the flag refuses before any embed work.


def test_refuses_non_server_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "local")
    emb.reset_backend_cache()
    _forbid_embed_all(monkeypatch)

    result = _invoke(
        "--db", str(tmp_path / "db.sqlite"), "--adopt-server-model", "cand-a"
    )

    assert result.exit_code == 1
    out = _flat(result)
    assert "--adopt-server-model" in out
    assert "server-family" in out
    assert "Embedding" not in out  # the embed flow never ran


# (f) explicit: a healthy server (no degraded state) is nothing to adopt.


def test_refuses_when_server_is_healthy(monkeypatch, tmp_path):
    _server_env(monkeypatch, model="served-model")
    db_path = str(tmp_path / "db.sqlite")
    stamp = _stamp_for("served-model")
    _seed_rows(db_path, stamp)
    _forbid_embed_all(monkeypatch)
    # the configured model IS listed: the ladder finds no degradation
    monkeypatch.setattr(embed_ladder, "_fetch_model_listing", lambda: ["served-model"])

    result = _invoke("--db", db_path, "--adopt-server-model", "served-model")

    assert result.exit_code == 1
    out = _flat(result)
    assert "not a parity-verified candidate" in out
    assert "healthy" in out
    assert _stamp_counts(db_path) == {stamp: 3}
