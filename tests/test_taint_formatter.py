"""Taint warning formatter: the shared text the explore/blast surfaces render."""

from __future__ import annotations

from cairn.graph.taint import TaintHop, TaintPath, format_taint_warning


def _path(*hops: tuple[str, str, str]) -> TaintPath:
    return TaintPath(
        hops=[TaintHop(file=f, symbol=s, resolution=r) for f, s, r in hops]
    )


def test_empty_path_list_renders_no_warning():
    assert format_taint_warning([]) == ""


def test_warning_names_the_path_with_a_resolution_label_per_hop():
    text = format_taint_warning(
        [
            _path(
                ("src/app.py", "handle_request", "exact"),
                ("src/app.py", "validate_input", "exact"),
                ("src/db.py", "run_query", "exact"),
            )
        ]
    )
    assert text == (
        "Taint path: src/app.py:handle_request [exact]"
        " -> src/app.py:validate_input [exact]"
        " -> src/db.py:run_query [exact]"
    )


def test_each_path_renders_on_its_own_line():
    text = format_taint_warning(
        [
            _path(
                ("src/app.py", "handle_request", "exact"),
                ("src/db.py", "run_query", "exact"),
            ),
            _path(
                ("src/cli.py", "main", "exact"),
                ("src/shell.py", "run_command", "ambiguous"),
            ),
        ]
    )
    lines = text.splitlines()
    assert len(lines) == 2
    assert lines[0] == (
        "Taint path: src/app.py:handle_request [exact]"
        " -> src/db.py:run_query [exact]"
    )
    assert lines[1] == (
        "Taint path: src/cli.py:main [exact]"
        " -> src/shell.py:run_command [ambiguous]"
    )


def test_warning_text_says_taint_and_never_the_degradation_substring():
    text = format_taint_warning(
        [
            _path(
                ("src/app.py", "handle_request", "exact"),
                ("src/db.py", "run_query", "exact"),
            )
        ]
    )
    assert "taint" in text.lower()
    assert "degraded: rung" not in text
