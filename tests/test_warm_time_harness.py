"""Smoke tests for the warm-time harness script (T022, FR-007).

The harness's real measurement (fresh-process model loads, warm-up thread)
needs the local embedder + cached weights and must NOT run under pytest
(``model_warmup._inside_pytest`` refuses to start the warm thread), so these
tests only prove the script RUNS: ``--help``, the dep-free hash-backend
build + cold arm end to end, and that the full mint refuses loudly under
pytest rather than minting a dishonest artifact. The measurement itself is
exercised by ``tests/test_model_warmup.py`` (unit) and the committed
``benchmarks/quality/warm_time.json`` (the artifact).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import pytest

pytestmark = pytest.mark.infra

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "measure_warm_time.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _last_json_line(proc: subprocess.CompletedProcess) -> dict:
    lines = [line for line in proc.stdout.strip().splitlines() if line.strip()]
    return json.loads(lines[-1])


def test_help_runs():
    proc = _run("--help")
    assert proc.returncode == 0, proc.stderr
    assert "--mode" in proc.stdout


def test_hash_backend_build_and_cold_arm(tmp_path):
    """The dep-free smoke path: build the tiny fixture, measure one cold arm.

    Hash mode never loads a model (that is the point -- it exists so tests
    can run the harness end to end without torch), and forces
    CAIRN_RERANK=0 so a machine with the persistent rerank marker stays
    dep-free too.
    """
    built = _run("--mode", "build", "--backend", "hash", "--workroot", str(tmp_path))
    assert built.returncode == 0, built.stderr
    build_payload = _last_json_line(built)
    assert build_payload["build"]["parse_errors"] == 0
    assert build_payload["build"]["symbols"] >= 8
    assert build_payload["embed"]["backend_effective"] == "hash"
    assert build_payload["embed"]["embedded"] == build_payload["embed"]["total"]

    cold = _run(
        "--mode", "cold", "--backend", "hash", "--db", build_payload["db"],
        "--query", "split a url string into components",
    )
    assert cold.returncode == 0, cold.stderr
    cold_payload = _last_json_line(cold)
    assert cold_payload["first_query_ms"] > 0
    assert cold_payload["results"] >= 1
    assert cold_payload["reranked"] is False  # hash policy forces CAIRN_RERANK=0
    assert cold_payload["warmup"]["called"] is False
    assert cold_payload["gates"]["env"]["CAIRN_EMBED_BACKEND"] == "hash"
    assert cold_payload["gates"]["rerank_enabled"] is False


def test_full_mint_refuses_under_pytest():
    """The mint must fail loudly here, not emit a cold-measurement artifact.

    PYTEST_CURRENT_TEST makes warm_models_in_background refuse to start its
    thread; the preflight catches that before any work runs so nobody ever
    commits a 'warm' number that was actually measured cold.
    """
    proc = _run("--mode", "all")
    assert proc.returncode != 0
    assert "refusing to mint" in proc.stderr
    assert "PYTEST_CURRENT_TEST" in proc.stderr
