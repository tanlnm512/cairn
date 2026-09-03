"""Contract tests for the wiki manifest (FR-005, D-006).

Pins the contract of ``src/cairn/wiki/manifest.py``:

- ``MANIFEST_SCHEMA = "cairn-wiki-manifest-3"``; the lifecycle vocabulary is
  DERIVED at read time (``cairn.wiki.lifecycle.DERIVED_STATES``) and never
  stored in the manifest;
- the manifest lives at ``<knowledge>/_wiki/manifest.json`` -- non-``.md``,
  so ``OKFBundle.list_concepts`` (rglob ``*.md``) never lists it;
- ``load_manifest(bundle_or_knowledge_root) -> dict`` returns
  ``{"schema": ..., "pages": {"{repo}/{page_id}": row}}``; a missing file
  is an empty pages dict, not an error; malformed JSON raises
  ``ValueError``; a schema-1 document (keyed by page id alone) is
  upgraded in memory -- repo from the row task's facts, else the promoted
  concept path, else the row is dropped with a warning -- and never
  written back on load;
- ``save_manifest(knowledge_root, manifest) -> bool`` writes atomically
  (the ``paths.set_config_values`` pattern: mkstemp in the target dir,
  flush + fsync, ``os.replace``, unlink on error), creates ``_wiki/`` when
  absent, and returns ``False`` on ``OSError``;
- per-page rows are keyed by ``{repo}/{page_id}`` and carry the full plan
  entry (page_id, title, description, module, source, seeds, input_hash)
  plus ``task_id`` and the cumulative ``queue_attempts`` counter — and
  NEVER a lifecycle ``state`` or a content ``commit_sha`` (the two-kind
  anti-collapse contract); schema-2 rows carrying those retired fields are
  normalized on load (``state``/``commit_sha`` stripped, ``attempts``
  renamed ``queue_attempts``);
- ``should_skip(page_row, current_plan_entry, bundle, repo)`` is True only
  when the recorded input hash equals the current plan hash AND the
  promoted concept (D-007 identity ``wiki/pages/{repo}/{page_id}``) is
  readable via ``bundle.read_concept``; promotion is derived from the
  concept, never from the recorded state. A changed module input re-queues
  exactly that page. ``--force`` is applied by the caller, not the helper.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept
from cairn.wiki.catalog import build_page_plan
from cairn.wiki.manifest import (
    MANIFEST_SCHEMA,
    load_manifest,
    save_manifest,
    should_skip,
)

REPO = "r"


def _seed_graph(conn: sqlite3.Connection) -> None:
    """Two single-file modules; no edges (degree ties break by name ASC)."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r', 'r', '/tmp/r')")
    for fid, path in [("f1", "m_a/core.py"), ("f2", "m_b/api.py")]:
        conn.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, 'r', ?, 'python')",
            (fid, path),
        )
    conn.commit()


@pytest.fixture
def plan(fresh_db):
    _seed_graph(fresh_db)
    return build_page_plan(fresh_db, REPO)


def _create_bundle(tmp_path) -> OKFBundle:
    """tmp_path bundle pattern per tests/test_tasks_safety.py."""
    knowledge_dir = tmp_path / ".knowledge"
    knowledge_dir.mkdir(exist_ok=True)
    (knowledge_dir / "_tasks").mkdir(exist_ok=True)
    return OKFBundle(knowledge_dir)


@pytest.fixture
def bundle(tmp_path):
    return _create_bundle(tmp_path)


def _row(
    plan_entry: dict,
    *,
    task_id: str = "",
    state: str = "planned",
    attempts: int = 0,
    legacy: bool = False,
) -> dict:
    """One manifest row: the full plan entry plus the tracking fields.

    Default (legacy=False) emits the schema-3 shape: ``queue_attempts``, no
    ``state``. ``legacy=True`` emits the retired schema-2 shape (``state`` +
    ``attempts``) for migration tests."""
    row = {**plan_entry, "task_id": task_id}
    if legacy:
        row["state"] = state
        row["attempts"] = attempts
    else:
        row["queue_attempts"] = attempts
    return row


def _manifest(plan: list) -> dict:
    return {
        "schema": MANIFEST_SCHEMA,
        "pages": {
            f"{REPO}/{page['page_id']}": _row(page, task_id=f"task-{page['page_id']}")
            for page in plan
        },
    }


def _empty_doc() -> dict:
    return {"schema": MANIFEST_SCHEMA, "pages": {}}


def _promote(bundle: OKFBundle, repo: str, page_id: str) -> None:
    """Write the D-007 promoted article so read_concept resolves it."""
    bundle.write_concept(
        OKFConcept(
            type="Wiki-Article",
            title=f"{page_id} page",
            body="## Sources\n",
            concept_id=f"wiki/pages/{repo}/{page_id}",
            tags=[repo, "wiki"],
        )
    )


def _changed_entry(plan_entry: dict) -> dict:
    """Re-derive a plan entry over a changed input (the planner hash recipe)."""
    entry = dict(plan_entry)
    entry["description"] = entry["description"] + " (changed)"
    entry.pop("input_hash", None)
    canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    entry["input_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return entry


class TestSchemaConstants:
    """The schema marker and lifecycle vocabulary are module constants."""

    def test_schema_marker_is_cairn_wiki_manifest_3(self):
        assert MANIFEST_SCHEMA == "cairn-wiki-manifest-3"

    def test_lifecycle_vocabulary_is_derived_not_stored(self):
        """The lifecycle lives in cairn.wiki.lifecycle as DERIVED_STATES —
        the manifest has no lifecycle vocabulary of its own."""
        from cairn.wiki.lifecycle import DERIVED_STATES as DERIVED

        assert DERIVED == (
            "planned",
            "queued",
            "in-progress",
            "promoted",
            "failed",
            "dropped",
        )
        import cairn.wiki.manifest as manifest_module

        assert not hasattr(manifest_module, "PAGE_STATES")


class TestManifestLocation:
    """D-006: the manifest lives at <knowledge>/_wiki/manifest.json."""

    def test_manifest_is_written_to_knowledge_root_wiki_manifest_json(self, bundle):
        knowledge_root = bundle.root
        assert save_manifest(knowledge_root, _empty_doc()) is True
        manifest_file = knowledge_root / "_wiki" / "manifest.json"
        assert manifest_file.is_file()
        on_disk = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert on_disk["schema"] == MANIFEST_SCHEMA

    def test_manifest_is_never_listed_as_a_concept(self, bundle, plan):
        _promote(bundle, REPO, plan[0]["page_id"])
        save_manifest(bundle.root, _manifest(plan))
        ids = bundle.list_concepts()
        # Control: the promoted page IS listed, so the exclusion below is
        # about the manifest, not a broken listing.
        assert f"wiki/pages/{REPO}/{plan[0]['page_id']}" in ids
        assert "_wiki/manifest" not in ids
        assert not any(cid.startswith("_wiki/") for cid in ids)

    def test_wiki_dir_holds_only_the_manifest_no_tmp_debris(self, bundle):
        save_manifest(bundle.root, _empty_doc())
        names = sorted(p.name for p in (bundle.root / "_wiki").iterdir())
        assert names == ["manifest.json"]


class TestLoadManifest:
    """load_manifest is tolerant: missing file -> empty pages, not an error."""

    def test_missing_file_returns_empty_pages_not_an_error(self, bundle):
        assert load_manifest(bundle.root) == {"schema": MANIFEST_SCHEMA, "pages": {}}

    def test_accepts_bundle_or_knowledge_root(self, bundle):
        save_manifest(bundle.root, _empty_doc())
        from_path = load_manifest(bundle.root)
        assert load_manifest(bundle) == from_path
        assert load_manifest(str(bundle.root)) == from_path

    def test_round_trip_preserves_schema_and_rows(self, bundle, plan):
        doc = _manifest(plan)
        assert save_manifest(bundle.root, doc) is True
        loaded = load_manifest(bundle)
        assert loaded["schema"] == MANIFEST_SCHEMA
        assert loaded["pages"] == doc["pages"]
        assert set(loaded["pages"]) == {
            f"{REPO}/{page['page_id']}" for page in plan
        }

    def test_document_without_pages_key_loads_with_empty_pages(self, bundle):
        save_manifest(bundle.root, {"schema": MANIFEST_SCHEMA})
        assert load_manifest(bundle.root)["pages"] == {}

    def test_unknown_top_level_sections_are_preserved(self, bundle):
        doc = {"schema": MANIFEST_SCHEMA, "pages": {}, "generated_at": "t"}
        save_manifest(bundle.root, doc)
        assert load_manifest(bundle.root)["generated_at"] == "t"

    def test_malformed_json_raises_value_error(self, bundle):
        manifest_file = bundle.root / "_wiki" / "manifest.json"
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_file.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError):
            load_manifest(bundle.root)


class TestSaveManifest:
    """save_manifest copies the atomic set_config_values pattern."""

    def test_creates_wiki_dir_when_absent(self, bundle):
        assert not (bundle.root / "_wiki").exists()
        assert save_manifest(bundle.root, _empty_doc()) is True
        assert (bundle.root / "_wiki" / "manifest.json").is_file()

    def test_write_is_byte_stable_json(self, bundle, plan):
        doc = _manifest(plan)
        save_manifest(bundle.root, doc)
        first = (bundle.root / "_wiki" / "manifest.json").read_bytes()
        save_manifest(bundle.root, doc)
        second = (bundle.root / "_wiki" / "manifest.json").read_bytes()
        assert first == second
        text = second.decode("utf-8")
        assert text.endswith("\n")
        assert json.loads(text) == doc

    def test_os_error_returns_false_and_leaves_the_target_intact(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("keep me", encoding="utf-8")
        assert save_manifest(blocker, _empty_doc()) is False
        assert blocker.read_text(encoding="utf-8") == "keep me"


class TestPageRows:
    """Rows are keyed by {repo}/{page_id} and carry the plan entry +
    tracking fields."""

    def test_row_carries_plan_entry_plus_tracking_fields(self, plan):
        entry = plan[0]
        row = _row(entry, task_id="task-1", attempts=2)
        assert set(row) == set(entry) | {"task_id", "queue_attempts"}
        assert row["input_hash"] == entry["input_hash"]
        assert row["task_id"] == "task-1"
        assert row["queue_attempts"] == 2
        assert "state" not in row
        assert "commit_sha" not in row

    def test_rows_survive_the_json_round_trip_losslessly(self, bundle, plan):
        rows = {
            f"{REPO}/{page['page_id']}": _row(
                page,
                task_id=f"task-{page['page_id']}",
                attempts=i,
            )
            for i, page in enumerate(plan)
        }
        save_manifest(bundle.root, {"schema": MANIFEST_SCHEMA, "pages": rows})
        loaded = load_manifest(bundle)["pages"]
        assert loaded == rows
        for page_id, row in loaded.items():
            assert isinstance(row["queue_attempts"], int)
            assert row["seeds"] == rows[page_id]["seeds"]

    def test_schema2_rows_normalize_on_load(self, bundle, plan):
        """Retired schema-2 fields (state/commit_sha/attempts) are stripped
        or renamed in memory — the plan kind no longer carries them."""
        rows = {
            f"{REPO}/{page['page_id']}": _row(
                page,
                task_id=f"task-{page['page_id']}",
                state="promoted",
                attempts=3,
                legacy=True,
            )
            for page in plan
        }
        for row in rows.values():
            row["commit_sha"] = "abc1234"
        save_manifest(bundle.root, {"schema": "cairn-wiki-manifest-2", "pages": rows})
        loaded = load_manifest(bundle)
        assert loaded["schema"] == MANIFEST_SCHEMA
        for row in loaded["pages"].values():
            assert "state" not in row
            assert "commit_sha" not in row
            assert "attempts" not in row
            assert row["queue_attempts"] == 3


class TestSchema1Migration:
    """A schema-1 document (keyed by page id alone) upgrades in memory on
    load: repo from the row task's facts, else the promoted concept path,
    else the row is dropped with a warning. Loads never write back."""

    def _write_v1(self, bundle, pages):
        manifest_file = bundle.root / "_wiki" / "manifest.json"
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        doc = {"schema": "cairn-wiki-manifest-1", "pages": pages}
        manifest_file.write_text(
            json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return doc

    def test_row_repo_recovered_from_task_facts(self, bundle, plan):
        from cairn.llm.tasks import create_task

        entry = plan[0]
        task = create_task(
            bundle, "wiki-page", entry["page_id"], facts={"repo": REPO}
        )
        old = _row(entry, task_id=task.id)
        self._write_v1(bundle, {entry["page_id"]: old})

        loaded = load_manifest(bundle)

        assert loaded["schema"] == MANIFEST_SCHEMA
        assert loaded["pages"] == {
            f"{REPO}/{entry['page_id']}": _row(entry, task_id=task.id)
        }

    def test_row_repo_recovered_from_promoted_concept_path(self, bundle, plan):
        entry = plan[0]
        _promote(bundle, REPO, entry["page_id"])
        old = _row(entry, task_id="gone-task")
        self._write_v1(bundle, {entry["page_id"]: old})

        loaded = load_manifest(bundle)

        assert loaded["pages"] == {f"{REPO}/{entry['page_id']}": old}

    def test_bare_path_load_recovers_repo_from_concept_paths(
        self, bundle, plan
    ):
        entry = plan[0]
        _promote(bundle, REPO, entry["page_id"])
        old = _row(entry, task_id="gone-task")
        self._write_v1(bundle, {entry["page_id"]: old})

        loaded = load_manifest(str(bundle.root))

        assert loaded["pages"] == {f"{REPO}/{entry['page_id']}": old}

    def test_unmigratable_row_dropped_with_warning(self, bundle, plan):
        entry = plan[0]
        old = _row(entry, task_id="gone-task")
        self._write_v1(bundle, {entry["page_id"]: old})

        with pytest.warns(UserWarning, match="no recoverable repo"):
            loaded = load_manifest(bundle)

        assert loaded["schema"] == MANIFEST_SCHEMA
        assert loaded["pages"] == {}

    def test_load_never_writes_the_migration_back(self, bundle, plan):
        entry = plan[0]
        old = _row(entry, task_id="gone-task")
        self._write_v1(bundle, {entry["page_id"]: old})
        raw = (bundle.root / "_wiki" / "manifest.json").read_bytes()

        with pytest.warns(UserWarning):
            load_manifest(bundle)

        assert (bundle.root / "_wiki" / "manifest.json").read_bytes() == raw

    def test_migrated_promoted_page_still_skips_on_generate(
        self, fresh_db, bundle, plan
    ):
        """The live-store scenario: a promoted page recorded under schema-1
        keeps its skip decision through the re-key."""
        from cairn.llm.tasks import list_tasks
        from cairn.wiki.pipeline import run_wiki_generate

        entry = plan[0]
        _promote(bundle, REPO, entry["page_id"])
        self._write_v1(
            bundle,
            {entry["page_id"]: _row(entry, state="queued", attempts=1, legacy=True)},
        )

        run_wiki_generate(fresh_db, bundle, REPO)

        queued_pages = {t.resource for t in list_tasks(bundle, kind="wiki-page")}
        assert entry["page_id"] not in queued_pages
        assert f"{REPO}/{entry['page_id']}" in load_manifest(bundle)["pages"]


class TestShouldSkip:
    """Skip rule: hash unchanged AND promoted concept readable (D-006)."""

    def test_unchanged_hash_with_promoted_concept_skips(self, bundle, plan):
        entry = plan[0]
        _promote(bundle, REPO, entry["page_id"])
        # No stored state exists: promotion is derived from the concept.
        assert should_skip(_row(entry), entry, bundle, REPO) is True

    def test_unchanged_hash_without_promoted_concept_does_not_skip(
        self, bundle, plan
    ):
        entry = plan[0]
        assert should_skip(_row(entry), entry, bundle, REPO) is False

    def test_changed_hash_with_promoted_concept_does_not_skip(self, bundle, plan):
        entry = plan[0]
        _promote(bundle, REPO, entry["page_id"])
        assert should_skip(_row(entry), _changed_entry(entry), bundle, REPO) is False

    def test_changed_hash_without_concept_does_not_skip(self, bundle, plan):
        entry = plan[0]
        assert should_skip(_row(entry), _changed_entry(entry), bundle, REPO) is False

    def test_missing_concept_file_does_not_raise(self, bundle, plan):
        # read_concept raises FileNotFoundError on a missing concept; the
        # helper must treat unreadable as not promoted.
        entry = plan[0]
        assert should_skip(_row(entry), entry, bundle, REPO) is False

    def test_a_changed_module_input_requeues_exactly_that_page(self, bundle, plan):
        first, second = plan[0], plan[1]
        for entry in (first, second):
            _promote(bundle, REPO, entry["page_id"])
        assert should_skip(_row(first), _changed_entry(first), bundle, REPO) is False
        assert should_skip(_row(second), second, bundle, REPO) is True

    def test_concept_under_another_repo_does_not_satisfy_the_skip(
        self, bundle, plan
    ):
        entry = plan[0]
        _promote(bundle, "other-repo", entry["page_id"])
        assert should_skip(_row(entry), entry, bundle, REPO) is False


class TestLiveTaskSkip:
    """Pipeline skip rule: an unchanged page with a live task is not
    re-queued, so no page carries two pending tasks for one attempt
    (a pending revise counts -- the chain, not the recorded task id,
    decides)."""

    def _run(self, fresh_db, bundle):
        from cairn.wiki.pipeline import run_wiki_generate

        return run_wiki_generate(fresh_db, bundle, REPO)

    def test_second_run_does_not_duplicate_unpromoted_pending_tasks(
        self, fresh_db, bundle, plan
    ):
        from cairn.llm.tasks import list_tasks

        first = self._run(fresh_db, bundle)
        assert len(first["queued_task_ids"]) == len(plan)
        second = self._run(fresh_db, bundle)
        assert second["queued_task_ids"] == []
        assert len(list_tasks(bundle, kind="wiki-page")) == len(plan)

    def test_changed_input_requeues_despite_a_live_task(
        self, fresh_db, bundle, plan
    ):
        from cairn.llm.tasks import list_tasks

        self._run(fresh_db, bundle)
        fresh_db.execute(
            "INSERT INTO files (id, repo_id, path, language) "
            "VALUES ('f3', 'r', 'm_a/extra.py', 'python')"
        )
        fresh_db.commit()
        second = self._run(fresh_db, bundle)
        by_id = {t.id: t for t in list_tasks(bundle)}
        queued_pages = {by_id[tid].resource for tid in second["queued_task_ids"]}
        assert f"{REPO}/m-a" in queued_pages
        assert f"{REPO}/m-b" not in queued_pages

    def test_force_requeues_all_despite_live_tasks(self, fresh_db, bundle, plan):
        self._run(fresh_db, bundle)
        second = self._run_force(fresh_db, bundle)
        assert len(second["queued_task_ids"]) == len(plan)

    def _run_force(self, fresh_db, bundle):
        from cairn.wiki.pipeline import run_wiki_generate

        return run_wiki_generate(fresh_db, bundle, REPO, force=True)

    def test_pending_revise_in_the_chain_blocks_duplicate_queueing(
        self, fresh_db, bundle, plan
    ):
        from cairn.llm.tasks import complete_task, create_task, list_tasks

        first = self._run(fresh_db, bundle)
        overview_task_id = first["queued_task_ids"][0]
        complete_task(bundle, overview_task_id, "plain completion")
        create_task(
            bundle, "wiki-page-revise", plan[0]["page_id"], facts={"repo": REPO}
        )
        second = self._run(fresh_db, bundle)
        assert second["queued_task_ids"] == []
        assert len(list_tasks(bundle)) == len(plan) + 1


class TestRowCarriesNoContentProvenance:
    """The two-kind anti-collapse contract, pinned from the negative side:
    queueing never writes content provenance (commit_sha) or a lifecycle
    verdict into the plan — the concept alone owns both."""

    def test_queued_row_never_carries_commit_sha_or_state(
        self, fresh_db, bundle, plan
    ):
        from cairn.wiki.pipeline import run_wiki_generate

        run_wiki_generate(fresh_db, bundle, REPO)
        for row in load_manifest(bundle)["pages"].values():
            assert "commit_sha" not in row
            assert "state" not in row
