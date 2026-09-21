"""Registry and never-raise generation contract of the SCIP indexer orchestrator."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from cairn.parsers import scip_indexers
from cairn.parsers.scip_indexers import (
    _INDEX_TIMEOUT_S,
    GEN_MISSING_BINARY,
    GEN_NONZERO_EXIT,
    GEN_OS_ERROR,
    GEN_TIMEOUT,
    GenerationResult,
    generate_index_result,
    known_languages,
    spec_for,
    try_generate_index,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "scip-indexing"
_AUTOGEN = _FIXTURES / "autogen"

SEVEN_LANGUAGES = {"swift", "java", "kotlin", "typescript", "python", "go", "rust"}


def _forbid_spawn(monkeypatch):
    """Make any subprocess.run call fail the test."""
    def boom(cmd, **kw):
        raise AssertionError(f"must not spawn {cmd}")

    monkeypatch.setattr(scip_indexers.subprocess, "run", boom)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_is_exactly_the_seven_languages():
    assert set(known_languages()) == SEVEN_LANGUAGES


def test_spec_for_unknown_language_is_none():
    assert spec_for("ruby") is None
    assert spec_for("csharp") is None
    assert spec_for("not-a-language") is None


def test_every_spec_carries_install_hint():
    for lang in known_languages():
        assert spec_for(lang).install_hint


def test_kotlin_routes_through_scip_java():
    kotlin = spec_for("kotlin")
    java = spec_for("java")
    assert kotlin.tool == "scip-java"
    assert java.tool == "scip-java"
    assert kotlin.build_command == java.build_command


def test_swift_spec_command_shape():
    cmd = spec_for("swift").build_command("/repo", "/out/x.scip")
    assert cmd == ["scip-swift", "index", "/repo", "--output", "/out/x.scip"]


def test_java_spec_command_shape():
    cmd = spec_for("java").build_command("/repo", "/out/j.scip")
    assert cmd == ["scip-java", "index", "--output", "/out/j.scip"]


def test_typescript_spec_command_shape():
    cmd = spec_for("typescript").build_command("/repo", "/out/t.scip")
    assert cmd == ["scip-typescript", "index", "--output", "/out/t.scip"]


def test_python_spec_command_shape():
    cmd = spec_for("python").build_command("/repo", "/out/p.scip")
    assert cmd == ["scip-python", "index", "/repo", "--output=/out/p.scip"]


def test_go_spec_command_shape():
    cmd = spec_for("go").build_command("/repo", "/out/g.scip")
    assert cmd == ["scip-go", "--output=/out/g.scip"]


def test_rust_spec_uses_rascipout_env_not_a_flag():
    spec = spec_for("rust")
    cmd = spec.build_command("/repo", "/out/r.scip")
    assert cmd == ["rust-analyzer", "scip", "/repo"]
    assert not any("--output" in a for a in cmd)
    assert spec.env("/out/r.scip") == {"RA_SCIPOUT": "/out/r.scip"}


def test_non_rust_specs_carry_no_env():
    for lang in SEVEN_LANGUAGES - {"rust"}:
        assert spec_for(lang).env("/out/x.scip") == {}


# ---------------------------------------------------------------------------
# try_generate_index: happy path + degrade paths (never raises)
# ---------------------------------------------------------------------------

def test_generate_returns_false_for_unknown_language(tmp_path, monkeypatch):
    monkeypatch.setattr(scip_indexers.shutil, "which", lambda tool: "/bin/" + tool)
    _forbid_spawn(monkeypatch)
    out = tmp_path / "x.scip"
    assert try_generate_index("ruby", out, str(tmp_path)) is False
    assert not out.exists()


def test_generate_runs_indexer_when_missing(tmp_path, monkeypatch):
    def fake_run(cmd, **kw):
        Path(cmd[cmd.index("--output") + 1]).write_bytes(b"\x12\x01x")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(scip_indexers.shutil, "which", lambda tool: "/bin/" + tool)
    monkeypatch.setattr(scip_indexers.subprocess, "run", fake_run)
    out = tmp_path / "build" / "swift.scip"
    assert try_generate_index("swift", out, str(tmp_path)) is True
    assert out.exists()


def test_generate_is_idempotent_when_index_exists(tmp_path, monkeypatch):
    _forbid_spawn(monkeypatch)
    out = tmp_path / "swift.scip"
    out.write_bytes(b"\x12\x01x")
    assert try_generate_index("swift", out, str(tmp_path)) is True


def test_generate_skips_when_tool_not_on_path(tmp_path, monkeypatch):
    _forbid_spawn(monkeypatch)
    monkeypatch.setattr(scip_indexers.shutil, "which", lambda tool: None)
    out = tmp_path / "swift.scip"
    logs = []
    assert try_generate_index("swift", out, str(tmp_path), log=logs.append) is False
    assert not out.exists()
    assert any("scip-swift" in str(m) for m in logs)  # install hint surfaced


def test_generate_swallows_nonzero_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(scip_indexers.shutil, "which", lambda tool: "/bin/" + tool)
    monkeypatch.setattr(
        scip_indexers.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2, stdout="", stderr="boom"),
    )
    out = tmp_path / "swift.scip"
    logs = []
    assert try_generate_index("swift", out, str(tmp_path), log=logs.append) is False
    assert not out.exists()
    assert any("exited" in str(m) for m in logs)


def test_generate_swallows_oserror(tmp_path, monkeypatch):
    monkeypatch.setattr(scip_indexers.shutil, "which", lambda tool: "/bin/" + tool)

    def raise_fnf(cmd, **kw):
        raise FileNotFoundError("[Errno 2] No such file")

    monkeypatch.setattr(scip_indexers.subprocess, "run", raise_fnf)
    out = tmp_path / "swift.scip"
    logs = []
    assert try_generate_index("swift", out, str(tmp_path), log=logs.append) is False
    assert not out.exists()
    assert any("invocation failed" in str(m) for m in logs)


def test_generate_swallows_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(scip_indexers.shutil, "which", lambda tool: "/bin/" + tool)

    def raise_timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)

    monkeypatch.setattr(scip_indexers.subprocess, "run", raise_timeout)
    out = tmp_path / "swift.scip"
    assert try_generate_index("swift", out, str(tmp_path)) is False
    assert not out.exists()


def test_subprocess_is_bounded_by_the_recorded_timeout(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(scip_indexers.shutil, "which", lambda tool: "/bin/" + tool)
    monkeypatch.setattr(scip_indexers.subprocess, "run", fake_run)
    assert _INDEX_TIMEOUT_S == 30 * 60
    try_generate_index("swift", tmp_path / "swift.scip", str(tmp_path))
    assert seen.get("timeout") == _INDEX_TIMEOUT_S


def test_spec_env_is_merged_with_the_process_env(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        Path(cmd[cmd.index("--output") + 1]).write_bytes(b"\x12\x01x")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(scip_indexers.shutil, "which", lambda tool: "/bin/" + tool)
    monkeypatch.setattr(scip_indexers.subprocess, "run", fake_run)
    out = tmp_path / "build" / "r.scip"
    assert try_generate_index("swift", out, str(tmp_path)) is True
    # env vars are additive: PATH must survive so the binary stays findable.
    assert seen["env"]["PATH"] == os.environ["PATH"]


def test_rust_env_reaches_the_subprocess(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        out = kw["env"]["RA_SCIPOUT"]
        Path(out).write_bytes(b"\x12\x01x")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(scip_indexers.shutil, "which", lambda tool: "/bin/" + tool)
    monkeypatch.setattr(scip_indexers.subprocess, "run", fake_run)
    out = tmp_path / "build" / "rust.scip"
    assert try_generate_index("rust", out, str(tmp_path)) is True
    assert out.exists()


# ---------------------------------------------------------------------------
# Structured outcomes: scip_gen_* skip reasons + per-entry isolation
# ---------------------------------------------------------------------------

def _stub_which(monkeypatch, missing=()):
    monkeypatch.setattr(
        scip_indexers.shutil, "which",
        lambda tool: None if tool in missing else "/bin/" + tool,
    )


def _stub_run(monkeypatch, behavior):
    monkeypatch.setattr(scip_indexers.subprocess, "run", behavior)


def _writer_run(cmd, **kw):
    Path(cmd[cmd.index("--output") + 1]).write_bytes(b"\x12\x01x")
    return subprocess.CompletedProcess(cmd, 0)


def test_reason_constants_carry_the_skip_surface_vocabulary():
    assert GEN_MISSING_BINARY == "scip_gen_missing_binary"
    assert GEN_NONZERO_EXIT == "scip_gen_nonzero_exit"
    assert GEN_TIMEOUT == "scip_gen_timeout"
    assert GEN_OS_ERROR == "scip_gen_os_error"


def test_missing_binary_maps_to_scip_gen_missing_binary(tmp_path, monkeypatch):
    _stub_which(monkeypatch, missing={"scip-swift"})
    _forbid_spawn(monkeypatch)
    out = tmp_path / "swift.scip"
    result = generate_index_result("swift", out, str(tmp_path))
    assert result.ok is False
    assert result.reason == GEN_MISSING_BINARY
    assert not out.exists()


def test_nonzero_exit_maps_to_scip_gen_nonzero_exit(tmp_path, monkeypatch):
    _stub_which(monkeypatch)
    _stub_run(monkeypatch, lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2, stdout="", stderr="boom"))
    result = generate_index_result("swift", tmp_path / "swift.scip", str(tmp_path))
    assert result.ok is False
    assert result.reason == GEN_NONZERO_EXIT


def test_exit_zero_without_an_index_maps_to_scip_gen_nonzero_exit(tmp_path, monkeypatch):
    _stub_which(monkeypatch)
    _stub_run(monkeypatch, lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""))
    result = generate_index_result("swift", tmp_path / "swift.scip", str(tmp_path))
    assert result.ok is False
    assert result.reason == GEN_NONZERO_EXIT


def test_timeout_maps_to_scip_gen_timeout(tmp_path, monkeypatch):
    _stub_which(monkeypatch)

    def raise_timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)

    _stub_run(monkeypatch, raise_timeout)
    result = generate_index_result("swift", tmp_path / "swift.scip", str(tmp_path))
    assert result.ok is False
    assert result.reason == GEN_TIMEOUT


@pytest.mark.parametrize("mode", ["file_not_found", "subprocess_error"])
def test_os_level_failures_map_to_scip_gen_os_error(mode, tmp_path, monkeypatch):
    _stub_which(monkeypatch)

    def raise_err(cmd, **kw):
        if mode == "file_not_found":
            raise FileNotFoundError("[Errno 2] No such file")
        raise subprocess.SubprocessError("broken pipe")

    _stub_run(monkeypatch, raise_err)
    result = generate_index_result("swift", tmp_path / "swift.scip", str(tmp_path))
    assert result.ok is False
    assert result.reason == GEN_OS_ERROR


@pytest.mark.parametrize("mode", ["missing_binary", "nonzero_exit", "timeout", "os_error"])
def test_install_hint_surfaces_for_every_failure_mode(mode, tmp_path, monkeypatch):
    hint = spec_for("swift").install_hint
    _stub_which(monkeypatch, missing={"scip-swift"} if mode == "missing_binary" else set())
    if mode == "nonzero_exit":
        _stub_run(monkeypatch, lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr=""))
    elif mode == "timeout":
        _stub_run(monkeypatch, lambda cmd, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd=cmd, timeout=1)))
    elif mode == "os_error":
        _stub_run(monkeypatch, lambda cmd, **kw: (_ for _ in ()).throw(OSError("errno 5")))

    logs = []
    result = generate_index_result("swift", tmp_path / "swift.scip", str(tmp_path), log=logs.append)
    assert result.ok is False
    assert hint in result.detail
    assert any(hint in str(m) for m in logs)


def test_success_result_carries_no_reason(tmp_path, monkeypatch):
    _stub_which(monkeypatch)
    _stub_run(monkeypatch, _writer_run)
    result = generate_index_result("swift", tmp_path / "build" / "swift.scip", str(tmp_path))
    assert isinstance(result, GenerationResult)
    assert result.ok is True
    assert result.reason is None


def test_existing_index_result_is_ok_without_reason(tmp_path, monkeypatch):
    _forbid_spawn(monkeypatch)
    out = tmp_path / "swift.scip"
    out.write_bytes(b"\x12\x01x")
    result = generate_index_result("swift", out, str(tmp_path))
    assert result.ok is True
    assert result.reason is None


def test_unknown_language_has_no_gen_reason(tmp_path, monkeypatch):
    _forbid_spawn(monkeypatch)
    result = generate_index_result("ruby", tmp_path / "r.scip", str(tmp_path))
    assert result.ok is False
    assert result.reason is None
    assert not (tmp_path / "r.scip").exists()


def test_bool_wrapper_matches_the_structured_outcome(tmp_path, monkeypatch):
    _stub_which(monkeypatch)
    _stub_run(monkeypatch, lambda cmd, **kw: subprocess.CompletedProcess(cmd, 3, stdout="", stderr=""))
    failed = tmp_path / "a.scip"
    assert try_generate_index("swift", failed, str(tmp_path)) is False
    assert generate_index_result("swift", failed, str(tmp_path)).ok is False

    _stub_run(monkeypatch, _writer_run)
    generated = tmp_path / "b.scip"
    assert try_generate_index("swift", generated, str(tmp_path)) is True
    assert generate_index_result("swift", generated, str(tmp_path)).ok is True


def test_one_languages_failure_does_not_block_another(tmp_path, monkeypatch):
    _stub_which(monkeypatch, missing={"scip-swift"})
    _stub_run(monkeypatch, _writer_run)
    missed = generate_index_result("swift", tmp_path / "swift.scip", str(tmp_path))
    assert missed.reason == GEN_MISSING_BINARY
    generated = generate_index_result("java", tmp_path / "java.scip", str(tmp_path))
    assert generated.ok is True


def test_registry_entry_crash_degrades_without_raising(tmp_path, monkeypatch):
    hint = spec_for("swift").install_hint

    def crash(repo, out):
        raise RuntimeError("exploded")

    monkeypatch.setitem(
        scip_indexers._KNOWN_INDEXERS, "swift",
        replace(spec_for("swift"), build_command=crash),
    )
    _stub_which(monkeypatch)
    _stub_run(monkeypatch, _writer_run)

    logs = []
    crashed = generate_index_result("swift", tmp_path / "swift.scip", str(tmp_path), log=logs.append)
    assert crashed.ok is False
    assert crashed.reason == GEN_OS_ERROR
    assert any(hint in str(m) for m in logs)

    sibling = generate_index_result("java", tmp_path / "java.scip", str(tmp_path))
    assert sibling.ok is True


# ---------------------------------------------------------------------------
# Fixture stubs (real subprocesses, no [scip] extra needed)
# ---------------------------------------------------------------------------

def test_autogen_stub_generates_the_committed_index(tmp_path, monkeypatch):
    bin_dir = _AUTOGEN / "bin"
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    calls_log = bin_dir / "calls.log"
    calls_log.unlink(missing_ok=True)
    try:
        out = tmp_path / "index.scip"
        assert try_generate_index("python", out, str(_AUTOGEN)) is True
        assert out.read_bytes() == (_AUTOGEN / "committed-index.scip").read_bytes()
        assert calls_log.exists() and len(calls_log.read_text().splitlines()) == 1
        # an existing index is never rebuilt: no second invocation
        assert try_generate_index("python", out, str(_AUTOGEN)) is True
        assert len(calls_log.read_text().splitlines()) == 1
    finally:
        calls_log.unlink(missing_ok=True)


def test_autogen_failing_stub_degrades_to_false(tmp_path, monkeypatch):
    bin_dir = _AUTOGEN / "bin-fail"
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    out = tmp_path / "index.scip"
    logs = []
    assert try_generate_index("python", out, str(_AUTOGEN), log=logs.append) is False
    assert not out.exists()
    assert any("exited" in str(m) for m in logs)


def test_seven_langs_fixture_configures_exactly_the_registry():
    cfg = json.loads((_FIXTURES / "seven-langs" / "cairn.json").read_text())
    indexes = cfg["scip"]["indexes"]
    assert set(indexes) == SEVEN_LANGUAGES
    for lang, rel in indexes.items():
        assert not (_FIXTURES / "seven-langs" / rel).exists(), lang
