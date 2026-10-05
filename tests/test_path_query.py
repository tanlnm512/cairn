"""Walk-level contracts behind the `cairn path` surface, over the on-demand-paths fixtures."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from cairn.graph.builder import build_graph
from cairn.graph.taint import find_symbol_paths

FIXTURES_ROOT = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "on-demand-paths"
)


def _fixture_db(tmp_path: Path, name: str) -> Path:
    # Empty dirs do not survive clones, so the .git repo marker is re-created.
    ws = tmp_path / name
    shutil.copytree(FIXTURES_ROOT / name, ws)
    (ws / ".git").mkdir(exist_ok=True)
    db = tmp_path / f"{name}.kg"
    build_graph(workspace=str(ws), db_path=str(db))
    return db


def _fixture_conn(tmp_path: Path, name: str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(_fixture_db(tmp_path, name)))
    conn.row_factory = sqlite3.Row
    return conn


def _symbol_ids(conn: sqlite3.Connection, *names: str) -> set[str]:
    placeholders = ",".join("?" * len(names))
    return {
        row["id"]
        for row in conn.execute(
            f"SELECT id FROM symbols WHERE name IN ({placeholders})", names
        )
    }


def _line_of(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute(
        "SELECT line_start FROM symbols WHERE name = ?", (name,)
    ).fetchone()
    return row["line_start"]


def _render(paths) -> list[str]:
    return [
        " -> ".join(
            f"{hop.file}:{hop.line}:{hop.symbol} [{hop.resolution}]"
            for hop in path.hops
        )
        for path in paths
    ]


def test_chain_shortest_path_hops_in_order_with_file_and_line(tmp_path):
    conn = _fixture_conn(tmp_path, "chain")
    paths = find_symbol_paths(
        conn,
        _symbol_ids(conn, "chain_a"),
        _symbol_ids(conn, "chain_d"),
    )
    assert len(paths) == 1
    hops = paths[0].hops
    assert [hop.symbol for hop in hops] == [
        "chain_a",
        "chain_b",
        "chain_c",
        "chain_d",
    ]
    assert [hop.file for hop in hops] == ["chain.py"] * 4
    assert [hop.line for hop in hops] == [
        _line_of(conn, name) for name in ("chain_a", "chain_b", "chain_c", "chain_d")
    ]
    assert hops[0].resolution == "exact"
    assert all(hop.resolution == "exact" for hop in hops[1:])
    conn.close()


def test_no_path_within_depth_bound_returns_empty(tmp_path):
    conn = _fixture_conn(tmp_path, "depth")
    entry = _symbol_ids(conn, "dep_entry")
    target = _symbol_ids(conn, "dep_target")
    assert find_symbol_paths(conn, entry, target, max_depth=1) == []
    found = find_symbol_paths(conn, entry, target, max_depth=2)
    assert len(found) == 1
    assert [hop.symbol for hop in found[0].hops][0] == "dep_entry"
    assert [hop.symbol for hop in found[0].hops][-1] == "dep_target"
    conn.close()


def test_island_crossing_yields_no_path(tmp_path):
    conn = _fixture_conn(tmp_path, "islands")
    assert (
        find_symbol_paths(
            conn, _symbol_ids(conn, "isl_a1"), _symbol_ids(conn, "isl_b2")
        )
        == []
    )
    within = find_symbol_paths(
        conn, _symbol_ids(conn, "isl_a1"), _symbol_ids(conn, "isl_a2")
    )
    assert [hop.symbol for hop in within[0].hops] == ["isl_a1", "isl_a2"]
    conn.close()


def test_fork_emits_one_path_through_deterministic_mid(tmp_path):
    conn = _fixture_conn(tmp_path, "fork")
    paths = find_symbol_paths(
        conn, _symbol_ids(conn, "hub_top"), _symbol_ids(conn, "hub_sink")
    )
    assert len(paths) == 1
    symbols = [hop.symbol for hop in paths[0].hops]
    assert symbols[0] == "hub_top"
    assert symbols[-1] == "hub_sink"
    assert symbols[1] in ("mid_left", "mid_right")
    conn.close()


def test_fork_tie_break_identical_across_fresh_rebuilds(tmp_path):
    """The fork's tie-break is build-stable: two independent builds of the
    same sources pick the same mid and render byte-identical paths."""
    renders = []
    for root in (tmp_path / "rebuild_a", tmp_path / "rebuild_b"):
        conn = _fixture_conn(root, "fork")
        renders.append(
            _render(
                find_symbol_paths(
                    conn, _symbol_ids(conn, "hub_top"), _symbol_ids(conn, "hub_sink")
                )
            )
        )
        conn.close()
    assert len(renders[0]) == 1
    assert renders[0] == renders[1]


def test_fanout_resolves_one_shortest_path_per_pair(tmp_path):
    conn = _fixture_conn(tmp_path, "fanout")
    fans = _symbol_ids(conn, *(f"fan_{i}" for i in range(60)))
    paths = find_symbol_paths(conn, fans, _symbol_ids(conn, "fan_sink"))
    assert len(paths) == 60
    assert all(len(path.hops) == 2 for path in paths)
    assert {path.hops[0].symbol for path in paths} == {f"fan_{i}" for i in range(60)}
    assert {path.hops[-1].symbol for path in paths} == {"fan_sink"}
    conn.close()


def test_exact_default_misses_ambiguous_hop_fuzzy_bridges(tmp_path):
    conn = _fixture_conn(tmp_path, "fuzzy")
    start = _symbol_ids(conn, "fz_start")
    end = _symbol_ids(conn, "fz_end")
    assert find_symbol_paths(conn, start, end) == []
    paths = find_symbol_paths(conn, start, end, fuzzy=True)
    assert len(paths) == 1
    hops = paths[0].hops
    assert [hop.symbol for hop in hops] == ["fz_start", "fz_shared", "fz_end"]
    assert hops[1].resolution == "ambiguous"
    assert hops[-1].resolution == "exact"
    conn.close()


def test_self_pair_returns_single_hop_path(tmp_path):
    conn = _fixture_conn(tmp_path, "chain")
    ids = _symbol_ids(conn, "chain_b")
    paths = find_symbol_paths(conn, ids, ids)
    assert len(paths) == 1
    assert [(hop.symbol, hop.file) for hop in paths[0].hops] == [
        ("chain_b", "chain.py")
    ]
    conn.close()


def test_unknown_endpoints_return_empty_without_error(tmp_path):
    conn = _fixture_conn(tmp_path, "chain")
    known = _symbol_ids(conn, "chain_a")
    assert find_symbol_paths(conn, set(), known) == []
    assert find_symbol_paths(conn, known, {"no-such-id"}) == []
    assert find_symbol_paths(conn, {"no-such-id"}, known) == []
    conn.close()


def test_double_run_is_byte_identical(tmp_path):
    conn = _fixture_conn(tmp_path, "fanout")
    fuzzy_conn = _fixture_conn(tmp_path, "fuzzy")
    fans = _symbol_ids(conn, *(f"fan_{i}" for i in range(60)))
    sink = _symbol_ids(conn, "fan_sink")
    start = _symbol_ids(fuzzy_conn, "fz_start")
    end = _symbol_ids(fuzzy_conn, "fz_end")
    first = _render(find_symbol_paths(conn, fans, sink))
    second = _render(find_symbol_paths(conn, fans, sink))
    fuzzy_first = _render(find_symbol_paths(fuzzy_conn, start, end, fuzzy=True))
    fuzzy_second = _render(find_symbol_paths(fuzzy_conn, start, end, fuzzy=True))
    assert first == second
    assert first
    assert fuzzy_first == fuzzy_second
    conn.close()
    fuzzy_conn.close()


def _invoke_path(db: Path, *args: str):
    from click.testing import CliRunner

    from cairn.cli import main

    return CliRunner().invoke(
        main,
        ["path", "--db", str(db), *args],
        env={"CAIRN_WORKSPACE": str(db.parent)},
    )


def test_path_cli_prints_ordered_hop_chain_with_file_and_line(tmp_path):
    db = _fixture_db(tmp_path, "chain")
    result = _invoke_path(db, "--from", "chain_a", "--to", "chain_d")
    assert result.exit_code == 0
    assert "path 1 (chain_a -> chain_d, 4 hops):" in result.output
    chain_lines = [
        line
        for line in result.output.splitlines()
        if line.startswith("  chain.py:")
    ]
    assert len(chain_lines) == 4
    assert chain_lines[0].startswith("  chain.py:")
    assert chain_lines[0].endswith("chain_a [exact]")
    assert [line.split()[-2] for line in chain_lines] == [
        "chain_a",
        "chain_b",
        "chain_c",
        "chain_d",
    ]


def test_path_cli_depth_bound_reports_no_path_and_exits_zero(tmp_path):
    db = _fixture_db(tmp_path, "chain")
    result = _invoke_path(
        db, "--from", "chain_a", "--to", "chain_d", "--max-depth", "1"
    )
    assert result.exit_code == 0
    assert "no path within 1 hops from 'chain_a' to 'chain_d'" in result.output


def test_path_cli_disconnected_symbols_report_no_path(tmp_path):
    db = _fixture_db(tmp_path, "islands")
    result = _invoke_path(db, "--from", "isl_a1", "--to", "isl_b2")
    assert result.exit_code == 0
    assert "no path within 4 hops from 'isl_a1' to 'isl_b2'" in result.output


def test_path_cli_substring_pattern_expands_to_both_fork_routes(tmp_path):
    db = _fixture_db(tmp_path, "fork")
    result = _invoke_path(db, "--from", "hub_top", "--to", "hub_sink")
    assert result.exit_code == 0
    hop_lines = [
        line for line in result.output.splitlines() if line.startswith("  fork.py:")
    ]
    assert len(hop_lines) == 3
    assert hop_lines[0].endswith("hub_top [exact]")
    assert hop_lines[-1].endswith("hub_sink [exact]")
    assert hop_lines[1].split()[-2] in ("mid_left", "mid_right")

    both = _invoke_path(db, "--from", "hub_top", "--to", "mid")
    assert both.exit_code == 0
    assert "path 1 (hub_top -> mid, 2 hops):" in both.output
    assert "path 2 (hub_top -> mid, 2 hops):" in both.output
    assert "mid_left" in both.output
    assert "mid_right" in both.output


def test_path_cli_limit_caps_printed_paths(tmp_path):
    db = _fixture_db(tmp_path, "fanout")
    result = _invoke_path(db, "--from", "fan_", "--to", "fan_sink")
    assert result.exit_code == 0
    headers = [line for line in result.output.splitlines() if line.startswith("path ")]
    assert len(headers) == 50
    fans = {
        line.split()[-2]
        for line in result.output.splitlines()
        if line.startswith("  ") and line.split()[-2].startswith("fan_")
    }
    assert 0 < len(fans) < 60

    trimmed = _invoke_path(db, "--from", "fan_", "--to", "fan_sink", "--limit", "5")
    assert trimmed.exit_code == 0
    assert len(
        [line for line in trimmed.output.splitlines() if line.startswith("path ")]
    ) == 5

    invalid = _invoke_path(db, "--from", "fan_", "--to", "fan_sink", "--limit", "0")
    assert invalid.exit_code != 0
    assert "--limit" in invalid.output


def test_path_cli_fuzzy_flag_bridges_ambiguous_hop(tmp_path):
    db = _fixture_db(tmp_path, "fuzzy")
    exact = _invoke_path(db, "--from", "fz_start", "--to", "fz_end")
    assert exact.exit_code == 0
    assert "no path within 4 hops from 'fz_start' to 'fz_end'" in exact.output

    fuzzy = _invoke_path(db, "--from", "fz_start", "--to", "fz_end", "--fuzzy")
    assert fuzzy.exit_code == 0
    hops = [line for line in fuzzy.output.splitlines() if line.startswith("  ")]
    assert [line.split()[-2] for line in hops] == ["fz_start", "fz_shared", "fz_end"]
    assert "[ambiguous]" in hops[1]
    assert "[exact]" in hops[0]


def test_path_cli_self_pattern_prints_single_hop(tmp_path):
    db = _fixture_db(tmp_path, "chain")
    result = _invoke_path(db, "--from", "chain_a", "--to", "chain_a")
    assert result.exit_code == 0
    assert "path 1 (chain_a -> chain_a, 1 hops):" in result.output
    assert "chain_b" not in result.output
    assert "chain_c" not in result.output
    assert "chain_d" not in result.output


def test_path_cli_unknown_pattern_exits_cleanly(tmp_path):
    db = _fixture_db(tmp_path, "chain")
    result = _invoke_path(db, "--from", "no_such_symbol_qa", "--to", "chain_d")
    assert result.exit_code == 0
    assert "no symbols match from pattern 'no_such_symbol_qa'" in result.output
    assert "Traceback" not in result.output
    assert "internal error" not in result.output.lower()


def test_path_cli_double_run_is_byte_identical(tmp_path):
    db = _fixture_db(tmp_path, "fanout")
    first = _invoke_path(db, "--from", "fan_", "--to", "fan_sink")
    second = _invoke_path(db, "--from", "fan_", "--to", "fan_sink")
    assert first.exit_code == second.exit_code == 0
    assert first.output == second.output
