"""claude_hooks contract tests: module-invocation fallback, stdin hardening,
and redact-before-truncate privacy ordering."""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

import cairn
from cairn.hooks import claude_hooks


# --------------------------------------------------------------------------
# C77: `python -m cairn.cli.main` is the hooks' not-on-PATH fallback and must
# actually run the CLI (it needs the __main__ guard in cli/main.py).
# --------------------------------------------------------------------------

def test_python_dash_m_runs_the_cli():
    proc = subprocess.run(
        [sys.executable, "-m", "cairn.cli.main", "--help"],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip(), "module invocation must print the CLI help"


def test_cli_package_dash_m_runs_the_cli():
    proc = subprocess.run(
        [sys.executable, "-m", "cairn.cli", "--help"],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip(), "package invocation must print the CLI help"
    assert "RuntimeWarning" not in proc.stderr


def test_fallback_resolvers_use_cli_package(monkeypatch):
    from cairn.agent_install import _common

    impact_guard = _load_impact_guard()
    monkeypatch.setattr("shutil.which", lambda _name: None)
    expected = [sys.executable, "-m", "cairn.cli"]

    assert claude_hooks._cg_command() == expected
    assert _common.resolve_cg_command() == expected
    assert impact_guard._cg_command() == expected


def test_run_cg_reports_failed_subprocess(monkeypatch):
    """Return a clear error string when cairn exits non-zero."""

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(["cairn"], 1, stdout="", stderr="boom")

    monkeypatch.setattr(claude_hooks.subprocess, "run", fake_run)
    monkeypatch.setattr(claude_hooks, "_cg_command", lambda: ["cairn"])

    assert claude_hooks._run_cg(["update"]) == "error: cairn exited 1: boom"


def _load_impact_guard():
    script = (Path(cairn.__file__).parent / "agent_integration" / "skill"
              / "scripts" / "impact_guard.py")
    spec = importlib.util.spec_from_file_location("impact_guard_under_test", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_impact_guard_exits_cleanly_on_empty_stdout(monkeypatch):
    """Empty subprocess stdout (the pre-C77 fallback no-op) must exit 1, not
    crash with JSONDecodeError."""
    mod = _load_impact_guard()

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(mod, "run_cg", lambda args: _R())
    monkeypatch.setattr(sys, "argv", ["impact_guard.py", "SomeSymbol"])

    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 1


# --------------------------------------------------------------------------
# C67: non-object stdin JSON (null/list/str) must degrade to {} so the
# hooks' never-raises contract holds.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("payload", ["null", "[]", '"text"', "42"])
def test_read_stdin_non_object_yields_empty_dict(monkeypatch, payload):
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    assert claude_hooks._read_stdin() == {}


def test_session_end_tolerates_null_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("null"))
    claude_hooks.session_end()
    assert "(no transcript" in capsys.readouterr().err


def test_post_tool_failure_tolerates_list_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("[]"))
    claude_hooks.post_tool_failure()


# --------------------------------------------------------------------------
# C66: payloads must be redacted BEFORE truncation — truncating first can
# split a secret so the patterns match nothing and the fragment is stored
# unredacted.
# --------------------------------------------------------------------------

def test_post_tool_failure_redacts_before_truncating(monkeypatch):
    secret = "sk-ant-" + "a" * 30
    error = "x" * 3990 + secret  # truncation at 4000 would split the token
    payload = json.dumps({"tool_name": "Bash", "error": error})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))

    captured: list[list[str]] = []

    class _FakePopen:
        def __init__(self, cmd, *args, **kwargs):
            captured.append(cmd)

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    claude_hooks.post_tool_failure()

    assert captured, "the capture subprocess must be invoked"
    body = captured[0][captured[0].index("--body") + 1]
    assert "sk-ant" not in body, "a split secret must not survive unredacted"
