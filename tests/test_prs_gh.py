"""gh wrapper + defensive PR-parsing contracts; gh is always a PATH stub."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cairn.graph import prs
from cairn.graph.prs import (
    GhError,
    PrRecord,
    PrView,
    list_open_prs,
    parse_pr_list,
    parse_pr_view,
)

FIXTURES = Path(__file__).parent / "fixtures" / "gh"
STUB_VERSION = "gh version 2.63.0 (2024-11-19)"
PR_LIST_ARGV = "pr list --json number,title,headRefName,author,state,statusCheckRollup,reviewDecision"


def _install_gh_stub(tmp_path: Path, monkeypatch, body: str) -> Path:
    """Shadow gh with an executable stub that logs argv; return the log path."""
    log = tmp_path / "gh_calls.log"
    bindir = tmp_path / "_gh_stub"
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    script = (
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$CAIRN_STUB_LOG"\n'
        f"{body}\n"
    )
    gh.write_text(script, encoding="utf-8")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("CAIRN_STUB_LOG", str(log))
    return log


def _version_stub(tmp_path: Path, monkeypatch) -> Path:
    return _install_gh_stub(
        tmp_path,
        monkeypatch,
        f'case "$1 $2" in "--version"*) echo \'{STUB_VERSION}\' ;; esac',
    )


def _list_fixture_items() -> list[dict]:
    return json.loads((FIXTURES / "pr_list.json").read_text(encoding="utf-8"))


def _item_with_rollup(rollup: list) -> list[dict]:
    return [
        {
            "number": 1,
            "title": "t",
            "headRefName": "b",
            "author": "a",
            "statusCheckRollup": rollup,
        }
    ]


# --- wrapper: argv allowlist + failure modes (D-001) ---


@pytest.mark.parametrize(
    "argv",
    [
        ["pr", "merge", "42"],
        ["pr", "edit", "42", "--title", "x"],
        ["api", "repos/example/example"],
        ["auth", "token"],
        ["repo", "view"],
        ["--version", "extra"],
    ],
)
def test_gh_wrapper_refuses_non_read_only_verbs(tmp_path, argv):
    with pytest.raises(GhError, match="read-only"):
        prs._gh(tmp_path, argv)


def test_gh_error_carries_gh_stderr(tmp_path, monkeypatch):
    _install_gh_stub(
        tmp_path,
        monkeypatch,
        'case "$1 $2" in "pr list") echo "gh: API rate limit exceeded" >&2\nexit 2 ;; esac',
    )
    with pytest.raises(GhError, match="rate limit"):
        list_open_prs(tmp_path)


def test_gh_missing_binary_yields_install_guidance(tmp_path, monkeypatch):
    empty = tmp_path / "no-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(GhError, match="install"):
        list_open_prs(tmp_path)


def test_gh_timeout_surfaces_as_gh_error(tmp_path, monkeypatch):
    _install_gh_stub(
        tmp_path, monkeypatch, 'case "$1 $2" in "pr list") sleep 5 ;; esac'
    )
    monkeypatch.setattr(prs, "_GH_TIMEOUT", 0.3)
    with pytest.raises(GhError, match="timed out"):
        list_open_prs(tmp_path)


# --- defensive list parsing (D-002) ---


def test_parse_pr_list_complete_fixture():
    records = parse_pr_list(
        (FIXTURES / "pr_list.json").read_text(encoding="utf-8"), Path("/unused")
    )
    assert records == [
        PrRecord(42, "Cap traversal depth", "fix/depth-cap", "ada", "pass", "APPROVED"),
        PrRecord(43, "Add compass export", "feat/export", "linus", "fail", "REVIEW_REQUIRED"),
    ]


def test_parse_pr_list_ignores_unknown_fields():
    items = _list_fixture_items()
    items[0]["someFutureField"] = {"nested": [1, 2, 3]}
    records = parse_pr_list(json.dumps(items), Path("/unused"))
    assert records[0].number == 42


@pytest.mark.parametrize("key", ["number", "title", "headRefName", "statusCheckRollup"])
def test_parse_pr_list_missing_required_field_names_gh_version(
    tmp_path, monkeypatch, key
):
    items = _list_fixture_items()
    del items[0][key]
    _version_stub(tmp_path, monkeypatch)
    with pytest.raises(GhError, match=r"2\.63"):
        parse_pr_list(json.dumps(items), tmp_path)


def test_parse_pr_list_mistyped_required_field_fails_closed(tmp_path, monkeypatch):
    items = _list_fixture_items()
    items[0]["number"] = "42"
    _version_stub(tmp_path, monkeypatch)
    with pytest.raises(GhError, match="number"):
        parse_pr_list(json.dumps(items), tmp_path)


@pytest.mark.parametrize("payload", ["not json at all", '{"unexpected": true}'])
def test_parse_pr_list_rejects_unparseable_output(tmp_path, monkeypatch, payload):
    _version_stub(tmp_path, monkeypatch)
    with pytest.raises(GhError, match=r"2\.63"):
        parse_pr_list(payload, tmp_path)


@pytest.mark.parametrize(
    "author, expected",
    [
        ({"login": "ada"}, "ada"),
        ("plain", "plain"),
        (None, ""),
        ({"nope": 1}, ""),
        (7, ""),
    ],
)
def test_author_coerced_from_object_or_string(author, expected):
    items = _list_fixture_items()
    items[0]["author"] = author
    records = parse_pr_list(json.dumps(items), Path("/unused"))
    assert records[0].author == expected


@pytest.mark.parametrize(
    "rollup, expected",
    [
        ([], "none"),
        ([{"conclusion": "SUCCESS"}], "pass"),
        ([{"state": "SUCCESS"}], "pass"),
        ([{"conclusion": "FAILURE"}], "fail"),
        ([{"state": "PENDING"}], "pending"),
        ([{"conclusion": "TIMED_OUT"}, {"state": "SUCCESS"}], "fail"),
        ([{"conclusion": "SUCCESS"}, {"state": "IN_PROGRESS"}], "pending"),
        ([{"conclusion": "SUCCESS"}, {"mystery": "shape"}], "pending"),
        ([{"mystery": "shape"}], "none"),
        (["junk"], "none"),
    ],
)
def test_ci_classification_is_conservative(rollup, expected):
    records = parse_pr_list(json.dumps(_item_with_rollup(rollup)), Path("/unused"))
    assert records[0].ci_state == expected


# --- pr view fixture contract ---


def test_parse_pr_view_complete_fixture():
    view = parse_pr_view(
        (FIXTURES / "pr_view.json").read_text(encoding="utf-8"), Path("/unused")
    )
    assert view == PrView(number=42, head_ref="fix/depth-cap", base_ref="main")


def test_parse_pr_view_missing_base_ref_fails_closed_with_version(
    tmp_path, monkeypatch
):
    view = json.loads((FIXTURES / "pr_view.json").read_text(encoding="utf-8"))
    del view["baseRefName"]
    _version_stub(tmp_path, monkeypatch)
    with pytest.raises(GhError, match=r"2\.63"):
        parse_pr_view(json.dumps(view), tmp_path)


# --- list_open_prs over a fake gh on PATH (no network) ---


def test_list_open_prs_over_stubbed_gh_runs_read_only_argv(tmp_path, monkeypatch):
    fixture = FIXTURES / "pr_list.json"
    log = _install_gh_stub(
        tmp_path,
        monkeypatch,
        'case "$1 $2" in\n'
        f'"--version"*) echo \'{STUB_VERSION}\' ;;\n'
        f'"pr list") cat \'{fixture}\' ;;\n'
        "esac",
    )
    records = list_open_prs(tmp_path)
    assert records[0] == PrRecord(
        42, "Cap traversal depth", "fix/depth-cap", "ada", "pass", "APPROVED"
    )
    assert records[1].ci_state == "fail"
    assert log.read_text(encoding="utf-8") == f"{PR_LIST_ARGV}\n"
