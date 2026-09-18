"""Resolved-comment event capture contracts (D-003, FR-003).

The adapter turns the pinned event payload shape into a draft-tier memory
keyed to the enclosing symbols (backtick refs per ``cairn/refs.py``) with
the PR-thread link in the body; an open comment records nothing.
"""

from __future__ import annotations

import sqlite3

import pytest

from cairn.graph.schema import get_build_db
from cairn.okf.bundle import OKFBundle
from cairn.review.capture import (
    PATTERN_MARKER,
    classify_event,
    capture_review_event,
    parse_event,
)

pytestmark = pytest.mark.usefixtures("hash_backend")

THREAD_URL = "https://github.com/acme/fixture/pull/42#discussion_r1984"


def _index() -> sqlite3.Connection:
    """In-memory fixture index: one indexed file with nested + lone spans."""
    conn = get_build_db()
    conn.execute(
        "INSERT INTO repos (id, name, path, language) "
        "VALUES ('demo', 'demo', 'demo', 'python')"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language, line_count) "
        "VALUES ('f1', 'demo', 'src/pkg/mod.py', 'python', 30)"
    )
    conn.executemany(
        "INSERT INTO symbols "
        "(id, file_id, name, qualified_name, kind, line_start, line_end) "
        "VALUES (?, 'f1', ?, ?, ?, ?, ?)",
        [
            ("s1", "outer", "mod.outer", "class", 1, 20),
            ("s2", "inner", "mod.outer.inner", "method", 5, 8),
            ("s3", "standalone", "mod.standalone", "function", 22, 24),
        ],
    )
    return conn


def _payload(**overrides) -> dict:
    payload = {
        "resolved": True,
        "file": "src/pkg/mod.py",
        "line": 6,
        "body": "fix the flake",
        "thread_url": THREAD_URL,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def conn():
    db = _index()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def bundle(tmp_path):
    return OKFBundle(str(tmp_path / "knowledge"))


def test_valid_payload_parses_all_fields():
    event = parse_event(_payload(line=None, body=""))
    assert event.resolved is True
    assert event.file_path == "src/pkg/mod.py"
    assert event.line is None
    assert event.body == ""
    assert event.thread_url == THREAD_URL


@pytest.mark.parametrize("key", ["resolved", "file", "line", "body", "thread_url"])
def test_missing_key_raises_value_error(key):
    payload = _payload()
    del payload[key]
    with pytest.raises(ValueError, match=key):
        parse_event(payload)


@pytest.mark.parametrize("value", ["true", 1, None])
def test_non_bool_resolved_raises_value_error(value):
    with pytest.raises(ValueError):
        parse_event(_payload(resolved=value))


@pytest.mark.parametrize("line", [0, -3, "6", True, 1.5])
def test_invalid_line_raises_value_error(line):
    with pytest.raises(ValueError):
        parse_event(_payload(line=line))


@pytest.mark.parametrize("file", ["", "   ", 7])
def test_invalid_file_raises_value_error(file):
    with pytest.raises(ValueError):
        parse_event(_payload(file=file))


@pytest.mark.parametrize("field", ["body", "thread_url"])
def test_non_string_text_fields_raise_value_error(field):
    with pytest.raises(ValueError):
        parse_event(_payload(**{field: 42}))


def test_non_object_payload_raises_value_error():
    with pytest.raises(ValueError):
        parse_event(["resolved"])


def test_pattern_marker_records_pattern_type():
    assert classify_event(parse_event(_payload(body=f"note {PATTERN_MARKER}"))) == "pattern"


def test_body_without_marker_records_mistake():
    assert classify_event(parse_event(_payload())) == "mistake"


def test_resolved_symbol_comment_records_draft_mistake(conn, bundle):
    result = capture_review_event(conn, bundle, _payload())

    assert result["recorded"] is True
    assert result["type"] == "mistake"
    assert result["tier"] == "drafts"
    assert result["file_level"] is False
    assert result["symbols"] == ["mod.outer.inner", "mod.outer"]

    stored = bundle.read_concept(result["path"])
    assert stored.title == "src/pkg/mod.py pull/42: fix the flake"
    assert stored.resource == "src/pkg/mod.py"
    assert f"Thread: {THREAD_URL}" in stored.body
    assert "`mod.outer.inner`, `mod.outer`" in stored.body
    assert "`src/pkg/mod.py`" in stored.body


def test_pattern_marker_payload_records_pattern_type(conn, bundle):
    result = capture_review_event(
        conn, bundle, _payload(body=f"extract a parser helper {PATTERN_MARKER}")
    )

    assert result["recorded"] is True
    assert result["type"] == "pattern"


def test_unresolved_comment_records_nothing(conn, bundle):
    result = capture_review_event(conn, bundle, _payload(resolved=False))

    assert result["recorded"] is False
    assert bundle.list_concepts(prefix="memory/") == []


def test_comment_without_line_keys_to_file_level(conn, bundle):
    result = capture_review_event(conn, bundle, _payload(line=None))

    assert result["recorded"] is True
    assert result["file_level"] is True
    assert result["symbols"] == []
    stored = bundle.read_concept(result["path"])
    assert "`src/pkg/mod.py`" in stored.body


def test_unindexed_file_degrades_to_file_level(conn, bundle):
    result = capture_review_event(conn, bundle, _payload(file="src/pkg/other.py"))

    assert result["recorded"] is True
    assert result["file_level"] is True
    assert result["symbols"] == []
    assert bundle.read_concept(result["path"]).title.startswith("src/pkg/other.py")


def test_missing_thread_url_omits_thread_line(conn, bundle):
    result = capture_review_event(conn, bundle, _payload(thread_url=""))

    assert result["recorded"] is True
    stored = bundle.read_concept(result["path"])
    assert "Thread:" not in stored.body
    assert "pull/" not in stored.title


def test_long_comment_summary_truncates_title(conn, bundle):
    result = capture_review_event(conn, bundle, _payload(body="x" * 120))

    stored = bundle.read_concept(result["path"])
    assert stored.title.startswith("src/pkg/mod.py pull/42: ")
    assert stored.title.endswith("...")
    assert len(stored.title) < len("src/pkg/mod.py pull/42: ") + 120
