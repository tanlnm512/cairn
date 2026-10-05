"""`cairn rationale` CLI contract: filters, ordering, JSON, and empty results.

Each test builds a self-contained mini workspace (tmp_path) with NOTE/WHY/HACK
markers, indexes it, and drives the command through CliRunner. Hermeticity per
CONSTITUTION C-04: `cairn.cli` is imported lazily inside the tests,
`CAIRN_WORKSPACE` scopes config resolution to the tmp workspace, and every
invocation passes `--db` so no store outside the sandbox is read or written.
"""

from __future__ import annotations

import json
from pathlib import Path

from cairn.graph.builder import build_graph

_MARKED_APP = '''\
# NOTE: module level note


def alpha(count):
    # WHY: linear scan keeps it simple
    return count + 1


def beta(count):
    # HACK: temporary until the retry service lands
    return count * 2
'''

_PLAIN_APP = '''\
def gamma(count):
    return count - 1
'''


def _make_ws(tmp_path: Path, sources: dict[str, str]) -> Path:
    ws = tmp_path / "ws"
    repo = ws / "demo"
    (repo / ".git").mkdir(parents=True)
    for name, text in sources.items():
        (repo / name).write_text(text, encoding="utf-8")
    return ws


def _build_db(ws: Path, tmp_path: Path) -> Path:
    db = tmp_path / "graph.kg"
    build_graph(workspace=str(ws), db_path=str(db))
    return db


def _invoke_rationale(ws: Path, db: Path, *args):
    from click.testing import CliRunner

    from cairn.cli import main

    return CliRunner().invoke(
        main,
        ["rationale", "--db", str(db), *args],
        env={"CAIRN_WORKSPACE": str(ws)},
    )


def test_symbol_filter_prints_kind_tagged_line(tmp_path):
    ws = _make_ws(tmp_path, {"app.py": _MARKED_APP})
    db = _build_db(ws, tmp_path)
    result = _invoke_rationale(ws, db, "--symbol", "alpha")
    assert result.exit_code == 0
    assert result.output == "app.py:5 [why] linear scan keeps it simple\n"


def test_file_filter_orders_all_records_by_line(tmp_path):
    ws = _make_ws(tmp_path, {"app.py": _MARKED_APP})
    db = _build_db(ws, tmp_path)
    result = _invoke_rationale(ws, db, "--file", "app.py")
    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "app.py:1 [note] module level note",
        "app.py:5 [why] linear scan keeps it simple",
        "app.py:10 [hack] temporary until the retry service lands",
    ]


def test_json_emits_row_dicts(tmp_path):
    ws = _make_ws(tmp_path, {"app.py": _MARKED_APP})
    db = _build_db(ws, tmp_path)
    result = _invoke_rationale(ws, db, "--symbol", "beta", "--json")
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert [(r["path"], r["line"], r["kind"]) for r in rows] == [
        ("app.py", 10, "hack")
    ]
    assert rows[0]["text"] == "temporary until the retry service lands"
    assert rows[0]["symbol_id"]


def test_no_records_exits_zero(tmp_path):
    ws = _make_ws(tmp_path, {"app.py": _PLAIN_APP})
    db = _build_db(ws, tmp_path)
    result = _invoke_rationale(ws, db)
    assert result.exit_code == 0
    assert "No rationale records" in result.output


def test_unknown_symbol_exits_zero_cleanly(tmp_path):
    ws = _make_ws(tmp_path, {"app.py": _MARKED_APP})
    db = _build_db(ws, tmp_path)
    result = _invoke_rationale(ws, db, "--symbol", "no-such-symbol")
    assert result.exit_code == 0
    assert "No rationale records" in result.output
    assert "Traceback" not in result.output
