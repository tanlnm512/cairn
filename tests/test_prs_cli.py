"""cairn prs CLI contracts: gh-stubbed list rendering, clean failures, JSON."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "gh"

DIFF_TOUCHES_CHANGED = (
    "diff --git a/call_chain.py b/call_chain.py\n"
    "--- a/call_chain.py\n"
    "+++ b/call_chain.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def changed():\n"
    "-    return 1\n"
    "+    return 11\n"
)

DIFF_TOUCHES_DIRECT = (
    "diff --git a/call_chain.py b/call_chain.py\n"
    "--- a/call_chain.py\n"
    "+++ b/call_chain.py\n"
    "@@ -5,2 +5,2 @@\n"
    " def direct():\n"
    "-    return changed()\n"
    "+    return changed() or 1\n"
)

DIFF_UNINDEXED_AND_DELETED = (
    DIFF_TOUCHES_CHANGED
    + "diff --git a/docs/design-note.md b/docs/design-note.md\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/docs/design-note.md\n"
    "@@ -0,0 +1,2 @@\n"
    "+Design note prose.\n"
    "diff --git a/gone.py b/gone.py\n"
    "deleted file mode 100644\n"
    "--- a/gone.py\n"
    "+++ /dev/null\n"
    "@@ -1,2 +0,0 @@\n"
    "-def changed():\n"
    "-    return 1\n"
)

CHAIN_FILE = (
    "def changed():\n"
    "    return 1\n"
    "\n"
    "\n"
    "def direct():\n"
    "    return changed()\n"
    "\n"
    "\n"
    "def transitive():\n"
    "    return direct()\n"
)


def _seed_store(db_path: Path) -> None:
    from cairn.graph.schema import get_db

    get_db(str(db_path)).close()


def _seed_workspace_store(workspace: Path, db_path: Path) -> None:
    from datetime import datetime, timezone

    from cairn.graph.schema import get_db

    conn = get_db(str(db_path))
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO repos (id, name, path, language, indexed_at) "
            "VALUES ('demo', 'demo', 'demo', 'python', ?)",
            (now,),
        )
        disk = workspace / "demo" / "call_chain.py"
        disk.parent.mkdir(parents=True, exist_ok=True)
        disk.write_text(CHAIN_FILE, encoding="utf-8")
        stat = disk.stat()
        conn.execute(
            "INSERT INTO files "
            "(id, repo_id, path, language, line_count, indexed_at, size, mtime) "
            "VALUES ('file-call_chain.py', 'demo', 'call_chain.py', 'python', "
            "?, ?, ?, ?)",
            (len(CHAIN_FILE.splitlines()), now, stat.st_size, stat.st_mtime),
        )
        for name, start, end in [
            ("changed", 1, 2),
            ("direct", 5, 6),
            ("transitive", 9, 10),
        ]:
            conn.execute(
                "INSERT INTO symbols "
                "(id, file_id, name, qualified_name, kind, line_start, line_end) "
                "VALUES (?, 'file-call_chain.py', ?, ?, 'function', ?, ?)",
                (f"symbol-{name}", name, name, start, end),
            )
        conn.execute(
            "INSERT INTO edges (source_id, target_id, kind, line, resolution) "
            "VALUES ('symbol-direct', 'symbol-changed', 'calls', 6, 'exact')"
        )
        conn.execute(
            "INSERT INTO edges (source_id, target_id, kind, line, resolution) "
            "VALUES ('symbol-transitive', 'symbol-direct', 'calls', 10, 'exact')"
        )
        conn.executemany(
            "INSERT INTO communities VALUES (?, ?, ?)",
            [(1, "core", 2), (2, "api", 1)],
        )
        conn.executemany(
            "INSERT INTO symbol_communities VALUES (?, ?, ?)",
            [
                (1, "symbol-changed", 3),
                (1, "symbol-direct", 1),
                (2, "symbol-direct", 2),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _install_gh_stub(tmp_path: Path, monkeypatch, body: str) -> None:
    bindir = tmp_path / "_gh_stub"
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    gh.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")


def _stub_body(tmp_path: Path, *, diff_42: str | None = None, diff_43: str | None = None) -> str:
    def _write(name: str, text: str | None) -> str:
        if text is None:
            return "''"
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    return (
        'case "$2" in\n'
        f'  list) cat \'{FIXTURES / "pr_list.json"}\' ;;\n'
        f'  view) cat \'{FIXTURES / "pr_view.json"}\' ;;\n'
        "  diff) case \"$3\" in\n"
        f"      42) cat '{_write('diff_42.patch', diff_42)}' ;;\n"
        f"      43) cat '{_write('diff_43.patch', diff_43)}' ;;\n"
        "    esac ;;\n"
        "esac"
    )


def _invoke_prs(db_path: Path, *args):
    from click.testing import CliRunner
    from cairn.cli import main

    return CliRunner().invoke(main, ["prs", "--db", str(db_path), *args])


# --- T002: list rendering + clean gh failure ---


def test_prs_help_exits_zero():
    from click.testing import CliRunner
    from cairn.cli import main

    result = CliRunner().invoke(main, ["prs", "--help"])
    assert result.exit_code == 0, result.output
    assert "--db" in result.output


def test_prs_lists_six_columns_over_stubbed_gh(tmp_path, monkeypatch):
    db_path = tmp_path / "graph.db"
    _seed_store(db_path)
    _install_gh_stub(tmp_path, monkeypatch, _stub_body(tmp_path))
    result = _invoke_prs(db_path)
    assert result.exit_code == 0, result.output
    for cell in (
        "42",
        "Cap traversal depth",
        "fix/depth-cap",
        "ada",
        "pass",
        "APPROVED",
        "43",
        "Add compass export",
        "feat/export",
        "linus",
        "fail",
        "REVIEW_REQUIRED",
    ):
        assert cell in result.output, result.output
    assert "Traceback" not in result.output


def test_prs_gh_failure_prints_single_error_no_table(tmp_path, monkeypatch):
    db_path = tmp_path / "graph.db"
    _seed_store(db_path)
    _install_gh_stub(
        tmp_path,
        monkeypatch,
        'case "$2" in list) echo "gh: API rate limit exceeded" >&2; exit 2 ;; esac',
    )
    result = _invoke_prs(db_path)
    assert result.exit_code != 0
    assert result.output.count("Error:") == 1, result.output
    assert "rate limit" in result.output
    assert "Cap traversal depth" not in result.output
    assert "Title" not in result.output
    assert "Traceback" not in result.output


# --- T005: --impact rendering + clean failures ---


@pytest.fixture
def impact_store(tmp_path):
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "graph.db"
    _seed_workspace_store(workspace, db_path)
    return db_path


def test_prs_impact_renders_store_line_symbols_and_dependents(
    impact_store, tmp_path, monkeypatch
):
    _install_gh_stub(
        tmp_path, monkeypatch, _stub_body(tmp_path, diff_42=DIFF_TOUCHES_CHANGED)
    )
    result = _invoke_prs(impact_store, "--impact", "42")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    store_idx = next(
        i for i, line in enumerate(lines) if line.startswith("Store: local index")
    )
    assert "never" in lines[store_idx]
    body = "\n".join(lines[store_idx:])
    dependents_idx = lines.index("Dependents:", store_idx)
    assert store_idx < dependents_idx
    assert "changed" in body
    assert "direct (depth 0)" in body
    assert "transitive (depth 1)" in body
    assert "main" in body


def test_prs_impact_lists_unindexed_and_deleted(
    impact_store, tmp_path, monkeypatch
):
    _install_gh_stub(
        tmp_path,
        monkeypatch,
        _stub_body(tmp_path, diff_42=DIFF_UNINDEXED_AND_DELETED),
    )
    result = _invoke_prs(impact_store, "--impact", "42")
    assert result.exit_code == 0, result.output
    assert "demo:docs/design-note.md" in result.output
    assert "demo:gone.py" in result.output


def test_prs_impact_rejects_bad_ref_before_subprocess(impact_store, tmp_path, monkeypatch):
    _install_gh_stub(tmp_path, monkeypatch, _stub_body(tmp_path))
    result = _invoke_prs(impact_store, "--impact", "-evil")
    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_prs_impact_gh_failure_single_error_no_store_line(
    impact_store, tmp_path, monkeypatch
):
    _install_gh_stub(
        tmp_path,
        monkeypatch,
        'case "$1 $2" in "--version"*) echo \'gh version 2.63.0\' ;; esac\n'
        f'case "$2" in list) cat \'{FIXTURES / "pr_list.json"}\' ;; '
        'view) echo "gh: Not Found" >&2; exit 1 ;; esac',
    )
    result = _invoke_prs(impact_store, "--impact", "42")
    assert result.exit_code != 0
    assert result.output.count("Error:") == 1, result.output
    assert "Not Found" in result.output
    assert "Store: local index" not in result.output
    assert "Traceback" not in result.output


def test_prs_impact_unregistered_store_fails_clean(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "graph.db"
    _seed_store(db_path)
    _install_gh_stub(
        tmp_path, monkeypatch, _stub_body(tmp_path, diff_42=DIFF_TOUCHES_CHANGED)
    )
    result = _invoke_prs(db_path, "--impact", "42")
    assert result.exit_code != 0
    assert result.output.count("Error:") == 1, result.output
    assert "Traceback" not in result.output


# --- T007: --json payload ---


def test_prs_json_defaults_to_null_sections(tmp_path, monkeypatch):
    db_path = tmp_path / "graph.db"
    _seed_store(db_path)
    _install_gh_stub(tmp_path, monkeypatch, _stub_body(tmp_path))
    result = _invoke_prs(db_path, "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert set(data) == {"store", "prs", "impact", "conflicts"}
    assert data["store"] == {"index": "local", "built": None}
    assert [pr["number"] for pr in data["prs"]] == [42, 43]
    assert data["impact"] is None
    assert data["conflicts"] is None


def test_prs_json_carries_requested_sections(impact_store, tmp_path, monkeypatch):
    _install_gh_stub(
        tmp_path,
        monkeypatch,
        _stub_body(
            tmp_path,
            diff_42=DIFF_TOUCHES_CHANGED,
            diff_43=DIFF_TOUCHES_DIRECT,
        ),
    )
    result = _invoke_prs(impact_store, "--json", "--impact", "42", "--conflicts")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["store"]["index"] == "local"
    assert data["impact"]["basis"] == {"kind": "pr", "base": "main"}
    assert [seed["name"] for seed in data["impact"]["seeds"]] == ["changed"]
    assert data["impact"]["unindexed_files"] == []
    assert data["impact"]["deleted_files"] == []
    assert data["conflicts"]["pairs"] == [
        {"a": 42, "b": 43, "shared": 1, "communities": ["core"]}
    ]
    assert data["conflicts"]["hint"] is None
