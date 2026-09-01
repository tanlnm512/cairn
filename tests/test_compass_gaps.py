"""detect_gaps coverage matching: module ids vs compass resource fields.

Modules from _get_all_modules are repo-qualified (`{repo_id}/{path}`) while
compass concepts store their `resource` repo-relative (generator.py's
`module_path` is documented repo-relative). The comparison must bridge the
repo prefix; a compass tagged with one repo must not cover a same-named
module in a different repo.
"""
from __future__ import annotations

import sqlite3

import pytest

from cairn.compass.gaps import detect_gaps
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept

COVERED_FILE = "/work/agent_runtime/app/services/jobs.py"


@pytest.fixture
def conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE repos (id TEXT PRIMARY KEY, name TEXT, path TEXT, "
        "language TEXT, git_remote TEXT, indexed_at TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE files (id TEXT PRIMARY KEY, repo_id TEXT, path TEXT, "
        "language TEXT, hash TEXT, line_count INTEGER, indexed_at TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE symbols (id TEXT PRIMARY KEY, file_id TEXT, name TEXT, "
        "qualified_name TEXT, kind TEXT, line_start INTEGER)"
    )
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES "
        "('agent_runtime', 'agent_runtime', '/work/agent_runtime'), "
        "('other_repo', 'other_repo', '/work/other_repo')"
    )
    for repo in ("agent_runtime", "other_repo"):
        fid = f"{repo}:jobs"
        conn.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES "
            "(?, ?, ?, 'python')",
            (fid, repo, f"/work/{repo}/app/services/jobs.py"),
        )
        for i in range(5):
            conn.execute(
                "INSERT INTO symbols (id, file_id, name, kind) "
                "VALUES (?, ?, ?, 'function')",
                (f"{fid}:sym{i}", fid, f"sym{i}", ),
            )
    extra_fid = "agent_runtime:extra"
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES "
        "(?, 'agent_runtime', ?, 'python')",
        (extra_fid, "/work/agent_runtime/app/services_extra/impl.py"),
    )
    for i in range(5):
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind) "
            "VALUES (?, ?, ?, 'function')",
            (f"{extra_fid}:sym{i}", extra_fid, f"sym{i}", ),
        )
    conn.commit()
    return conn


def _compass(knowledge, resource, *, repo=None):
    tags = [repo] if repo else []
    OKFBundle(str(knowledge)).write_concept(
        OKFConcept(
            type="Compass",
            title="test compass",
            resource=resource,
            tags=tags,
            concept_id="compass/" + resource.replace("/", "-").replace(".", "-"),
            body="Navigation guide.",
        )
    )


def test_compass_resource_repo_relative_covers_repo_qualified_module(conn, tmp_path):
    # The reported bug: resource `app/services/jobs.py` vs module
    # `agent_runtime/app/services/jobs.py` never matched, so every module
    # stayed a gap forever.
    _compass(tmp_path / "k", "app/services/jobs.py", repo="agent_runtime")
    assert detect_gaps(conn, OKFBundle(str(tmp_path / "k"))) == [
        "agent_runtime/app/services_extra/impl.py",
        "other_repo/app/services/jobs.py",
    ]


def test_compass_covers_by_segment_prefix_and_respects_boundary(conn, tmp_path):
    knowledge = tmp_path / "k"
    # `app/services` is a path-segment prefix of app/services/jobs.py
    # (covers both repos' modules only via the repo tag — it is tagged
    # agent_runtime here) but NOT of app/services_extra/impl.py: the
    # segment boundary keeps `services` from matching `services_extra`.
    _compass(knowledge, "app/services", repo="agent_runtime")
    assert detect_gaps(conn, OKFBundle(str(knowledge))) == [
        "agent_runtime/app/services_extra/impl.py",
        "other_repo/app/services/jobs.py",
    ]


def test_compass_from_other_repo_does_not_cover_same_named_module(conn, tmp_path):
    # Both repos have app/services/jobs.py; a compass tagged for
    # other_repo covers only other_repo's module.
    _compass(tmp_path / "k", "app/services/jobs.py", repo="other_repo")
    assert detect_gaps(conn, OKFBundle(str(tmp_path / "k"))) == [
        "agent_runtime/app/services/jobs.py",
        "agent_runtime/app/services_extra/impl.py",
    ]


def test_untagged_compass_covers_any_repo(conn, tmp_path):
    # Compasses without a resolvable repo tag keep repo-agnostic matching.
    _compass(tmp_path / "k", "app/services/jobs.py")
    assert detect_gaps(conn, OKFBundle(str(tmp_path / "k"))) == [
        "agent_runtime/app/services_extra/impl.py"
    ]
