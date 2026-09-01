"""Tests for scripts/fetch_t3_corpus.py -- T020 (FR-006/AC7, TC-030..TC-033).

Hermetic strategy (same pattern as tests/test_gen_benchmark_tables.py):
the script is loaded by file path so the object under test is the module
the subprocess executes; every fetch test mints a TINY LOCAL bare git repo
as the "remote" (git init + one commit + git clone --bare -- all offline,
git being available locally and on CI runners), pins it in a scratch
manifest, and drives the script as a library call. The real multi-GB T3
corpora are never touched: --list over the committed manifest is the only
read of real data, and it is read-only. The ``--run-bench`` path stubs the
bench subprocess (the invocation shape and the t3_entry stamp are THIS
script's logic; running a real bench suite is the maintainer's local run).

Covers the spec's test plan:
  TC-030 --list prints pins without touching the network
  TC-031 an unreachable/moved pin fails loudly: exit non-zero, naming the
          entry, the expected pin, and what was found
  TC-032 the verified success path (detached HEAD == pin exactly) and the
          --run-bench result marker stamped with the manifest entry
  TC-033 nothing here belongs in CI -- the script lives in scripts/ (D-009)
          and the test never needs a network.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.infra

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "fetch_t3_corpus.py"
REAL_MANIFEST = REPO_ROOT / "benchmarks" / "datasource" / "manifest.json"

# scripts/ is not a package; load the fetcher by file path so the object
# under test is the same module the subprocess executes (same pattern as
# tests/test_gen_benchmark_tables.py).
_spec = importlib.util.spec_from_file_location("fetch_t3_corpus", SCRIPT)
ft3 = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("fetch_t3_corpus", ft3)
_spec.loader.exec_module(ft3)


# ---------------------------------------------------------------------------
# Fixtures: a tiny local "remote" + a scratch manifest pinning it
# ---------------------------------------------------------------------------


def _git_ok(args: list[str], cwd: Path | None = None) -> str:
    """Run git expecting success; returns stdout (fails the test otherwise)."""
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout.strip()


@pytest.fixture
def origin_repo(tmp_path: Path) -> tuple[Path, str]:
    """A bare repo with exactly one commit, addressable by local path.

    ``git clone <this path>`` is a filesystem operation -- no network is
    involved, which is what keeps these tests hermetic (TC-033 spirit).
    """
    src = tmp_path / "src-repo"
    src.mkdir()
    _git_ok(["init", "-q"], cwd=src)
    (src / "hello.py").write_text("def hello():\n    return 'pinned'\n", encoding="utf-8")
    _git_ok(["add", "hello.py"], cwd=src)
    _git_ok(
        ["-c", "user.name=t3-fixture", "-c", "user.email=t3@example.com", "commit", "-qm", "one"],
        cwd=src,
    )
    head = _git_ok(["rev-parse", "HEAD"], cwd=src)
    origin = tmp_path / "origin.git"
    _git_ok(["clone", "-q", "--bare", str(src), str(origin)])
    return origin, head


def _write_manifest(path: Path, url: str, commit: str, name: str = "fixture/tiny") -> Path:
    """A scratch manifest pinning one entry at ``commit`` against ``url``.

    Deliberately minimal (only the t3 section): the script reads t3 pins,
    and full-schema validation of the t1 corpus is verify_datasource.py's
    job in CI -- not this command's.
    """
    manifest = {
        "t3": {
            "entries": [
                {
                    "name": name,
                    "url": str(url),
                    "commit": commit,
                    "scale_hint": "1 file -- tiny local fixture",
                }
            ]
        }
    }
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def _run(argv: list[str]) -> int:
    return ft3.main(argv)


BOGUS_PIN = "e" * 40  # valid sha SHAPE, exists in no clone


# ---------------------------------------------------------------------------
# --list: offline by construction (TC-030)
# ---------------------------------------------------------------------------


class TestListMode:
    def test_list_real_manifest_prints_both_entries(self, capsys):
        rc = _run(["--list"])
        out = capsys.readouterr().out
        assert rc == ft3.EXIT_OK
        assert "(2)" in out
        for fragment in (
            "home-assistant/core",
            "torvalds/linux",
            "0308f01b295a8ecfef9938b67514aa1b7b95e5bc",
            "3eb40771c00a8488fa6ed2cc1fe203477908bf38",
            "never the default-branch HEAD",
        ):
            assert fragment in out

    def test_list_creates_no_directories(self, tmp_path, capsys):
        """--list must not touch the network OR the disk: no cache root, no
        dest dir (TC-030 -- the mode is safe anywhere, any runner)."""
        cache = tmp_path / "cache"
        dest = tmp_path / "dest"
        rc = _run(["--list", "--cache", str(cache), "--dest", str(dest)])
        assert rc == ft3.EXIT_OK
        assert not cache.exists()
        assert not dest.exists()

    def test_list_without_t3_section_is_a_manifest_error(self, tmp_path, capsys):
        path = tmp_path / "empty.json"
        path.write_text("{}", encoding="utf-8")
        rc = _run(["--list", "--manifest", str(path)])
        assert rc == ft3.EXIT_MANIFEST
        assert "no t3 section" in capsys.readouterr().err

    def test_list_unreadable_manifest_exits_manifest(self, tmp_path, capsys):
        rc = _run(["--list", "--manifest", str(tmp_path / "absent.json")])
        assert rc == ft3.EXIT_MANIFEST
        assert "manifest unusable" in capsys.readouterr().err

    def test_list_and_entry_together_is_usage(self, capsys):
        rc = _run(["--list", "fixture/tiny"])
        assert rc == ft3.EXIT_USAGE
        assert "not both" in capsys.readouterr().err

    def test_no_entry_and_no_list_is_usage(self, capsys):
        rc = _run([])
        assert rc == ft3.EXIT_USAGE


# ---------------------------------------------------------------------------
# fetch-by-pin: enforcement is the product (TC-031/TC-032)
# ---------------------------------------------------------------------------


class TestFetchByPin:
    def test_verified_checkout_lands_exactly_on_pin(self, origin_repo, tmp_path, capsys):
        origin, head = origin_repo
        manifest = _write_manifest(tmp_path / "m.json", origin, head)
        cache = tmp_path / "cache"
        rc = _run(["fixture/tiny", "--manifest", str(manifest), "--cache", str(cache)])
        out = capsys.readouterr().out
        assert rc == ft3.EXIT_OK
        assert "verified" in out and head in out
        checkout = cache / "fixture__tiny"  # '/' sanitized out of the name
        assert _git_ok(["rev-parse", "HEAD"], cwd=checkout) == head

    def test_checkout_is_detached_never_default_head(self, origin_repo, tmp_path):
        """The clone is --no-checkout and the only checkout is --detach at the
        pin: HEAD must not be a symbolic ref to any branch (the default-branch
        HEAD is never materialized -- FR-006's contamination lesson)."""
        origin, head = origin_repo
        manifest = _write_manifest(tmp_path / "m.json", origin, head)
        cache = tmp_path / "cache"
        assert _run(["fixture/tiny", "--manifest", str(manifest), "--cache", str(cache)]) == 0
        checkout = cache / "fixture__tiny"
        proc = subprocess.run(
            ["git", "symbolic-ref", "-q", "HEAD"], cwd=checkout, capture_output=True, text=True
        )
        assert proc.returncode != 0, "HEAD is on a branch -- the pin was not enforced"

    def test_rerun_updates_cached_clone_and_reverifies(self, origin_repo, tmp_path, capsys):
        origin, head = origin_repo
        manifest = _write_manifest(tmp_path / "m.json", origin, head)
        cache = tmp_path / "cache"
        assert _run(["fixture/tiny", "--manifest", str(manifest), "--cache", str(cache)]) == 0
        rc = _run(["fixture/tiny", "--manifest", str(manifest), "--cache", str(cache)])
        assert rc == ft3.EXIT_OK
        assert "updating cached clone" in capsys.readouterr().out

    def test_bogus_pin_fails_loudly_naming_entry_expected_found(
        self, origin_repo, tmp_path, capsys
    ):
        """TC-031: a pin that does not exist in the cloned repository (moved,
        force-pushed away, typo'd) fails with the entry named, the expected
        pin, and what was found instead -- never a silent default-HEAD run."""
        origin, head = origin_repo
        manifest = _write_manifest(tmp_path / "m.json", origin, BOGUS_PIN)
        cache = tmp_path / "cache"
        rc = _run(["fixture/tiny", "--manifest", str(manifest), "--cache", str(cache)])
        err = capsys.readouterr().err
        assert rc == ft3.EXIT_PIN
        assert "fixture/tiny" in err  # the entry, named
        assert BOGUS_PIN in err  # the expected pin
        assert head in err  # what was found instead (the clone's HEAD)
        assert "UNREACHABLE" in err

    def test_bogus_pin_via_subprocess_exit_code(self, origin_repo, tmp_path):
        """Wire-level proof of the same contract: the ``__main__`` entry maps
        PinError to exit 3 (the after-audit failure-mode demonstration,
        encoded so it can never regress)."""
        origin, head = origin_repo
        manifest = _write_manifest(tmp_path / "m.json", origin, BOGUS_PIN)
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "fixture/tiny",
             "--manifest", str(manifest), "--cache", str(tmp_path / "cache")],
            capture_output=True, text=True,
        )
        assert proc.returncode == 3
        assert "fixture/tiny" in proc.stderr and BOGUS_PIN in proc.stderr and head in proc.stderr

    def test_unknown_entry_is_usage_and_lists_available(self, origin_repo, tmp_path, capsys):
        origin, head = origin_repo
        manifest = _write_manifest(tmp_path / "m.json", origin, head)
        rc = _run(["nope", "--manifest", str(manifest), "--cache", str(tmp_path / "c")])
        err = capsys.readouterr().err
        assert rc == ft3.EXIT_USAGE
        assert "'nope'" in err and "fixture/tiny" in err  # the typo + the fix in hand

    def test_malformed_pin_is_a_manifest_error(self, origin_repo, tmp_path, capsys):
        origin, _ = origin_repo
        manifest = _write_manifest(tmp_path / "m.json", origin, "not-hex")
        rc = _run(["fixture/tiny", "--manifest", str(manifest), "--cache", str(tmp_path / "c")])
        assert rc == ft3.EXIT_MANIFEST
        assert "not a 40/64-char hex git sha" in capsys.readouterr().err

    def test_missing_git_is_usage(self, origin_repo, tmp_path, monkeypatch, capsys):
        origin, head = origin_repo
        manifest = _write_manifest(tmp_path / "m.json", origin, head)
        monkeypatch.setattr(ft3.shutil, "which", lambda _: None)
        rc = _run(["fixture/tiny", "--manifest", str(manifest), "--cache", str(tmp_path / "c")])
        assert rc == ft3.EXIT_USAGE
        assert "git is required" in capsys.readouterr().err

    def test_cache_inside_the_repository_is_refused(self, origin_repo, tmp_path, monkeypatch, capsys):
        """D-009: multi-GB clones must never land inside the cairn checkout
        (REPO_ROOT monkeypatched to the scratch dir so the real repo is never
        a party to the test)."""
        origin, head = origin_repo
        manifest = _write_manifest(tmp_path / "m.json", origin, head)
        monkeypatch.setattr(ft3, "REPO_ROOT", tmp_path)
        rc = _run(["fixture/tiny", "--manifest", str(manifest), "--cache", str(tmp_path / "inside")])
        assert rc == ft3.EXIT_USAGE
        assert "inside this repository" in capsys.readouterr().err

    def test_sanitize_name(self):
        assert ft3.sanitize_name("home-assistant/core") == "home-assistant__core"
        assert ft3.sanitize_name("plain") == "plain"
        with pytest.raises(ft3.ManifestError):
            ft3.sanitize_name("..")


# ---------------------------------------------------------------------------
# --run-bench: invocation shape + the t3_entry result stamp (TC-032)
# ---------------------------------------------------------------------------


class TestRunBench:
    @staticmethod
    def _fake_bench_factory(calls: list[list[str]], payload: str):
        """A run_bench_subprocess stub: records argv, writes ``payload`` to
        the path after --save (what `cairn bench --save` would leave)."""

        def fake(argv: list[str]) -> int:
            calls.append(argv)
            save = Path(argv[argv.index("--save") + 1])
            save.write_text(payload, encoding="utf-8")
            return 0

        return fake

    def test_invokes_bench_on_the_verified_checkout_and_stamps_the_entry(
        self, origin_repo, tmp_path, monkeypatch, capsys
    ):
        """TC-032's result marker: after a VERIFIED checkout the script runs
        `cairn bench --workspace <checkout> --json --save <dest>/<name>.json`
        and the saved artifact's dataset.t3_entry is the manifest entry
        verbatim (repo + commit + scale -- the T013 hook's shape)."""
        origin, head = origin_repo
        manifest = _write_manifest(tmp_path / "m.json", origin, head)
        cache, dest = tmp_path / "cache", tmp_path / "results"
        calls: list[list[str]] = []
        payload = json.dumps(
            {"dataset": {"name": "benchmark-datasource"}, "results": []}
        )
        monkeypatch.setattr(ft3, "run_bench_subprocess", self._fake_bench_factory(calls, payload))

        rc = _run(["fixture/tiny", "--manifest", str(manifest),
                   "--cache", str(cache), "--dest", str(dest), "--run-bench"])
        assert rc == ft3.EXIT_OK

        # Invocation shape: the cairn CLI's bench subcommand against the
        # verified checkout, JSON output, saved under --dest with the
        # sanitized entry name (--dest/--manifest flags respected).
        assert len(calls) == 1
        argv = calls[0]
        assert "bench" in argv
        assert argv[argv.index("--workspace") + 1] == str(cache / "fixture__tiny")
        assert "--json" in argv
        save = Path(argv[argv.index("--save") + 1])
        assert save == dest / "fixture__tiny.json"

        # The result marker: the saved JSON records the manifest entry.
        stamped = json.loads(save.read_text(encoding="utf-8"))
        assert stamped["dataset"]["t3_entry"] == {
            "name": "fixture/tiny",
            "url": str(origin),
            "commit": head,
            "scale_hint": "1 file -- tiny local fixture",
        }
        assert "stamped" in capsys.readouterr().out

    def test_stamp_creates_a_missing_dataset_block(self, origin_repo, tmp_path, monkeypatch):
        """A bench artifact without a dataset block still gets the t3_entry
        stamp -- the wiring must not silently no-op on an unexpected shape."""
        origin, head = origin_repo
        manifest = _write_manifest(tmp_path / "m.json", origin, head)
        calls: list[list[str]] = []
        monkeypatch.setattr(ft3, "run_bench_subprocess", self._fake_bench_factory(calls, "{}"))
        rc = _run(["fixture/tiny", "--manifest", str(manifest),
                   "--cache", str(tmp_path / "cache"), "--dest", str(tmp_path / "dest"),
                   "--run-bench"])
        assert rc == ft3.EXIT_OK
        stamped = json.loads((tmp_path / "dest" / "fixture__tiny.json").read_text(encoding="utf-8"))
        assert stamped["dataset"]["t3_entry"]["commit"] == head

    def test_bench_failure_is_exit_4_and_unstamped(self, origin_repo, tmp_path, monkeypatch, capsys):
        """A failed bench run maps to the bench-specific exit code and never
        writes a t3_entry stamp (a stamp asserts a verified run happened)."""
        origin, head = origin_repo
        manifest = _write_manifest(tmp_path / "m.json", origin, head)

        def failing(argv: list[str]) -> int:
            return 2  # e.g. the --compare regression signal

        monkeypatch.setattr(ft3, "run_bench_subprocess", failing)
        rc = _run(["fixture/tiny", "--manifest", str(manifest),
                   "--cache", str(tmp_path / "cache"), "--dest", str(tmp_path / "dest"),
                   "--run-bench"])
        assert rc == ft3.EXIT_BENCH
        assert "exited non-zero (code 2)" in capsys.readouterr().err
        assert not (tmp_path / "dest" / "fixture__tiny.json").exists()
