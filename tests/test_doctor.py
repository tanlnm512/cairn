"""T12: `cairn doctor` -- 8 health checks, PASS/WARN/FAIL, exit 0/1, --json.

Doctor surfaces silent degradations (spec observability-telemetry §6.5): schema
integrity, embedding/ANN backend fallbacks, graph freshness, parse errors, lock
contention, per-tool error/latency health, and a config echo. It is read-only
and crash-proof (a missing/corrupt store degrades to WARN/FAIL, never raises).

Coverage here is fixture-driven and each FAIL/WARN condition is independently
provable. The environmental backend checks (embeddings hash fallback, ANN
unavailable) are driven via ``monkeypatch`` so they are deterministic
regardless of whether sentence-transformers / sqlite-vec happen to be installed
in the test environment.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone

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
    """Doctor always emits exactly the 8 spec checks, in order, via --json."""
    db = tmp_path / "graph.db"
    _make_db(db)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    expected = [
        "schema",
        "embeddings",
        "ann",
        "freshness",
        "parse_errors",
        "concurrency",
        "tool_health",
        "config",
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
# Aggregation -- any FAIL flips the exit code to 1
# ---------------------------------------------------------------------------


def test_any_fail_exits_one(tmp_path):
    """A FAIL anywhere (here: corrupt store) makes the aggregate exit code 1."""
    db = tmp_path / "garbage.db"
    db.write_bytes(b"\x00not a database\x00" * 20)

    result = _run(db)
    assert result.exit_code == 1
    assert "FAIL" in result.output
