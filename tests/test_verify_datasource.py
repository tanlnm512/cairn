"""Tests for scripts/verify_datasource.py -- the FR-001/AC2 assert-mode gate.

Hermetic strategy: the full-size real manifest (sizes 100..5000) is the CI
job's business, not the unit suite's -- every test below mints a SMALL
scratch manifest over a tiny corpus using the same helpers the real minter
used (generate_corpus + tree_hash + corpus_stats + save_manifest), then
drives the validator as a library call (or one subprocess for the
wire-level exit code). One cheap end-to-end run against the real manifest
at size 100 (~0.5s) proves the committed pin actually verifies.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from cairn.bench.corpus import corpus_stats, generate_corpus
from cairn.bench.datasource import (
    MANIFEST_SCHEMA,
    MANIFEST_VERSION,
    save_manifest,
    tree_hash,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify_datasource.py"
REAL_MANIFEST = REPO_ROOT / "benchmarks" / "datasource" / "manifest.json"

# scripts/ is not a package; load the validator by file path so the object
# under test is the same module the subprocess executes. Registered in
# sys.modules first so dataclass/argparse introspection behaves identically
# to a normal import.
_spec = importlib.util.spec_from_file_location("verify_datasource", SCRIPT)
vd = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("verify_datasource", vd)
_spec.loader.exec_module(vd)


def _mint_manifest(path, corpus_root, sizes, *, seed=0xC0DE, complexity="low"):
    """Build a REAL scratch manifest: hash/counts computed from actual corpora.

    Uses exactly the T002 minter's recipe (generate_corpus -> tree_hash +
    corpus_stats -> save_manifest), so a verify against this manifest is the
    same code path CI runs, just at toy scale.
    """
    entries = {}
    for size in sizes:
        repo = generate_corpus(corpus_root / f"size_{size}", size, complexity=complexity, seed=seed)
        entries[str(size)] = {"tree_hash": tree_hash(repo), "counts": corpus_stats(repo)}
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "version": MANIFEST_VERSION,
        "t1": {
            "generator_git_sha": "9" * 40,
            "seed": seed,
            "sizes": list(sizes),
            "complexity": complexity,
            "entries": entries,
        },
    }
    save_manifest(path, manifest)
    return manifest


class TestHappyPath:
    def test_all_declared_sizes_verify_ok(self, tmp_path):
        _mint_manifest(tmp_path / "m.json", tmp_path / "corpora", [3, 5])
        report = vd.verify_manifest(tmp_path / "m.json")
        assert report.schema_errors == []
        assert [r.status for r in report.results] == ["ok", "ok"]
        assert report.ok and report.exit_code() == vd.EXIT_OK
        assert vd.main(["--manifest", str(tmp_path / "m.json")]) == 0

    def test_size_flag_selects_one_declared_size(self, tmp_path):
        _mint_manifest(tmp_path / "m.json", tmp_path / "corpora", [3, 5])
        report = vd.verify_manifest(tmp_path / "m.json", sizes=[5])
        assert [r.size for r in report.results] == [5]
        assert report.ok

    def test_recipe_recorded_in_report(self, tmp_path):
        _mint_manifest(tmp_path / "m.json", tmp_path / "corpora", [3])
        report = vd.verify_manifest(tmp_path / "m.json")
        assert report.recipe["seed"] == 0xC0DE
        assert report.recipe["complexity"] == "low"


class TestDrift:
    def test_flipped_byte_in_generated_file_exits_nonzero(self, tmp_path):
        """TC-003 via content: mint the pin from a corpus with one flipped
        byte, so the manifest pins a tree regeneration cannot reproduce.

        The flip is length-preserving (bytes XOR) so ONLY the hash fact
        drifts -- counts stay equal, isolating the hash detector. The failure
        names the size and the fact; per-file attribution is impossible by
        design (the manifest pins one aggregate digest).
        """
        manifest = _mint_manifest(tmp_path / "m.json", tmp_path / "corpora", [4])
        repo = tmp_path / "corpora" / "size_4" / "benchrepo"
        victim = repo / "module_0000.py"
        data = bytearray(victim.read_bytes())
        data[0] ^= 0x20  # flip one byte in one generated file
        victim.write_bytes(bytes(data))
        # Re-pin against the flipped tree: the manifest now promises a corpus
        # the deterministic generator cannot reproduce.
        manifest["t1"]["entries"]["4"]["tree_hash"] = tree_hash(repo)
        save_manifest(tmp_path / "m.json", manifest)
        rc = vd.main(["--manifest", str(tmp_path / "m.json")])
        assert rc == vd.EXIT_DRIFT

    def test_hash_mismatch_report_names_size_and_facts(self, tmp_path, capsys):
        manifest = _mint_manifest(tmp_path / "m.json", tmp_path / "corpora", [4])
        manifest["t1"]["entries"]["4"]["tree_hash"] = "f" * 64
        save_manifest(tmp_path / "m.json", manifest)
        rc = vd.main(["--manifest", str(tmp_path / "m.json")])
        captured = capsys.readouterr()
        assert rc == vd.EXIT_DRIFT
        assert "size 4" in captured.err
        assert "tree-hash mismatch" in captured.err
        assert "f" * 64 in captured.err  # the expected/actual pair is the handle

    def test_count_mismatch_names_field_with_expected_and_actual(self, tmp_path):
        manifest = _mint_manifest(tmp_path / "m.json", tmp_path / "corpora", [4])
        manifest["t1"]["entries"]["4"]["counts"]["lines"] += 1
        save_manifest(tmp_path / "m.json", manifest)
        report = vd.verify_manifest(tmp_path / "m.json")
        assert report.exit_code() == vd.EXIT_DRIFT
        (res,) = report.results
        assert res.status == "count_mismatch"
        assert res.count_mismatches == [
            {
                "field": "lines",
                "expected": manifest["t1"]["entries"]["4"]["counts"]["lines"],
                "actual": res.actual_counts["lines"],
            }
        ]

    def test_seed_flip_exits_nonzero(self, tmp_path):
        """TC-003's scratch experiment: bump the seed, leave the pin alone."""
        manifest = _mint_manifest(tmp_path / "m.json", tmp_path / "corpora", [4])
        manifest["t1"]["seed"] += 1
        save_manifest(tmp_path / "m.json", manifest)
        report = vd.verify_manifest(tmp_path / "m.json")
        assert report.exit_code() == vd.EXIT_DRIFT
        (res,) = report.results
        assert res.status == "hash_mismatch"

    def test_two_consecutive_runs_identical_hash(self, tmp_path):
        """TC-005 determinism substrate: same recipe, two fresh regenerations
        into different roots -> identical actual hash, both runs green."""
        _mint_manifest(tmp_path / "m.json", tmp_path / "corpora", [5])
        first = vd.verify_manifest(tmp_path / "m.json", workroot=tmp_path / "run1")
        second = vd.verify_manifest(tmp_path / "m.json", workroot=tmp_path / "run2")
        assert first.ok and second.ok
        assert first.results[0].actual_hash == second.results[0].actual_hash


class TestManifestFailureClass:
    def test_schema_error_is_distinct_from_drift(self, tmp_path):
        manifest = _mint_manifest(tmp_path / "m.json", tmp_path / "corpora", [4])
        del manifest["t1"]["seed"]
        save_manifest(tmp_path / "m.json", manifest)
        report = vd.verify_manifest(tmp_path / "m.json")
        assert report.exit_code() == vd.EXIT_MANIFEST
        assert report.schema_errors and any("seed" in e for e in report.schema_errors)
        assert report.results == []  # nothing was compared -- not drift

    def test_undeclared_size_is_manifest_error(self, tmp_path):
        _mint_manifest(tmp_path / "m.json", tmp_path / "corpora", [4])
        rc = vd.main(["--manifest", str(tmp_path / "m.json"), "--size", "7"])
        assert rc == vd.EXIT_MANIFEST

    def test_missing_manifest_file_is_manifest_error(self, tmp_path):
        assert vd.main(["--manifest", str(tmp_path / "absent.json")]) == vd.EXIT_MANIFEST


class TestJsonOutput:
    def test_shape_is_machine_readable(self, tmp_path, capsys):
        _mint_manifest(tmp_path / "m.json", tmp_path / "corpora", [4])
        rc = vd.main(["--manifest", str(tmp_path / "m.json"), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["ok"] is True
        assert payload["schema_errors"] == []
        (res,) = payload["results"]
        assert res["size"] == 4
        assert res["status"] == "ok"
        assert set(res) == {"size", "status", "expected_hash", "actual_hash", "count_mismatches"}

    def test_drift_shape_carries_expected_actual(self, tmp_path, capsys):
        manifest = _mint_manifest(tmp_path / "m.json", tmp_path / "corpora", [4])
        manifest["t1"]["entries"]["4"]["tree_hash"] = "a" * 64
        save_manifest(tmp_path / "m.json", manifest)
        rc = vd.main(["--manifest", str(tmp_path / "m.json"), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == vd.EXIT_DRIFT
        assert payload["ok"] is False
        assert payload["results"][0]["status"] == "hash_mismatch"
        assert payload["results"][0]["expected_hash"] == "a" * 64


class TestEndToEnd:
    def test_real_manifest_size_100_verifies_over_the_wire(self):
        """The committed pin, the real CLI surface, a real subprocess: exit 0
        with a human summary. Size 100 only (~0.5s) -- the larger sizes and
        the all-sizes default are the CI bench job's business (T004)."""
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--size", "100"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert proc.returncode == 0, proc.stderr
        assert "OK" in proc.stdout
        assert "100" in proc.stdout
