"""Export-parity tests for the CSV/JSON export routes (TC-007 / FR-005,
spec ui-dashboard-polish).

An export must contain exactly the rows the filtered view shows: every
route's rows are compared for identity against the view's own data
function (``list_history`` / ``get_tool_tokens``) under the same filter,
window, and store params the request carried -- ``limit=_EXPORT_ROW_LIMIT``
on the history side, because exports are unpaginated while the view pages.
The CSV half round-trips through :mod:`csv` (RFC 4180): the seeded
``args_summary`` / ``error_message`` values contain commas, double quotes,
and embedded newlines, and must survive verbatim with quotes doubled at
the byte level. Parity holds in either tokenizer mode by construction --
both sides of each comparison go through the same in-process data
function, so no semantic-extra pinning is needed.
"""
from __future__ import annotations

import csv
import io
import sqlite3
import time

import pytest

# Values designed to break a naive comma-join CSV writer: embedded
# commas, doubled-quote escapes, LF and CRLF inside one field, and all
# three combined.
_HOSTILE_ARGS = (
    "plain-summary",
    "alpha, beta, gamma",
    'he said "run it"',
    "line one\nline two",
    "crlf first\r\nsecond",
    'quoted, comma "and" newline\nmixed "x"',
)

_EXPLORE_ROWS = 60  # > HISTORY_PAGE_SIZE: the export must not page


def _seed_export_store(db_path: str) -> None:
    """A graph-schema store whose rows exercise every export filter and
    every CSV-hostile field shape.

    60 ``explore``/``cli`` calls inside the last hour (one pre-migration
    NULL-sizes row per nine, one NULL ``args_summary``); 3 ``explore``/``mcp``
    calls 10 days back (inside 30d, outside 7d/24h); 4 ``ask_compass``
    calls 40 days back (outside every preset); 2 ``edge tool``/``mcp``
    calls inside the last hour, one an error whose message carries
    commas, quotes, and a newline.
    """
    from cairn.graph.schema import _apply_schema

    now = time.time()
    rows = []
    for i in range(_EXPLORE_ROWS):
        null_sizes = i % 9 == 8
        rows.append(
            (
                "explore",
                "sess-a",
                now - 60.0 * (i + 1),
                5.0 + i,
                "ok",
                None,
                None if null_sizes else 120 + i,
                None if null_sizes else 2400 + 10 * i,
                None if i == _EXPLORE_ROWS - 1 else _HOSTILE_ARGS[i % len(_HOSTILE_ARGS)],
                "cli",
            )
        )
    for i in range(3):
        rows.append(
            ("explore", "sess-a2", now - 10 * 86400 - i, 30.0, "ok", None,
             500, 900, "old, mcp call", "mcp")
        )
    for i in range(4):
        rows.append(
            ("ask_compass", "sess-b", now - 40 * 86400 - i, 40.0, "ok", None,
             50, 80, 'ancient "quoted" args', "mcp")
        )
    rows.append(("edge tool", "sess-c", now - 120.0, 10.0, "ok", None,
                 10, 20, "space in name", "mcp"))
    rows.append(("edge tool", "sess-c", now - 121.0, 10.0, "error",
                 'boom, "bad" thing\nfailed', 10, None, "failed call", "mcp"))

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    conn.executemany(
        "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
        "duration_ms, status, error_message, req_chars, resp_chars, "
        "args_summary, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _export_db_file(tmp_path) -> str:
    db_path = str(tmp_path / "export.db")
    _seed_export_store(db_path)
    return db_path


def _client_over(db_path: str, tmp_path):
    """A dashboard TestClient over ``db_path`` (httpx optional, matching
    the other dashboard suites)."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app

    return TestClient(
        create_app(db_path=db_path, knowledge_dir=str(tmp_path / "missing"))
    )


def _history_rows(db_path: str, **params) -> list:
    """The view's data function under the caller's exact filters, at the
    export's unpaginated limit -- TC-007's parity oracle."""
    from cairn.dashboard.app import _EXPORT_ROW_LIMIT
    from cairn.dashboard.data import get_read_only_db, list_history

    conn = get_read_only_db(db_path)
    try:
        return list_history(conn, limit=_EXPORT_ROW_LIMIT, **params)["rows"]
    finally:
        conn.close()


def _since(window: str):
    from cairn.dashboard.app import _resolve_window

    return _resolve_window(window)[1]


def _parse_csv(text: str) -> list:
    return list(csv.reader(io.StringIO(text)))


def _assert_csv_parity(text: str, expected_rows: list) -> None:
    """CSV body vs the oracle rows: the header is the row dict's keys and
    every field round-trips (None as the empty field, others as str())."""
    parsed = _parse_csv(text)
    header, body = parsed[0], parsed[1:]
    assert header == list(expected_rows[0].keys())
    assert len(body) == len(expected_rows)
    for fields, row in zip(body, expected_rows):
        assert fields == ["" if row[k] is None else str(row[k]) for k in header]


# ---------------------------------------------------------------------------
# TC-007: history export parity (filtered row sets, quoted fields)
# ---------------------------------------------------------------------------


def test_history_json_export_matches_filtered_view_rows(tmp_path):
    """TC-007 / FR-005: /history.json under tool+source+window filters
    returns exactly the rows ``list_history`` returns under the same
    params -- a bare JSON row array, so parsed identity is the check."""
    db_path = _export_db_file(tmp_path)
    client = _client_over(db_path, tmp_path)

    resp = client.get(
        "/history.json",
        params={"tool": "explore", "source": "cli", "window": "24h"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")

    expected = _history_rows(
        db_path, tool_name="explore", source="cli", since=_since("24h")
    )
    assert resp.json() == expected
    assert len(expected) == _EXPLORE_ROWS  # filters excluded the other rows


def test_history_csv_export_roundtrips_quoted_fields(tmp_path):
    """TC-007 / FR-005: /history.csv under the same filters parses back
    via csv.reader into exactly the oracle rows, and the RFC 4180 quoting
    is visible at the byte level (embedded quotes doubled inside a
    quoted field); every hostile args_summary value survives verbatim."""
    db_path = _export_db_file(tmp_path)
    client = _client_over(db_path, tmp_path)

    resp = client.get(
        "/history.csv",
        params={"tool": "explore", "source": "cli", "window": "24h"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")

    # Byte-level RFC 4180: the embedded quotes are doubled, the whole
    # field wrapped -- a naive comma-join cannot produce this.
    assert '"he said ""run it"""' in resp.text

    expected = _history_rows(
        db_path, tool_name="explore", source="cli", since=_since("24h")
    )
    _assert_csv_parity(resp.text, expected)

    args_idx = _parse_csv(resp.text)[0].index("args_summary")
    surviving = {row[args_idx] for row in _parse_csv(resp.text)[1:]}
    for hostile in _HOSTILE_ARGS:
        assert hostile in surviving  # commas, quotes, LF, CRLF intact


def test_history_export_is_unpaginated_beyond_one_page(tmp_path):
    """TC-007: the export is not the view's first page -- with 60 matching
    rows the view pages at HISTORY_PAGE_SIZE while the export carries all
    of them."""
    from cairn.dashboard.data import (
        HISTORY_PAGE_SIZE,
        get_read_only_db,
        list_history,
    )

    assert _EXPLORE_ROWS > HISTORY_PAGE_SIZE  # the page cap is really exceeded

    db_path = _export_db_file(tmp_path)
    client = _client_over(db_path, tmp_path)

    resp = client.get(
        "/history.json",
        params={"tool": "explore", "source": "cli", "window": "24h"},
    )
    assert len(resp.json()) == _EXPLORE_ROWS

    # The view's own call (default page limit) would stop at one page.
    conn = get_read_only_db(db_path)
    try:
        view_page = list_history(
            conn, tool_name="explore", source="cli", since=_since("24h")
        )
    finally:
        conn.close()
    assert len(view_page["rows"]) == HISTORY_PAGE_SIZE
    assert view_page["next"] is not None


def test_history_export_parity_with_session_and_hostile_tool_name(tmp_path):
    """TC-007: the session filter composes in parity too, a tool name
    with a space filters exactly, and the error row's hostile
    error_message survives the CSV round-trip; the attachment filename
    collapses the unsafe characters (space -> ``_``) while keeping the
    filter hints."""
    db_path = _export_db_file(tmp_path)
    client = _client_over(db_path, tmp_path)

    expected = _history_rows(db_path, tool_name="edge tool", session_id="sess-c")
    assert len(expected) == 2

    resp = client.get(
        "/history.csv", params={"tool": "edge tool", "session": "sess-c"}
    )
    assert resp.status_code == 200
    _assert_csv_parity(resp.text, expected)
    assert (
        'filename="history-tool-edge_tool-session-sess-c.csv"'
        in resp.headers["content-disposition"]
    )


# ---------------------------------------------------------------------------
# TC-007: tokens export parity
# ---------------------------------------------------------------------------


def test_tokens_export_parity_under_window(tmp_path):
    """TC-007 / FR-005: /tokens.json?window=7d equals
    ``get_tool_tokens(since=...)`` for the same window, and /tokens.csv
    round-trips the same aggregates (only the tools with in-window calls
    appear)."""
    from cairn.dashboard.data import get_read_only_db, get_tool_tokens

    db_path = _export_db_file(tmp_path)
    client = _client_over(db_path, tmp_path)
    since = _since("7d")

    conn = get_read_only_db(db_path)
    try:
        expected = list(get_tool_tokens(conn, since=since))
    finally:
        conn.close()
    assert {row["tool_name"] for row in expected} == {"explore", "edge tool"}

    as_json = client.get("/tokens.json", params={"window": "7d"})
    assert as_json.status_code == 200
    assert as_json.headers["content-type"].startswith("application/json")
    assert as_json.json() == expected

    as_csv = client.get("/tokens.csv", params={"window": "7d"})
    assert as_csv.status_code == 200
    assert as_csv.headers["content-type"].startswith("text/csv")
    _assert_csv_parity(as_csv.text, expected)
    assert (
        'filename="tokens-window-7d.csv"' in as_csv.headers["content-disposition"]
    )


# ---------------------------------------------------------------------------
# TC-007: attachment disposition on all four routes
# ---------------------------------------------------------------------------


def test_export_routes_attach_filenames_and_media_types(tmp_path):
    """TC-007 / FR-005: every export route answers with an attachment
    Content-Disposition whose filename carries the view plus its active
    filter hints, and the right media type."""
    db_path = _export_db_file(tmp_path)
    client = _client_over(db_path, tmp_path)

    cases = [
        (
            "/history.csv",
            {"tool": "explore", "source": "cli", "window": "24h"},
            "text/csv",
            "history-tool-explore-source-cli-window-24h.csv",
        ),
        ("/history.json", {}, "application/json", "history.json"),
        ("/tokens.csv", {"window": "7d"}, "text/csv", "tokens-window-7d.csv"),
        ("/tokens.json", {}, "application/json", "tokens.json"),
    ]
    for path, params, media, filename in cases:
        resp = client.get(path, params=params)
        assert resp.status_code == 200, path
        assert resp.headers["content-type"].startswith(media), path
        assert resp.headers["content-disposition"] == (
            f'attachment; filename="{filename}"'
        ), path


# ---------------------------------------------------------------------------
# TC-007: ?store= selection honored by an export route
# ---------------------------------------------------------------------------

_SW_KEY_A = "1234567890abc001"  # 16 hex chars: the store-dir layout
_SW_KEY_B = "1234567890abc002"


def _seed_store_db(home, key: str, tool: str):
    """A populated store at ``<home>/<key>/.kg`` seeded with calls of one
    distinctive tool -- the app suite's store-switch convention."""
    from cairn.graph.schema import get_db

    kg = home / key / ".kg"
    kg.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db(str(kg))
    try:
        now = time.time()
        conn.executemany(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
            "duration_ms, status) VALUES (?, ?, ?, ?, ?)",
            [(tool, f"sess-{key}", now - 3600.0 - i, 25.0, "ok") for i in range(3)],
        )
        conn.commit()
    finally:
        conn.close()
    return kg


def test_history_export_honors_store_selection(tmp_path, monkeypatch):
    """TC-007: an export route rides the same store selection as the
    views -- ``?store=<keyA>`` exports store A's rows, ``?store=<keyB>``
    store B's, each matching ``list_history`` over that store's DB."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn import paths
    from cairn.dashboard.app import create_app

    home = tmp_path / "cairn-home"
    home.mkdir()
    kg_a = _seed_store_db(home, _SW_KEY_A, "store_a_tool")
    kg_b = _seed_store_db(home, _SW_KEY_B, "store_b_tool")

    monkeypatch.setattr(paths, "CAIRN_HOME", home)  # per-request resolution seam
    client = TestClient(create_app(db_path=str(tmp_path / "launch.db")))

    for key, kg, tool in (
        (_SW_KEY_A, kg_a, "store_a_tool"),
        (_SW_KEY_B, kg_b, "store_b_tool"),
    ):
        resp = client.get("/history.json", params={"store": key})
        assert resp.status_code == 200, key
        assert resp.json() == _history_rows(str(kg), tool_name=tool)
