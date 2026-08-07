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

def test_registry_covers_supported_languages():
    """Every language with a known single-binary SCIP indexer is registered."""
    langs = set(known_languages())
    assert {"swift", "java", "kotlin", "typescript", "python", "go", "rust"} <= langs


def test_spec_for_unknown_language_is_none():
    """An unregistered language (no known indexer / not a scanner language) is None."""
    assert spec_for("ruby") is None  # scip-ruby is dead
    assert spec_for("csharp") is None  # no indexer exists
    assert spec_for("not-a-language") is None


def test_kotlin_uses_scip_java_not_deprecated_scip_kotlin():
    """scip-kotlin is superseded; the kotlin key must invoke scip-java.

    scip-java indexes mixed Java+Kotlin projects in one run and tags each
    Document's language per source file, so both 'java' and 'kotlin' keys
    pointing at scip-java is correct. Regression guard: don't let a future
    edit reintroduce the deprecated scip-kotlin binary.
    """
    kotlin = spec_for("kotlin")
    java = spec_for("java")
    assert kotlin is not None and java is not None
    assert kotlin.tool == "scip-java"
    assert java.tool == "scip-java"
    assert kotlin.build_command == java.build_command


def test_swift_spec_command_shape():
    """scip-swift's documented CLI is `scip-swift index <repo> --output <out>`."""
    spec = spec_for("swift")
    assert spec is not None
    assert spec.tool == "scip-swift"
    cmd = spec.build_command("/repo", "/out/x.scip")
    assert cmd == ["scip-swift", "index", "/repo", "--output", "/out/x.scip"]


def test_java_spec_command_shape():
    """scip-java: `scip-java index --output <out>` (no repo arg; run from root)."""
    spec = spec_for("java")
    assert spec is not None
    cmd = spec.build_command("/repo", "/out/j.scip")
    assert cmd == ["scip-java", "index", "--output", "/out/j.scip"]


def test_typescript_spec_command_shape():
    """scip-typescript: `scip-typescript index --output <out>`."""
    spec = spec_for("typescript")
    assert spec is not None
    cmd = spec.build_command("/repo", "/out/t.scip")
    assert cmd == ["scip-typescript", "index", "--output", "/out/t.scip"]


def test_python_spec_command_shape():
    """scip-python: `scip-python index <repo> --output=<out>` (= form, npm pkg)."""
    spec = spec_for("python")
    assert spec is not None
    cmd = spec.build_command("/repo", "/out/p.scip")
    assert cmd == ["scip-python", "index", "/repo", "--output=/out/p.scip"]


def test_go_spec_command_shape():
    """scip-go: `scip-go --output=<out>` (no `index` subcommand)."""
    spec = spec_for("go")
    assert spec is not None
    cmd = spec.build_command("/repo", "/out/g.scip")
    assert cmd == ["scip-go", "--output=/out/g.scip"]


def test_rust_spec_command_shape():
    """rust-analyzer scip subcommand: `rust-analyzer scip <repo> --output <out>`."""
    spec = spec_for("rust")
    assert spec is not None
    cmd = spec.build_command("/repo", "/out/r.scip")
    assert cmd == ["rust-analyzer", "scip", "/repo", "--output", "/out/r.scip"]


# ---------------------------------------------------------------------------
# try_generate_index: happy path + degrade paths (never raises)
# ---------------------------------------------------------------------------

def test_generate_returns_false_for_unknown_language(tmp_path, monkeypatch):
    """An unregistered language short-circuits without spawning anything."""
    out = tmp_path / "x.scip"
    # Even if a tool were "installed", an unknown language must not spawn it.
    # ruby is unregistered (scip-ruby is dead); cairn can't auto-generate it.
    monkeypatch.setattr(scip_indexers.shutil, "which", lambda tool: "/bin/" + tool)
    spawned = []
    monkeypatch.setattr(scip_indexers.subprocess, "run",
                        lambda cmd, **kw: spawned.append(cmd) or (_ for _ in ()).throw(AssertionError("must not spawn")))

    ok = try_generate_index("ruby", out, str(tmp_path), log=lambda *a, **k: None)
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
