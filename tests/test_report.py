"""T20: ``cairn report`` -- redacted diagnostic bundle for bug reports / issues.

``cairn report`` assembles versions, the 8 doctor checks, recent error-ish
events / ``tool_metrics`` errors, and the effective ``CAIRN_*`` config into one
bundle. Every string field passes through ``memory.privacy.strip_private_data``
(spec observability-telemetry §7) so the bundle is safe to paste into a public
GitHub issue, and nothing is ever uploaded.

Coverage here mirrors test_doctor.py / test_metrics_extensions.py: a file-backed
fixture DB built with ``_apply_schema`` and seeded directly, then driven through
the CliRunner. Asserts (a) the expected sections, (b) valid ``--json``, (c)
REDACTION -- a secret-shaped value seeded into tool_metrics / events is
scrubbed and does NOT appear verbatim, (d) ``--out`` writes a file, and (e)
graceful behavior on an empty / missing store.
"""
from __future__ import annotations

import json
import sqlite3
import time

from click.testing import CliRunner

from cairn.cli import main
from cairn.graph.schema import _apply_schema


# ---------------------------------------------------------------------------
# Fixture helpers (same shape as test_doctor.py)
# ---------------------------------------------------------------------------


def _make_db(path, setup=None):
    """Create a file-backed DB with the full schema, optionally seed rows."""
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
    """Invoke `cairn report --db <db> [extra]` and return the CliRunner result."""
    return CliRunner().invoke(main, ["report", "--db", str(db), *extra])


# Secret-shaped values that strip_private_data redacts. Both match the regex
# catalog in privacy.py (Bearer <token>; token=<value>), so they become
# "[REDACTED_SECRET]" -- proving the privacy gate runs on every string field.
_SECRET = "Bearer abcdefghijklmnopqrstuvwxyz1234567890abcd"
_EVENT_SECRET = "token=sk-1234567890abcdefghijklmnopqrstuv"

_DOCTOR_NAMES = [
    "schema",
    "embeddings",
    "ann",
    "freshness",
    "parse_errors",
    "concurrency",
    "tool_health",
    "config",
]


# ---------------------------------------------------------------------------
# (a) Bundle contains the expected sections
# ---------------------------------------------------------------------------


def test_json_bundle_has_expected_sections(tmp_path):
    """--json emits an object with versions/doctor/recent_errors/config."""
    db = tmp_path / "graph.db"
    _make_db(db)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert set(data.keys()) >= {"generated_at", "versions", "doctor", "recent_errors", "config"}

    v = data["versions"]
    assert v["cairn"]          # populated from cairn.__version__
    assert v["python"]
    assert v["platform"]
    assert v["sqlite"]
    assert "db_schema_user_version" in v

    # Doctor reuses _run_doctor -> exactly the 8 checks, in order.
    assert [r["name"] for r in data["doctor"]] == _DOCTOR_NAMES
    for row in data["doctor"]:
        assert row["status"] in {"PASS", "WARN", "FAIL"}

    # recent_errors is the structured {events, tool_errors} shape.
    assert set(data["recent_errors"].keys()) == {"events", "tool_errors"}

    # config echoes the CAIRN_* knobs (same list as _check_config).
    assert set(data["config"].keys()) == {
        "workers", "read_only", "fusion", "ann_backend",
        "embed_backend", "telemetry", "log_level",
    }


def test_human_output_renders_sections(tmp_path):
    """Plain-text output carries a header and every section heading."""
    db = tmp_path / "graph.db"
    _make_db(db)

    result = _run(db)
    assert result.exit_code == 0, result.output
    assert "cairn report" in result.output
    for section in ("Versions", "Doctor", "Recent error events",
                    "Recent tool errors", "Config"):
        assert section in result.output
    # The privacy-gate notice is present so the user knows it was scrubbed.
    assert "strip_private_data" in result.output


def test_doctor_results_surface_recent_errors(tmp_path):
    """Seeded degradation events + tool errors appear in the bundle."""
    db = tmp_path / "graph.db"

    def setup(conn):
        now = time.time()
        conn.execute(
            "INSERT INTO events (ts, name, session_id, attrs) "
            "VALUES (?, 'ann_fallback', 's1', ?)",
            (now, json.dumps({"reason": "not_installed"})),
        )
        conn.execute(
            "INSERT INTO events (ts, name, session_id, attrs) "
            "VALUES (?, 'semantic_backend', 's1', ?)",
            (now, json.dumps({"backend": "ann"})),
        )
        conn.execute(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
            "duration_ms, status, error_message) "
            "VALUES ('explore', 's1', ?, 12.0, 'error', 'boom')",
            (now,),
        )

    _make_db(db, setup)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    # Only the error-ish event (ann_fallback) is surfaced; semantic_backend is
    # normal operation and is excluded.
    assert [e["name"] for e in data["recent_errors"]["events"]] == ["ann_fallback"]
    assert len(data["recent_errors"]["tool_errors"]) == 1
    assert data["recent_errors"]["tool_errors"][0]["tool_name"] == "explore"


# ---------------------------------------------------------------------------
# (b) --json output is valid JSON
# ---------------------------------------------------------------------------


def test_json_is_valid_on_clean_store(tmp_path):
    db = tmp_path / "graph.db"
    _make_db(db)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)  # raises on invalid JSON
    assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# (c) REDACTION -- strip_private_data scrubs secret-shaped content
# ---------------------------------------------------------------------------


def test_redaction_scrubs_secret_in_json_and_human(tmp_path):
    """A secret seeded into tool_metrics + events is scrubbed in both outputs.

    The raw secret must NOT appear verbatim; the [REDACTED_SECRET] marker must.
    This is the spec §7 Tier-1 invariant: every string field passes through
    strip_private_data before inclusion in the bundle.
    """
    db = tmp_path / "graph.db"

    def setup(conn):
        now = time.time()
        conn.execute(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
            "duration_ms, status, error_message) "
            "VALUES ('explore', 's1', ?, 12.0, 'error', ?)",
            (now, f"conn failed: {_SECRET}"),
        )
        conn.execute(
            "INSERT INTO events (ts, name, session_id, attrs) "
            "VALUES (?, 'ann_fallback', 's1', ?)",
            (now, json.dumps({"reason": "load_failed", "note": _EVENT_SECRET})),
        )

    _make_db(db, setup)

    for extra in (("--json",), ()):
        result = _run(db, *extra)
        assert result.exit_code == 0, result.output
        assert _SECRET not in result.output, f"raw secret leaked ({extra})"
        assert _EVENT_SECRET not in result.output, f"raw event secret leaked ({extra})"
        assert "[REDACTED_SECRET]" in result.output


# ---------------------------------------------------------------------------
# (d) --out writes a file
# ---------------------------------------------------------------------------


def test_out_writes_file_matching_stdout(tmp_path):
    """--out writes the bundle to a file as valid, section-complete JSON.

    Asserted against the file (not ``result.output``): with ``--out`` the
    write-confirmation goes to stderr, which CliRunner merges into
    ``result.output``, so only the file is guaranteed to hold clean JSON.
    """
    db = tmp_path / "graph.db"
    _make_db(db)
    out = tmp_path / "report.json"

    result = _run(db, "--json", "--out", str(out))
    assert result.exit_code == 0, result.output
    assert out.exists()

    data = json.loads(out.read_text())
    assert "generated_at" in data
    assert "versions" in data and "doctor" in data
    assert "recent_errors" in data and "config" in data
    assert [r["name"] for r in data["doctor"]] == _DOCTOR_NAMES


def test_out_writes_human_text_without_json(tmp_path):
    """Without --json, --out captures the human-readable text."""
    db = tmp_path / "graph.db"
    _make_db(db)
    out = tmp_path / "report.txt"

    result = _run(db, "--out", str(out))
    assert result.exit_code == 0, result.output
    assert out.exists()
    text = out.read_text()
    assert "cairn report" in text
    assert "Versions" in text


# ---------------------------------------------------------------------------
# (e) Graceful behavior on empty / missing / corrupt store
# ---------------------------------------------------------------------------


def test_graceful_on_empty_store(tmp_path):
    """A clean, empty store yields empty error sections and exits 0."""
    db = tmp_path / "graph.db"
    _make_db(db)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["recent_errors"] == {"events": [], "tool_errors": []}
    # Versions are still populated (they don't need the store).
    assert data["versions"]["cairn"]


def test_graceful_on_missing_store(tmp_path):
    """A store whose path can't be opened never crashes; schema FAILs."""
    bad = tmp_path / "nodir" / "missing.db"

    result = _run(bad, "--json")
    assert result.exit_code == 0, result.output
    # Parse result.stdout (pure JSON channel), NOT result.output -- click's
    # Result.output interleaves stderr, and a leaked DEBUG log line there would
    # break json.loads even though real-world stdout stays pure JSON.
    data = json.loads(result.stdout)
    # Runtime versions are independent of the store.
    assert data["versions"]["cairn"]
    assert data["versions"]["db_schema_user_version"] is None
    # DB-dependent sections degrade to empty / FAIL (mirrors cairn doctor).
    assert data["recent_errors"] == {"events": [], "tool_errors": []}
    schema = next(r for r in data["doctor"] if r["name"] == "schema")
    assert schema["status"] == "FAIL"
    assert "cannot open database" in schema["detail"]


def test_missing_store_in_existing_dir_never_created(tmp_path):
    """A missing store in an existing dir degrades without materializing.

    report is a read-only diagnostic; creating the store would mask a typo'd
    --db and put an empty fresh-install bundle in its place.
    """
    db = tmp_path / "typo.db"
    assert not db.exists()

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    schema = next(r for r in data["doctor"] if r["name"] == "schema")
    assert schema["status"] == "FAIL"
    assert "store not found" in schema["detail"]
    assert not db.exists(), "report must not materialize a store"


def test_graceful_on_corrupt_store(tmp_path):
    """A garbage (non-SQLite) file still produces a bundle; schema FAILs."""
    db = tmp_path / "garbage.db"
    db.write_bytes(b"\x00not a database\x00" * 20)

    result = _run(db, "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    schema = next(r for r in data["doctor"] if r["name"] == "schema")
    assert schema["status"] == "FAIL"


# ---------------------------------------------------------------------------
# (c2) PATH REDACTION -- absolute local paths collapse to [PATH]/<basename>
#
# str(exc) from file I/O routinely embeds absolute paths; strip_private_data
# does not touch them. The privacy gate must collapse them so the bundle
# never ships the user's directory structure.
# ---------------------------------------------------------------------------

_USER_DIR = "/Users/secret-project"


def test_redaction_collapses_absolute_paths(tmp_path):
    """Absolute paths in tool errors / events / doctor detail become [PATH]/x.

    The user's directory component must NOT appear in either output form;
    the basename survives so the report stays debuggable.
    """
    db = tmp_path / "graph.db"

    def setup(conn):
        now = time.time()
        conn.execute(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
            "duration_ms, status, error_message) "
            "VALUES ('get_memory', 's1', ?, 3.0, 'error', ?)",
            (now, f"[Errno 2] No such file or directory: {_USER_DIR}/memory/tribal/foo.md"),
        )
        conn.execute(
            "INSERT INTO events (ts, name, session_id, attrs) "
            "VALUES (?, 'ann_fallback', 's1', ?)",
            (now, json.dumps({"reason": "load_failed"})),
        )

    _make_db(db, setup)

    for extra in (("--json",), ()):
        result = _run(db, *extra)
        assert result.exit_code == 0, result.output
        assert _USER_DIR not in result.output, f"absolute path leaked ({extra})"
        assert "[PATH]/foo.md" in result.output, f"basename marker missing ({extra})"


def test_redaction_keeps_relative_and_url_paths(tmp_path):
    """Workspace-relative paths and URL path portions survive redaction.

    Only ABSOLUTE local paths leak directory structure; ``src/main.py`` and
    ``https://collector/x`` are useful, non-identifying signals.
    """
    from cairn.cli.system import _redact_paths

    assert _redact_paths("src/main.py: SyntaxError at line 3") == "src/main.py: SyntaxError at line 3"
    assert _redact_paths("POST https://collector.internal:4318/v1/logs failed") == \
        "POST https://collector.internal:4318/v1/logs failed"
    assert _redact_paths("postgres://user:pass@host/db") == "postgres://user:pass@host/db"
    # Absolute forms collapse, basename kept; ~ shorthand and Windows too.
    assert _redact_paths(f"open failed: {_USER_DIR}/src/main.py:10") == \
        "open failed: [PATH]/main.py:10"
    assert _redact_paths("config at ~/.config/cairn/config.toml") == \
        "config at [PATH]/config.toml"
    assert _redact_paths(r"missing C:\Users\bob\work\app\db.sqlite") == \
        "missing [PATH]/db.sqlite"
