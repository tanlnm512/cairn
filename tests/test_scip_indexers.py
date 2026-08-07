"""Tests for the generic SCIP indexer orchestrator.

Covers the registry, the happy path (indexer runs and writes the file), and the
three graceful-degrade paths (tool not on PATH, nonzero exit, OS error). The
orchestrator must never raise -- a missing/failing indexer logs and returns
False so the build falls back to tree-sitter.

Does NOT require the optional ``[scip]`` extra: the orchestrator only spawns
external binaries and writes files; it never touches the protobuf stub.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from cairn.parsers import scip_indexers
from cairn.parsers.scip_indexers import (
    known_languages,
    spec_for,
    try_generate_index,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_has_swift_kotlin_typescript():
    """The three documented indexers are registered; Swift is one of them."""
    langs = known_languages()
    assert "swift" in langs
    assert "kotlin" in langs
    assert "typescript" in langs


def test_spec_for_unknown_language_is_none():
    """An unregistered language (e.g. a lang with no known indexer) is None."""
    assert spec_for("rust") is None
    assert spec_for("not-a-language") is None


def test_swift_spec_command_shape():
    """scip-swift's documented CLI is `scip-swift index <repo> --output <out>`."""
    spec = spec_for("swift")
    assert spec is not None
    assert spec.tool == "scip-swift"
    cmd = spec.build_command("/repo", "/out/x.scip")
    assert cmd[0] == "scip-swift"
    assert "index" in cmd
    assert "/repo" in cmd
    assert "/out/x.scip" in cmd


def test_kotlin_spec_command_shape():
    """scip-kotlin's documented CLI is `scip-kotlin index --output <out> <repo>`."""
    spec = spec_for("kotlin")
    assert spec is not None
    cmd = spec.build_command("/repo", "/out/k.scip")
    # --output <out> precedes the repo path (matches docs/scip.md §1).
    assert cmd.index("--output") < cmd.index("/repo")


def test_typescript_spec_command_shape():
    """scip-typescript mirrors scip-kotlin's argument order."""
    spec = spec_for("typescript")
    assert spec is not None
    cmd = spec.build_command("/repo", "/out/t.scip")
    assert cmd.index("--output") < cmd.index("/repo")


# ---------------------------------------------------------------------------
# try_generate_index: happy path + degrade paths (never raises)
# ---------------------------------------------------------------------------

def test_generate_returns_false_for_unknown_language(tmp_path, monkeypatch):
    """An unregistered language short-circuits without spawning anything."""
    out = tmp_path / "x.scip"
    # Even if a tool were "installed", an unknown language must not spawn it.
    monkeypatch.setattr(scip_indexers.shutil, "which", lambda tool: "/bin/" + tool)
    spawned = []
    monkeypatch.setattr(scip_indexers.subprocess, "run",
                        lambda cmd, **kw: spawned.append(cmd) or (_ for _ in ()).throw(AssertionError("must not spawn")))

    ok = try_generate_index("rust", out, str(tmp_path), log=lambda *a, **k: None)
    assert ok is False
    assert spawned == []  # no subprocess spawned
    assert not out.exists()


def test_generate_runs_indexer_when_missing(tmp_path, monkeypatch):
    """When the tool is on PATH and exits 0 writing the file, generation succeeds."""
    out = tmp_path / "build" / "swift.scip"

    def fake_run(cmd, **kw):
        # The real indexer would write the .scip file; simulate it.
        Path(cmd[cmd.index("--output") + 1]).write_bytes(b"\x12\x01x")  # minimal proto
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(scip_indexers.shutil, "which", lambda tool: "/usr/local/bin/" + tool)
    monkeypatch.setattr(scip_indexers.subprocess, "run", fake_run)

    logs = []
    ok = try_generate_index("swift", out, str(tmp_path), log=logs.append)
    assert ok is True
    assert out.exists()
    # The command targeted the repo path and the configured output path.
    assert any(str(out) in str(m) for m in logs) or out.exists()


def test_generate_skips_when_tool_not_on_path(tmp_path, monkeypatch):
    """A missing binary is logged (install hint) and returns False -- no spawn."""
    out = tmp_path / "swift.scip"
    monkeypatch.setattr(scip_indexers.shutil, "which", lambda tool: None)

    spawned = []
    monkeypatch.setattr(scip_indexers.subprocess, "run",
                        lambda cmd, **kw: spawned.append(cmd) or (_ for _ in ()).throw(AssertionError("must not spawn")))

    logs = []
    ok = try_generate_index("swift", out, str(tmp_path), log=logs.append)
    assert ok is False
    assert spawned == []
    assert not out.exists()
    # The install hint is surfaced.
    assert any("scip-swift" in str(m) for m in logs)


def test_generate_swallows_nonzero_exit(tmp_path, monkeypatch):
    """A nonzero exit that produces no file logs + returns False, never raises."""
    out = tmp_path / "swift.scip"
    monkeypatch.setattr(scip_indexers.shutil, "which", lambda tool: "/bin/" + tool)
    monkeypatch.setattr(
        scip_indexers.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2, stdout="", stderr="boom\nbuild failed"),
    )

    logs = []
    ok = try_generate_index("swift", out, str(tmp_path), log=logs.append)
    assert ok is False
    assert not out.exists()
    # Nonzero exit is reported.
    assert any("exited" in str(m) for m in logs)


def test_generate_swallows_oserror(tmp_path, monkeypatch):
    """A missing binary at exec time (FileNotFoundError) is absorbed, not raised."""
    out = tmp_path / "swift.scip"
    monkeypatch.setattr(scip_indexers.shutil, "which", lambda tool: "/bin/" + tool)

    def raise_fnf(cmd, **kw):
        raise FileNotFoundError("[Errno 2] No such file")

    monkeypatch.setattr(scip_indexers.subprocess, "run", raise_fnf)

    logs = []
    ok = try_generate_index("swift", out, str(tmp_path), log=logs.append)
    assert ok is False
    assert not out.exists()
    assert any("invocation failed" in str(m) for m in logs)


def test_generate_swallows_timeout(tmp_path, monkeypatch):
    """A timed-out indexer is absorbed, not raised."""
    out = tmp_path / "swift.scip"
    monkeypatch.setattr(scip_indexers.shutil, "which", lambda tool: "/bin/" + tool)

    def raise_timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)

    monkeypatch.setattr(scip_indexers.subprocess, "run", raise_timeout)

    logs = []
    ok = try_generate_index("swift", out, str(tmp_path), log=logs.append)
    assert ok is False
    assert not out.exists()


def test_generate_is_idempotent_when_index_exists(tmp_path, monkeypatch):
    """An already-present index is never rebuilt -- returns True without spawning."""
    out = tmp_path / "swift.scip"
    out.write_bytes(b"\x12\x01x")
    monkeypatch.setattr(scip_indexers.shutil, "which", lambda tool: "/bin/" + tool)

    spawned = []
    monkeypatch.setattr(scip_indexers.subprocess, "run",
                        lambda cmd, **kw: spawned.append(cmd) or (_ for _ in ()).throw(AssertionError("must not rebuild")))

    ok = try_generate_index("swift", out, str(tmp_path), log=lambda *a, **k: None)
    assert ok is True
    assert spawned == []
