"""Tests for the worked-bundle contract (cairn bench --worked).

Pins src/cairn/bench/worked.py's public surface and the
cairn.worked.manifest.v1 schema: bundle structure, payload identity with
--save, canonical run-dir layout, atomic overwrite, privacy scrub, the
recorded command's re-parsability, and the unwritable-target fail-clean
ordering. Determinism-only asserts -- no wall-clock figures.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.infra

MANIFEST_SCHEMA = "cairn.worked.manifest.v1"
DEFAULT_SEED = 0xC0DE

# Cheapest deterministic perf invocation: tiny low-complexity corpus,
# dep-free hash backend, one repeat.
_PERF_ARGS = [
    "--suite", "perf", "--json", "--n-files", "5",
    "--complexity", "low", "--embed-backend", "hash", "--repeats", "1",
]


def _stamped_payload(dataset_version=None):
    dataset = {"name": "benchmark-datasource"}
    if dataset_version is not None:
        dataset["version"] = dataset_version
    return {
        "ops": [{"name": "op", "median_ms": 1.0}],
        "dataset": dataset,
        "cairn_version": "0.0.0-test",
        "machine_profile": {"arch": "arm64", "runner_class": "reference-local"},
    }


def _write(payload, worked_dir, tmp_path, **kw):
    from cairn.bench.worked import write_worked_bundle

    kw.setdefault("command", ["cairn", "bench", "--suite", "perf"])
    kw.setdefault("repo_root", tmp_path / "repo")
    kw.setdefault("benchmarks_root", tmp_path / "benchmarks")
    return write_worked_bundle(payload, worked_dir, suite="perf", **kw)


def _read_manifest(target):
    return json.loads((target / "manifest.json").read_text(encoding="utf-8"))


# --- bundle structure + payload identity -------------------------------------


class TestBundleWriter:
    def test_bundle_holds_three_files_and_returns_paths(self, tmp_path):
        target = tmp_path / "out"
        written = _write(_stamped_payload(dataset_version="DS-v1.1"), target, tmp_path)
        assert [p.name for p in written] == ["perf.json", "manifest.json", "README.md"]
        assert all(p.is_file() for p in written)
        assert sorted(p.name for p in target.iterdir()) == [
            "README.md", "manifest.json", "perf.json",
        ]

    def test_raw_json_is_the_save_payload_and_payload_unmutated(self, tmp_path):
        payload = _stamped_payload(dataset_version="DS-v1.1")
        before = json.loads(json.dumps(payload))
        written = _write(payload, tmp_path / "out", tmp_path)
        raw = next(p for p in written if p.name == "perf.json")
        assert raw.read_text(encoding="utf-8") == json.dumps(payload, indent=2)
        assert payload == before

    def test_manifest_schema_inputs_stamp_command(self, tmp_path):
        payload = _stamped_payload(dataset_version="DS-v1.1")
        _write(
            payload, tmp_path / "out", tmp_path,
            seed=DEFAULT_SEED, repeats=3, runs=2, embed_backend="hash",
        )
        manifest = _read_manifest(tmp_path / "out")
        assert manifest["schema"] == MANIFEST_SCHEMA
        assert manifest["inputs"] == {
            "suite": "perf",
            "dataset_version": "DS-v1.1",
            "seed": DEFAULT_SEED,
            "repeats": 3,
            "runs": 2,
            "embed_backend": "hash",
        }
        assert manifest["stamp"] == {
            key: payload[key]
            for key in ("dataset", "cairn_version", "machine_profile")
        }
        assert manifest["command"] == ["cairn", "bench", "--suite", "perf"]
        assert manifest["artifacts"] == ["README.md", "manifest.json", "perf.json"]

    def test_manifest_serialization_is_byte_stable(self, tmp_path):
        _write(_stamped_payload(dataset_version="DS-v1.1"), tmp_path / "out", tmp_path)
        text = (tmp_path / "out" / "manifest.json").read_text(encoding="utf-8")
        manifest = json.loads(text)
        assert text == json.dumps(manifest, sort_keys=True, indent=2) + "\n"


# --- run-dir layout (canonical at benchmarks root, exact otherwise) ----------


class TestRunDirLayout:
    def test_canonical_layout_when_dir_is_benchmarks_root(self, tmp_path):
        benchmarks = tmp_path / "benchmarks"
        benchmarks.mkdir()
        written = _write(
            _stamped_payload(dataset_version="DS-v1.1"), benchmarks, tmp_path,
            benchmarks_root=benchmarks,
        )
        assert written[0] == benchmarks / "worked" / "perf-DS-v1.1" / "perf.json"

    def test_exact_dir_otherwise(self, tmp_path):
        target = tmp_path / "anywhere" / "bundle"
        written = _write(
            _stamped_payload(dataset_version="DS-v1.1"), target, tmp_path,
        )
        assert written[0] == target / "perf.json"

    @pytest.mark.parametrize(
        "dataset,expected",
        [
            ({"version": "DS-v1.1"}, "perf-DS-v1.1"),
            ({"revision_sha": "a" * 40}, "perf-" + "a" * 12),
            ({}, "perf-unversioned"),
        ],
    )
    def test_version_segment_degrades_never_crashes(self, tmp_path, dataset, expected):
        benchmarks = tmp_path / "benchmarks"
        benchmarks.mkdir()
        payload = _stamped_payload()
        payload["dataset"] = {"name": "benchmark-datasource", **dataset}
        written = _write(payload, benchmarks, tmp_path, benchmarks_root=benchmarks)
        assert written[0].parent.name == expected


# --- atomic overwrite on re-run ------------------------------------------------


class TestAtomicOverwrite:
    def test_rerun_replaces_never_appends_and_leaves_no_temp(self, tmp_path):
        target = tmp_path / "out"
        _write(_stamped_payload(dataset_version="DS-v1.1"), target, tmp_path)
        first = (target / "perf.json").read_text(encoding="utf-8")
        changed = _stamped_payload(dataset_version="DS-v1.1")
        changed["ops"] = [{"name": "op", "median_ms": 2.0}]
        _write(changed, target, tmp_path)
        second = (target / "perf.json").read_text(encoding="utf-8")
        assert first != second
        assert json.loads(second)["ops"] == changed["ops"]
        assert sorted(p.name for p in target.iterdir()) == [
            "README.md", "manifest.json", "perf.json",
        ]
        assert not list(target.glob("*.tmp-*"))


# --- privacy scrub (NFR-002) -----------------------------------------------


class TestScrub:
    def test_repo_relative_home_tempdir_then_verbatim(self, tmp_path):
        from cairn.bench.worked import scrub_path

        repo = tmp_path / "repo"
        (repo / "benchmarks").mkdir(parents=True)
        assert scrub_path(str(repo / "benchmarks" / "x.json"), repo) == "benchmarks/x.json"
        assert scrub_path(str(tmp_path / "_home" / "secret"), repo) == "$HOME/secret"
        tmpdir = Path(tempfile.gettempdir()).resolve()
        assert scrub_path(str(tmpdir / "cg_bench_x" / "bench.db"), repo).startswith(
            "$TMPDIR/"
        )
        assert scrub_path("benchmarks", repo) == "benchmarks"
        assert scrub_path("/elsewhere/not-templated", repo) == "/elsewhere/not-templated"

    def test_manifest_scrubs_command_paths(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "benchmarks").mkdir(parents=True)
        workspace = tmp_path / "cg_ws"
        from cairn.bench.worked import write_worked_bundle

        write_worked_bundle(
            _stamped_payload(), tmp_path / "out", suite="perf",
            command=["cairn", "bench", "--suite", "perf", "--workspace", str(workspace)],
            repo_root=repo, benchmarks_root=tmp_path / "benchmarks",
        )
        text = (tmp_path / "out" / "manifest.json").read_text(encoding="utf-8")
        assert str(workspace) not in text
        assert "$TMPDIR/" in text


# --- CLI wiring ----------------------------------------------------------------


def _invoke_perf(tmp_path, monkeypatch, extra, cwd=None):
    monkeypatch.setenv("CAIRN_DB", str(tmp_path / "worked.db"))
    from click.testing import CliRunner
    from cairn.cli.main import main

    if cwd is not None:
        monkeypatch.chdir(cwd)
    return CliRunner().invoke(main, ["bench", *_PERF_ARGS, *extra], catch_exceptions=True)


class TestWorkedFlag:
    def test_flag_writes_bundle_and_command_reparses(self, tmp_path, monkeypatch):
        target = tmp_path / "bundle"
        result = _invoke_perf(tmp_path, monkeypatch, ["--worked", str(target)])
        assert result.exit_code == 0, result.output
        manifest = _read_manifest(target)
        assert manifest["schema"] == MANIFEST_SCHEMA
        assert manifest["inputs"]["suite"] == "perf"
        assert manifest["inputs"]["seed"] == DEFAULT_SEED
        assert manifest["inputs"]["repeats"] == 1
        assert manifest["inputs"]["embed_backend"] == "hash"
        command = manifest["command"]
        assert command[:2] == ["cairn", "bench"]
        assert "--worked" in command
        # The recorded command re-parses against the bench command.
        from cairn.cli.main import main as cli_main

        bench_cmd = cli_main.get_command(None, "bench")
        parsed = bench_cmd.make_context("bench", command[2:])
        assert parsed.params["suite"] == "perf"
        assert parsed.params["worked"] == command[command.index("--worked") + 1]

    def test_seed_null_when_workspace_given(self, tmp_path, monkeypatch):
        from cairn.bench import generate_corpus

        workspace = generate_corpus(tmp_path / "ws", 3, complexity="low")
        target = tmp_path / "bundle"
        result = _invoke_perf(
            tmp_path, monkeypatch, ["--workspace", str(workspace), "--worked", str(target)]
        )
        assert result.exit_code == 0, result.output
        manifest = _read_manifest(target)
        assert manifest["inputs"]["seed"] is None
        text = (target / "manifest.json").read_text(encoding="utf-8")
        assert str(workspace) not in text

    def test_unwritable_target_exits_1_with_results_on_stdout(self, tmp_path, monkeypatch):
        blocker = tmp_path / "blocker.txt"
        blocker.write_text("not a directory\n", encoding="utf-8")
        result = _invoke_perf(tmp_path, monkeypatch, ["--worked", str(blocker / "bundle")])
        assert result.exit_code == 1
        payload, _ = json.JSONDecoder().raw_decode(result.stdout)
        assert payload["dataset"]["name"] == "benchmark-datasource"
        assert "worked" in result.output.lower()
        assert not (blocker / "bundle").exists()


# --- inventory rows (benchmarks/README.md append) ------------------------------

_INVENTORY_FIXTURE = """\
# Test artifact inventory

Rows key on repo-relative path, never basename.

| Artifact | Named by |
|----------|----------|
| `benchmarks/baselines/DS-v1/perf.json` | `benchmarks/baselines/DS-v1/README.md` |

## Keeping this inventory true

Existing prose tail that appends must leave byte-identical.
"""


def _tmp_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "benchmarks" / "baselines").mkdir(parents=True)
    (repo / "benchmarks" / "README.md").write_text(_INVENTORY_FIXTURE, encoding="utf-8")
    return repo


def _assert_lines_preserved(old_text, new_text):
    """Every pre-existing line still present, in order, nothing deleted."""
    new_lines = iter(new_text.splitlines())
    for old_line in old_text.splitlines():
        for candidate in new_lines:
            if candidate == old_line:
                break
        else:
            raise AssertionError(f"line removed or reordered: {old_line!r}")


class TestInventoryRows:
    def _run_in_repo(self, repo, tmp_path, monkeypatch, worked="benchmarks"):
        return _invoke_perf(tmp_path, monkeypatch, ["--worked", worked], cwd=repo)

    def _run_name(self, repo):
        worked_root = repo / "benchmarks" / "worked"
        (run_dir,) = [p for p in worked_root.iterdir() if p.is_dir()]
        return f"benchmarks/worked/{run_dir.name}"

    def test_one_row_per_json_artifact_keyed_repo_relative(self, tmp_path, monkeypatch):
        repo = _tmp_repo(tmp_path)
        result = self._run_in_repo(repo, tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output
        run = self._run_name(repo)
        text = (repo / "benchmarks" / "README.md").read_text(encoding="utf-8")
        assert f"| `{run}/perf.json` | `{run}/README.md` |" in text
        assert f"| `{run}/manifest.json` | `{run}/README.md` |" in text
        assert text.count(f"`{run}/perf.json`") == 1
        assert text.count(f"`{run}/manifest.json`") == 1
        # The companion README is named BY the rows; it is not itself a row.
        assert f"`{run}/README.md` |" not in text.replace(
            f"| `{run}/perf.json` | `{run}/README.md` |", ""
        ).replace(f"| `{run}/manifest.json` | `{run}/README.md` |", "")
        # Pre-existing rows and companion prose untouched except for appends.
        _assert_lines_preserved(_INVENTORY_FIXTURE, text)
        assert text.index(f"`{run}/manifest.json`") > text.index(
            "`benchmarks/baselines/DS-v1/perf.json`"
        )
        assert text.index("## Keeping this inventory true") > text.index(f"`{run}/perf.json`")

    def test_second_run_adds_no_duplicate_rows(self, tmp_path, monkeypatch):
        repo = _tmp_repo(tmp_path)
        assert self._run_in_repo(repo, tmp_path, monkeypatch).exit_code == 0
        after_first = (repo / "benchmarks" / "README.md").read_text(encoding="utf-8")
        assert self._run_in_repo(repo, tmp_path, monkeypatch).exit_code == 0
        after_second = (repo / "benchmarks" / "README.md").read_text(encoding="utf-8")
        assert after_second == after_first

    def test_bundle_outside_repo_appends_nothing(self, tmp_path, monkeypatch):
        repo = _tmp_repo(tmp_path)
        outside = tmp_path / "outside"
        result = self._run_in_repo(repo, tmp_path, monkeypatch, worked=str(outside))
        assert result.exit_code == 0, result.output
        assert outside.is_dir()
        text = (repo / "benchmarks" / "README.md").read_text(encoding="utf-8")
        assert text == _INVENTORY_FIXTURE
