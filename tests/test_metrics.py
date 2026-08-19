"""Tests for MCP tool metric buffering (task T05).

``metric_buffering`` is the buffered-sink pattern that all 27 MCP tools flow
through: each invocation is timed, tagged ok/error, buffered in a process-global
deque, and flushed to ``tool_metrics`` on a 30s daemon thread + atexit. This
file is the first test coverage for that path.

Covers:
  1. ``instrument`` -- ok path records (tool, duration, status='ok'); error
     path records status='error' + the exception message AND re-raises; string
     results over ``MAX_RESULT_CHARS`` are truncated with the remediation note
     while non-string results pass through untouched; ``functools.wraps``
     metadata is preserved so ``@mcp.tool()`` introspection stays intact.
  2. ``_truncate_result`` -- under cap unchanged; over cap the head is cut at
     the last newline and the ``[TRUNCATED: ...]`` suffix names the tool, the
     cap, and the remediation; the at-cap boundary is left alone.
  3. ``_flush_metrics`` -- a successful executemany+commit drains exactly the
     flushed batch (rows appended *during* the flush survive); a failed flush
     (conn factory raises) retains the rows for retry; an empty buffer or a
     missing conn factory are no-ops.
  4. ``CAIRN_READ_ONLY=1`` makes ``_log_metric`` a no-op; ``CAIRN_SESSION``
     stamps ``session_id`` ('unknown' default). (The ``CAIRN_TELEMETRY=off``
     master-switch gate and error-message redaction added on top of
     ``_log_metric`` are covered in ``test_redaction_chokepoints.py``.)
  5. Extended payload columns (``req_chars`` / ``resp_chars`` /
     ``args_summary``) -- trailing-kwargs calls round-trip through a flush
     while positional-only calls leave the new columns NULL; ``instrument``
     measures the call (kwargs-JSON length, post-truncation result length,
     the JSON summary); ``args_summary`` is scrubbed and capped at
     ``MAX_ARGS_SUMMARY_CHARS`` at the write chokepoint; a single explicit
     flush drains a K-row buffer completely (no silent drops).

Module-global state (``_METRIC_BUFFER``, ``_conn_factory``,
``_METRIC_FLUSHER_STARTED``) is reset by the autouse ``_reset_metric_state``
fixture so no test poisons its siblings -- the production flusher never clears
these between sessions. ``_flush_metrics`` is called directly rather than
waiting on the daemon's 30s sleep, and no test relies on ``time.sleep``.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from cairn.mcp_server import metric_buffering as mb


# ---------------------------------------------------------------------------
# Module-global state reset
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_metric_state(monkeypatch):
    """Reset metric_buffering's process-global state around each test.

    ``_METRIC_BUFFER`` (deque), ``_conn_factory``, and
    ``_METRIC_FLUSHER_STARTED`` are module-level and never cleared in
    production (the flusher only drains after a successful commit). Without
    this reset, rows/connections from one test would bleed into the next.
    ``CAIRN_READ_ONLY``, ``CAIRN_SESSION``, and ``CAIRN_TELEMETRY`` are also
    cleared so read-only, session, and telemetry-gate tests start from a
    known baseline; ``monkeypatch`` restores the originals on teardown.
    """
    with mb._METRIC_LOCK:
        mb._METRIC_BUFFER.clear()
    mb._conn_factory = None
    mb._METRIC_FLUSHER_STARTED = False
    monkeypatch.delenv("CAIRN_READ_ONLY", raising=False)
    monkeypatch.delenv("CAIRN_SESSION", raising=False)
    monkeypatch.delenv("CAIRN_TELEMETRY", raising=False)
    yield
    # Tear down: clear again so a stray daemon-flusher tick (from a test that
    # called _log_metric, which starts the flusher) can't write leftover rows
    # into a connection the next test isn't expecting.
    with mb._METRIC_LOCK:
        mb._METRIC_BUFFER.clear()
    mb._conn_factory = None


class _UnclosableConn:
    """Wraps a sqlite connection so ``close()`` is a no-op.

    ``_flush_metrics`` closes the connection it opens in a ``finally`` block.
    The ``fresh_db`` fixture yields a single private ``:memory:`` connection
    that is destroyed once closed, so without this wrapper the test could not
    read back the rows it just flushed. Only the three methods
    ``_flush_metrics`` touches (``executemany``, ``commit``, ``close``) are
    forwarded.
    """

    def __init__(self, real: sqlite3.Connection):
        self._real = real

    def executemany(self, sql, params):
        return self._real.executemany(sql, params)

    def commit(self):
        return self._real.commit()

    def close(self):  # keep the underlying DB alive for post-flush assertions
        pass


# ---------------------------------------------------------------------------
# 1. instrument decorator
# ---------------------------------------------------------------------------


def test_instrument_ok_path_records_metric_row(monkeypatch):
    """A successful call appends exactly one (tool, duration, status='ok') row.

    The row carries the function's ``__name__`` as ``tool_name``, the
    'unknown' default session, a positive duration, and a NULL error message
    (``error_message=''`` is falsy and stored as None).
    """
    monkeypatch.delenv("CAIRN_SESSION", raising=False)

    @mb.instrument
    def my_tool():
        return "ok"

    result = my_tool()
    assert result == "ok"

    rows = list(mb._METRIC_BUFFER)
    assert len(rows) == 1, "exactly one metric row per successful call"
    (tool_name, session_id, invoked_at, duration_ms, status, error_message,
     _req, _resp, _args) = rows[0]
    assert tool_name == "my_tool"
    assert session_id == "unknown"
    assert status == "ok"
    assert error_message is None, "ok path stores None, not empty string"
    assert isinstance(duration_ms, (int, float)) and duration_ms >= 0
    assert isinstance(invoked_at, float)


def test_instrument_error_path_records_status_and_reraises():
    """A raised exception records status='error' + str(exc), then re-raises.

    Re-raising is load-bearing: FastMCP's ``Tool.run`` converts the exception
    into an MCP error response (``isError: true``). If ``instrument``
    swallowed it, the client would see a prose string that looks like success.
    """

    @mb.instrument
    def boom():
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        boom()

    rows = list(mb._METRIC_BUFFER)
    assert len(rows) == 1, "exactly one error row even on exception"
    tool_name, _session, _ts, _dur, status, error_message, _req, _resp, _args = rows[0]
    assert tool_name == "boom"
    assert status == "error"
    assert error_message == "kaboom"


def test_instrument_truncates_long_string_result(monkeypatch):
    """A string result over MAX_RESULT_CHARS is truncated with the note.

    ``instrument`` centralizes truncation so a tool that forgets its own
    limit degrades to a notice instead of the MCP client's opaque
    'exceeds maximum allowed tokens' hard failure.
    """
    monkeypatch.setattr(mb, "MAX_RESULT_CHARS", 50)

    @mb.instrument
    def big_result():
        return "x" * 200

    result = big_result()
    assert "[TRUNCATED:" in result
    assert "'big_result'" in result, "tool name appears in the notice"
    assert "200 chars" in result, "original length reported"
    assert "50-char cap" in result
    assert "Narrow the query" in result, "remediation hint present"
    # The returned content (head before the notice) is bounded by the cap, not
    # the full output -- the suffix itself is ~220 chars of guidance, so total
    # length can exceed a small original. What matters is the payload is gone.
    head = result.split("\n\n[TRUNCATED:")[0]
    assert len(head) <= 50, f"head bounded by cap, got {len(head)} chars"


def test_instrument_leaves_non_string_result_untouched():
    """Non-string results (dict, list, int, None) bypass truncation entirely.

    ``_truncate_result`` only applies to ``str``; returning a structured
    object (common for tools returning dataclasses/JSON) must pass through
    unchanged so callers don't receive a truncated string by accident.
    """

    @mb.instrument
    def returns_dict():
        return {"key": "value", "n": 42}

    assert returns_dict() == {"key": "value", "n": 42}

    @mb.instrument
    def returns_none():
        return None

    assert returns_none() is None

    # Still recorded a metric row in each case.
    assert len(mb._METRIC_BUFFER) == 2


def test_instrument_preserves_function_metadata():
    """``functools.wraps`` keeps ``__name__``/``__wrapped__`` intact.

    FastMCP's ``@mcp.tool()`` introspects the signature of whatever it
    decorates to build the tool's JSON schema; without ``__wrapped__`` it
    would only see ``(*args, **kwargs)`` and emit a broken schema.
    """

    @mb.instrument
    def find_definition(name: str, fuzzy: bool = False) -> str:
        """Docstring that must survive wrapping."""
        return name

    assert find_definition.__name__ == "find_definition"
    assert find_definition.__doc__ == "Docstring that must survive wrapping."
    assert find_definition.__wrapped__ is find_definition.__wrapped__  # attr exists


# ---------------------------------------------------------------------------
# 2. _truncate_result
# ---------------------------------------------------------------------------


def test_truncate_under_cap_returns_unchanged(monkeypatch):
    """Strings at or below the cap pass through verbatim."""
    monkeypatch.setattr(mb, "MAX_RESULT_CHARS", 100)
    assert mb._truncate_result("tool", "short string") == "short string"


def test_truncate_at_cap_boundary_returns_unchanged(monkeypatch):
    """A string whose length equals the cap is NOT truncated (``<=`` guard)."""
    monkeypatch.setattr(mb, "MAX_RESULT_CHARS", 10)
    text = "0123456789"  # exactly 10 chars
    assert mb._truncate_result("tool", text) == text


def test_truncate_over_cap_cuts_at_last_newline(monkeypatch):
    """Over cap, the head is cut at the last newline within the cap window.

    Cutting at a newline boundary keeps the truncation note from landing
    mid-line, so the output stays readable instead of splitting a token.
    """
    monkeypatch.setattr(mb, "MAX_RESULT_CHARS", 30)
    # Four 10-char lines joined by newlines (total 43 chars > 30).
    text = "0123456789\n0123456789\n0123456789\n0123456789"
    result = mb._truncate_result("explore", text)

    assert "[TRUNCATED:" in result
    assert "'explore'" in result
    assert "43 chars" in result, "original length reported"
    assert "30-char cap" in result
    assert "Narrow the query" in result, "remediation present"

    # The head is everything before the "\n\n[TRUNCATED:" separator.
    # text[:30] = "0123456789\n0123456789\n01234567" -- last \n is at index 21,
    # so the head is the first two complete lines (cut at the newline).
    head = result.split("\n\n[TRUNCATED:")[0]
    assert head == "0123456789\n0123456789", (
        f"head should end at a newline boundary, got: {head!r}"
    )
    # The head payload is bounded by the cap; the suffix is fixed guidance text
    # (~220 chars) so total output length is not a meaningful bound here.
    assert len(head) < len(text)


def test_truncate_over_cap_no_newline_keeps_full_head(monkeypatch):
    """When there is no newline in the cap window, the full head is kept.

    ``rfind`` returns -1 (not > 0), so the ``if cut > 0`` guard is skipped
    and ``head`` stays as ``result[:cap]`` -- the note still appends.
    """
    monkeypatch.setattr(mb, "MAX_RESULT_CHARS", 20)
    text = "a" * 100  # no newlines anywhere
    result = mb._truncate_result("search_symbols", text)

    assert result.startswith("a" * 20), "full 20-char head retained"
    assert "\n\n[TRUNCATED:" in result
    assert "'search_symbols'" in result
    assert "100 chars" in result


# ---------------------------------------------------------------------------
# 3. _flush_metrics
# ---------------------------------------------------------------------------


def test_flush_empty_buffer_is_noop(fresh_db):
    """Flushing an empty buffer touches neither the DB nor the lock's drain."""
    mb.configure_conn(lambda: _UnclosableConn(fresh_db))
    mb._flush_metrics()  # must not raise

    count = fresh_db.execute("SELECT COUNT(*) AS c FROM tool_metrics").fetchone()["c"]
    assert count == 0
    assert len(mb._METRIC_BUFFER) == 0


def test_flush_without_conn_factory_is_noop():
    """With no conn_factory configured, _flush_metrics returns after snapshotting.

    The buffer is snapshotted but never drained (the rows stay queued) so a
    later ``configure_conn`` + flush can still land them.
    """
    # _conn_factory is None (reset by the autouse fixture).
    mb._log_metric("tool_a", 10.0, "ok")
    assert len(mb._METRIC_BUFFER) == 1

    mb._flush_metrics()  # must not raise

    assert len(mb._METRIC_BUFFER) == 1, "row retained when no factory is set"


def test_flush_drains_buffer_on_success(fresh_db):
    """A successful executemany+commit drains the buffer into tool_metrics.

    Both ok and error rows land with their status and (for errors) the
    truncated error message intact.
    """
    mb.configure_conn(lambda: _UnclosableConn(fresh_db))
    mb._log_metric("tool_a", 10.0, "ok")
    mb._log_metric("tool_b", 20.0, "error", "disk full")
    assert len(mb._METRIC_BUFFER) == 2

    mb._flush_metrics()

    assert len(mb._METRIC_BUFFER) == 0, "buffer drained after successful commit"
    rows = fresh_db.execute(
        "SELECT tool_name, status, error_message FROM tool_metrics ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert (rows[0]["tool_name"], rows[0]["status"], rows[0]["error_message"]) == (
        "tool_a",
        "ok",
        None,
    )
    assert (rows[1]["tool_name"], rows[1]["status"], rows[1]["error_message"]) == (
        "tool_b",
        "error",
        "disk full",
    )


def test_flush_keeps_rows_appended_during_flush(fresh_db):
    """Rows appended mid-flush survive: the drain pops exactly len(snapshot).

    ``_flush_metrics`` snapshots the buffer, writes the snapshot, then
    ``popleft``s exactly ``len(batch)`` rows. A row appended by another
    thread (or, here, inside ``executemany``) after the snapshot sits to the
    right of the drained rows and is left queued for the next flush.
    """
    concurrent_row = ("concurrent_tool", "unknown", 9999.0, 5.0, "ok", None)

    class _LateAppendConn(_UnclosableConn):
        def executemany(self, sql, params):
            # Simulate a concurrent tool call landing while the flush is
            # mid-write (between snapshot and drain).
            with mb._METRIC_LOCK:
                mb._METRIC_BUFFER.append(concurrent_row)
            return self._real.executemany(sql, params)

    mb.configure_conn(lambda: _LateAppendConn(fresh_db))
    mb._log_metric("tool_a", 10.0, "ok")
    mb._log_metric("tool_b", 20.0, "ok")
    assert len(mb._METRIC_BUFFER) == 2

    mb._flush_metrics()

    # The late-appended row survived the drain.
    assert list(mb._METRIC_BUFFER) == [concurrent_row], (
        "only the pre-flush snapshot was drained; the concurrent row survives"
    )
    # Only the two pre-flush rows were written.
    rows = fresh_db.execute("SELECT tool_name FROM tool_metrics ORDER BY id").fetchall()
    assert [r["tool_name"] for r in rows] == ["tool_a", "tool_b"]


def test_flush_failure_retains_buffer_for_retry(fresh_db):
    """When the conn factory raises, rows stay buffered for the next attempt.

    Metrics are best-effort: a transient failure (e.g. 'database is locked')
    must never raise into the caller and must never drop rows silently --
    they remain queued so the next flush tick retries them.
    """

    def _boom_factory():
        raise RuntimeError("database is locked")

    mb.configure_conn(_boom_factory)
    mb._log_metric("tool_a", 10.0, "ok")
    mb._log_metric("tool_b", 20.0, "ok")
    assert len(mb._METRIC_BUFFER) == 2

    mb._flush_metrics()  # must not raise

    assert len(mb._METRIC_BUFFER) == 2, "rows retained for retry on flush failure"
    count = fresh_db.execute("SELECT COUNT(*) AS c FROM tool_metrics").fetchone()["c"]
    assert count == 0, "nothing landed in the DB"


def test_flush_executemany_failure_retains_buffer(fresh_db):
    """A failure inside executemany (not the factory) also retains the buffer.

    This is the more common real failure mode -- the connection opens fine
    but the INSERT itself hits 'database is locked'. The same try/except
    guard catches it and leaves the rows queued.
    """

    class _LockedWriteConn(_UnclosableConn):
        def executemany(self, sql, params):
            raise sqlite3.OperationalError("database is locked")

    mb.configure_conn(lambda: _LockedWriteConn(fresh_db))
    mb._log_metric("tool_a", 10.0, "ok")
    assert len(mb._METRIC_BUFFER) == 1

    mb._flush_metrics()  # must not raise

    assert len(mb._METRIC_BUFFER) == 1, "row retained when executemany raises"


# ---------------------------------------------------------------------------
# 4. CAIRN_READ_ONLY / CAIRN_SESSION env gates
# ---------------------------------------------------------------------------


def test_log_metric_skipped_when_read_only(monkeypatch):
    """CAIRN_READ_ONLY=1 makes _log_metric a no-op so the buffer never fills.

    Read-only daemons open the DB with mode=ro, so INSERT would fail every
    flush and buffer indefinitely (capped by the deque maxlen). Skipping the
    write entirely keeps the table from silently staying empty and stops the
    flush thread from spinning on a guaranteed failure.
    """
    monkeypatch.setenv("CAIRN_READ_ONLY", "1")
    mb._log_metric("tool", 10.0, "ok")
    assert len(mb._METRIC_BUFFER) == 0, "read-only mode skips the buffer entirely"


@pytest.mark.parametrize("flag", ["1", "true", "yes"])
def test_log_metric_skipped_for_all_read_only_truthy_values(monkeypatch, flag):
    """All three truthy spellings of CAIRN_READ_ONLY gate the write."""
    monkeypatch.setenv("CAIRN_READ_ONLY", flag)
    mb._log_metric("tool", 10.0, "ok")
    assert len(mb._METRIC_BUFFER) == 0


def test_log_metric_records_when_read_only_unset(monkeypatch):
    """With CAIRN_READ_ONLY unset (or falsy), the row is buffered normally."""
    monkeypatch.delenv("CAIRN_READ_ONLY", raising=False)
    mb._log_metric("tool", 10.0, "ok")
    assert len(mb._METRIC_BUFFER) == 1


def test_session_env_stamps_session_id(monkeypatch):
    """CAIRN_SESSION stamps the session_id column for cross-call correlation."""
    monkeypatch.setenv("CAIRN_SESSION", "trace-42")
    mb._log_metric("tool", 10.0, "ok")
    assert len(mb._METRIC_BUFFER) == 1
    _tool, session_id, _ts, _dur, _status, _err, _req, _resp, _args = mb._METRIC_BUFFER[0]
    assert session_id == "trace-42"


def test_session_defaults_to_unknown(monkeypatch):
    """Without CAIRN_SESSION, session_id defaults to 'unknown'."""
    monkeypatch.delenv("CAIRN_SESSION", raising=False)
    mb._log_metric("tool", 10.0, "ok")
    assert len(mb._METRIC_BUFFER) == 1
    _tool, session_id, _ts, _dur, _status, _err, _req, _resp, _args = mb._METRIC_BUFFER[0]
    assert session_id == "unknown"


# ---------------------------------------------------------------------------
# 5. Extended payload columns (req_chars / resp_chars / args_summary)
# ---------------------------------------------------------------------------


def test_log_metric_extended_kwargs_round_trip_through_flush(fresh_db):
    """Trailing-kwargs calls persist the new size/summary columns verbatim.

    The buffered tuple carries the three new fields in trailing positions
    and a flush lands them in the columns the dashboard reads -- a
    row-tuple/INSERT column-count mismatch would instead show up as rows
    permanently buffered at debug level, so the SELECT proves the lockstep.
    """
    mb.configure_conn(lambda: _UnclosableConn(fresh_db))
    summary = '{"query":"get_symbol_graph","depth":2}'

    mb._log_metric(
        "explore",
        12.5,
        "ok",
        req_chars=len(summary),
        resp_chars=4321,
        args_summary=summary,
    )

    row = mb._METRIC_BUFFER[0]
    (_tool, _sess, _ts, _dur, _status, _err, req, resp, args) = row
    assert (req, resp, args) == (len(summary), 4321, summary)

    mb._flush_metrics()

    stored = fresh_db.execute(
        "SELECT req_chars, resp_chars, args_summary FROM tool_metrics"
    ).fetchone()
    assert stored["req_chars"] == len(summary)
    assert stored["resp_chars"] == 4321
    assert stored["args_summary"] == summary


def test_log_metric_positional_backcompat_stores_null_new_columns(fresh_db):
    """Positional-only calls (the pre-extension shape) stay valid.

    The payload kwargs are optional so a caller that measures nothing leaves
    NULL columns, never a broken row: legacy rows and extended rows coexist
    in one table, and pre-existing call sites need no change.
    """
    mb.configure_conn(lambda: _UnclosableConn(fresh_db))
    mb._log_metric("legacy_tool", 10.0, "ok")
    mb._log_metric("legacy_tool", 20.0, "error", "boom")
    assert len(mb._METRIC_BUFFER) == 2

    mb._flush_metrics()

    rows = fresh_db.execute(
        "SELECT status, req_chars, resp_chars, args_summary "
        "FROM tool_metrics ORDER BY id"
    ).fetchall()
    assert [r["status"] for r in rows] == ["ok", "error"]
    for r in rows:
        assert r["req_chars"] is None
        assert r["resp_chars"] is None
        assert r["args_summary"] is None


def test_instrument_captures_sizes_and_args_summary():
    """The wrapper measures the call into the new columns.

    req_chars/args_summary come from the compact JSON of the call's kwargs
    (the call shape, not a payload replay); resp_chars is the length of what
    the client actually receives, measured after the cap. On the error path
    there is no result, so resp_chars stays NULL while the request side is
    still recorded.
    """

    @mb.instrument
    def explore(query: str, fuzzy: bool = False) -> str:
        return "x" * 120

    @mb.instrument
    def boom(query: str) -> str:
        raise ValueError("kaboom")

    explore(query="metric_buffering", fuzzy=True)
    with pytest.raises(ValueError, match="kaboom"):
        boom(query="explosion")

    expected = json.dumps(
        {"query": "metric_buffering", "fuzzy": True},
        default=str,
        separators=(",", ":"),
    )
    _t1, _s1, _ts1, _d1, status1, _e1, req1, resp1, args1 = mb._METRIC_BUFFER[0]
    assert status1 == "ok"
    assert req1 == len(expected)
    assert resp1 == 120, "post-truncation result length (under cap, unchanged)"
    assert args1 == expected

    _t2, _s2, _ts2, _d2, status2, _e2, req2, resp2, args2 = mb._METRIC_BUFFER[1]
    assert status2 == "error"
    assert req2 == len(json.dumps({"query": "explosion"}, separators=(",", ":")))
    assert resp2 is None, "no result on the error path -> NULL, not 0"
    assert args2 == '{"query":"explosion"}'


def test_args_summary_redacted_at_write_chokepoint(fresh_db):
    """Secret-shaped kwargs are scrubbed from args_summary before storing.

    kwargs routinely embed credentials; the summary is run through
    ``strip_private_data`` inside ``_log_metric`` so the raw secret is never
    buffered nor persisted -- redact-then-store, never the reverse.
    """
    mb.configure_conn(lambda: _UnclosableConn(fresh_db))
    raw_summary = (
        '{"dsn":"postgres://admin:sup3rs3cret@db.internal:5432/prod",'
        '"api_key":"sk-proj-abcdefghijklmnopqrstuvwx",'
        '"note":"<private>inner words</private>"}'
    )

    mb._log_metric("recall_memory", 5.0, "ok", args_summary=raw_summary)

    buffered = mb._METRIC_BUFFER[0][-1]
    assert "sup3rs3cret" not in buffered
    assert "sk-proj-abcdefghijklmnopqrstuvwx" not in buffered
    assert "<private>" not in buffered
    assert "[REDACTED_SECRET]" in buffered
    assert "[REDACTED]" in buffered

    mb._flush_metrics()
    stored = fresh_db.execute(
        "SELECT args_summary FROM tool_metrics"
    ).fetchone()["args_summary"]
    assert "sup3rs3cret" not in stored
    assert "sk-proj-abcdefghijklmnopqrstuvwx" not in stored
    assert "[REDACTED_SECRET]" in stored


def test_args_summary_truncated_at_write_chokepoint():
    """args_summary is capped at MAX_ARGS_SUMMARY_CHARS when buffered.

    The summary identifies the call shape, not a payload replay -- anything
    past the cap is noise in an analytics table. The cut is a plain prefix
    slice (redaction has already run); a summary at exactly the cap passes
    through unmodified.
    """
    long_summary = '{"query":"' + "x" * 400 + '"}'
    mb._log_metric("search_symbols", 5.0, "ok", args_summary=long_summary)
    at_cap = '{"q":"' + "y" * 193 + "}"
    assert len(at_cap) == mb.MAX_ARGS_SUMMARY_CHARS
    mb._log_metric("search_symbols", 6.0, "ok", args_summary=at_cap)

    stored_long = mb._METRIC_BUFFER[0][-1]
    stored_at_cap = mb._METRIC_BUFFER[1][-1]
    assert len(stored_long) == mb.MAX_ARGS_SUMMARY_CHARS
    assert stored_long == long_summary[: mb.MAX_ARGS_SUMMARY_CHARS]
    assert stored_at_cap == at_cap, "at-cap boundary is left alone"


def test_single_explicit_flush_drains_every_buffered_row(fresh_db, monkeypatch):
    """Durability: one explicit flush drains 100% of a K-row buffer.

    K=60 extended rows are buffered across two sessions with one error
    among them (K >= 50 sits far past any single-row hand-wave while
    staying under the deque's 2000 maxlen, so nothing was dropped before the
    flush either). The shared flush daemon ticks on a 30s cadence and this
    test never sleeps, so the daemon cannot have interfered -- the one
    explicit flush alone accounts for every row: count == K, buffer empty,
    and every new column populated wherever it was provided.
    """
    mb.configure_conn(lambda: _UnclosableConn(fresh_db))
    k = 60

    monkeypatch.setenv("CAIRN_SESSION", "sess-alpha")
    for i in range(30):
        mb._log_metric(
            "explore",
            5.0 + i,
            "ok",
            req_chars=10 + i,
            resp_chars=1000 + i,
            args_summary=f'{{"i":{i},"scope":"symbol"}}',
        )
    monkeypatch.setenv("CAIRN_SESSION", "sess-beta")
    for i in range(30):
        if i == 29:
            mb._log_metric(
                "search_symbols",
                7.0 + i,
                "error",
                "synthetic failure",
                req_chars=10 + i,
                args_summary=f'{{"i":{i},"pattern":"metric*"}}',
            )
        else:
            mb._log_metric(
                "search_symbols",
                7.0 + i,
                "ok",
                req_chars=10 + i,
                resp_chars=1000 + i,
                args_summary=f'{{"i":{i},"pattern":"metric*"}}',
            )

    assert len(mb._METRIC_BUFFER) == k

    mb._flush_metrics()

    assert len(mb._METRIC_BUFFER) == 0, "explicit flush drained every buffered row"
    count = fresh_db.execute("SELECT COUNT(*) AS c FROM tool_metrics").fetchone()["c"]
    assert count == k

    rows = fresh_db.execute(
        "SELECT session_id, status, req_chars, resp_chars, args_summary "
        "FROM tool_metrics"
    ).fetchall()
    per_session = {"sess-alpha": 0, "sess-beta": 0}
    errors = 0
    for r in rows:
        per_session[r["session_id"]] += 1
        errors += r["status"] == "error"
        assert r["req_chars"] is not None, "req_chars populated in every row"
        assert r["args_summary"] is not None, "args_summary populated in every row"
        if r["status"] == "ok":
            assert r["resp_chars"] is not None
        else:
            # The one error row provided no resp_chars (no result exists).
            assert r["resp_chars"] is None
    assert per_session == {"sess-alpha": 30, "sess-beta": 30}
    assert errors == 1
