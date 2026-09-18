"""CLI integration tests for the swe-bench bench arm.

Pins the end-to-end ``cairn bench --suite swe-bench`` chain over a local
fixture origin: pin manifest -> loader -> workspace checkout -> suite run ->
artifact stamp. The dataset fetch is stubbed at the loader seam
(``fetch_dataset_rows``); everything downstream -- the real checkout into the
content-addressed cache, both suite arms over the checked-out tree, the
stamp, and ``--save`` -- is the shipped code path.

Pinned here:

1. A ``--smoke`` run exits 0 and reports per-task rows for both arms plus
   cross-task medians (FR-001).
2. The dataset stamp records the pin identity (revision sha, manifest
   digest, instance count, slice) and the persisted payload carries no
   timestamp and no wall-clock figures (D-013).
3. Two saves are byte-identical, the second run fully offline behind an
   unreachable proxy (FR-002; the same contract TC-002 checks on the real
   dataset).
4. Usage errors fail promptly with exit 1: missing manifest, invalid
   manifest, empty slice selection, ``--smoke`` + ``--slice`` together.

Hermetic (C-04): the fixture origin is a local bare git repo reached
through a ``GIT_CONFIG_GLOBAL`` ``insteadOf`` rewrite, so the real clone +
checkout run without any network; ``CAIRN_HOME`` points into ``tmp_path``
so the workspace cache never touches ``~/.cairn``; no subprocess patching.

Marked ``infra`` like the other swe-bench modules: real git subprocesses
and real graph builds; the per-PR ``-m core`` leg is covered by the
hermetic smoke test instead.
"""
from __future__ import annotations

import hashlib
import json
import statistics
import subprocess
import types

import pytest

from cairn.bench.swe_bench import DATASET_NAME, PIN_MANIFEST_SCHEMA

pytestmark = pytest.mark.infra

REPO_SLUG = "cairn-fixture/fixture-repo"
INSTANCE_IDS = ["fixture__repo-1", "fixture__repo-2"]

DISPATCH_PY = '''\
class SignalRouter:
    def __init__(self):
        self.routes = {}

    def route(self, name):
        return self.routes.get(name)


def dispatch_event(router, name):
    return router.route(name)
'''

WORKER_PY = '''\
from dispatch import SignalRouter, dispatch_event


def run_worker():
    return dispatch_event(SignalRouter(), "worker")
'''


def _git(*args: str, cwd=None) -> None:
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


def _make_origin(tmp_path):
    """Fixture origin: a tiny bare repo plus the insteadOf rewrite that
    maps ``https://github.com/<REPO_SLUG>`` onto it."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "dispatch.py").write_text(DISPATCH_PY, encoding="utf-8")
    (work / "worker.py").write_text(WORKER_PY, encoding="utf-8")
    _git("init", "-q", "-b", "main", str(work))
    _git("-C", str(work), "add", ".")
    _git("-C", str(work), "-c", "user.email=fixture@example.com",
         "-c", "user.name=fixture", "commit", "-qm", "fixture tree")
    sha = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    origin = tmp_path / "origin" / "fixture-repo.git"
    origin.parent.mkdir(parents=True)
    _git("init", "-q", "--bare", "-b", "main", str(origin))
    _git("-C", str(work), "push", "-q", str(origin), "main")
    _git("-C", str(origin), "config", "uploadpack.allowFilter", "true")

    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(
        f'[url "file://{tmp_path}/origin/fixture-repo"]\n'
        f"\tinsteadOf = https://github.com/{REPO_SLUG}\n",
        encoding="utf-8",
    )
    return origin, sha, gitconfig


def _pin_manifest(tmp_path, sha) -> "types.SimpleNamespace":
    path = tmp_path / "swe-bench-subset.json"
    manifest = {
        "schema": PIN_MANIFEST_SCHEMA,
        "dataset_revision_sha": sha,
        "subset": list(INSTANCE_IDS),
        "reported": True,
    }
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _dataset_rows(sha):
    statements = {
        "fixture__repo-1": "dispatch_event drops frames when the router rebuilds.",
        "fixture__repo-2": "SignalRouter loses routes after a rebuild.",
    }
    return [
        {
            "instance_id": instance_id,
            "repo": REPO_SLUG,
            "base_commit": sha,
            "environment_setup_commit": sha,
            "problem_statement": statements[instance_id],
        }
        for instance_id in INSTANCE_IDS
    ]


@pytest.fixture
def swe_cli(tmp_path, monkeypatch):
    """Fixture origin + pin manifest + hermetic env, with the fetch stubbed
    at the loader seam (the only network touchpoint in the real path)."""
    origin, sha, gitconfig = _make_origin(tmp_path)
    manifest = _pin_manifest(tmp_path, sha)
    home = tmp_path / "home"

    def fake_fetch(revision):
        assert revision == sha
        return [dict(row) for row in _dataset_rows(sha)]

    monkeypatch.setattr("cairn.bench.swe_bench.fetch_dataset_rows", fake_fetch)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("CAIRN_HOME", str(home))
    return types.SimpleNamespace(
        manifest=manifest,
        sha=sha,
        cache=home / "cache" / "swe-bench",
        workspace=home / "cache" / "swe-bench"
        / f"{REPO_SLUG.replace('/', '__')}-{sha}",
    )


def _invoke(swe_cli, *extra):
    from click.testing import CliRunner
    from cairn.cli import main

    return CliRunner().invoke(main, [
        "bench", "--suite", "swe-bench",
        "--manifest", str(swe_cli.manifest),
        "--runs", "1",
        *extra,
    ])


def _run_json(swe_cli, *extra):
    result = _invoke(swe_cli, "--json", *extra)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _run_save(swe_cli, save_path, *extra):
    """``--save`` runs print the save confirmation after the JSON payload,
    so only the exit code and the saved file are read here."""
    result = _invoke(swe_cli, "--json", "--save", str(save_path), *extra)
    assert result.exit_code == 0, result.output


# --- end-to-end run (manifest -> loader -> checkout -> suite) ---------------


class TestSweBenchCliRun:
    def test_smoke_reports_both_arms_and_medians(self, swe_cli):
        payload = _run_json(swe_cli, "--smoke")
        assert [row["instance_id"] for row in payload["tasks"]] == INSTANCE_IDS
        for row in payload["tasks"]:
            for arm in ("cairn", "control"):
                assert row[arm]["tool_calls"] >= 1
                assert row[arm]["est_tokens"] >= 1
        assert set(payload["medians"]) == {"cairn", "control"}
        for arm in ("cairn", "control"):
            for field in ("tool_calls", "est_tokens"):
                assert payload["medians"][arm][field] == statistics.median(
                    row[arm][field] for row in payload["tasks"]
                )
        assert payload["runs"] == 1
        assert payload["chars_per_token"] == 4

    def test_real_checkout_lands_in_content_addressed_cache(self, swe_cli):
        _run_json(swe_cli, "--smoke")
        assert (swe_cli.workspace / ".git").is_dir()
        assert (swe_cli.workspace / "dispatch.py").is_file()
        assert [p.name for p in swe_cli.cache.iterdir()] == [
            swe_cli.workspace.name
        ]

    def test_stamp_records_pin_identity_and_strips_wall_clock(self, swe_cli):
        payload = _run_json(swe_cli, "--slice", "0:2")
        digest = hashlib.sha256(swe_cli.manifest.read_bytes()).hexdigest()
        assert payload["dataset"] == {
            "name": DATASET_NAME,
            "schema": PIN_MANIFEST_SCHEMA,
            "revision_sha": swe_cli.sha,
            "manifest_digest": digest,
            "instance_count": len(INSTANCE_IDS),
            "slice": "0:2",
        }
        assert set(payload) >= {"cairn_version", "machine_profile"}
        assert "timestamp" not in payload
        for row in payload["tasks"]:
            for arm in ("cairn", "control"):
                assert "wall_ms" not in row[arm]
            assert "time_ratio" not in row["reduction"]
        for arm in payload["medians"].values():
            assert "wall_ms" not in arm

    def test_rerun_saves_are_byte_identical_offline(self, swe_cli, monkeypatch):
        """The plan checkpoint's rerun contract, end to end through the CLI:
        two ``--save`` runs produce identical bytes, the second with every
        network route forced through an unreachable proxy."""
        save_a = swe_cli.manifest.parent / "run-a.json"
        save_b = swe_cli.manifest.parent / "run-b.json"
        _run_save(swe_cli, save_a, "--smoke")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
        monkeypatch.setenv("HF_HUB_OFFLINE", "1")
        _run_save(swe_cli, save_b, "--smoke")
        assert save_a.read_bytes() == save_b.read_bytes()

    def test_human_output_renders_median_row(self, swe_cli):
        result = _invoke(swe_cli, "--smoke")
        assert result.exit_code == 0, result.output
        assert "SWE-bench benchmark" in result.output
        assert "MEDIAN" in result.output
        for header in ("cairn calls", "grep calls", "tok saved"):
            assert header in result.output


# --- fail-promptly usage errors (exit 1, before any suite work) -------------


class TestSweBenchCliUsageErrors:
    def test_missing_manifest_exits_1(self, tmp_path):
        from click.testing import CliRunner
        from cairn.cli import main

        absent = tmp_path / "absent.json"
        result = CliRunner().invoke(main, [
            "bench", "--suite", "swe-bench", "--manifest", str(absent),
        ])
        assert result.exit_code == 1
        assert "No such file or directory" in result.output
        assert str(absent) in result.output.replace("\n", "")

    def test_invalid_manifest_exits_1(self, tmp_path, monkeypatch):
        from click.testing import CliRunner
        from cairn.cli import main

        manifest = tmp_path / "broken.json"
        manifest.write_text(json.dumps({
            "schema": PIN_MANIFEST_SCHEMA,
            "dataset_revision_sha": "not-hex",
            "subset": INSTANCE_IDS,
        }), encoding="utf-8")
        result = CliRunner().invoke(main, [
            "bench", "--suite", "swe-bench", "--manifest", str(manifest),
        ])
        assert result.exit_code == 1
        assert "dataset_revision_sha" in result.output

    def test_empty_slice_selection_exits_1(self, swe_cli):
        result = _invoke(swe_cli, "--slice", "9:11")
        assert result.exit_code == 1
        assert "selects 0 of 2" in result.output

    def test_smoke_and_slice_together_exits_1(self, swe_cli):
        result = _invoke(swe_cli, "--smoke", "--slice", "0:1")
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output
