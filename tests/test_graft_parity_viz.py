"""FR-009 contract: `cairn viz --export FILE.html` writes one self-contained
HTML file that renders the selected graph scope offline (TC-031; TC-032's
browser check is the manual half).

The export must reference no network assets (no http(s) or protocol-relative
src/href targets), produce no sidecar files, and carry the selected scope's
nodes and edges inline as Mermaid content. Until `--export` exists these fail
at click option parsing (exit 2), never deeper.

Hermeticity (CONSTITUTION C-04): `cairn.cli` is imported lazily inside tests,
the graph store is a tmp_path SQLite file passed via --db, and the export
lands in an empty tmp_path directory.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

# http://, https://, and protocol-relative // targets in src/href attributes.
_NETWORK_ASSET_RE = re.compile(
    r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//[^"']+["']""",
    re.IGNORECASE,
)


def _seed_graph(db_path: Path) -> None:
    """A symbol scope with one caller, two callees, and one out-of-scope
    symbol: enough to prove selection, not just non-emptiness."""
    from cairn.graph.schema import _apply_schema

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES ('r', 'r', '/tmp/r')"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) "
        "VALUES ('f1', 'r', 'src/demo.py', 'python')"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) "
        "VALUES ('f2', 'r', 'src/other.py', 'python')"
    )
    conn.executemany(
        "INSERT INTO symbols (id, file_id, name, kind) VALUES (?, ?, ?, ?)",
        [
            ("s-main", "f1", "demo_main", "function"),
            ("s-helper", "f1", "demo_helper", "function"),
            ("s-base", "f1", "demo_base", "class"),
            ("s-unrelated", "f2", "unrelated_symbol", "function"),
        ],
    )
    conn.executemany(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("e-callee", "s-main", "s-helper", "demo_helper", "calls"),
            ("e-caller", "s-base", "s-main", "demo_main", "references"),
        ],
    )
    conn.commit()
    conn.close()


def _export_symbol_scope(tmp_path: Path):
    """Export demo_main's symbol scope through the CLI; return the runner
    result plus the empty directory the file must land in."""
    from click.testing import CliRunner

    from cairn.cli import main

    db_path = tmp_path / "graph.db"
    _seed_graph(db_path)
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    html_path = export_dir / "scope.html"
    result = CliRunner().invoke(
        main,
        [
            "viz",
            "--scope",
            "symbol",
            "--symbol",
            "demo_main",
            "--export",
            str(html_path),
            "--db",
            str(db_path),
        ],
        catch_exceptions=False,
    )
    return result, export_dir, html_path


def test_export_writes_exactly_one_html_file(tmp_path):
    """The export is one HTML file in the target directory — no sidecar
    assets the page would need to render."""
    result, export_dir, html_path = _export_symbol_scope(tmp_path)

    assert result.exit_code == 0, result.output
    assert html_path.is_file()
    produced = sorted(p.relative_to(export_dir) for p in export_dir.rglob("*"))
    assert produced == [Path(html_path.name)]
    assert html_path.read_text(encoding="utf-8").lstrip().lower().startswith(
        "<!doctype html"
    )


def test_export_references_no_network_assets(tmp_path):
    """No src/href target may be http(s) or protocol-relative; the page
    renders with networking disabled."""
    result, _, html_path = _export_symbol_scope(tmp_path)

    assert result.exit_code == 0, result.output
    html = html_path.read_text(encoding="utf-8")
    matches = _NETWORK_ASSET_RE.findall(html)
    assert not matches, f"network asset references in export: {matches}"


def test_export_embeds_selected_scope_mermaid_graph(tmp_path):
    """The HTML carries the selected scope's nodes and edges inline as
    Mermaid content, and nothing from outside the scope."""
    result, _, html_path = _export_symbol_scope(tmp_path)

    assert result.exit_code == 0, result.output
    html = html_path.read_text(encoding="utf-8")
    for node in ("demo_main", "demo_helper", "demo_base"):
        assert node in html, f"selected node missing from export: {node}"
    assert re.search(r"demo_base[^\n]*-->[^\n]*demo_main", html), (
        "caller edge missing from export"
    )
    assert re.search(r"demo_main[^\n]*-->[^\n]*demo_helper", html), (
        "callee edge missing from export"
    )
    assert "unrelated_symbol" not in html, "out-of-scope symbol leaked into export"
