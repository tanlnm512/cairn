"""T13: ``cairn metrics`` extensions -- --builds / --quality / --contention.

The three flags render from the telemetry tables added by spec
observability-telemetry §6.5 (``build_runs``, ``events``). The default
(no-flag) path is the original ``tool_metrics`` aggregation and MUST stay
unchanged -- this file pins that explicitly against a deterministic fixture,
in addition to covering each new flag's human + JSON rendering, the
empty-table case, the multi-flag shape, and the defensive "missing table does
not crash" contract.

Coverage mirrors test_doctor.py: a file-backed fixture DB is built with
``_apply_schema`` and seeded directly, then driven through the CliRunner.
"""
from __future__ import annotations

import json
import sqlite3
import time

import pytest
from click.testing import CliRunner

from cairn.cli import main
from cairn.graph.schema import _apply_schema


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _wide_terminal(monkeypatch):
    """Render rich tables full-width so column content isn't wrapped/split.

    CliRunner stdout isn't a TTY, so rich falls back to 80 columns and wraps the
    10-column builds table, breaking contiguous cell assertions (e.g. the
    '250/8/42' resolution mix). COLUMNS is honored by rich via
    shutil.get_terminal_size; 200 fits every table here. JSON output is
    width-independent, so this never affects the shape assertions.
    """
    monkeypatch.setenv("COLUMNS", "200")


def _make_db(path, setup=None):
    """Create a file-backed DB with the full schema, optionally seed rows.

    Mirrors test_doctor.py's helper so the fixture DB carries every telemetry
    table (build_runs, events, tool_metrics) the metrics flags read.
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    if setup:
        setup(conn)
    conn.commit()
    conn.close()


def _run(db, *extra):
    """Invoke `cairn metrics --db <db> [extra]` and return the CliRunner result."""
    return CliRunner().invoke(main, ["metrics", "--db", str(db), *extra])


def _evt(conn, ts, name, attrs):
    conn.execute(
        "INSERT INTO events (ts, name, session_id, attrs) VALUES (?, ?, 's1', ?)",
        (ts, name, json.dumps(attrs)),
    )


# ---------------------------------------------------------------------------
# Default (no flag) path -- byte-for-byte unchanged
# ---------------------------------------------------------------------------


def _seed_tools(conn):
    """Two tools with deterministic aggregates (call counts / avg / errors)."""
    now = time.time()
    # explore: 5 calls, durations 10,10,10,10,20 -> avg 12.0; one error.
    for i, d in enumerate([10.0, 10.0, 10.0, 10.0, 20.0]):
        conn.execute(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, duration_ms, status) "
            "VALUES ('explore', 's1', ?, ?, ?)",
            (now, d, "error" if i == 0 else "ok"),
        )
    # search_symbols: 2 calls, durations 8,8 -> avg 8.0; no errors.
    for d in (8.0, 8.0):
        conn.execute(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, duration_ms, status) "
            "VALUES ('search_symbols', 's1', ?, ?, 'ok')",
            (now, d),
        )


def test_default_json_unchanged(tmp_path):
    """No flag + --json emits the original aggregation, in calls-desc order.

    The JSON shape is byte-stable (not terminal-width dependent), so this is
    the tightest assertion that the default path is unchanged: the exact rows
    the original command produced for this fixture.
    """
    db = tmp_path / "graph.db"
    _make_db(db, _seed_tools)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data == [
        {"tool_name": "explore", "calls": 5, "avg_ms": 12.0, "errors": 1},
        {"tool_name": "search_symbols", "calls": 2, "avg_ms": 8.0, "errors": 0},
    ]


def test_default_human_renders_table_and_leaks_no_new_section(tmp_path):
    """No flag renders the tool table and none of the new section titles.

    The human table is terminal-dependent, so we assert substrings (both tools
    and their counts appear) plus the negative: the new flags' headers must not
    leak into the default path. This pairs with the JSON snapshot above to pin
    the default output.
    """
    db = tmp_path / "graph.db"
    _make_db(db, _seed_tools)

    result = _run(db)
    assert result.exit_code == 0, result.output
    assert "explore" in result.output
    assert "search_symbols" in result.output
    # No new-flag section title leaks into the default aggregation.
    for absent in ("Build runs", "Quality signals", "Lock contention"):
        assert absent not in result.output


def test_default_empty_message(tmp_path):
    """No tool_metrics rows -> the original 'No tool metrics recorded yet.' line."""
    db = tmp_path / "graph.db"
    _make_db(db)

    result = _run(db)
    assert result.exit_code == 0, result.output
    assert "No tool metrics recorded yet." in result.output


# ---------------------------------------------------------------------------
# --builds
# ---------------------------------------------------------------------------


def _seed_builds(conn):
    """A full build (rich row) and a sparse embed row, newest-first by started_at."""
    conn.execute(
        "INSERT INTO build_runs (kind, started_at, duration_s, phase_timings, repos, files, "
        "symbols, edges, resolution_exact, resolution_ambiguous, resolution_unresolved, "
        "parse_errors, skipped, workers, session_id) "
        "VALUES ('build', '2026-08-13T10:00:00+00:00', 1.5, '{}', 1, 10, 120, 300, "
        "250, 8, 42, 2, 3, 4, 's1')"
    )
    # Embed pass: only symbols/skipped populated; the rest must render as '—'.
    conn.execute(
        "INSERT INTO build_runs (kind, started_at, duration_s, symbols, skipped) "
        "VALUES ('embed', '2026-08-12T09:00:00+00:00', 0.5, 42, 1)"
    )


def test_builds_human_shows_resolution_mix(tmp_path):
    """--builds renders both rows newest-first with the resolution mix column."""
    db = tmp_path / "graph.db"
    _make_db(db, _seed_builds)

    result = _run(db, "--builds")
    assert result.exit_code == 0, result.output
    assert "Build runs" in result.output
    assert "build" in result.output and "embed" in result.output
    # The full row's resolution mix renders as exact/ambiguous/unresolved.
    assert "250/8/42" in result.output
    # ISO started_at is reformatted for display.
    assert "2026-08-13 10:00:00" in result.output
    # build precedes embed (newest-first).
    assert result.output.index("2026-08-13 10:00:00") < result.output.index("2026-08-12 09:00:00")


def test_builds_json_is_row_list_newest_first(tmp_path):
    """--builds --json emits a bare list of row dicts, newest-first."""
    db = tmp_path / "graph.db"
    _make_db(db, _seed_builds)

    result = _run(db, "--builds", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert [r["kind"] for r in data] == ["build", "embed"]
    build = data[0]
    assert build["resolution_exact"] == 250
    assert build["resolution_ambiguous"] == 8
    assert build["resolution_unresolved"] == 42
    # Sparse embed row preserves NULLs in JSON (not a sentinel).
    embed = data[1]
    assert embed["repos"] is None
    assert embed["resolution_exact"] is None


def test_builds_empty_is_calm(tmp_path):
    """No build_runs rows -> a 'No build runs recorded yet.' line, never a crash."""
    db = tmp_path / "graph.db"
    _make_db(db)

    result = _run(db, "--builds")
    assert result.exit_code == 0, result.output
    assert "No build runs recorded yet." in result.output


def test_builds_missing_table_degrades(tmp_path):
    """A store without the build_runs table degrades to the no-data line.

    Schema normally guarantees the table; this proves the defensive try/except
    -- a missing/unreadable table never raises (spec: empty tables don't crash).
    """
    db = tmp_path / "graph.db"
    _make_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE build_runs")
    conn.commit()
    conn.close()

    result = _run(db, "--builds")
    assert result.exit_code == 0, result.output
    assert "No build runs recorded yet." in result.output


# ---------------------------------------------------------------------------
# --quality
# ---------------------------------------------------------------------------


def _seed_quality(conn):
    """semantic_backend / empty_result / truncate_result events.

    semantic_backend: ann x3, brute x2, hash x1 (6 total).
    empty_result: 4 total -- semantic_search x2, explore x1, search_symbols x1.
      Only the semantic_search empties share a denominator with semantic_backend,
      so the rate is scoped to that kind (2/6 = 0.333...); the explore /
      search_symbols empties prove non-semantic kinds don't pollute the rate.
    truncate_result: explore x2, search_symbols x1 (3 total).
    """
    t = 1_700_000_000.0
    for backend, n in (("ann", 3), ("brute", 2), ("hash", 1)):
        for _ in range(n):
            _evt(conn, t, "semantic_backend", {"backend": backend, "fusion": 1, "rerank": 0})
    for _ in range(2):
        _evt(conn, t, "empty_result", {"query_kind": "semantic_search"})
    _evt(conn, t, "empty_result", {"query_kind": "explore"})
    _evt(conn, t, "empty_result", {"query_kind": "search_symbols"})
    for _ in range(2):
        _evt(conn, t, "truncate_result", {"tool": "explore", "chars_bucket": "10-100k"})
    _evt(conn, t, "truncate_result", {"tool": "search_symbols", "chars_bucket": "10-100k"})


def test_quality_human_summary(tmp_path):
    """--quality renders empty rate, truncation count, and backend mix."""
    db = tmp_path / "graph.db"
    _make_db(db, _seed_quality)

    result = _run(db, "--quality")
    assert result.exit_code == 0, result.output
    assert "Quality signals" in result.output
    assert "2 / 6 (33.3%)" in result.output
    # Per-kind breakdown surfaces the non-semantic empties without polluting rate.
    assert "empty by kind" in result.output
    assert "explore: 1" in result.output
    assert "backend mix" in result.output
    # Backend mix is count-desc then key; ann(3) leads.
    assert result.output.index("ann") < result.output.index("brute")
    assert result.output.index("brute") < result.output.index("hash")


def test_quality_json_aggregates(tmp_path):
    """--quality --json emits the aggregate dict with rate ~1/3."""
    db = tmp_path / "graph.db"
    _make_db(db, _seed_quality)

    result = _run(db, "--quality", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert isinstance(data, dict)
    assert data["semantic_total"] == 6
    assert data["empty_results"] == 4
    assert data["empty_by_kind"] == {
        "semantic_search": 2,
        "explore": 1,
        "search_symbols": 1,
    }
    assert data["empty_result_rate"] == 2 / 6
    assert data["truncations"] == 3
    assert data["backend_mix"] == {"ann": 3, "brute": 2, "hash": 1}
    assert data["truncations_by_tool"] == {"explore": 2, "search_symbols": 1}


def test_quality_empty_is_calm(tmp_path):
    """No events of any kind -> 'No quality events recorded yet.'."""
    db = tmp_path / "graph.db"
    _make_db(db)

    result = _run(db, "--quality")
    assert result.exit_code == 0, result.output
    assert "No quality events recorded yet." in result.output


def test_quality_rate_is_na_without_semantic_calls(tmp_path):
    """Truncations present but no semantic calls -> rate renders 'n/a', not empty."""
    db = tmp_path / "graph.db"

    def setup(conn):
        _evt(conn, 1_700_000_000.0, "truncate_result", {"tool": "explore", "chars_bucket": "x"})

    _make_db(db, setup)

    result = _run(db, "--quality")
    assert result.exit_code == 0, result.output
    assert "n/a" in result.output
    assert "0 / 0" in result.output


def test_quality_missing_table_degrades(tmp_path):
    """A store without the events table degrades to the no-data line."""
    db = tmp_path / "graph.db"
    _make_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE events")
    conn.commit()
    conn.close()

    result = _run(db, "--quality")
    assert result.exit_code == 0, result.output
    assert "No quality events recorded yet." in result.output


# ---------------------------------------------------------------------------
# --contention
# ---------------------------------------------------------------------------


def _seed_contention(conn):
    """lock_contention events across two sites.

    schema.migration: 3 events (ts ascending -> last_ts is the newest).
    ann_index.try_load: 1 event.
    """
    base = 1_700_000_000.0
    for i in range(3):
        _evt(conn, base + i, "lock_contention", {"site": "schema.migration"})
    _evt(conn, base + 5, "lock_contention", {"site": "ann_index.try_load"})


def test_contention_human_grouped_by_site(tmp_path):
    """--contention renders a count-desc table with most-recent ts per site."""
    db = tmp_path / "graph.db"
    _make_db(db, _seed_contention)

    result = _run(db, "--contention")
    assert result.exit_code == 0, result.output
    assert "Lock contention by site" in result.output
    assert "schema.migration" in result.output
    assert "ann_index.try_load" in result.output
    # Count-desc: schema.migration (3) before ann_index.try_load (1).
    assert result.output.index("schema.migration") < result.output.index("ann_index.try_load")
    # Most-recent ts for schema.migration is the 3rd event (base + 2).
    assert "2023-11-14 22:13:22" in result.output  # fromtimestamp(1700000002, utc)


def test_contention_json_is_site_list(tmp_path):
    """--contention --json emits a bare list of site dicts, count-desc."""
    db = tmp_path / "graph.db"
    _make_db(db, _seed_contention)

    result = _run(db, "--contention", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert [d["site"] for d in data] == ["schema.migration", "ann_index.try_load"]
    assert data[0]["count"] == 3
    assert data[1]["count"] == 1
    # last_ts is the newest (epoch) event for each site.
    assert data[0]["last_ts"] == 1_700_000_002.0
    assert data[1]["last_ts"] == 1_700_000_005.0


def test_contention_unknown_site_bucket(tmp_path):
    """An event with a missing/unreadable site attrs is bucketed under <unknown>."""
    db = tmp_path / "graph.db"

    def setup(conn):
        conn.execute(
            "INSERT INTO events (ts, name, session_id, attrs) VALUES (?, ?, 's1', ?)",
            (1_700_000_000.0, "lock_contention", "{not json"),
        )

    _make_db(db, setup)

    result = _run(db, "--contention", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data == [{"site": "<unknown>", "count": 1, "last_ts": 1_700_000_000.0}]


def test_contention_empty_is_calm(tmp_path):
    """No lock_contention events -> 'No lock-contention events recorded.'."""
    db = tmp_path / "graph.db"
    _make_db(db)

    result = _run(db, "--contention")
    assert result.exit_code == 0, result.output
    assert "No lock-contention events recorded." in result.output


# ---------------------------------------------------------------------------
# --tasks
# ---------------------------------------------------------------------------


def _seed_tasks(conn):
    """task_lifecycle events: claimed x5 (compass-synthesize x3, wiki x2),
    completed x3 (all wiki), revised x1, dropped x1 (compass-synthesize). Kind totals: compass-synthesize 5, wiki 5."""
    t = 1_700_000_000.0
    for _ in range(3):
        _evt(conn, t, "task_lifecycle", {"task_kind": "compass-synthesize", "event": "claimed", "attempt": 1})
    for _ in range(2):
        _evt(conn, t, "task_lifecycle", {"task_kind": "wiki", "event": "claimed", "attempt": 1})
    for _ in range(3):
        _evt(conn, t, "task_lifecycle", {"task_kind": "wiki", "event": "completed", "attempt": 1})
    _evt(conn, t, "task_lifecycle", {"task_kind": "compass-synthesize", "event": "revised", "attempt": 2})
    _evt(conn, t, "task_lifecycle", {"task_kind": "compass-synthesize", "event": "dropped", "attempt": 3})


def test_tasks_human_summary(tmp_path):
    """--tasks renders the lifecycle funnel (by event) and the kind breakdown."""
    db = tmp_path / "graph.db"
    _make_db(db, _seed_tasks)

    result = _run(db, "--tasks")
    assert result.exit_code == 0, result.output
    assert "Task queue lifecycle" in result.output
    assert "events (total)" in result.output
    assert "10" in result.output
    assert "by event" in result.output
    assert "claimed: 5" in result.output
    assert "completed: 3" in result.output
    assert "by kind" in result.output
    assert "compass-synthesize: 5" in result.output
    assert "wiki: 5" in result.output


def test_tasks_json_aggregates(tmp_path):
    """--tasks --json emits {total, by_event, by_kind}."""
    db = tmp_path / "graph.db"
    _make_db(db, _seed_tasks)

    result = _run(db, "--tasks", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["total"] == 10
    assert data["by_event"] == {"claimed": 5, "completed": 3, "revised": 1, "dropped": 1}
    assert data["by_kind"] == {"compass-synthesize": 5, "wiki": 5}


def test_tasks_empty_is_calm(tmp_path):
    """No task events -> 'No task-lifecycle events recorded yet.'."""
    db = tmp_path / "graph.db"
    _make_db(db)

    result = _run(db, "--tasks")
    assert result.exit_code == 0, result.output
    assert "No task-lifecycle events recorded yet." in result.output


def test_tasks_missing_table_degrades(tmp_path):
    """A store without the events table degrades to the no-data line."""
    db = tmp_path / "graph.db"
    _make_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE events")
    conn.commit()
    conn.close()

    result = _run(db, "--tasks")
    assert result.exit_code == 0, result.output
    assert "No task-lifecycle events recorded yet." in result.output


def test_tasks_flag_composes_with_others(tmp_path):
    """--tasks rides the multi-flag dispatch (renders after --quality)."""
    db = tmp_path / "graph.db"

    def setup(conn):
        _seed_quality(conn)
        _seed_tasks(conn)

    _make_db(db, setup)

    result = _run(db, "--quality", "--tasks", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert set(data.keys()) == {"quality", "tasks"}
    assert data["tasks"]["total"] == 10


# ---------------------------------------------------------------------------
# Multiple flags + --json shape
# ---------------------------------------------------------------------------


def test_multiple_flags_render_each_section(tmp_path):
    """Two flags render both sections in the human output."""
    db = tmp_path / "graph.db"

    def setup(conn):
        _seed_builds(conn)
        _seed_contention(conn)

    _make_db(db, setup)

    result = _run(db, "--builds", "--contention")
    assert result.exit_code == 0, result.output
    assert "Build runs" in result.output
    assert "Lock contention by site" in result.output


def test_multiple_flags_json_is_keyed_object(tmp_path):
    """Multiple flags + --json -> one object keyed by section name."""
    db = tmp_path / "graph.db"

    def setup(conn):
        _seed_builds(conn)
        _seed_quality(conn)

    _make_db(db, setup)

    result = _run(db, "--builds", "--quality", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert set(data.keys()) == {"builds", "quality"}
    assert isinstance(data["builds"], list)
    assert isinstance(data["quality"], dict)
    assert data["builds"][0]["kind"] == "build"
    assert data["quality"]["semantic_total"] == 6
