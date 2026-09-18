"""Loader contract tests for the SWE-bench task source.

Pins the public contract of ``src/cairn/bench/swe_bench.py``:

1. Pin-manifest validation -- schema tag, revision sha hex, ordered unique
   instance ids; validation runs before any fetch.
2. Dataset fetch resolution -- ``load_dataset("princeton-nlp/SWE-bench_Lite",
   split="test", revision=<manifest sha>)`` through a lazy ``datasets``
   import that raises an actionable ImportError without the ``bench`` extra
   (D-004).
3. Subset slicing -- only the manifest's instance ids, never the full split.
4. Deterministic ordering -- output order is the manifest's order, a pure
   function of (manifest, fetched rows).
5. Offline behavior -- the assembly path needs no ``datasets`` import when
   the fetch is injected; two loads produce identical task lists.

Hermetic (C-04): manifests live in tmp_path; the ``datasets`` dependency is
stubbed or hidden in sys.modules -- never a live Hub call; no subprocess
patching; no workspaces outside tmp_path.
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from cairn.bench.swe_bench import (
    PIN_MANIFEST_SCHEMA,
    fetch_dataset_rows,
    load_pin_manifest,
    load_tasks,
    validate_pin_manifest,
)

DATASET_NAME = "princeton-nlp/SWE-bench_Lite"
REVISION = "a" * 40
TASK_FIELDS = {
    "instance_id",
    "repo",
    "base_commit",
    "problem_statement",
    "environment_setup_commit",
}
GOLD_PATCH_FIELDS = {"patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS"}


def _valid_manifest() -> dict:
    return {
        "schema": PIN_MANIFEST_SCHEMA,
        "dataset_revision_sha": REVISION,
        "subset": ["django__django-16379", "sympy__sympy-24102"],
    }


def _dataset_rows() -> list[dict]:
    """Fake Lite rows: the five contract fields plus gold-patch/noise fields
    the loader must keep out of its task output."""

    def _row(instance_id: str, base_commit: str) -> dict:
        return {
            "instance_id": instance_id,
            "repo": "django/django",
            "base_commit": base_commit,
            "environment_setup_commit": "e" * 40,
            "problem_statement": f"Signal.format raises in {instance_id}.",
            "patch": "diff --git a/x.py b/x.py",
            "test_patch": "diff --git a/t.py b/t.py",
            "FAIL_TO_PASS": '["test_signal_format"]',
            "PASS_TO_PASS": '["test_signal_repr"]',
            "hints_text": "",
            "created_at": "2024-01-01T00:00:00",
            "version": "4.2",
        }

    return [
        _row("astropy__astropy-14995", "b" * 40),
        _row("django__django-16379", "c" * 40),
        _row("sympy__sympy-24102", "d" * 40),
    ]


def _stub_fetch(rows: list[dict]):
    """Return (fetch, revisions_seen): a fetch double standing in for the
    (cached) Hub load -- the only network touchpoint in the contract."""
    revisions: list[str] = []

    def fetch(revision: str) -> list[dict]:
        revisions.append(revision)
        return [dict(row) for row in rows]

    return fetch, revisions


def _install_datasets_stub(monkeypatch, rows: list[dict] | None = None) -> dict:
    """Swap a fake ``datasets`` module into sys.modules and record the
    load_dataset call, so the real dependency is never imported."""
    calls: dict = {}

    def fake_load_dataset(name, **kwargs):
        calls["name"] = name
        calls.update(kwargs)
        return [dict(row) for row in (rows if rows is not None else _dataset_rows())]

    stub = types.ModuleType("datasets")
    stub.load_dataset = fake_load_dataset
    monkeypatch.setitem(sys.modules, "datasets", stub)
    return calls


# --- pin-manifest validation ------------------------------------------------


class TestValidatePinManifest:
    def test_valid_manifest_has_no_errors(self):
        assert validate_pin_manifest(_valid_manifest()) == []

    @pytest.mark.parametrize("key", ["schema", "dataset_revision_sha", "subset"])
    def test_missing_required_key_rejected(self, key):
        manifest = _valid_manifest()
        del manifest[key]
        assert any(key in error for error in validate_pin_manifest(manifest))

    def test_wrong_schema_tag_rejected(self):
        manifest = _valid_manifest()
        manifest["schema"] = "something-else"
        assert any("schema" in error for error in validate_pin_manifest(manifest))

    def test_non_hex_revision_rejected(self):
        manifest = _valid_manifest()
        manifest["dataset_revision_sha"] = "not-hex"
        assert any(
            "dataset_revision_sha" in error
            for error in validate_pin_manifest(manifest)
        )

    def test_short_revision_rejected(self):
        manifest = _valid_manifest()
        manifest["dataset_revision_sha"] = "a" * 8
        assert any(
            "dataset_revision_sha" in error
            for error in validate_pin_manifest(manifest)
        )

    def test_non_string_revision_rejected(self):
        manifest = _valid_manifest()
        manifest["dataset_revision_sha"] = 40
        assert any(
            "dataset_revision_sha" in error
            for error in validate_pin_manifest(manifest)
        )

    def test_empty_subset_rejected(self):
        manifest = _valid_manifest()
        manifest["subset"] = []
        assert any("subset" in error for error in validate_pin_manifest(manifest))

    def test_subset_must_be_a_list(self):
        manifest = _valid_manifest()
        manifest["subset"] = {"django__django-16379": True}
        assert any("subset" in error for error in validate_pin_manifest(manifest))

    def test_non_string_instance_id_rejected(self):
        manifest = _valid_manifest()
        manifest["subset"] = [16379]
        assert any("subset" in error for error in validate_pin_manifest(manifest))

    def test_duplicate_instance_ids_rejected(self):
        manifest = _valid_manifest()
        manifest["subset"] = ["django__django-16379", "django__django-16379"]
        assert any(
            "django__django-16379" in error
            for error in validate_pin_manifest(manifest)
        )

    def test_unknown_keys_ignored(self):
        """Forward compatibility: extra keys (e.g. ``reported``) must not
        invalidate a manifest the contract otherwise accepts."""
        manifest = _valid_manifest()
        manifest["reported"] = True
        assert validate_pin_manifest(manifest) == []

    def test_non_dict_rejected(self):
        assert validate_pin_manifest(["not", "a", "manifest"]) != []


# --- pin-manifest load ------------------------------------------------------


class TestLoadPinManifest:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "swe-bench-subset.json"
        path.write_text(json.dumps(_valid_manifest()), encoding="utf-8")
        assert load_pin_manifest(path) == _valid_manifest()

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_pin_manifest(tmp_path / "absent.json")

    def test_invalid_json_raises_value_error(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="not valid JSON"):
            load_pin_manifest(path)

    def test_invalid_content_raises_value_error(self, tmp_path):
        manifest = _valid_manifest()
        manifest["dataset_revision_sha"] = "zzz"
        path = tmp_path / "invalid.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(ValueError, match="dataset_revision_sha"):
            load_pin_manifest(path)


# --- dataset fetch resolution -----------------------------------------------


class TestFetchDatasetRows:
    def test_loads_lite_test_at_the_pinned_revision(self, monkeypatch):
        calls = _install_datasets_stub(monkeypatch)
        rows = fetch_dataset_rows(REVISION)
        assert calls["name"] == DATASET_NAME
        assert calls["split"] == "test"
        assert calls["revision"] == REVISION
        assert len(rows) == 3

    def test_missing_datasets_raises_actionable_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "datasets", None)
        with pytest.raises(ImportError) as excinfo:
            fetch_dataset_rows(REVISION)
        message = str(excinfo.value)
        assert "datasets" in message
        assert "bench" in message


# --- task-list assembly -----------------------------------------------------


class TestLoadTasks:
    def test_projects_rows_to_the_five_field_contract(self):
        fetch, _ = _stub_fetch(_dataset_rows())
        tasks = load_tasks(_valid_manifest(), fetch=fetch)
        assert len(tasks) == 2
        for task in tasks:
            assert set(task) == TASK_FIELDS
            assert not (set(task) & GOLD_PATCH_FIELDS)

    def test_field_values_are_carried_through(self):
        fetch, _ = _stub_fetch(_dataset_rows())
        tasks = load_tasks(_valid_manifest(), fetch=fetch)
        by_id = {task["instance_id"]: task for task in tasks}
        django = by_id["django__django-16379"]
        assert django["repo"] == "django/django"
        assert django["base_commit"] == "c" * 40
        assert django["problem_statement"].startswith("Signal.format raises")

    def test_slices_to_the_manifest_subset(self):
        fetch, _ = _stub_fetch(_dataset_rows())
        manifest = _valid_manifest()
        manifest["subset"] = ["django__django-16379"]
        tasks = load_tasks(manifest, fetch=fetch)
        assert [task["instance_id"] for task in tasks] == ["django__django-16379"]

    def test_order_follows_the_manifest_not_the_dataset(self):
        fetch, _ = _stub_fetch(list(reversed(_dataset_rows())))
        tasks = load_tasks(_valid_manifest(), fetch=fetch)
        assert [task["instance_id"] for task in tasks] == _valid_manifest()["subset"]

    def test_two_loads_are_identical(self):
        fetch, revisions = _stub_fetch(_dataset_rows())
        manifest = _valid_manifest()
        assert load_tasks(manifest, fetch=fetch) == load_tasks(manifest, fetch=fetch)
        assert revisions == [REVISION, REVISION]

    def test_fetch_receives_the_manifest_revision(self):
        fetch, revisions = _stub_fetch(_dataset_rows())
        load_tasks(_valid_manifest(), fetch=fetch)
        assert revisions == [REVISION]

    def test_missing_instance_id_fails(self):
        manifest = _valid_manifest()
        manifest["subset"] = ["astropy__astropy-404"]
        fetch, _ = _stub_fetch(_dataset_rows())
        with pytest.raises(ValueError, match="astropy__astropy-404"):
            load_tasks(manifest, fetch=fetch)

    def test_invalid_manifest_fails_before_any_fetch(self):
        manifest = _valid_manifest()
        manifest["dataset_revision_sha"] = "not-hex"
        fetch, revisions = _stub_fetch(_dataset_rows())
        with pytest.raises(ValueError):
            load_tasks(manifest, fetch=fetch)
        assert revisions == []

    def test_default_fetch_resolves_the_dataset(self, monkeypatch):
        calls = _install_datasets_stub(monkeypatch)
        tasks = load_tasks(_valid_manifest())
        assert calls["revision"] == REVISION
        assert [task["instance_id"] for task in tasks] == _valid_manifest()["subset"]


# --- offline rerun path -----------------------------------------------------


class TestOfflineRerun:
    def test_assembly_needs_no_datasets_when_fetch_is_injected(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "datasets", None)
        fetch, _ = _stub_fetch(_dataset_rows())
        tasks = load_tasks(_valid_manifest(), fetch=fetch)
        assert [task["instance_id"] for task in tasks] == _valid_manifest()["subset"]

    def test_offline_env_rerun_yields_identical_task_lists(self, monkeypatch):
        fetch, _ = _stub_fetch(_dataset_rows())
        online = load_tasks(_valid_manifest(), fetch=fetch)
        monkeypatch.setenv("HF_HUB_OFFLINE", "1")
        assert load_tasks(_valid_manifest(), fetch=fetch) == online
