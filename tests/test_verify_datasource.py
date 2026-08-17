"""Tests for scripts/verify_datasource.py -- the FR-001/AC2 content gate and
the FR-002/AC4 size-budget gate.

Hermetic strategy: the full-size real manifest (sizes 100..5000) is the CI
job's business, not the unit suite's -- every manifest test below mints a
SMALL scratch manifest over a tiny corpus using the same helpers the real
minter used (generate_corpus + tree_hash + corpus_stats + save_manifest),
then drives the validator as a library call (or one subprocess for the
wire-level exit code). Budget tests follow the same injectable-paths
pattern: verify_budgets(repo_root=...) measures a scratch tree that mirrors
benchmarks/datasource/ under a tmp dir, or monkeypatches vd.REPO_ROOT for
wire-level runs. One cheap end-to-end run against the real manifest at size
100 (~0.5s) proves the committed pin verifies and the committed tree is
within budget.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

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


class TestTreeHashNoiseExclusion:
    """tree_hash must not see machine build-noise dropped inside a corpus tree.

    The DS-v2 defect this guards: the attrs seal was minted on the authoring
    machine with a pre-commit ruff run's ``.ruff_cache`` inside the vendored
    tree, so a fresh clone (no caches) hashed differently and the seal failed
    with "corpus content drifted" despite the data being fine. Caches are
    machine state, not corpus content -- same class of noise as ``.git``
    metadata, so the walk prunes them (NOISE_DIR_NAMES) exactly as it prunes
    ``.git``. Recorded before-state: with the unfixed function, adding
    ``__pycache__/x.pyc`` + ``.ruff_cache/entries.bin`` to the pristine attrs
    tree changed ad6eec77... -> 52b920c8..., i.e. the hash tracked the noise.
    """

    def test_dropped_caches_never_change_the_digest(self, tmp_path):
        """Hash a clean tree, drop every noise dir into it, hash again: the
        digest must be noise-neutral -- a used dev tree and a fresh clone of
        the same content seal identically."""
        tree = tmp_path / "corpus"
        (tree / "src").mkdir(parents=True)
        (tree / "src" / "m.py").write_text("x = 1\n")
        clean = tree_hash(tree)
        (tree / "__pycache__").mkdir()
        (tree / "__pycache__" / "m.cpython-312.pyc").write_bytes(b"\x00pyc")
        (tree / ".ruff_cache").mkdir()
        (tree / ".ruff_cache" / "entries.bin").write_bytes(b"ruff")
        (tree / ".mypy_cache").mkdir()
        (tree / ".mypy_cache" / "cache.json").write_text("{}")
        (tree / ".pytest_cache").mkdir()
        (tree / ".pytest_cache" / "CACHEDIR.TAG").write_text("pytest")
        assert tree_hash(tree) == clean

    def test_every_noise_dir_name_is_pruned_at_any_depth(self, tmp_path):
        """The contract is the SET (NOISE_DIR_NAMES), not a special case per
        name: each name is pruned wherever os.walk meets it, nested included,
        and the exclusion survives include_git_dir_marker=True (that flag
        governs .git only -- noise stays noise)."""
        from cairn.bench.datasource import NOISE_DIR_NAMES

        for name in sorted(NOISE_DIR_NAMES):
            tree = tmp_path / f"corpus-{name.lstrip('.')}"
            (tree / "pkg").mkdir(parents=True)
            (tree / "pkg" / "m.py").write_text("x = 1\n")
            clean = tree_hash(tree)
            (tree / "pkg" / name).mkdir()
            (tree / "pkg" / name / "dropped.bin").write_bytes(b"noise")
            assert tree_hash(tree) == clean, name
            assert tree_hash(tree, include_git_dir_marker=True) == clean, name

    def test_content_changes_are_still_detected_with_noise_present(self, tmp_path):
        """Noise-blind, not blind: with caches sitting in the tree, a real
        content edit still moves the digest -- the seal keeps its bite."""
        tree = tmp_path / "corpus"
        tree.mkdir()
        (tree / "m.py").write_text("x = 1\n")
        (tree / "__pycache__").mkdir()
        (tree / "__pycache__" / "m.cpython-312.pyc").write_bytes(b"\x00pyc")
        before = tree_hash(tree)
        (tree / "m.py").write_text("x = 2\n")
        assert tree_hash(tree) != before

    def test_real_ds2_seal_pins_verify_against_local_checkouts(self):
        """The defect's exact failure mode, end to end against the committed
        dataset: both DS-v2 corpus pins must match tree_hash of the LOCAL
        working trees -- which carry this machine's untracked .ruff_cache /
        __pycache__ noise -- because the pins (re)minted over pristine trees
        are only reproducible when hashing ignores that noise."""
        ds2 = REPO_ROOT / "benchmarks" / "datasource" / "ds2"
        manifest = json.loads((ds2 / "ground_truth" / "manifest.json").read_text())
        corpora = manifest["corpora"]
        for name, pin in (
            ("attrs-26.1.0", corpora["attrs-26.1.0"]["tree_hash"]),
            ("yarl", corpora["yarl"]["tree_hash"]),
        ):
            source = REPO_ROOT / corpora[name]["source"]
            assert tree_hash(source) == pin, name


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


class TestSizeBudgets:
    """FR-002/AC4 library layer: verify_budgets measures trees as byte sums
    against the per-corpus and total limits, injectable via repo_root
    (T003's pattern)."""

    def test_real_tree_within_all_budgets(self):
        """TC-017 pass leg: the committed tree (T005's yarl snapshot) is under
        every limit with exactly the budget rules the spec names -- t2 and ds2
        per-corpus, datasource total."""
        results = vd.verify_budgets()
        by_path = {b.path: b for b in results}
        assert set(by_path) == {
            "benchmarks/datasource/t2",
            "benchmarks/datasource/ds2",
            "benchmarks/datasource",
        }
        assert by_path["benchmarks/datasource/t2"].limit_kb == 3072
        assert by_path["benchmarks/datasource/ds2"].limit_kb == 3072
        assert by_path["benchmarks/datasource"].limit_kb == 5120
        assert all(not b.breached for b in results)
        assert by_path["benchmarks/datasource/t2"].actual_bytes > 0  # t2 vendored (T005)

    def test_t2_breach_detected_with_injected_oversized_file(self, tmp_path):
        """TC-017 breach leg: pad t2/ past 3 MB while the WHOLE tree stays
        under 5 MB -- the subtree budget fires alone, proving the limits
        are independent checks, not one folded into the other."""
        t2 = tmp_path / "benchmarks" / "datasource" / "t2"
        t2.mkdir(parents=True)
        (t2 / "yarl.py").write_text("# vendored snapshot stand-in\n")
        (t2 / "padding.bin").write_bytes(b"\0" * (3073 * 1024))
        t2_result, ds2_result, total_result = vd.verify_budgets(repo_root=tmp_path)
        assert t2_result.path == "benchmarks/datasource/t2"
        assert t2_result.breached
        assert not total_result.breached
        assert ds2_result.actual_bytes == 0  # absent in this scratch layout
        assert not ds2_result.breached

    def test_total_budget_breaches_independently_of_t2(self, tmp_path):
        """TC-018: > 5 MB of files OUTSIDE t2/ breaches the total budget
        while both per-corpus dirs are absent (measured as 0 bytes --
        nothing vendored, nothing to guard)."""
        ds = tmp_path / "benchmarks" / "datasource"
        ds.mkdir(parents=True)
        (ds / "manifest.json").write_text("{}")
        (ds / "t1-scale.bin").write_bytes(b"\0" * (5121 * 1024))
        t2_result, ds2_result, total_result = vd.verify_budgets(repo_root=tmp_path)
        assert t2_result.actual_bytes == 0
        assert not t2_result.breached
        assert total_result.breached
        assert ds2_result.actual_bytes == 0
        assert not ds2_result.breached

    def test_budget_boundary_exact_limit_passes_one_byte_over_breaches(self, tmp_path):
        """The contract is "<=": exactly 3072 KB passes; one byte more fails.
        The verdict compares raw bytes, so the KB rounding never flips it."""
        t2 = tmp_path / "benchmarks" / "datasource" / "t2"
        t2.mkdir(parents=True)
        payload = t2 / "exact.bin"
        payload.write_bytes(b"\0" * (3072 * 1024))
        result = vd.verify_budgets(
            repo_root=tmp_path, budgets=(("benchmarks/datasource/t2", 3072),)
        )[0]
        assert not result.breached
        payload.write_bytes(b"\0" * (3072 * 1024 + 1))
        result = vd.verify_budgets(
            repo_root=tmp_path, budgets=(("benchmarks/datasource/t2", 3072),)
        )[0]
        assert result.breached

    def test_missing_tree_measures_zero_bytes(self, tmp_path):
        """A root with no datasource tree at all: nothing to guard, every
        budget trivially satisfied, actual reported as 0.0 KB."""
        results = vd.verify_budgets(repo_root=tmp_path)
        assert all(b.actual_bytes == 0 and b.actual_kb == 0.0 for b in results)
        assert not any(b.breached for b in results)

    def test_pycache_build_noise_is_not_counted(self, tmp_path):
        """The budget guards the COMMITTED tree (D-002): a local graph build
        over t2/ drops git-ignored __pycache__ dirs into the vendored tree,
        and that noise must not count -- or a clean CI checkout and a used
        dev tree would measure differently (and could false-breach)."""
        t2 = tmp_path / "benchmarks" / "datasource" / "t2"
        (t2 / "yarl").mkdir(parents=True)
        (t2 / "yarl" / "__pycache__").mkdir()
        (t2 / "yarl" / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\0" * (3073 * 1024))
        (t2 / "yarl" / "b.py").write_text("x = 1\n")
        result = vd.verify_budgets(repo_root=tmp_path)[0]
        # Only b.py counts; the 3 MB .pyc is ignored noise.
        assert result.actual_bytes == (t2 / "yarl" / "b.py").stat().st_size
        assert not result.breached


class TestDs2Budget:
    """FR-002/TC-009: the ds2 sibling corpus dir is covered by its own
    per-corpus rule, not exempt by omission -- the rule is declared whether
    or not the dir exists yet."""

    def test_ds2_rule_declared_with_same_per_corpus_ceiling_as_t2(self):
        by_path = {rel: limit for rel, limit in vd.BUDGETS}
        assert by_path["benchmarks/datasource/ds2"] == vd.DS2_BUDGET_KB == 3072
        assert by_path["benchmarks/datasource/t2"] == 3072  # same class
        # Subtree-before-total ordering keeps the report inside-out.
        paths = list(by_path)
        assert paths.index("benchmarks/datasource/ds2") < paths.index("benchmarks/datasource")

    def test_ds2_absent_measures_zero_and_is_not_an_error(self, tmp_path):
        """The dataset is authored in stages: with no ds2/ dir the rule holds
        trivially (0 bytes measured) instead of erroring or being skipped."""
        by_path = {b.path: b for b in vd.verify_budgets(repo_root=tmp_path)}
        assert by_path["benchmarks/datasource/ds2"].actual_bytes == 0
        assert not by_path["benchmarks/datasource/ds2"].breached

    def test_ds2_within_budget_passes_and_counts_toward_total(self, tmp_path):
        """Present-within-budget pass leg: ds2 content under 3072 KB keeps
        every rule green, and its bytes land in the DATASOURCE total --
        ds2 contributes exactly as t2 does."""
        ds = tmp_path / "benchmarks" / "datasource"
        t2 = ds / "t2"
        t2.mkdir(parents=True)
        (t2 / "yarl.py").write_text("# vendored snapshot stand-in\n")
        ds2 = ds / "ds2"
        ds2.mkdir()
        (ds2 / "corpus.bin").write_bytes(b"\0" * (512 * 1024))
        by_path = {b.path: b for b in vd.verify_budgets(repo_root=tmp_path)}
        assert not any(b.breached for b in by_path.values())
        assert by_path["benchmarks/datasource/ds2"].actual_kb == 512.0
        assert by_path["benchmarks/datasource"].actual_bytes == (
            by_path["benchmarks/datasource/t2"].actual_bytes
            + by_path["benchmarks/datasource/ds2"].actual_bytes
        )

    def test_ds2_over_budget_breaches_its_own_rule_alone(self, tmp_path):
        """Over-budget fail leg: > 3 MB in ds2/ with the total still under
        5 MB -- the ds2 per-corpus rule fires independently of the others."""
        ds2 = tmp_path / "benchmarks" / "datasource" / "ds2"
        ds2.mkdir(parents=True)
        (ds2 / "corpus.bin").write_bytes(b"\0" * (3073 * 1024))
        by_path = {b.path: b for b in vd.verify_budgets(repo_root=tmp_path)}
        assert by_path["benchmarks/datasource/ds2"].breached
        assert not by_path["benchmarks/datasource"].breached
        assert not by_path["benchmarks/datasource/t2"].breached


class TestBudgetCli:
    """FR-002/AC4 wire layer: --budget mode, default-run wiring, exit codes.
    vd.REPO_ROOT is monkeypatched to a scratch tree so main() measures the
    tmp layout, not the repo (verify_budgets resolves it at call time)."""

    def test_budget_only_skips_regeneration_and_reports_json_shape(self, tmp_path, capsys):
        """--budget: no corpus regenerated (results empty), JSON carries the
        exact {path, actual_kb, limit_kb, breached} budget shape."""
        _mint_manifest(tmp_path / "m.json", tmp_path / "corpora", [4])
        # Deliberately measures the REAL tree (absolute paths, ~35 files):
        # the committed datasource is within budget, so this is the pass leg.
        rc = vd.main(["--budget", "--manifest", str(tmp_path / "m.json"), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == vd.EXIT_OK
        assert payload["budget_only"] is True
        assert payload["results"] == []  # nothing was regenerated
        assert payload["ok"] is True
        by_path = {b["path"]: b for b in payload["budgets"]}
        assert set(by_path) == {
            "benchmarks/datasource/t2",
            "benchmarks/datasource/ds2",
            "benchmarks/datasource",
        }
        for b in payload["budgets"]:
            assert set(b) == {"path", "actual_kb", "limit_kb", "breached"}
            assert b["breached"] is False
            assert isinstance(b["actual_kb"], float)

    def test_budget_breach_exits_nonzero_naming_budget_and_limit(self, tmp_path, capsys, monkeypatch):
        """TC-017/TC-018 failure path over the wire: exit 3, stderr names the
        breached budget path and the limit it exceeded."""
        _mint_manifest(tmp_path / "m.json", tmp_path / "corpora", [4])
        t2 = tmp_path / "benchmarks" / "datasource" / "t2"
        t2.mkdir(parents=True)
        (t2 / "padding.bin").write_bytes(b"\0" * (3073 * 1024))
        monkeypatch.setattr(vd, "REPO_ROOT", tmp_path)
        rc = vd.main(["--budget", "--manifest", str(tmp_path / "m.json")])
        captured = capsys.readouterr()
        assert rc == vd.EXIT_BUDGET
        assert "benchmarks/datasource/t2" in captured.err
        assert "3072" in captured.err
        assert "exceeds limit" in captured.err

    def test_default_run_checks_budgets_too(self, tmp_path, capsys, monkeypatch):
        """The wiring requirement: even WITHOUT --budget, a hash-verified run
        against a breached tree exits non-zero on the budget fact alone."""
        _mint_manifest(tmp_path / "m.json", tmp_path / "corpora", [4])
        t2 = tmp_path / "benchmarks" / "datasource" / "t2"
        t2.mkdir(parents=True)
        (t2 / "padding.bin").write_bytes(b"\0" * (5121 * 1024))
        monkeypatch.setattr(vd, "REPO_ROOT", tmp_path)
        rc = vd.main(["--manifest", str(tmp_path / "m.json")])
        captured = capsys.readouterr()
        assert rc == vd.EXIT_BUDGET
        assert "OK: 1/1 size(s)" in captured.out  # the pin itself verified...
        assert "budget" in captured.out  # ...budget lines are part of every run
        assert "benchmarks/datasource" in captured.err  # ...but the tree did not

    def test_drift_outranks_budget_breach(self, tmp_path, monkeypatch):
        """Exit-code precedence: broken pin AND breached tree -> EXIT_DRIFT
        (1), the documented 2 > 1 > 3 order -- the pin contract is primary."""
        manifest = _mint_manifest(tmp_path / "m.json", tmp_path / "corpora", [4])
        manifest["t1"]["entries"]["4"]["tree_hash"] = "f" * 64
        save_manifest(tmp_path / "m.json", manifest)
        t2 = tmp_path / "benchmarks" / "datasource" / "t2"
        t2.mkdir(parents=True)
        (t2 / "padding.bin").write_bytes(b"\0" * (5121 * 1024))
        monkeypatch.setattr(vd, "REPO_ROOT", tmp_path)
        assert vd.main(["--manifest", str(tmp_path / "m.json")]) == vd.EXIT_DRIFT

    def test_size_and_budget_flags_are_mutually_exclusive(self, tmp_path):
        """--size promises regeneration, --budget promises none: argparse
        rejects the contradiction instead of guessing which was meant."""
        with pytest.raises(SystemExit):
            vd.main(["--size", "100", "--budget", "--manifest", str(tmp_path / "m.json")])


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

    def test_real_run_includes_budget_lines(self):
        """T007 acceptance over the wire: the default --size 100 run now also
        prints the budget lines (both limits visible), still exit 0 -- CI
        gets content and budget enforcement from the one step (TC-017)."""
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--size", "100"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert proc.returncode == 0, proc.stderr
        assert "budget benchmarks/datasource/t2" in proc.stdout
        assert "budget benchmarks/datasource" in proc.stdout
        assert "3072" in proc.stdout and "5120" in proc.stdout
        assert "size budget(s) within limits" in proc.stdout
