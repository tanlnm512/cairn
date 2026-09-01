"""Tests for bench-artifact stamping (FR-004, T013; decisions D-005/D-006).

Three layers:
1. Helper unit tests -- build_artifact_stamp / runner_class / machine_profile:
   fields present, env-driven runner classification, and the never-crash
   degradation contract (missing/unreadable manifest -> reason, not raise).
2. The repo's real manifest -- the stamp reads the T1 identity (tree-hash at
   the default size) via the same auto-location the CLI uses.
3. CLI payload tests -- `cairn bench --json` (perf + scaling) carries the
   stamp keys beside the timestamp (D-006: CLI layer, to_dict untouched).
"""
from __future__ import annotations

import json
import re

from cairn import __version__
from cairn.bench.datasource import (
    MANIFEST_SCHEMA,
    STAMP_IDENTITY_SIZE,
    build_artifact_stamp,
    default_manifest_path,
    machine_profile,
    runner_class,
    save_manifest,
)
import pytest

pytestmark = pytest.mark.infra

_HEX64 = re.compile(r"[0-9a-f]{64}")


def _synthetic_manifest(path, *, dataset_version=None, sizes=(300,), identity_hash=None):
    """Write a schema-valid manifest; identity_hash defaults per size."""
    entries = {}
    for size in sizes:
        entries[str(size)] = {
            "tree_hash": identity_hash or (f"{size:064d}"),
            "counts": {"files": size + 1, "lines": 100 * size, "bytes": 200 * size},
        }
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "version": 1,
        "t1": {
            "generator_git_sha": "a" * 40,
            "seed": 49374,
            "sizes": list(sizes),
            "complexity": "medium",
            "entries": entries,
        },
    }
    if dataset_version is not None:
        manifest["dataset_version"] = dataset_version
    save_manifest(path, manifest)
    return path


# --- helper: full stamp ----------------------------------------------------


class TestBuildArtifactStamp:
    def test_all_fields_present_with_explicit_version(self, tmp_path):
        manifest = _synthetic_manifest(
            tmp_path / "manifest.json", dataset_version="DS-test"
        )
        stamp = build_artifact_stamp(manifest_path=manifest)
        assert set(stamp) == {"dataset", "cairn_version", "machine_profile"}
        assert stamp["cairn_version"] == __version__
        assert stamp["dataset"] == {
            "name": "benchmark-datasource",
            "version": "DS-test",
            "tree_hash": f"{300:064d}",
            "identity_size": STAMP_IDENTITY_SIZE,
        }
        assert set(stamp["machine_profile"]) == {
            "arch", "cpu", "cpu_count", "os", "runner_class",
        }

    def test_version_null_when_manifest_records_none(self, tmp_path):
        """Today's real shape: no dataset_version key in the manifest yet."""
        manifest = _synthetic_manifest(tmp_path / "manifest.json")
        stamp = build_artifact_stamp(manifest_path=manifest)
        dataset = stamp["dataset"]
        assert dataset["version"] is None
        assert "dataset_version" in dataset["reason"]
        assert dataset["tree_hash"] == f"{300:064d}"  # identity still stamped

    def test_manifest_missing_degrades_never_crashes(self, tmp_path):
        stamp = build_artifact_stamp(manifest_path=tmp_path / "absent.json")
        assert stamp["dataset"] == {
            "name": "benchmark-datasource",
            "version": None,
            "reason": "manifest missing",
        }
        # The rest of the stamp is unaffected -- the bench must still run.
        assert stamp["cairn_version"] == __version__
        from cairn.bench.datasource import runner_class as _rc

        # Env-dependent by design (D-005): reference-local locally,
        # ci-github-actions-<runner> under CI -- assert the computed class.
        assert stamp["machine_profile"]["runner_class"] == _rc()

    def test_manifest_invalid_json_degrades(self, tmp_path):
        bad = tmp_path / "manifest.json"
        bad.write_text("{not json", encoding="utf-8")
        stamp = build_artifact_stamp(manifest_path=bad)
        assert stamp["dataset"]["version"] is None
        assert stamp["dataset"]["reason"].startswith("manifest unreadable")

    def test_missing_identity_size_entry_degrades(self, tmp_path):
        manifest = _synthetic_manifest(tmp_path / "m.json", sizes=(100, 500))
        stamp = build_artifact_stamp(manifest_path=manifest)
        dataset = stamp["dataset"]
        assert dataset["tree_hash"] is None
        assert str(STAMP_IDENTITY_SIZE) in dataset["reason"]

    def test_t3_entry_recorded_when_given(self, tmp_path):
        manifest = _synthetic_manifest(tmp_path / "m.json", dataset_version="DS-x")
        pin = {"name": "big-repo", "url": "https://example.com/r", "commit": "b" * 40}
        stamped = build_artifact_stamp(manifest_path=manifest, t3_entry=pin)["dataset"]
        assert stamped["t3_entry"] == pin
        # And omitted entirely when unset (the `t3-entry?` optional field).
        plain = build_artifact_stamp(manifest_path=manifest)["dataset"]
        assert "t3_entry" not in plain

    def test_env_threads_through_to_runner_class(self, tmp_path):
        manifest = _synthetic_manifest(tmp_path / "m.json")
        stamp = build_artifact_stamp(
            manifest_path=manifest, env={"GITHUB_ACTIONS": "true", "RUNNER_NAME": "Runner 7"}
        )
        assert stamp["machine_profile"]["runner_class"] == "ci-runner-7"


# --- helper: runner_class ---------------------------------------------------


class TestRunnerClass:
    def test_default_is_reference_local(self):
        assert runner_class({}) == "reference-local"
        assert runner_class({"RUNNER_NAME": "Runner 7"}) == "reference-local"

    def test_github_actions_makes_ci_class(self):
        assert runner_class({"GITHUB_ACTIONS": "true"}) == "ci-github-actions"

    def test_runner_name_is_slugified(self):
        got = runner_class({"GITHUB_ACTIONS": "1", "RUNNER_NAME": "GitHub Actions 12"})
        assert got == "ci-github-actions-12"  # spaces -> '-', lowercase


# --- helper: machine_profile ------------------------------------------------


class TestMachineProfile:
    def test_fields_are_real_values(self):
        profile = machine_profile({})
        assert profile["arch"]  # platform.machine() is non-empty everywhere we run
        assert profile["cpu"]  # never "" -- falls back to arch
        assert profile["cpu_count"] is None or profile["cpu_count"] >= 1
        assert isinstance(profile["os"], str) and profile["os"]


# --- the repo's real manifest ------------------------------------------------


class TestRepoManifestStamp:
    def test_default_path_resolves_repo_manifest(self):
        path = default_manifest_path()
        assert path is not None and path.name == "manifest.json"

    def test_stamp_carries_t1_identity_hash(self):
        """No-arg stamp (exactly what the CLI computes) reads the real manifest."""
        from cairn.bench.datasource import load_manifest

        stamp = build_artifact_stamp(env={})
        dataset = stamp["dataset"]
        manifest = load_manifest(default_manifest_path())
        expected = manifest["t1"]["entries"][str(STAMP_IDENTITY_SIZE)]["tree_hash"]
        assert dataset["name"] == "benchmark-datasource"
        assert dataset["tree_hash"] == expected
        assert _HEX64.fullmatch(dataset["tree_hash"])
        assert dataset["identity_size"] == STAMP_IDENTITY_SIZE
        # Version is whatever the manifest records -- None until it records one.
        assert dataset["version"] is None or isinstance(dataset["version"], str)


# --- CLI payload layer (D-006) -----------------------------------------------


def _invoke(extra, suite_args):
    from click.testing import CliRunner
    from cairn.cli import main

    runner = CliRunner()
    return runner.invoke(main, ["bench", *suite_args, "--json", *extra])


class TestBenchCliStamp:
    def test_perf_payload_carries_stamp(self, tmp_path, monkeypatch):
        # Pin CAIRN_DB (the perf suite restores it on exit; without a pin the
        # CLI's temp DB path leaks into the shared test process) and pin the
        # runner env so the class is deterministic even when run on CI.
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "stamp.db"))
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        monkeypatch.delenv("RUNNER_NAME", raising=False)
        result = _invoke(
            ["--n-files", "5", "--complexity", "low", "--embed-backend", "hash",
             "--repeats", "1"],
            ["--suite", "perf"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert "timestamp" in payload  # the pre-existing stamp is intact
        assert payload["cairn_version"] == __version__
        profile = payload["machine_profile"]
        assert set(profile) == {"arch", "cpu", "cpu_count", "os", "runner_class"}
        from cairn.bench.datasource import runner_class as _rc

        assert profile["runner_class"] == _rc()  # env-dependent (D-005)
        dataset = payload["dataset"]
        assert dataset["name"] == "benchmark-datasource"
        assert dataset["identity_size"] == STAMP_IDENTITY_SIZE
        assert _HEX64.fullmatch(dataset["tree_hash"])

    def test_scaling_payload_carries_stamp(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        result = _invoke(
            ["--sizes", "5,10", "--complexity", "low", "--embed-backend", "hash"],
            ["--suite", "scaling"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert {"dataset", "cairn_version", "machine_profile", "timestamp"} <= set(payload)
        assert payload["cairn_version"] == __version__
        assert payload["dataset"]["name"] == "benchmark-datasource"

    def test_agent_payload_carries_stamp(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "stamp.db"))
        result = _invoke(
            ["--n-files", "4", "--complexity", "low", "--embed-backend", "hash",
             "--runs", "1"],
            ["--suite", "agent"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["cairn_version"] == __version__
        assert "machine_profile" in payload and "dataset" in payload
