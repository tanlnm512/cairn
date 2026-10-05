"""PR fetcher and store-age tests: fake gh on PATH serving committed fixtures."""

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("cairn.graph.prs_fetch")

from cairn.graph.prs import GhError
from cairn.graph.prs_fetch import (
    fetch_pr_diff,
    fetch_pr_view,
    normalize_impact_ref,
    store_build_age,
)

FIXTURES = Path(__file__).parent / "fixtures" / "gh"

_STUB = """#!/bin/sh
echo "$@" >> "$GH_STUB_LOG"
if [ -n "$GH_STUB_STDERR" ]; then
    echo "$GH_STUB_STDERR" >&2
    exit 1
fi
case "$2" in
    view) cat "$GH_STUB_VIEW" ;;
    diff) cat "$GH_STUB_DIFF" ;;
esac
"""


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "gh"
    stub.write_text(_STUB, encoding="utf-8")
    stub.chmod(0o755)
    log = tmp_path / "gh-args.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GH_STUB_LOG", str(log))
    monkeypatch.setenv("GH_STUB_VIEW", str(FIXTURES / "pr_view_fetch.json"))
    monkeypatch.setenv("GH_STUB_DIFF", str(FIXTURES / "pr_diff.patch"))
    return log


def test_fetch_pr_view_returns_view_fields(fake_gh, tmp_path):
    assert fetch_pr_view(tmp_path, "#148") == (
        148,
        "specs/steal-campaign-2026-10",
        "main",
    )
    assert fake_gh.read_text(encoding="utf-8").split() == [
        "pr",
        "view",
        "148",
        "--json",
        "number,headRefName,baseRefName",
    ]


@pytest.mark.parametrize("payload", ["not json", '{"number": 148}'])
def test_fetch_pr_view_malformed_json_fails_closed(
    fake_gh, tmp_path, monkeypatch, payload
):
    bad = tmp_path / "bad.json"
    bad.write_text(payload, encoding="utf-8")
    monkeypatch.setenv("GH_STUB_VIEW", str(bad))
    with pytest.raises(GhError):
        fetch_pr_view(tmp_path, "148")


def test_fetch_pr_view_bad_ref_skips_subprocess(fake_gh, tmp_path):
    with pytest.raises(ValueError):
        fetch_pr_view(tmp_path, "-branch")
    assert not fake_gh.exists()


@pytest.mark.parametrize("bad", ["-branch", "#", "", " "])
def test_normalize_impact_ref_rejects(bad):
    with pytest.raises(ValueError):
        normalize_impact_ref(bad)


def test_fetch_pr_diff_returns_patch_text(fake_gh, tmp_path):
    text = fetch_pr_diff(tmp_path, 148)
    assert text == (FIXTURES / "pr_diff.patch").read_text(encoding="utf-8")
    assert fake_gh.read_text(encoding="utf-8").split() == ["pr", "diff", "148"]


def test_fetch_pr_diff_surfaces_gh_stderr(fake_gh, tmp_path, monkeypatch):
    monkeypatch.setenv("GH_STUB_STDERR", "gh: Not Found")
    with pytest.raises(GhError, match="Not Found"):
        fetch_pr_diff(tmp_path, 148)


def _conn_with_build_runs(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE build_runs (started_at TEXT)")
    conn.executemany("INSERT INTO build_runs VALUES (?)", [(r,) for r in rows])
    return conn


def test_store_build_age_reports_newest_row():
    now = datetime.now(timezone.utc)
    conn = _conn_with_build_runs(
        [
            (now - timedelta(days=3)).isoformat(),
            (now - timedelta(hours=2)).isoformat(),
        ]
    )
    assert store_build_age(conn) == "2h old"


def test_store_build_age_absent_paths():
    assert store_build_age(sqlite3.connect(":memory:")) is None
    assert store_build_age(_conn_with_build_runs([])) is None
    assert store_build_age(_conn_with_build_runs(["garbage"])) is None
