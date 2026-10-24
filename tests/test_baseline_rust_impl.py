"""The rust leg of the rust-migration baseline harness."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "rust_migration" / "baseline.py"
RUST_BIN = REPO_ROOT / "rust" / "target" / "release" / "cairn"
YARL_CORPUS = REPO_ROOT / "rust" / "golden" / "stores" / "yarl" / "corpus"

pytestmark = [
    pytest.mark.infra,
    pytest.mark.skipif(
        not RUST_BIN.exists(),
        reason="release cairn binary absent (cargo build --release -p cairn-cli)",
    ),
]


def test_rust_impl_measures_daemon_rss_and_tool_latency(tmp_path):
    """--impl rust serves the store from the release daemon and records the
    RSS and per-tool latency artifact the gate reads."""
    out = tmp_path / "artifact.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--impl",
            "rust",
            "--workspace",
            str(YARL_CORPUS),
            "--label",
            "yarl-rust-smoke",
            "--repeats",
            "1",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    artifact = json.loads(out.read_text())
    assert artifact["impl"] == "rust"
    assert artifact["daemon"]["impl"] == "rust"
    assert artifact["daemon"]["rss"]["peak_kb"] > 0
    assert len(artifact["tools"]) == 26
    assert all(not entry["errors"] for entry in artifact["tools"])
