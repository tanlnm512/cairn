"""Pre-submit guard contracts: only mistake/pattern matches warn, warnings
render through the pack section emitters in both formats, and a
memory-reader failure degrades instead of raising.
"""

from __future__ import annotations

from types import SimpleNamespace

from cairn.review import engine
from cairn.review.engine import (
    PACK_FORMATS,
    build_pre_submit,
    has_blocking_matches,
    render_pre_submit,
)


def _seed():
    return {
        "id": "s1",
        "name": "parse_date",
        "qualified_name": "ledger.parse_date",
        "kind": "function",
        "file_path": "ledger.py",
        "repo": "fixture",
        "line_start": 5,
        "line_end": 7,
        "hunks": [{"start": 5, "end": 7}],
    }


def _concept(memory_type, title, body):
    return SimpleNamespace(
        extensions={"memory_type": memory_type}, title=title, body=body
    )


def _stub_blast(monkeypatch, expected_base=None):
    def fake_compute_blast(conn, workspace, **kwargs):
        if expected_base is not None:
            assert kwargs["base"] == expected_base
        return {"seeds": [_seed()]}

    monkeypatch.setattr(engine, "compute_blast", fake_compute_blast)


def test_pre_submit_keeps_only_mistake_and_pattern_matches(monkeypatch):
    import cairn.memory.promotion as promotion

    _stub_blast(monkeypatch, expected_base="main")
    concepts = [
        _concept("mistake", "Never parse dates with regex", "Use the date parser."),
        _concept("pattern", "Prefer exponential backoff", "Back off exponentially."),
        _concept("decision", "Cache-first reads", "Read the cache first."),
    ]
    monkeypatch.setattr(
        promotion, "search_memory", lambda conn, bundle, query: concepts
    )

    result = build_pre_submit(None, "unused-ws")
    assert result["memories"]["error"] is None
    matches = [
        match
        for entry in result["memories"]["entries"]
        for match in entry["matches"]
    ]
    assert [(match["type"], match["title"]) for match in matches] == [
        ("mistake", "Never parse dates with regex"),
        ("pattern", "Prefer exponential backoff"),
    ]


def test_pre_submit_without_guard_matches_renders_nothing(monkeypatch):
    import cairn.memory.promotion as promotion

    _stub_blast(monkeypatch)
    monkeypatch.setattr(
        promotion,
        "search_memory",
        lambda conn, bundle, query: [
            _concept("decision", "Cache-first reads", "Read the cache first.")
        ],
    )

    result = build_pre_submit(None, "unused-ws")
    assert result["memories"]["entries"] == []
    for output_format in PACK_FORMATS:
        assert render_pre_submit(result, output_format) == ""


def test_pre_submit_warnings_render_in_both_formats(monkeypatch):
    import cairn.memory.promotion as promotion

    _stub_blast(monkeypatch)
    monkeypatch.setattr(
        promotion,
        "search_memory",
        lambda conn, bundle, query: [
            _concept(
                "mistake",
                "Never parse dates with regex",
                "Use the dedicated date parser.",
            )
        ],
    )

    result = build_pre_submit(None, "unused-ws")
    text = render_pre_submit(result, "text")
    assert "[mistake] Never parse dates with regex" in text
    assert "Use the dedicated date parser." in text
    markdown = render_pre_submit(result, "markdown")
    assert "**[mistake]** Never parse dates with regex" in markdown
    assert "Use the dedicated date parser." in markdown


def test_pre_submit_reader_failure_degrades(monkeypatch):
    import cairn.memory.promotion as promotion

    _stub_blast(monkeypatch)

    def _raise_reader(*args, **kwargs):
        raise RuntimeError("reader down")

    monkeypatch.setattr(promotion, "search_memory", _raise_reader)
    result = build_pre_submit(None, "unused-ws")
    assert result["memories"]["error"] == "reader down"
    for output_format in PACK_FORMATS:
        assert "unavailable: reader down" in render_pre_submit(
            result, output_format
        )


def test_gate_predicate_blocks_on_guard_match(monkeypatch):
    import cairn.memory.promotion as promotion

    _stub_blast(monkeypatch)
    monkeypatch.setattr(
        promotion,
        "search_memory",
        lambda conn, bundle, query: [
            _concept(
                "mistake",
                "Never parse dates with regex",
                "Use the dedicated date parser.",
            )
        ],
    )

    assert has_blocking_matches(build_pre_submit(None, "unused-ws"))


def test_gate_predicate_spares_other_memory_types(monkeypatch):
    import cairn.memory.promotion as promotion

    _stub_blast(monkeypatch)
    monkeypatch.setattr(
        promotion,
        "search_memory",
        lambda conn, bundle, query: [
            _concept("decision", "Cache-first reads", "Read the cache first.")
        ],
    )

    assert not has_blocking_matches(build_pre_submit(None, "unused-ws"))


def test_gate_predicate_spares_reader_failure(monkeypatch):
    import cairn.memory.promotion as promotion

    _stub_blast(monkeypatch)

    def _raise_reader(*args, **kwargs):
        raise RuntimeError("reader down")

    monkeypatch.setattr(promotion, "search_memory", _raise_reader)

    assert not has_blocking_matches(build_pre_submit(None, "unused-ws"))
