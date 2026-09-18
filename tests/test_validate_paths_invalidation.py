"""Tests for build-time auto-invalidation via ``validate-paths --mark``.

A stale memory keeps its record: ``valid_until`` is stamped at build time
and the ``memory_validity`` projection row follows. A successor symbol is
linked only when the graph identifies exactly one candidate sharing the
dead ref's identity anchors; removed or ambiguous refs stay unlinked.
Non-memory concepts keep the stale flag without validity side effects;
without ``--mark`` nothing is written.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from cairn.graph.schema import _apply_schema
from cairn.memory.promotion import capture_memory
from cairn.memory.store import get_memory
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "g.db"


@pytest.fixture
def db(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    # Release the schema DDL's write lock so the CLI can open the file.
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def bundle(tmp_path):
    # Sibling layout <db dir>/.knowledge: the CLI's get_db pairs them.
    return OKFBundle(str(tmp_path / ".knowledge"))


def _seed_memory(db, bundle, title, body):
    result = capture_memory(db, bundle, "decision", title, body)
    db.commit()
    return result["path"], result["concept"].extensions["valid_from"]


def _seed_stale_memory(db, bundle):
    return _seed_memory(
        db,
        bundle,
        "DefunctProbe policy notes",
        "Applies while `defunct_helper_qa` exists",
    )


def _seed_graph_symbol(db, path, name, qualified_name=None, kind="function"):
    db.execute("INSERT OR IGNORE INTO repos (id, name, path) VALUES ('r1', 'r', '/r')")
    db.execute(
        "INSERT OR IGNORE INTO files (id, repo_id, path, language)"
        " VALUES (?, 'r1', ?, 'py')",
        (f"f:{path}", path),
    )
    db.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind)"
        " VALUES (?, ?, ?, ?, ?)",
        (f"s:{path}:{name}", f"f:{path}", name, qualified_name, kind),
    )
    db.commit()


def _run_validate_paths(db_path, bundle, *extra):
    from click.testing import CliRunner

    from cairn.cli import main

    return CliRunner().invoke(
        main,
        ["validate-paths", "--db", str(db_path), "--knowledge", str(bundle.root)]
        + list(extra),
    )


def _validity_row(db, concept_id):
    return db.execute(
        "SELECT valid_from, valid_until, successor_symbol "
        "FROM memory_validity WHERE concept_id = ?",
        (concept_id,),
    ).fetchone()


class TestMarkAutoInvalidation:
    def test_mark_stamps_valid_until_and_updates_projection(self, db, bundle, db_path):
        cid, valid_from = _seed_stale_memory(db, bundle)
        before = _now()

        result = _run_validate_paths(db_path, bundle, "--mark")

        assert result.exit_code != 0  # non-zero stale-detection exit preserved
        on_disk = get_memory(bundle, cid)
        assert on_disk.extensions["stale"] is True
        assert on_disk.extensions["valid_from"] == valid_from
        assert before <= on_disk.extensions["valid_until"]
        # Removed symbol, no candidates: no successor is invented.
        assert on_disk.extensions.get("successor_symbol") is None
        row = _validity_row(db, cid)
        assert row["valid_from"] == valid_from
        assert row["valid_until"] == on_disk.extensions["valid_until"]
        assert row["successor_symbol"] is None
        # The mark upserts the existing projection row; it never duplicates it.
        assert db.execute(
            "SELECT COUNT(*) FROM memory_validity WHERE concept_id = ?", (cid,)
        ).fetchone()[0] == 1

    def test_mark_does_not_touch_existing_successor_link(self, db, bundle, db_path):
        cid, _ = _seed_stale_memory(db, bundle)
        concept = get_memory(bundle, cid)
        concept.extensions["successor_symbol"] = "renamed_helper_qa"
        bundle.write_concept(concept)

        _run_validate_paths(db_path, bundle, "--mark")

        on_disk = get_memory(bundle, cid)
        assert on_disk.extensions["valid_until"] is not None
        assert on_disk.extensions["successor_symbol"] == "renamed_helper_qa"
        assert _validity_row(db, cid)["successor_symbol"] == "renamed_helper_qa"

    def test_mark_keys_projection_by_relative_id_under_symlinked_root(
            self, db, bundle, db_path, tmp_path):
        # A symlinked knowledge root makes read_concept resolve concept ids
        # through the real path; the projection row must still land on the
        # bundle-relative key capture wrote.
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        seeded = OKFBundle(str(link / ".knowledge"))
        cid, valid_from = _seed_stale_memory(db, seeded)

        from click.testing import CliRunner

        from cairn.cli import main

        result = CliRunner().invoke(
            main,
            ["validate-paths", "--db", str(db_path), "--knowledge",
             str(link / ".knowledge"), "--mark"],
        )

        assert result.exit_code != 0
        rows = db.execute(
            "SELECT valid_from, valid_until FROM memory_validity "
            "WHERE concept_id = ?",
            (cid,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["valid_from"] == valid_from
        assert rows[0]["valid_until"] is not None

    def test_mark_leaves_non_memory_concepts_without_validity(self, db, bundle, db_path):
        bundle.write_concept(
            OKFConcept(
                type="guide",
                title="QA module",
                body="See `ghost_symbol_qa` for details.",
                concept_id="compass/qa_module",
            )
        )

        result = _run_validate_paths(db_path, bundle, "--mark")

        assert result.exit_code != 0
        on_disk = bundle.read_concept("compass/qa_module")
        assert on_disk.extensions["stale"] is True
        assert "valid_until" not in on_disk.extensions
        assert _validity_row(db, "compass/qa_module") is None


class TestMarkGating:
    def test_without_mark_nothing_is_written(self, db, bundle, db_path):
        cid, _ = _seed_stale_memory(db, bundle)

        result = _run_validate_paths(db_path, bundle)

        assert result.exit_code != 0
        on_disk = get_memory(bundle, cid)
        assert "stale" not in on_disk.extensions
        assert on_disk.extensions.get("valid_until") is None
        assert _validity_row(db, cid)["valid_until"] is None


class TestMarkSuccessorLinks:
    def test_unique_rename_records_successor_link(self, db, bundle, db_path):
        _seed_graph_symbol(
            db, "src/api/client.py", "safe_api_call_with_retry",
            qualified_name="ApiClient.safe_api_call_with_retry",
        )
        cid, valid_from = _seed_memory(
            db, bundle,
            "Probe policy notes",
            "Applies while `ApiClient.safe_api_call` exists",
        )
        before = _now()

        result = _run_validate_paths(db_path, bundle, "--mark")

        assert result.exit_code != 0
        on_disk = get_memory(bundle, cid)
        assert on_disk.extensions["stale"] is True
        assert before <= on_disk.extensions["valid_until"]
        assert on_disk.extensions["successor_symbol"] == (
            "ApiClient.safe_api_call_with_retry"
        )
        row = _validity_row(db, cid)
        assert row["valid_from"] == valid_from
        assert row["successor_symbol"] == "ApiClient.safe_api_call_with_retry"

    def test_removed_symbol_stays_invalidated_but_unlinked(self, db, bundle, db_path):
        # An unrelated live symbol is not a successor: a bare dead ref has
        # no knowable identity anchors.
        _seed_graph_symbol(
            db, "src/api/client.py", "unrelated_thing",
            qualified_name="ApiClient.unrelated_thing",
        )
        cid, _ = _seed_memory(
            db, bundle,
            "DefunctProbe policy notes",
            "Applies while `defunct_helper_qa` exists",
        )

        result = _run_validate_paths(db_path, bundle, "--mark")

        assert result.exit_code != 0
        on_disk = get_memory(bundle, cid)
        assert on_disk.extensions["valid_until"] is not None
        assert on_disk.extensions.get("successor_symbol") is None
        assert _validity_row(db, cid)["successor_symbol"] is None

    def test_ambiguous_rename_stays_invalidated_but_unlinked(self, db, bundle, db_path):
        _seed_graph_symbol(
            db, "src/api/client.py", "safe_api_call_retry",
            qualified_name="ApiClient.safe_api_call_retry",
        )
        _seed_graph_symbol(
            db, "src/api/client.py", "safe_api_call_v2",
            qualified_name="ApiClient.safe_api_call_v2",
        )
        cid, _ = _seed_memory(
            db, bundle,
            "Probe policy notes",
            "Applies while `ApiClient.safe_api_call` exists",
        )

        result = _run_validate_paths(db_path, bundle, "--mark")

        assert result.exit_code != 0
        on_disk = get_memory(bundle, cid)
        assert on_disk.extensions["valid_until"] is not None
        assert on_disk.extensions.get("successor_symbol") is None
        assert _validity_row(db, cid)["successor_symbol"] is None

    def test_unique_symbol_in_cited_file_records_link(self, db, bundle, db_path):
        _seed_graph_symbol(db, "src/api/client.py", "safe_api_call_with_retry")
        cid, _ = _seed_memory(
            db, bundle,
            "Retry policy notes",
            "Covers `safe_api_call` retry handling in `src/api/client.py`",
        )

        result = _run_validate_paths(db_path, bundle, "--mark")

        assert result.exit_code != 0
        on_disk = get_memory(bundle, cid)
        assert on_disk.extensions["valid_until"] is not None
        assert on_disk.extensions["successor_symbol"] == "safe_api_call_with_retry"
        assert _validity_row(db, cid)["successor_symbol"] == (
            "safe_api_call_with_retry"
        )

    def test_ambiguous_file_scope_stays_unlinked(self, db, bundle, db_path):
        _seed_graph_symbol(db, "src/api/client.py", "safe_api_call_with_retry")
        _seed_graph_symbol(db, "src/api/client.py", "unrelated_helper")
        cid, _ = _seed_memory(
            db, bundle,
            "Retry policy notes",
            "Covers `safe_api_call` retry handling in `src/api/client.py`",
        )

        result = _run_validate_paths(db_path, bundle, "--mark")

        assert result.exit_code != 0
        on_disk = get_memory(bundle, cid)
        assert on_disk.extensions["valid_until"] is not None
        assert on_disk.extensions.get("successor_symbol") is None
        assert _validity_row(db, cid)["successor_symbol"] is None

    def test_dead_file_ref_alone_records_no_link(self, db, bundle, db_path):
        # The stale entry's dead ref is a file path, not a symbol: no
        # successor-symbol resolution applies.
        cid, _ = _seed_memory(
            db, bundle,
            "Removed module notes",
            "Lives in `src/gone/module.py` for details",
        )

        result = _run_validate_paths(db_path, bundle, "--mark")

        assert result.exit_code != 0
        on_disk = get_memory(bundle, cid)
        assert on_disk.extensions["valid_until"] is not None
        assert on_disk.extensions.get("successor_symbol") is None


class TestSuccessorResolver:
    def test_dotted_ref_matches_suffix_qualified_candidate(self, db):
        from cairn.refs import successor_candidates

        _seed_graph_symbol(
            db, "src/pkg/a.py", "safe_api_call_v2",
            qualified_name="pkg.ApiClient.safe_api_call_v2",
        )

        assert successor_candidates(db, "ApiClient.safe_api_call") == [
            "pkg.ApiClient.safe_api_call_v2"
        ]

    def test_bare_ref_without_anchors_has_no_candidates(self, db):
        from cairn.refs import successor_candidates

        _seed_graph_symbol(
            db, "src/api/client.py", "other",
            qualified_name="ApiClient.other",
        )

        assert successor_candidates(db, "defunct_thing") == []

    def test_prefix_underscores_do_not_wildcard(self, db):
        from cairn.refs import successor_candidates

        _seed_graph_symbol(
            db, "src/api/client.py", "new_thing",
            qualified_name="ApiClient_v2.new_thing",
        )
        # Without LIKE escaping, prefix `ApiClient_v2` would also match
        # `ApiClientXv2.*`.
        _seed_graph_symbol(
            db, "src/api/client.py", "thing",
            qualified_name="ApiClientXv2.thing",
        )

        assert successor_candidates(db, "ApiClient_v2.old_thing") == [
            "ApiClient_v2.new_thing"
        ]

    def test_resolve_links_only_on_unique_consensus(self, db):
        from cairn.refs import resolve_successor

        _seed_graph_symbol(
            db, "src/api/client.py", "a_new",
            qualified_name="ApiClient.a_new",
        )
        body = "Uses `ApiClient.a_old` and `ApiClient.b_old` together"

        assert resolve_successor(db, body) == "ApiClient.a_new"

    def test_resolve_returns_none_when_dead_refs_disagree(self, db):
        from cairn.refs import resolve_successor

        _seed_graph_symbol(
            db, "src/api/client.py", "a_new",
            qualified_name="ApiClient.a_new",
        )
        _seed_graph_symbol(
            db, "src/other/mod.py", "b_new",
            qualified_name="Other.b_new",
        )
        body = "Uses `ApiClient.a_old` and `Other.b_old` together"

        assert resolve_successor(db, body) is None

    def test_resolve_ignores_live_refs(self, db):
        from cairn.refs import resolve_successor

        _seed_graph_symbol(
            db, "src/api/client.py", "safe_api_call_v2",
            qualified_name="ApiClient.safe_api_call_v2",
        )

        assert resolve_successor(
            db, "Applies while `ApiClient.safe_api_call_v2` exists"
        ) is None
