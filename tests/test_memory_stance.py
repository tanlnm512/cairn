"""Tests for the record-time stance prior and refs-baseline seeding (FR-001).

A memory may carry `memory_stance` (preferred|tentative|contested) in its
frontmatter, set through the capture chokepoint; `memory_refs_baseline` records
the refs-verified fraction at capture time so reflect can measure drops against
it. Zero-ref bodies get no baseline; reflect owns `memory_stance_peer`, so
capture never sets it.
"""
from __future__ import annotations

import copy
import sqlite3

import pytest
from click.testing import CliRunner

from cairn.graph.schema import _apply_schema, get_db
from cairn.memory.promotion import capture_memory
from cairn.memory.stance import (
    REFS_BASELINE_KEY,
    STANCE_KEY,
    STANCE_PEER_KEY,
    reflect_store,
)
from cairn.memory.store import create_memory, get_memory
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept


def _seed_symbol(conn: sqlite3.Connection) -> None:
    """One repo/file/symbol so a memory's backtick ref verifies."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'r1', '.')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'r1', 'src/auth.py', 'python')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (1, 1, 'login', 'function', 'auth.login', 1, 10)"
    )
    conn.commit()


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    _seed_symbol(conn)
    yield conn
    conn.close()


@pytest.fixture
def bundle(tmp_path):
    return OKFBundle(str(tmp_path / "knowledge"))


class TestStancePrior:
    """create_memory/capture_memory validate and persist memory_stance."""

    def test_create_memory_valid_stance_sets_key(self):
        for stance in ("preferred", "tentative", "contested"):
            concept = create_memory(
                type_="decision", title="s", body="b", stance=stance
            )
            assert concept.extensions["memory_stance"] == stance

    def test_create_memory_default_leaves_stance_unset(self):
        concept = create_memory(type_="decision", title="s", body="b")
        assert "memory_stance" not in concept.extensions

    def test_create_memory_invalid_stance_raises(self):
        with pytest.raises(ValueError, match="stance"):
            create_memory(type_="decision", title="s", body="b", stance="certain")

    def test_capture_invalid_stance_raises_before_any_write(self, db, bundle):
        with pytest.raises(ValueError, match="stance"):
            capture_memory(
                db, bundle, type_="decision", title="never written",
                body="b", stance="certain",
            )
        assert bundle.list_concepts(prefix="memory/") == []

    def test_capture_stance_round_trips_through_frontmatter(self, db, bundle):
        result = capture_memory(
            db, bundle, type_="decision", title="stance round trip",
            body="Use `login()` on 401.", confidence=0.8, stance="tentative",
        )
        on_disk = get_memory(bundle, result["path"])
        assert on_disk.extensions["memory_stance"] == "tentative"

    def test_capture_never_sets_stance_peer(self, db, bundle):
        result = capture_memory(
            db, bundle, type_="decision", title="peer owned by reflect",
            body="b", stance="contested",
        )
        on_disk = get_memory(bundle, result["path"])
        assert "memory_stance_peer" not in on_disk.extensions


class TestRefsBaselineSeeding:
    """capture seeds memory_refs_baseline from the live fraction (D-006)."""

    def test_capture_seeds_baseline_with_all_refs_verified(self, db, bundle):
        result = capture_memory(
            db, bundle, type_="decision", title="verified at capture",
            body="Use `login()` on 401.", confidence=0.8,
        )
        on_disk = get_memory(bundle, result["path"])
        assert on_disk.extensions["memory_refs_baseline"] == 1.0

    def test_capture_seeds_baseline_with_partial_fraction(self, db, bundle):
        result = capture_memory(
            db, bundle, type_="decision", title="half verified at capture",
            body="Use `login()` and `logout()` together.", confidence=0.8,
        )
        on_disk = get_memory(bundle, result["path"])
        assert on_disk.extensions["memory_refs_baseline"] == 0.5

    def test_baseline_is_recorded_not_recomputed(self, db, bundle):
        result = capture_memory(
            db, bundle, type_="decision", title="frozen at capture",
            body="Use `login()` on 401.", confidence=0.8,
        )
        db.execute("DELETE FROM symbols WHERE name = 'login'")
        db.commit()
        on_disk = get_memory(bundle, result["path"])
        assert on_disk.extensions["memory_refs_baseline"] == 1.0

    def test_zero_ref_body_gets_no_baseline(self, db, bundle):
        result = capture_memory(
            db, bundle, type_="pattern", title="prose only",
            body="We deploy on Tuesdays.", confidence=0.7,
        )
        on_disk = get_memory(bundle, result["path"])
        assert "memory_refs_baseline" not in on_disk.extensions
        assert "memory_refs_baseline" not in (bundle.root / f"{result['path']}.md").read_text()


# ---------------------------------------------------------------------------
# Reflect engine: deterministic stance verdicts (FR-002/003/005/006)
# ---------------------------------------------------------------------------


def _add_symbol(conn: sqlite3.Connection, name: str, sid: int) -> None:
    """One more live file+symbol so a backtick ref to `name` verifies."""
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (?, 'r1', ?, 'python')",
        (sid, f"src/mod{sid}.py"),
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (?, ?, ?, 'class', ?, 1, 10)",
        (sid, sid, name, f"mod{sid}.{name}"),
    )
    conn.commit()


def _write_memory(bundle: OKFBundle, cid: str, body: str, **ext) -> None:
    extensions = {
        "memory_tier": "tribal",
        "memory_type": "decision",
        "memory_score": 0.8,
        "memory_is_latest": True,
        "memory_superseded_by": None,
    }
    extensions.update(ext)
    bundle.write_concept(OKFConcept(
        type="Tribal-decision",
        title=cid.rsplit("/", 1)[-1],
        body=body,
        concept_id=cid,
        tags=["decision"],
        extensions=extensions,
    ))


def _link_superseded(bundle: OKFBundle, old_cid: str, new_cid: str) -> None:
    old = bundle.read_concept(old_cid)
    old.extensions["memory_superseded_by"] = new_cid
    bundle.write_concept(old)


def _snapshot(bundle: OKFBundle) -> dict:
    return {
        str(p.relative_to(bundle.root)): p.read_bytes()
        for p in sorted(bundle.root.rglob("*"))
        if p.is_file()
    }


class TestReflectVerdicts:
    """reflect_store computes contested/tentative/preferred from evidence."""

    def test_contested_names_superseding_peer(self, db, bundle):
        """US1 AC1: verified preferred + contradicting superseded peer."""
        _add_symbol(db, "ApiClient", 2)
        _write_memory(
            bundle, "memory/tribal/aaa-old-auth",
            "Set `ApiClient` retries directly.", memory_refs_baseline=1.0,
        )
        _write_memory(
            bundle, "memory/tribal/bbb-new-auth",
            "Route `ApiClient` through the factory.", memory_refs_baseline=1.0,
        )
        _link_superseded(bundle, "memory/tribal/aaa-old-auth", "memory/tribal/bbb-new-auth")

        summary = reflect_store(bundle, db)

        assert set(summary) == {"changed", "unchanged", "contested"}
        old = bundle.read_concept("memory/tribal/aaa-old-auth")
        assert old.extensions[STANCE_KEY] == "contested"
        assert old.extensions[STANCE_PEER_KEY] == "memory/tribal/bbb-new-auth"
        successor = bundle.read_concept("memory/tribal/bbb-new-auth")
        assert successor.extensions[STANCE_KEY] == "preferred"
        assert summary == {"changed": 2, "unchanged": 0, "contested": 1}

    def test_superseded_without_shared_verified_ref_not_contested(self, db, bundle):
        _add_symbol(db, "NewLogin", 2)
        _write_memory(
            bundle, "memory/tribal/aaa-legacy", "Trust `LegacyLogin` for retries.",
            memory_refs_baseline=1.0,
        )
        _write_memory(
            bundle, "memory/tribal/bbb-new", "Trust `NewLogin` for retries.",
            memory_refs_baseline=1.0,
        )
        _link_superseded(bundle, "memory/tribal/aaa-legacy", "memory/tribal/bbb-new")

        reflect_store(bundle, db)

        old = bundle.read_concept("memory/tribal/aaa-legacy")
        assert old.extensions[STANCE_KEY] == "tentative"
        assert STANCE_PEER_KEY not in old.extensions

    def test_baseline_drop_marks_tentative(self, db, bundle):
        """US3 AC1: cited symbol rotted below the recorded baseline."""
        _write_memory(
            bundle, "memory/tribal/rotted", "Trust `LegacyLogin` for retries.",
            memory_refs_baseline=1.0,
        )

        summary = reflect_store(bundle, db)

        m = bundle.read_concept("memory/tribal/rotted")
        assert m.extensions[STANCE_KEY] == "tentative"
        assert STANCE_PEER_KEY not in m.extensions
        assert summary["changed"] == 1

    def test_contested_outranks_baseline_drop(self, db, bundle):
        _add_symbol(db, "ApiClient", 2)
        _write_memory(
            bundle, "memory/tribal/aaa-old", "Use `ApiClient` or `LegacyLogin`.",
            memory_refs_baseline=1.0,
        )
        _write_memory(
            bundle, "memory/tribal/bbb-new", "Use `ApiClient` via factory.",
            memory_refs_baseline=1.0,
        )
        _link_superseded(bundle, "memory/tribal/aaa-old", "memory/tribal/bbb-new")

        reflect_store(bundle, db)

        old = bundle.read_concept("memory/tribal/aaa-old")
        assert old.extensions[STANCE_KEY] == "contested"
        assert old.extensions[STANCE_PEER_KEY] == "memory/tribal/bbb-new"

    def test_preferred_overrides_prior_and_raises_baseline(self, db, bundle):
        _add_symbol(db, "ApiClient", 2)
        _write_memory(
            bundle, "memory/tribal/recovered", "Use `ApiClient` per flavor.",
            **{STANCE_KEY: "tentative", REFS_BASELINE_KEY: 0.5},
        )

        reflect_store(bundle, db)

        m = bundle.read_concept("memory/tribal/recovered")
        assert m.extensions[STANCE_KEY] == "preferred"
        assert m.extensions[REFS_BASELINE_KEY] == 1.0

    def test_zero_ref_memory_keeps_prior_and_gets_no_baseline(self, db, bundle):
        _write_memory(
            bundle, "memory/tribal/prose", "Prose guidance with no citations.",
            **{STANCE_KEY: "tentative"},
        )
        _write_memory(bundle, "memory/tribal/prose-unset", "More prose, no stance.")

        before = (bundle.root / "memory/tribal/prose.md").read_bytes()
        summary = reflect_store(bundle, db)

        prose = bundle.read_concept("memory/tribal/prose")
        assert prose.extensions[STANCE_KEY] == "tentative"
        assert REFS_BASELINE_KEY not in prose.extensions
        unset = bundle.read_concept("memory/tribal/prose-unset")
        assert STANCE_KEY not in unset.extensions
        assert (bundle.root / "memory/tribal/prose.md").read_bytes() == before
        assert summary["unchanged"] == 2

    def test_legacy_memory_seeds_baseline_without_downgrade(self, db, bundle):
        _add_symbol(db, "ApiClient", 2)
        _write_memory(bundle, "memory/tribal/legacy", "Use `ApiClient` sometimes.")

        reflect_store(bundle, db)

        m = bundle.read_concept("memory/tribal/legacy")
        assert m.extensions[STANCE_KEY] == "preferred"
        assert m.extensions[REFS_BASELINE_KEY] == 1.0

    def test_reflect_writes_stance_keys_only(self, db, bundle):
        _add_symbol(db, "ApiClient", 2)
        _write_memory(
            bundle, "memory/tribal/aaa-old", "Use `ApiClient` directly.",
            memory_refs_baseline=1.0,
        )
        _write_memory(
            bundle, "memory/tribal/bbb-new", "Use `ApiClient` via factory.",
            memory_refs_baseline=1.0,
        )
        _link_superseded(bundle, "memory/tribal/aaa-old", "memory/tribal/bbb-new")

        def _payload(cid: str) -> dict:
            c = bundle.read_concept(cid)
            ext = copy.deepcopy(c.extensions)
            for key in (STANCE_KEY, STANCE_PEER_KEY, REFS_BASELINE_KEY):
                ext.pop(key, None)
            return {"extensions": ext, "body": c.body, "title": c.title, "type": c.type}

        cids = ("memory/tribal/aaa-old", "memory/tribal/bbb-new")
        before = {cid: _payload(cid) for cid in cids}

        reflect_store(bundle, db)

        assert {cid: _payload(cid) for cid in cids} == before


class TestReflectIdempotent:
    """A second reflect run on an unchanged store rewrites nothing (US1 AC2)."""

    def _mixed_store(self, db, bundle):
        _add_symbol(db, "ApiClient", 2)
        _write_memory(
            bundle, "memory/tribal/aaa-old-auth",
            "Set `ApiClient` retries directly.", memory_refs_baseline=1.0,
        )
        _write_memory(
            bundle, "memory/tribal/bbb-new-auth",
            "Route `ApiClient` through the factory.", memory_refs_baseline=1.0,
        )
        _link_superseded(bundle, "memory/tribal/aaa-old-auth", "memory/tribal/bbb-new-auth")
        _write_memory(
            bundle, "memory/tribal/rotted", "Trust `LegacyRetry` for backoff.",
            memory_refs_baseline=1.0,
        )
        _write_memory(
            bundle, "memory/tribal/fresh", "Use `ApiClient` per flavor.",
            memory_refs_baseline=1.0,
        )
        _write_memory(
            bundle, "memory/tribal/prose", "Prose guidance with no citations.",
            **{STANCE_KEY: "tentative"},
        )
        _write_memory(
            bundle, "memory/tribal/stable", "Use `ApiClient` per flavor.",
            **{STANCE_KEY: "preferred", REFS_BASELINE_KEY: 1.0},
        )

    def test_double_run_is_byte_identical_no_op(self, db, bundle):
        self._mixed_store(db, bundle)

        summary1 = reflect_store(bundle, db)
        assert summary1["changed"] == 4
        assert summary1["unchanged"] == 2
        assert summary1["contested"] == 1
        after_first = _snapshot(bundle)

        summary2 = reflect_store(bundle, db)

        assert summary2 == {"changed": 0, "unchanged": 6, "contested": 1}
        assert _snapshot(bundle) == after_first


# ---------------------------------------------------------------------------
# CLI surface: record --stance, the reflect verb, _memory_line rendering
# ---------------------------------------------------------------------------


class TestMemoryStanceCli:
    """The stance surface rides the existing memory verbs end to end."""

    @pytest.fixture(autouse=True)
    def _hash_backend(self, hash_backend):
        """The CLI record/search paths embed; keep them on the dep-free backend."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def file_db(self, tmp_path):
        db_path = str(tmp_path / "g.db")
        conn = get_db(db_path)
        _seed_symbol(conn)
        conn.close()
        return db_path

    def _invoke(self, runner, argv):
        from cairn.cli import main

        result = runner.invoke(main, argv)
        assert result.exit_code == 0, result.output
        return result.output

    def test_record_stance_round_trips_to_search(self, runner, file_db, tmp_path):
        knowledge = str(tmp_path / "k")
        self._invoke(runner, [
            "memory", "record", "decision", "auth retry rule",
            "--body", "Always retry 401 via `login()`.",
            "--stance", "tentative",
            "--db", file_db, "--knowledge", knowledge,
        ])
        out = self._invoke(runner, [
            "memory", "search", "auth retry rule",
            "--db", file_db, "--knowledge", knowledge,
        ])
        assert "refs-verified=1.0, stance=tentative" in out, out

    def test_record_without_stance_leaves_line_unstanced(self, runner, file_db, tmp_path):
        knowledge = str(tmp_path / "k")
        self._invoke(runner, [
            "memory", "record", "pattern", "deploy rule",
            "--body", "Deploy on Tuesdays.",
            "--db", file_db, "--knowledge", knowledge,
        ])
        out = self._invoke(runner, [
            "memory", "search", "deploy rule",
            "--db", file_db, "--knowledge", knowledge,
        ])
        assert "refs-verified=" in out, out
        assert "stance=" not in out, out

    def test_reflect_verb_prints_counts_and_is_idempotent(self, runner, file_db, tmp_path):
        knowledge = str(tmp_path / "k")
        bundle = OKFBundle(knowledge)
        conn = get_db(file_db)
        _add_symbol(conn, "ApiClient", 2)
        conn.close()
        _write_memory(
            bundle, "memory/tribal/aaa-old-auth",
            "Set `ApiClient` retries directly.", memory_refs_baseline=1.0,
        )
        _write_memory(
            bundle, "memory/tribal/bbb-new-auth",
            "Route `ApiClient` through the factory.", memory_refs_baseline=1.0,
        )
        _link_superseded(bundle, "memory/tribal/aaa-old-auth", "memory/tribal/bbb-new-auth")

        argv = ["memory", "reflect", "--db", file_db, "--knowledge", knowledge]
        out1 = self._invoke(runner, argv)
        assert "2 changed, 0 unchanged, 1 contested" in out1, out1
        out2 = self._invoke(runner, argv)
        assert "0 changed, 2 unchanged, 1 contested" in out2, out2

    def test_list_renders_stance_and_contested_peer(self, runner, file_db, tmp_path):
        knowledge = str(tmp_path / "k")
        bundle = OKFBundle(knowledge)
        conn = get_db(file_db)
        _add_symbol(conn, "ApiClient", 2)
        conn.close()
        _write_memory(
            bundle, "memory/tribal/aaa-old-auth",
            "Set `ApiClient` retries directly.", memory_refs_baseline=1.0,
        )
        _write_memory(
            bundle, "memory/tribal/bbb-new-auth",
            "Route `ApiClient` through the factory.", memory_refs_baseline=1.0,
        )
        _link_superseded(bundle, "memory/tribal/aaa-old-auth", "memory/tribal/bbb-new-auth")
        self._invoke(runner, ["memory", "reflect", "--db", file_db, "--knowledge", knowledge])

        out = self._invoke(runner, [
            "memory", "list", "--db", file_db, "--knowledge", knowledge,
        ])
        assert (
            "refs-verified=1.0, stance=contested peer=memory/tribal/bbb-new-auth" in out
        ), out

    def test_memory_line_renders_stance_and_peer(self, file_db):
        from cairn.cli.memory import _memory_line

        concept = OKFConcept(
            type="Tribal-decision", title="t", body="Use `login()`.",
            extensions={
                "memory_score": 0.9,
                "memory_stance": "contested",
                "memory_stance_peer": "memory/tribal/bbb",
            },
        )
        conn = get_db(file_db)
        try:
            line = _memory_line(concept, conn)
        finally:
            conn.close()
        assert line == "  [0.9, refs-verified=1.0, stance=contested peer=memory/tribal/bbb] t"
