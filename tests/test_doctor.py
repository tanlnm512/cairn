"""T12: `cairn doctor` -- health checks, PASS/WARN/FAIL, exit 0/1, --json.

Doctor surfaces silent degradations (spec observability-telemetry §6.5): schema
integrity, embedding/ANN backend fallbacks, embed-server health (probe /
model-listing / parity sample / latency when a server backend is configured,
otherwise one informational line -- D-012), graph freshness, parse errors,
lock contention, per-tool error/latency health, tribal-memory reference
staleness, and a config echo, plus the
environment-wiring audit (FR-007/D-007: store resolution, client
registration consistency, platform/transport, binary coherence). It is
read-only and crash-proof (a missing/corrupt store degrades to WARN/FAIL,
never raises).

Coverage here is fixture-driven and each FAIL/WARN condition is independently
provable. The environmental backend checks (embeddings hash fallback, ANN
unavailable) are driven via ``monkeypatch`` so they are deterministic
regardless of whether sentence-transformers / sqlite-vec happen to be installed
in the test environment.
"""
from __future__ import annotations

import http.server
import json
import math
import os
import sqlite3
import struct
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from cairn.cli import main
from cairn.graph.schema import _apply_schema


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_db(path, setup=None):
    """Create a file-backed DB with the full schema, optionally seed rows.

    ``setup(conn)`` runs inside the same connection before commit/close so a
    test can populate pending_sync / parse_errors / events / tool_metrics /
    build_runs. The connection uses ``_apply_schema`` (matching the rest of the
    suite); FK enforcement is off by default, but a repos row is seeded anyway
    so parse_errors rows mirror production shape.
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    conn.execute(
        "INSERT INTO repos (id, name, path, language, git_remote, indexed_at) "
        "VALUES ('r1', 'r1', '.', '', NULL, '2026-08-13T00:00:00')"
    )
    if setup:
        setup(conn)
    conn.commit()
    conn.close()


def _run(db, *extra):
    """Invoke `cairn doctor --db <db> [extra]` and return the CliRunner result."""
    return CliRunner().invoke(main, ["doctor", "--db", str(db), *extra])


def _by_name(results, name):
    return next(r for r in results if r["name"] == name)


# ---------------------------------------------------------------------------
# Clean store + structural invariants
# ---------------------------------------------------------------------------


def test_clean_db_exits_zero(tmp_path):
    """A fresh, empty store exits 0: every check is PASS or WARN, none FAIL.

    This is the agent-gating baseline -- a healthy (if uninstrumented) store
    must not trip the FAIL exit code even when optional backends are absent
    (absence degrades to WARN, not FAIL).
    """
    db = tmp_path / "graph.db"
    _make_db(db)

    result = _run(db)
    assert result.exit_code == 0, result.output
    # No FAIL lines in the human output.
    assert "FAIL" not in result.output
    # Schema and config are unconditionally PASS on a clean store.
    assert "PASS schema" in result.output
    assert "PASS config" in result.output


def test_eight_checks_always_emitted(tmp_path):
    """Doctor always emits the check sequence, in order, via --json.

    The historical 8 checks keep their positions; T015 slots embed_server
    after ann (an informational PASS line unless a server backend is
    configured, D-012); T021 appends ``environment`` last (the FR-007 wiring
    audit, D-007).
    """
    db = tmp_path / "graph.db"
    _make_db(db)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    expected = [
        "schema",
        "embeddings",
        "ann",
        "embed_server",
        "freshness",
        "parse_errors",
        "concurrency",
        "tool_health",
        "memory_staleness",
        "config",
        "environment",
    ]
    assert [d["name"] for d in data] == expected
    # Every row carries the documented keys; status is one of the three values.
    for row in data:
        assert set(row.keys()) >= {"name", "status", "detail"}
        assert row["status"] in {"PASS", "WARN", "FAIL"}


# ---------------------------------------------------------------------------
# Schema -- FAIL conditions
# ---------------------------------------------------------------------------


def test_schema_fail_unopenable_db(tmp_path):
    """A store whose path can't be opened FAILs schema and exits 1.

    Parent directory missing -> sqlite3.connect raises OperationalError ->
    doctor degrades (never crashes) with schema FAIL.
    """
    bad = tmp_path / "nodir" / "missing.db"
    result = _run(bad, "--json")
    assert result.exit_code == 1, result.output
    schema = _by_name(json.loads(result.stdout), "schema")
    assert schema["status"] == "FAIL"
    assert "cannot open database" in schema["detail"]


def test_missing_store_fails_instead_of_creating(tmp_path):
    """A missing store in an existing dir FAILs; doctor never creates it.

    get_db creates missing stores; a read-only diagnostic silently doing so
    would mask a typo'd --db with an all-PASS "fresh install" and hand
    exit-code-gating agents a false green.
    """
    db = tmp_path / "typo.db"
    assert not db.exists()

    result = _run(db, "--json")
    assert result.exit_code == 1, result.output
    schema = _by_name(json.loads(result.stdout), "schema")
    assert schema["status"] == "FAIL"
    assert "store not found" in schema["detail"]
    assert not db.exists(), "doctor must not materialize a store"


def test_schema_fail_corrupt_db(tmp_path):
    """A garbage file (not a SQLite DB) FAILs the integrity check.

    get_db's executescript raises DatabaseError on a non-database file; doctor
    maps that to schema FAIL and still emits all 8 blocks (the rest WARN).
    """
    db = tmp_path / "garbage.db"
    db.write_bytes(b"not a sqlite database at all" * 50)

    result = _run(db, "--json")
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert _by_name(data, "schema")["status"] == "FAIL"
    # The DB-dependent checks degrade to WARN, not a crash/empty output.
    assert _by_name(data, "freshness")["status"] == "WARN"
    assert _by_name(data, "config")["status"] == "PASS"  # env-only, independent


# ---------------------------------------------------------------------------
# Embeddings backend -- WARN when hash fallback active
# ---------------------------------------------------------------------------


def test_embeddings_warn_hash_fallback(tmp_path, monkeypatch):
    """Silent hash-backend degradation -> embeddings WARN, remediation hint set."""
    db = tmp_path / "graph.db"
    _make_db(db)

    import cairn.graph.embeddings as emb

    monkeypatch.setattr(emb, "is_hash_fallback", lambda: True)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output  # WARN, not FAIL
    row = _by_name(json.loads(result.stdout), "embeddings")
    assert row["status"] == "WARN"
    assert "install-deps" in (row.get("hint") or "")


def test_embeddings_pass_real_backend(tmp_path, monkeypatch):
    """A real (non-hash) backend -> embeddings PASS."""
    db = tmp_path / "graph.db"
    _make_db(db)

    import cairn.graph.embeddings as emb

    monkeypatch.setattr(emb, "is_hash_fallback", lambda: False)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    assert _by_name(json.loads(result.stdout), "embeddings")["status"] == "PASS"


# ---------------------------------------------------------------------------
# ANN -- WARN when expected-but-unavailable; PASS when explicitly off
# ---------------------------------------------------------------------------


def test_ann_warn_when_expected_but_unavailable(tmp_path, monkeypatch):
    """Default sqlite-vec expected but unavailable -> ANN WARN + install hint."""
    db = tmp_path / "graph.db"
    _make_db(db)

    import cairn.graph.ann_index as ann

    monkeypatch.setenv("CAIRN_ANN_BACKEND", "sqlite-vec")
    monkeypatch.setattr(ann, "ann_backend_enabled", lambda: False)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    row = _by_name(json.loads(result.stdout), "ann")
    assert row["status"] == "WARN"
    assert "brute-force" in row["detail"]
    assert "install-deps" in (row.get("hint") or "")


def test_ann_pass_when_explicitly_disabled(tmp_path, monkeypatch):
    """CAIRN_ANN_BACKEND=off is an informed choice -> ANN PASS (not a WARN).

    Mirrors ann_index.warn_ann_fallback_once: an explicit opt-out is not a
    degradation, so doctor must not flag it.
    """
    db = tmp_path / "graph.db"
    _make_db(db)

    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    row = _by_name(json.loads(result.stdout), "ann")
    assert row["status"] == "PASS"
    assert "disabled by config" in row["detail"]


# --- ANN index-level probes (F1b no-index, F7 staleness) ---------------------


def _seed_embeddings(conn, n, model):
    """n embeddings rows for `model` (the doctor's embed_count/current_model
    probes read exactly these)."""
    for i in range(n):
        conn.execute(
            "INSERT INTO embeddings (symbol_id, model, dim, vec, chunk) "
            "VALUES (?, ?, 8, ?, ?)",
            (f"s{i}", model, b"\x00" * 32, "chunk"),
        )


def _force_ann_loadable(monkeypatch):
    """Make the extension checks deterministic regardless of the host build."""
    import cairn.graph.ann_index as ann

    monkeypatch.delenv("CAIRN_ANN_BACKEND", raising=False)
    monkeypatch.setattr(ann, "ann_backend_enabled", lambda: True)
    monkeypatch.setattr(ann, "try_load", lambda conn: True)
    return ann


def test_ann_warn_when_embeddings_present_but_no_index(tmp_path, monkeypatch):
    """F1b: sqlite-vec loads, embeddings exist for the current model, but no
    vec0 table was ever built -> WARN with the `cairn embed` rebuild hint
    (previously this state was invisible: the load probe alone said PASS)."""
    import cairn.graph.embeddings as emb

    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")  # deterministic current_model()
    _force_ann_loadable(monkeypatch)
    model = emb.current_model()
    db = tmp_path / "graph.db"
    _make_db(db, setup=lambda conn: _seed_embeddings(conn, 3, model))

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    row = _by_name(json.loads(result.stdout), "ann")
    assert row["status"] == "WARN"
    assert "no vec0 index" in row["detail"]
    assert "3" in row["detail"], "the unindexed embedding count is surfaced"
    assert "run `cairn embed`" in (row.get("hint") or "")


def test_ann_warn_when_index_row_count_drifted(tmp_path, monkeypatch):
    """F7: embeddings changed since the last vec0 rebuild (incremental syncs
    add embeddings without rebuilding the index) -> WARN 'ANN index stale' with
    both counts and the rebuild hint. The stand-in table is a plain table with
    the sanitized vec0 name -- index_exists/index_row_count only read
    sqlite_master / COUNT(*)."""
    import cairn.graph.embeddings as emb

    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
    ann = _force_ann_loadable(monkeypatch)
    model = emb.current_model()
    vec_table = ann._table_name(model)

    def setup(conn):
        _seed_embeddings(conn, 5, model)
        conn.execute(f"CREATE TABLE {vec_table} (rowid INTEGER PRIMARY KEY)")
        conn.execute(f"INSERT INTO {vec_table} (rowid) VALUES (1), (2)")

    db = tmp_path / "graph.db"
    _make_db(db, setup=setup)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    row = _by_name(json.loads(result.stdout), "ann")
    assert row["status"] == "WARN"
    assert "stale" in row["detail"].lower()
    assert "5" in row["detail"] and "2" in row["detail"]
    assert "run `cairn embed`" in (row.get("hint") or "")


def test_ann_pass_when_index_matches_embeddings(tmp_path, monkeypatch):
    """A fresh index (row counts equal) -> PASS with the indexed count; the
    index-level probes do not false-positive on a healthy store."""
    import cairn.graph.embeddings as emb

    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
    ann = _force_ann_loadable(monkeypatch)
    model = emb.current_model()
    vec_table = ann._table_name(model)

    def setup(conn):
        _seed_embeddings(conn, 4, model)
        conn.execute(f"CREATE TABLE {vec_table} (rowid INTEGER PRIMARY KEY)")
        conn.executemany(
            f"INSERT INTO {vec_table} (rowid) VALUES (?)", [(i,) for i in range(4)]
        )

    db = tmp_path / "graph.db"
    _make_db(db, setup=setup)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    row = _by_name(json.loads(result.stdout), "ann")
    assert row["status"] == "PASS"
    assert "4" in row["detail"]


def test_ann_pass_when_no_embeddings_to_index(tmp_path, monkeypatch):
    """A store with no embeddings legitimately has no vec0 table: the index
    probes stay out of the embeddings check's territory (PASS, qualified)."""
    _force_ann_loadable(monkeypatch)
    db = tmp_path / "graph.db"
    _make_db(db)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    row = _by_name(json.loads(result.stdout), "ann")
    assert row["status"] == "PASS"
    assert "no embeddings" in row["detail"]


# ---------------------------------------------------------------------------
# Freshness -- WARN on pending_sync and stale last build
# ---------------------------------------------------------------------------


def test_freshness_warn_pending_sync(tmp_path):
    """Unindexed edits in the debounce window -> freshness WARN."""
    db = tmp_path / "graph.db"

    def setup(conn):
        conn.execute(
            "INSERT INTO pending_sync (path, repo_id, changed_at) "
            "VALUES ('/repo/src/a.py', 'r1', ?)",
            (time.time(),),
        )

    _make_db(db, setup)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    row = _by_name(json.loads(result.stdout), "freshness")
    assert row["status"] == "WARN"
    assert "pending-sync" in row["detail"]


def test_freshness_warn_interrupted_repo_rebuild(tmp_path):
    """A stale 'building' marker -> freshness WARN with the recovery hint.

    builder._set_repo_build_state writes the marker before clearing a repo
    for an on-disk rebuild and removes it only after the SCIP hook; a crash
    in between leaves the repo partial. Doctor is the surface that makes
    the marker observable (the detection contract repo_build_state exists
    for).
    """
    db = tmp_path / "graph.db"

    def setup(conn):
        conn.execute(
            "INSERT INTO repo_build_state (repo_id, state, started_at) "
            "VALUES ('demo', 'building', ?)",
            ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),),
        )
        # A recent successful build for another repo so ONLY the marker warns.
        conn.execute(
            "INSERT INTO build_runs (kind, started_at) VALUES ('build', ?)",
            ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),),
        )

    _make_db(db, setup)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output  # WARN, not FAIL
    row = _by_name(json.loads(result.stdout), "freshness")
    assert row["status"] == "WARN"
    assert "interrupted rebuild of demo" in row["detail"]
    assert "cairn build --repo demo" in (row.get("hint") or "")


def test_freshness_pass_when_marker_cleared(tmp_path):
    """No marker -> the interrupted-rebuild arm adds nothing (regression)."""
    db = tmp_path / "graph.db"

    def setup(conn):
        conn.execute(
            "INSERT INTO build_runs (kind, started_at) VALUES ('build', ?)",
            ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),),
        )

    _make_db(db, setup)

    result = _run(db, "--json")
    row = _by_name(json.loads(result.stdout), "freshness")
    assert row["status"] == "PASS"
    assert "interrupted" not in row["detail"]


def test_freshness_warn_stale_build(tmp_path):
    """Last build_runs row older than STALE_BUILD_DAYS -> freshness WARN."""
    db = tmp_path / "graph.db"
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()

    def setup(conn):
        conn.execute(
            "INSERT INTO build_runs (kind, started_at) VALUES ('build', ?)",
            (old,),
        )

    _make_db(db, setup)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    row = _by_name(json.loads(result.stdout), "freshness")
    assert row["status"] == "WARN"
    assert "last build" in row["detail"]


def test_freshness_pass_recent_build(tmp_path):
    """A build within the freshness window -> freshness PASS."""
    db = tmp_path / "graph.db"
    recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    def setup(conn):
        conn.execute(
            "INSERT INTO build_runs (kind, started_at) VALUES ('build', ?)",
            (recent,),
        )

    _make_db(db, setup)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    assert _by_name(json.loads(result.stdout), "freshness")["status"] == "PASS"


# ---------------------------------------------------------------------------
# Parse errors -- WARN when >0 (newest 5 surfaced)
# ---------------------------------------------------------------------------


def test_parse_errors_warn_and_lists_newest(tmp_path):
    """parse_errors > 0 -> WARN; the newest rows appear in the detail."""
    db = tmp_path / "graph.db"

    def setup(conn):
        for i in range(3):
            conn.execute(
                "INSERT INTO parse_errors "
                "(id, file_path, repo_id, error_message, stack_trace, timestamp) "
                "VALUES (?, ?, 'r1', ?, NULL, ?)",
                (
                    str(uuid.uuid4()),
                    f"/repo/src/file{i}.py",
                    f"SyntaxError num{i}",
                    f"2026-08-13T00:00:{i:02d}",
                ),
            )

    _make_db(db, setup)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    row = _by_name(json.loads(result.stdout), "parse_errors")
    assert row["status"] == "WARN"
    assert "3 parse error(s)" in row["detail"]
    # Newest-first (timestamp DESC) -> file2 before file0.
    assert row["detail"].index("file2") < row["detail"].index("file0")


def test_parse_errors_pass_when_empty(tmp_path):
    """Zero parse errors -> PASS."""
    db = tmp_path / "graph.db"
    _make_db(db)

    result = _run(db, "--json")
    assert _by_name(json.loads(result.stdout), "parse_errors")["status"] == "PASS"


# ---------------------------------------------------------------------------
# Concurrency -- WARN on lock_contention; stray_swept alone stays PASS
# ---------------------------------------------------------------------------


def test_concurrency_warn_on_lock_contention(tmp_path):
    """A lock_contention event in the window -> concurrency WARN."""
    db = tmp_path / "graph.db"

    def setup(conn):
        conn.execute(
            "INSERT INTO events (ts, name, session_id, attrs) VALUES (?, ?, ?, ?)",
            (time.time(), "lock_contention", "s1", json.dumps({"site": "schema.migration"})),
        )

    _make_db(db, setup)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    row = _by_name(json.loads(result.stdout), "concurrency")
    assert row["status"] == "WARN"
    assert "1 lock-contention event" in row["detail"]


def test_concurrency_pass_with_only_stray_sweeps(tmp_path):
    """stray_swept events alone do NOT WARN -- sweeping strays is remediation."""
    db = tmp_path / "graph.db"

    def setup(conn):
        conn.execute(
            "INSERT INTO events (ts, name, session_id, attrs) VALUES (?, ?, ?, ?)",
            (time.time(), "stray_swept", "s1", json.dumps({"count": 3})),
        )

    _make_db(db, setup)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    row = _by_name(json.loads(result.stdout), "concurrency")
    assert row["status"] == "PASS"
    assert "3 stray" in row["detail"]  # reported in detail, just not a WARN trigger


# ---------------------------------------------------------------------------
# Tool health -- WARN on high error rate or high p95 latency
# ---------------------------------------------------------------------------


def _seed_tool_metrics(conn, tool, durations, n_errors=0):
    """Insert tool_metrics rows for `tool` with given durations + error count."""
    now = time.time()
    for i, d in enumerate(durations):
        status = "error" if i < n_errors else "ok"
        conn.execute(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, duration_ms, status) "
            "VALUES (?, 's1', ?, ?, ?)",
            (tool, now, d, status),
        )


def test_tool_health_warn_high_error_rate(tmp_path):
    """Error rate above TOOL_ERROR_RATE_WARN (>10%) -> tool_health WARN."""
    db = tmp_path / "graph.db"

    def setup(conn):
        # 5 of 10 calls erroring = 50%.
        _seed_tool_metrics(conn, "search_symbols", [10.0] * 10, n_errors=5)

    _make_db(db, setup)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    row = _by_name(json.loads(result.stdout), "tool_health")
    assert row["status"] == "WARN"
    assert "search_symbols" in row["detail"]
    assert "50% err" in row["detail"]


def test_tool_health_warn_high_latency(tmp_path):
    """p95 latency above TOOL_P95_LATENCY_MS_WARN (>5s) -> tool_health WARN."""
    db = tmp_path / "graph.db"

    def setup(conn):
        # 20 calls; most are slow so the interpolated p95 lands on a >5s value.
        # (One outlier among 20 would interpolate down to ~495ms -- the slow
        # values must dominate the 95th percentile, not just touch the max.)
        durations = [100.0] * 5 + [6000.0] * 15
        _seed_tool_metrics(conn, "explore", durations)

    _make_db(db, setup)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    row = _by_name(json.loads(result.stdout), "tool_health")
    assert row["status"] == "WARN"
    assert "p95" in row["detail"]


def test_tool_health_pass_when_healthy(tmp_path):
    """All tools within thresholds -> tool_health PASS."""
    db = tmp_path / "graph.db"

    def setup(conn):
        _seed_tool_metrics(conn, "get_callers", [20.0, 30.0, 40.0])

    _make_db(db, setup)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    assert _by_name(json.loads(result.stdout), "tool_health")["status"] == "PASS"


# ---------------------------------------------------------------------------
# Config echo -- always PASS, self-describing
# ---------------------------------------------------------------------------


def test_config_echo_always_pass(tmp_path, monkeypatch):
    """Config echo is informational: always PASS, reflects effective knobs."""
    db = tmp_path / "graph.db"
    _make_db(db)

    monkeypatch.setenv("CAIRN_WORKERS", "4")
    monkeypatch.setenv("CAIRN_TELEMETRY", "off")

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    row = _by_name(json.loads(result.stdout), "config")
    assert row["status"] == "PASS"
    assert "workers=4" in row["detail"]
    assert "telemetry=off" in row["detail"]


# ---------------------------------------------------------------------------
# Config echo -- file layer (T021, FR-010/FR-011, D-008): each A2.1 embedding
# knob echoes effective value + source layer (env / file / default); the API
# key reports presence only. conftest's hermetic env re-points paths.CONFIG_FILE
# into the sandbox, so a dev machine's real config.json cannot leak in.
# ---------------------------------------------------------------------------


def test_config_echo_embedding_knobs_default(tmp_path):
    """(a) No env, no config file: every A2.1 knob echoes its default with a
    (default) marker, the API key reports <unset>, and the historical
    env-only knobs keep their shape."""
    db = tmp_path / "graph.db"
    _make_db(db)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    row = _by_name(json.loads(result.stdout), "config")
    assert row["status"] == "PASS"
    assert "workers=<unset>" in row["detail"]  # historical knobs unchanged
    assert "embed_backend=local (default)" in row["detail"]
    assert "embed_base_url=<preset> (default)" in row["detail"]
    assert "embed_server_model=bge-m3 (default)" in row["detail"]
    assert "embed_timeout=30 (default)" in row["detail"]
    assert "embed_server_batch=32 (default)" in row["detail"]
    assert "embed_model_stamp=<derived> (default)" in row["detail"]
    assert "api_key=<unset>" in row["detail"]
    assert "(env)" not in row["detail"]
    assert "(file)" not in row["detail"]


def test_config_echo_env_pins_over_file(tmp_path, monkeypatch):
    """(b) D-008 precedence: an env-pinned knob echoes the env value even when
    the config file holds a DIFFERENT value."""
    from cairn import paths

    db = tmp_path / "graph.db"
    _make_db(db)
    assert paths.set_config_values({"CAIRN_EMBED_TIMEOUT": "99"})
    monkeypatch.setenv("CAIRN_EMBED_TIMEOUT", "7")

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    detail = _by_name(json.loads(result.stdout), "config")["detail"]
    assert "embed_timeout=7 (env)" in detail
    assert "99" not in detail, "the file value must not surface when env pins"


def test_config_echo_file_set_knob(tmp_path):
    """(c) A knob persisted in the (sandboxed) config.json echoes the file
    value with a (file) marker."""
    from cairn import paths

    db = tmp_path / "graph.db"
    _make_db(db)
    assert paths.set_config_values({"CAIRN_EMBED_SERVER_MODEL": "file-model"})

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    detail = _by_name(json.loads(result.stdout), "config")["detail"]
    assert "embed_server_model=file-model (file)" in detail


def test_config_echo_api_key_presence_only(tmp_path):
    """(d) A file-persisted API key is reported as present only -- the secret
    value never reaches doctor output (JSON or human render)."""
    from cairn import paths

    secret = "sk-totally-secret-value-12345"
    db = tmp_path / "graph.db"
    _make_db(db)
    assert paths.set_config_values({"CAIRN_EMBED_API_KEY": secret})

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    detail = _by_name(json.loads(result.stdout), "config")["detail"]
    assert "api_key=<set via file>" in detail
    assert secret not in detail
    assert secret not in result.output


def test_config_echo_survives_corrupt_config_file(tmp_path, caplog):
    """(e) A corrupt config.json degrades to defaults-with-warning (paths logs
    one warning): the echo still PASSes, every knob marked (default), no raise."""
    import logging

    from cairn import paths

    db = tmp_path / "graph.db"
    _make_db(db)
    paths.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    paths.CONFIG_FILE.write_text("{not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="cairn.paths"):
        result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    row = _by_name(json.loads(result.stdout), "config")
    assert row["status"] == "PASS"
    assert "embed_backend=local (default)" in row["detail"]
    assert "api_key=<unset>" in row["detail"]
    assert "not valid JSON" in caplog.text


# ---------------------------------------------------------------------------
# Aggregation -- any FAIL flips the exit code to 1
# ---------------------------------------------------------------------------


def test_any_fail_exits_one(tmp_path):
    """A FAIL anywhere (here: corrupt store) makes the aggregate exit code 1."""
    db = tmp_path / "garbage.db"
    db.write_bytes(b"\x00not a database\x00" * 20)

    result = _run(db)
    assert result.exit_code == 1
    assert "FAIL" in result.output


# ---------------------------------------------------------------------------
# Embed server (T015, FR-007/FR-013) -- informational PASS unless a
# server-family backend (server/omlx/ollama) is configured (D-012). HTTP only
# against a loopback stub on an ephemeral port (or a dead loopback port),
# never the network.
# ---------------------------------------------------------------------------

DIM = 8


def _blob(vec):
    """float32-LE blob, matching the embeddings storage format."""
    return struct.pack(f"<{len(vec)}f", *vec)


def _unit(vec):
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


class _StubEmbedServer:
    """Loopback OpenAI-compatible /v1 stand-in: GET /models + POST /embeddings."""

    def __init__(self, model_ids, vec):
        self.model_ids = list(model_ids)
        self.vec = list(vec)
        self.embed_requests = 0
        outer = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def _send(self, payload):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                self._send(
                    json.dumps(
                        {"data": [{"id": m} for m in outer.model_ids]}
                    ).encode("utf-8")
                )

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                outer.embed_requests += 1
                self._send(
                    json.dumps(
                        {
                            "data": [
                                {"index": i, "embedding": outer.vec}
                                for i, _ in enumerate(body["input"])
                            ]
                        }
                    ).encode("utf-8")
                )

            def log_message(self, *args):
                pass

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(
            target=self._httpd.serve_forever,
            daemon=True,
            kwargs={"poll_interval": 0.05},
        ).start()

    @property
    def netloc(self):
        host, port = self._httpd.server_address[:2]
        return f"{host}:{port}"

    @property
    def base_url(self):
        return f"http://{self.netloc}/v1"

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()


def _dead_base_url():
    """A loopback URL whose port is (transiently) guaranteed refusing."""
    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), http.server.BaseHTTPRequestHandler
    )
    host, port = httpd.server_address[:2]
    httpd.server_close()
    return f"http://{host}:{port}/v1"


def _server_env(monkeypatch, base_url, model="stub-model"):
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
    monkeypatch.setenv("CAIRN_EMBED_BASE_URL", base_url)
    monkeypatch.setenv("CAIRN_EMBED_SERVER_MODEL", model)


def _seed_stamp_rows(conn, stamp, vec, n=1):
    """Stored embeddings under a server stamp for the parity arm to sample."""
    for i in range(n):
        conn.execute(
            "INSERT INTO embeddings (symbol_id, model, dim, vec, chunk, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"s{i}", stamp, len(vec), _blob(vec), f"chunk {i}", f"h{i}"),
        )


@pytest.fixture
def embed_cache_reset():
    """Backend/ladder caches keyed to mutated env die with each test."""
    import cairn.graph.embeddings as emb

    emb.reset_backend_cache()
    yield
    emb.reset_backend_cache()


def test_embed_server_informational_when_disabled(tmp_path, monkeypatch):
    """(a) Default (local) config: one informational PASS line; the historical
    8 checks keep their names, order, and statuses (D-012 byte-stability);
    ``environment`` appends last (D-007)."""
    monkeypatch.delenv("CAIRN_EMBED_BACKEND", raising=False)
    db = tmp_path / "graph.db"
    _make_db(db)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert [d["name"] for d in data] == [
        "schema",
        "embeddings",
        "ann",
        "embed_server",
        "freshness",
        "parse_errors",
        "concurrency",
        "tool_health",
        "memory_staleness",
        "config",
        "environment",
    ]
    row = _by_name(data, "embed_server")
    assert row["status"] == "PASS"
    assert row["detail"] == "disabled by config (CAIRN_EMBED_BACKEND=local)"
    assert row["hint"] is None


def test_embed_server_pass_healthy_stub(tmp_path, monkeypatch, embed_cache_reset):
    """(b) Healthy stub (probe 200 + model listed + parity pass on seeded rows)
    -> PASS naming host, model, and the parity/latency outcome."""
    vec = _unit([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    server = _StubEmbedServer(["stub-model"], vec)
    try:
        _server_env(monkeypatch, server.base_url)
        db = tmp_path / "graph.db"
        stamp = f"server/{server.netloc}/stub-model"
        _make_db(db, setup=lambda conn: _seed_stamp_rows(conn, stamp, vec, n=2))

        result = _run(db, "--json")
        assert result.exit_code == 0, result.output
        row = _by_name(json.loads(result.stdout), "embed_server")
        assert row["status"] == "PASS"
        assert server.netloc in row["detail"]
        assert "stub-model" in row["detail"]
        assert "parity 1.0000" in row["detail"]
        assert "embed latency" in row["detail"]
    finally:
        server.close()


def test_embed_server_probe_down_fails_with_remediation(
    tmp_path, monkeypatch, embed_cache_reset
):
    """(c) Closed port -> FAIL naming the base URL with a remediation hint."""
    base = _dead_base_url()
    _server_env(monkeypatch, base)
    db = tmp_path / "graph.db"
    _make_db(db)

    result = _run(db, "--json")
    assert result.exit_code == 1, result.output
    row = _by_name(json.loads(result.stdout), "embed_server")
    assert row["status"] == "FAIL"
    assert base in row["detail"]
    assert "/models" in row["detail"]
    assert "start the embedding server" in (row.get("hint") or "")


def test_embed_server_model_missing_fails(tmp_path, monkeypatch, embed_cache_reset):
    """(d) 200 listing without the configured id -> FAIL naming
    available-vs-configured."""
    server = _StubEmbedServer(["other-model"], [1.0] * DIM)
    try:
        _server_env(monkeypatch, server.base_url, model="gone-model")
        db = tmp_path / "graph.db"
        _make_db(db)

        result = _run(db, "--json")
        assert result.exit_code == 1, result.output
        row = _by_name(json.loads(result.stdout), "embed_server")
        assert row["status"] == "FAIL"
        assert "gone-model" in row["detail"]
        assert "other-model" in row["detail"]
        assert "serve the model" in (row.get("hint") or "")
    finally:
        server.close()


def test_embed_server_parity_fail_warns_with_mean(
    tmp_path, monkeypatch, embed_cache_reset
):
    """(e) Stored rows the stub can't reproduce -> WARN carrying the measured
    mean (advice: exit stays 0)."""
    stored = _unit([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    served = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # orthogonal -> cosine 0.0
    server = _StubEmbedServer(["stub-model"], served)
    try:
        _server_env(monkeypatch, server.base_url)
        db = tmp_path / "graph.db"
        stamp = f"server/{server.netloc}/stub-model"
        _make_db(db, setup=lambda conn: _seed_stamp_rows(conn, stamp, stored))

        result = _run(db, "--json")
        assert result.exit_code == 0, result.output
        row = _by_name(json.loads(result.stdout), "embed_server")
        assert row["status"] == "WARN"
        assert "0.0000" in row["detail"]
        assert "below gate" in row["detail"]
        assert "cairn embed" in (row.get("hint") or "")
    finally:
        server.close()


def test_embed_server_parity_vacuous_with_zero_rows(
    tmp_path, monkeypatch, embed_cache_reset
):
    """(f) No stored rows under the stamp -> parity skipped (vacuous), PASS.

    The stub sees exactly one embed POST (the latency probe); the parity
    sampler never fires."""
    server = _StubEmbedServer(["stub-model"], [1.0] * DIM)
    try:
        _server_env(monkeypatch, server.base_url)
        db = tmp_path / "graph.db"
        _make_db(db)

        result = _run(db, "--json")
        assert result.exit_code == 0, result.output
        row = _by_name(json.loads(result.stdout), "embed_server")
        assert row["status"] == "PASS"
        assert "parity vacuous" in row["detail"]
        assert server.embed_requests == 1
    finally:
        server.close()


def test_embed_server_active_degradation_warn_entry(
    tmp_path, monkeypatch, embed_cache_reset
):
    """(g) An active ladder degradation (FR-012) surfaces as an appended WARN
    entry naming rung/reason, independent of the current probe verdict."""
    from cairn.graph import embed_ladder

    server = _StubEmbedServer(["stub-model"], [1.0] * DIM)
    try:
        _server_env(monkeypatch, server.base_url)
        db = tmp_path / "graph.db"
        _make_db(db)
        monkeypatch.setitem(
            embed_ladder._LADDER_CACHE,
            "state",
            embed_ladder.LadderState(3, "server_down", "server unreachable", None, True),
        )

        result = _run(db, "--json")
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        # The server answers now, so the aggregate check PASSes...
        assert _by_name(data, "embed_server")["status"] == "PASS"
        # ...but the degradation recorded earlier in this process still surfaces.
        row = _by_name(data, "embed_server_degraded")
        assert row["status"] == "WARN"
        assert "rung 3" in row["detail"]
        assert "server_down" in row["detail"]
    finally:
        server.close()


def test_embed_server_exit_mapping_unchanged(tmp_path, monkeypatch, embed_cache_reset):
    """(h) Exit semantics untouched: the informational line keeps exit 0, and
    the new check's FAIL alone flips the same store to exit 1."""
    db = tmp_path / "graph.db"
    _make_db(db)

    monkeypatch.delenv("CAIRN_EMBED_BACKEND", raising=False)
    assert _run(db).exit_code == 0

    _server_env(monkeypatch, _dead_base_url())
    result = _run(db)
    assert result.exit_code == 1
    assert "FAIL embed_server" in result.output


# ---------------------------------------------------------------------------
# Environment wiring (T020, FR-007 / D-007): one `environment` check appended
# to BOTH doctor return paths, auditing (a) resolved-store existence, (b)
# client-registration consistency, (c) platform/transport supportability, and
# (d) binary coherence. Every test in this section is RED until T021 lands
# the check (today: 9 checks, no `environment` row) -- the same C-02 red
# convention as tests/test_config_probe.py. Fixtures shape the machine with
# tmp homes + monkeypatched bindings/env (never global subprocess patching);
# the platform arm is driven through lifecycle.is_macos -- the function the
# doctor reads -- never sys.platform.
# ---------------------------------------------------------------------------


def _repoint_cairn_home(monkeypatch, home):
    """Re-point paths.py's import-time CAIRN_HOME bindings into the sandbox.

    Same pit as tests/test_config_probe.py::_repoint_bindings: under pytest
    the binding happens at collection time, before conftest's hermetic env
    runs, so resolve_store would otherwise read the real ~/.cairn.
    """
    from cairn import paths

    monkeypatch.setattr(paths, "CAIRN_HOME", home)
    monkeypatch.setattr(paths, "REGISTRY_FILE", home / "workspaces.json")


def _store_db(home, ws):
    """The .kg path cairn resolves for workspace ``ws`` under ``home``."""
    from cairn.paths import store_key

    return home / store_key(ws) / ".kg"


def _dead_sse_url():
    """A loopback SSE URL whose port is (transiently) guaranteed refusing."""
    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), http.server.BaseHTTPRequestHandler
    )
    host, port = httpd.server_address[:2]
    httpd.server_close()
    return f"http://{host}:{port}/sse"


def _force_non_macos(monkeypatch):
    """Drive the platform/transport sub-audit off-darwin on any host.

    The audit reads the platform through lifecycle.is_macos; patch THAT (the
    function the doctor calls), never sys.platform. Both plausible bindings
    are covered: the lifecycle module attribute and a from-import binding in
    cli.system (raising=False until T021 introduces the latter).
    """
    from cairn.cli import system
    from cairn.mcp_server import lifecycle as lifecycle_mod

    monkeypatch.setattr(lifecycle_mod, "is_macos", lambda: False)
    monkeypatch.setattr(system, "is_macos", lambda: False, raising=False)


def test_environment_fails_on_incident_wiring(tmp_path, monkeypatch):
    """#70 machine (TC-012 automated half / AC8): a populated custom store, an
    empty default store, an SSE registration whose daemon is gone, on a
    non-macOS host -> `environment` FAILs naming the client and the
    macOS-only lifecycle, and the run exits 1 -- even though the store itself
    is schema-healthy (the wiring, not the store, is what broke)."""
    from cairn.agent_install._common import mcp_config_json

    custom_home = tmp_path / "custom_home"
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("CAIRN_HOME", str(custom_home))
    _repoint_cairn_home(monkeypatch, custom_home)
    monkeypatch.chdir(ws)

    # The populated custom store the machine actually built (repos row seeded).
    db = _store_db(custom_home, ws.resolve())
    db.parent.mkdir(parents=True)
    _make_db(db)
    # The default location exists but holds no store: the empty store #70's
    # clients silently resolved instead.
    (Path.home() / ".cairn").mkdir(parents=True, exist_ok=True)

    # A stale SSE registration (env-less by construction) with no daemon
    # behind its (dead loopback) endpoint.
    cfg = mcp_config_json(transport="sse", sse_url=_dead_sse_url())
    (ws / ".mcp.json").write_text(json.dumps(cfg), encoding="utf-8")
    # The macOS-only daemon lifecycle can never serve it on this host.
    _force_non_macos(monkeypatch)

    result = _run(db, "--json")
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    # The store itself is healthy -- the wiring is the failure.
    assert _by_name(data, "schema")["status"] == "PASS"
    row = _by_name(data, "environment")
    assert row["status"] == "FAIL"
    findings = row["detail"] + " " + (row.get("hint") or "")
    assert "claude" in findings, "the mismatching client must be named"
    assert "macOS-only" in findings, "the macOS-only daemon lifecycle must be named"


def test_environment_passes_on_healthy_default_install(tmp_path, monkeypatch):
    """AC9 / TC-013: a healthy default install -- default home, built store,
    no client wiring in the sandbox to contradict it -- PASSes the
    environment check. That the prior 9 checks are unchanged in name and
    order is pinned by the sequence assertions above; with no SSE
    registration the platform arm is silent, so the PASS holds on any host."""
    ws = tmp_path / "ws"
    ws.mkdir()
    default_home = Path.home() / ".cairn"
    _repoint_cairn_home(monkeypatch, default_home)
    monkeypatch.delenv("CAIRN_HOME", raising=False)
    monkeypatch.chdir(ws)

    db = _store_db(default_home, ws.resolve())
    db.parent.mkdir(parents=True)
    _make_db(db)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    assert _by_name(json.loads(result.stdout), "environment")["status"] == "PASS"


def test_environment_emitted_when_db_unavailable(tmp_path, monkeypatch):
    """The degraded (store missing/unopenable) return path still emits the
    `environment` check, last in sequence: the wiring audit needs no db
    connection, and a broken store is precisely when wiring matters (D-007
    appends it to BOTH return paths). The missing store must not double-FAIL
    here -- schema already carries that FAIL (mixed-severity ruling)."""
    sandbox_home = tmp_path / "_sandbox_home"
    _repoint_cairn_home(monkeypatch, sandbox_home)
    monkeypatch.setenv("CAIRN_HOME", str(sandbox_home))

    db = tmp_path / "typo.db"
    assert not db.exists()

    result = _run(db, "--json")
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert _by_name(data, "schema")["status"] == "FAIL"
    assert data[-1]["name"] == "environment"
    assert data[-1]["status"] in {"PASS", "WARN"}, (
        "the store's absence is schema's FAIL; environment must not repeat it"
    )


def test_environment_warns_on_stale_envless_registration(tmp_path, monkeypatch):
    """TC-015: a registration written by the previous release (no environment
    entry) that still resolves the doctor's own store draws a WARN advising
    `cairn install-agents` -- warned, not failed; exit stays 0."""
    from cairn.agent_install._common import mcp_config_json

    ws = tmp_path / "ws"
    ws.mkdir()
    default_home = Path.home() / ".cairn"
    _repoint_cairn_home(monkeypatch, default_home)
    monkeypatch.delenv("CAIRN_HOME", raising=False)
    monkeypatch.chdir(ws)

    db = _store_db(default_home, ws.resolve())
    db.parent.mkdir(parents=True)
    _make_db(db)
    # Generated by the real generator with the home at default: exactly the
    # env-less stdio shape the previous release wrote for this machine.
    cfg = mcp_config_json(transport="stdio")
    assert "env" not in cfg["mcpServers"]["cairn"]
    (ws / ".mcp.json").write_text(json.dumps(cfg), encoding="utf-8")

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    row = _by_name(json.loads(result.stdout), "environment")
    assert row["status"] == "WARN"
    assert "install-agents" in (row.get("hint") or "")


# ---------------------------------------------------------------------------
# Memory staleness (T015, FR-011 / TC-022 / TC-023): the write-only-memory
# detector. Tribal memories whose mtime is older than the reference window
# with zero memory_refs rows inside it draw a WARN; a recent reference turns
# it PASS. The bundle is resolved from the --db path's directory (a tmp_path
# workspace, never the real ~/.cairn), and the check is read-only.
# ---------------------------------------------------------------------------


def _seed_tribal_memory(db, name="stale-note.md", age_days=None):
    """Write one tribal OKF file into the bundle beside ``db``, optionally
    mtime-aged ``age_days`` into the past (no YAML parsing needed -- the check
    stats files only)."""
    tribal = Path(db).parent / ".knowledge" / "memory" / "tribal"
    tribal.mkdir(parents=True, exist_ok=True)
    f = tribal / name
    f.write_text("---\ntitle: stale note\n---\nbody\n", encoding="utf-8")
    if age_days is not None:
        stamp = time.time() - age_days * 86400
        os.utime(f, (stamp, stamp))
    return f


def _record_ref(db, memory_path, age_days):
    """Insert one memory_refs row ``age_days`` old for ``memory_path``."""
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO memory_refs (id, memory_path, session_id, referenced_at, context) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                str(memory_path),
                "session-doctor-test",
                (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat(),
                "test",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_memory_staleness_warns_on_write_only_memory(tmp_path):
    """TC-022: a tribal memory mtime-aged past the 30d window with zero
    memory_refs rows in that window -> memory_staleness WARN whose detail
    names write-only memory; WARN keeps the doctor exit code at 0."""
    db = tmp_path / "graph.db"
    _make_db(db)
    _seed_tribal_memory(db, age_days=40)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    row = _by_name(json.loads(result.stdout), "memory_staleness")
    assert row["status"] == "WARN"
    assert "write-only" in row["detail"]
    assert row["hint"], "the WARN carries a remediation hint"


def test_memory_staleness_passes_when_memories_referenced(tmp_path):
    """TC-023: the tribal memory is old but holds a memory_refs row inside the
    window -> PASS (no WARN) reporting the reference count."""
    db = tmp_path / "graph.db"
    _make_db(db)
    stale = _seed_tribal_memory(db, age_days=40)
    _record_ref(db, stale, age_days=1)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    row = _by_name(json.loads(result.stdout), "memory_staleness")
    assert row["status"] == "PASS"
    assert "1 reference" in row["detail"]
