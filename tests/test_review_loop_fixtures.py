"""`cairn review --base main` pack contract over the review-loop fixtures:
the TC-001..TC-011 pass conditions (specs/pr-review-loop/test.md).

Each test builds a scenario via tests/fixtures/review-loop/prepare.sh into
tmp_path and drives the installed `cairn` console script as a subprocess,
with every store location (CAIRN_HOME, CAIRN_DB, CAIRN_KNOWLEDGE,
CAIRN_WORKSPACE) pinned inside the sandbox, so no workspace leaks into the
real ~/.cairn (C-04). `cairn.cli` is never imported (C-04): the surface
under test is the CLI itself, invoked from inside the fixture workspace.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PREPARE = Path(__file__).resolve().parent / "fixtures" / "review-loop" / "prepare.sh"
EVENTS = Path(__file__).resolve().parent / "fixtures" / "review-loop" / "events"

# TC-003/TC-004 wording contract for an empty dependents section.
NO_DEPENDENTS_RE = re.compile(
    r"no (downstream |reverse )?dependents|zero dependents", re.IGNORECASE
)


def _cairn_bin() -> str:
    """Resolve the cairn console script next to the running interpreter."""
    # No resolve(): venv pythons are symlinks; the bin dir is what matters.
    candidate = Path(sys.executable).parent / "cairn"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("cairn")
    if found:
        return found
    raise RuntimeError("cairn console script not found beside sys.executable or on PATH")


def _build_scenario(tmp_path: Path, scenario: str) -> Path:
    """Build one fixture scenario; return the scenario root dir."""
    target = tmp_path / scenario
    env = dict(os.environ)
    env["CAIRN_BIN"] = _cairn_bin()
    env["CAIRN_HOME"] = str(target / "home")
    proc = subprocess.run(
        ["bash", str(PREPARE), scenario, str(target)],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    assert proc.returncode == 0, (
        f"prepare.sh {scenario} failed:\n{proc.stdout}\n{proc.stderr}"
    )
    return Path(proc.stdout.strip().splitlines()[-1])


def _run_review(root: Path, *args: str) -> subprocess.CompletedProcess:
    """Run `cairn review` inside the workspace with the sandbox store env."""
    return _run_cairn(root, "review", *args)


def _run_cairn(root: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a cairn command inside the workspace with the sandbox store env."""
    ws = root / "ws"
    env = dict(os.environ)
    env.update(
        {
            "CAIRN_BIN": _cairn_bin(),
            "CAIRN_HOME": str(root / "home"),
            "CAIRN_WORKSPACE": str(ws),
            "CAIRN_DB": str(root / "store" / "graph.kg"),
            "CAIRN_KNOWLEDGE": str(root / "store" / ".knowledge"),
            "CAIRN_EMBED_BACKEND": "hash",
        }
    )
    return subprocess.run(
        [env["CAIRN_BIN"], *args],
        cwd=str(ws),
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )


def test_tc001_pack_assembles_all_four_layers(tmp_path):
    ws = _build_scenario(tmp_path, "pack")
    proc = _run_review(ws, "--base", "main", "--format", "text")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "reporting" in out
    assert "api" in out
    assert "regex" in out.lower()
    assert "ownership" in out.lower()
    assert "invariant" in out.lower()


def test_tc002_pack_renders_text_and_markdown(tmp_path):
    ws = _build_scenario(tmp_path, "pack")
    markdown = _run_review(ws, "--base", "main", "--format", "markdown")
    assert markdown.returncode == 0, markdown.stderr
    assert any(line.startswith("#") for line in markdown.stdout.splitlines())
    text = _run_review(ws, "--base", "main", "--format", "text")
    assert text.returncode == 0, text.stderr
    assert "reporting" in text.stdout


def test_tc003_change_with_no_dependents(tmp_path):
    ws = _build_scenario(tmp_path, "banner")
    proc = _run_review(ws, "--base", "main")
    assert proc.returncode == 0, proc.stderr
    assert NO_DEPENDENTS_RE.search(proc.stdout)


def test_tc004_change_with_no_enrichment(tmp_path):
    ws = _build_scenario(tmp_path, "banner")
    proc = _run_review(ws, "--base", "main")
    assert proc.returncode == 0, proc.stderr
    assert NO_DEPENDENTS_RE.search(proc.stdout)
    assert "regex" not in proc.stdout.lower()


def test_tc005_pre_submit_surfaces_keyed_matches_as_warnings(tmp_path):
    ws = _build_scenario(tmp_path, "guard")
    proc = _run_review(ws, "--pre-submit")
    assert proc.returncode == 0, proc.stderr
    assert "regex" in proc.stdout.lower()
    assert "backoff" in proc.stdout.lower()


def test_tc006_pre_submit_with_no_keyed_matches_stays_silent(tmp_path):
    ws = _build_scenario(tmp_path, "quiet")
    proc = _run_review(ws, "--pre-submit")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == ""
    assert "regex" not in proc.stdout.lower()
    assert "backoff" not in proc.stdout.lower()


def test_tc007_memories_of_other_types_never_warn(tmp_path):
    ws = _build_scenario(tmp_path, "types")
    proc = _run_review(ws, "--pre-submit")
    assert proc.returncode == 0, proc.stderr
    assert "cache-first" not in proc.stdout.lower()


def test_tc008_gating_turns_keyed_matches_into_failure(tmp_path):
    ws = _build_scenario(tmp_path, "gate")
    gated = _run_review(ws, "--pre-submit", "--gate")
    assert gated.returncode != 0
    assert "regex" in gated.stdout.lower()
    ungated = _run_review(ws, "--pre-submit")
    assert ungated.returncode == 0, ungated.stderr
    assert "regex" in ungated.stdout.lower()
    assert "backoff" in ungated.stdout.lower()


def test_tc009_accepted_comment_becomes_keyed_linked_memory(tmp_path):
    root = _build_scenario(tmp_path, "capture")
    proc = _run_review(root, "--capture-event", str(EVENTS / "resolved-comment.json"))
    assert proc.returncode == 0, proc.stderr
    listed = _run_cairn(root, "memory", "list")
    assert listed.returncode == 0, listed.stderr
    assert "regex" in listed.stdout.lower()
    assert "ledger" in listed.stdout.lower()
    assert "pull/42" in listed.stdout


def test_tc010_open_comment_records_nothing(tmp_path):
    root = _build_scenario(tmp_path, "capture")
    proc = _run_review(
        root, "--capture-event", str(EVENTS / "unresolved-comment.json")
    )
    assert proc.returncode == 0, proc.stderr
    listed = _run_cairn(root, "memory", "list")
    assert listed.returncode == 0, listed.stderr
    assert "regex" not in listed.stdout.lower()


def test_tc011_comment_outside_any_module_degrades_to_file_level(tmp_path):
    root = _build_scenario(tmp_path, "capture")
    proc = _run_review(
        root, "--capture-event", str(EVENTS / "orphan-line-comment.json")
    )
    assert proc.returncode == 0, proc.stderr
    listed = _run_cairn(root, "memory", "list")
    assert listed.returncode == 0, listed.stderr
    assert "swallow" in listed.stdout.lower()
    assert "changelog.md" in listed.stdout
