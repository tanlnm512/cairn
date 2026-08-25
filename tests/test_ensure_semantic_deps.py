"""Tests for ensure_semantic_deps' install + verification flow.

Regression guard for the silent-hang bug: after the pip subprocess finished,
the verification `import sentence_transformers` used to run IN-PROCESS with no
output. The first import of a fresh install takes 30s+ (dyld validates ~150
new .so files), so `cairn embed --install-deps` sat silent for that entire
window and looked hung. The verification now runs in a fresh subprocess under
a progress bar, so these tests pin that contract:

* the verification import happens in a CHILD interpreter (never in-process),
  with the shared lib dir first on its PYTHONPATH;
* a failing child import surfaces the child's error and returns False;
* the user sees a "Verifying install" line before the silent window starts.

sentence_transformers is absent from the test venv by design, which is what
drives ensure_semantic_deps into the install path at all. The subprocess is
faked at the _run_subprocess_with_progress seam (not by patching
subprocess.Popen globally -- the mcp package evaluates `subprocess.Popen[...]`
annotations at import time and explodes under a patched Popen).
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from cairn.graph import embeddings


@pytest.fixture(autouse=True)
def _isolated_from_real_lib(monkeypatch):
    """Hide any real shared lib dir from this test.

    cairn.paths injects ~/.cairn/lib into sys.path at import time (when it
    exists), which would make ensure_semantic_deps' in-process probe import
    succeed and skip the install path entirely -- exactly what happened on
    dev machines while debugging this feature. Strip those entries and any
    cached sentence_transformers modules for the duration of the test.
    """
    for name in [
        m
        for m in sys.modules
        if m == "sentence_transformers" or m.startswith("sentence_transformers.")
    ]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    before_path = list(sys.path)
    sys.path[:] = [p for p in sys.path if ".cairn/lib" not in p]
    yield
    sys.path[:] = before_path
    embeddings.reset_backend_cache()


@pytest.fixture
def isolated_lib(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    lib.mkdir()
    monkeypatch.setenv("CAIRN_LIB", str(lib))
    return lib


@pytest.fixture
def fake_install(monkeypatch):
    """Skip the real pip install; record the command instead."""
    calls = []

    def _install(cmd, lib_dir):
        calls.append(cmd)

    monkeypatch.setattr(embeddings, "_run_install_with_progress", _install)
    return calls


def test_verification_runs_in_fresh_subprocess(
    isolated_lib, fake_install, monkeypatch, capsys
):
    seen = {}

    def fake_run(cmd, description, env=None):
        seen["cmd"] = cmd
        seen["env"] = env
        return ""

    monkeypatch.setattr(embeddings, "_run_subprocess_with_progress", fake_run)

    assert embeddings.ensure_semantic_deps(auto_install=True) is True

    # The pip install ran with the expected target...
    assert fake_install and "--target" in fake_install[0]
    # ...and the verification import ran in a CHILD interpreter with the lib
    # dir first on PYTHONPATH, not in this process.
    assert seen["cmd"] == [
        sys.executable,
        "-c",
        "import sentence_transformers, numpy, sqlite_vec",
    ]
    pythonpath = (seen["env"] or {}).get("PYTHONPATH", "")
    assert pythonpath.split(":")[0] == str(isolated_lib)
    # The user is told a silent window is coming.
    assert "Verifying install" in capsys.readouterr().out


def test_verification_failure_returns_false_with_child_error(
    isolated_lib, fake_install, monkeypatch, capsys
):
    child_err = (
        "Traceback (most recent call last):\n"
        "ModuleNotFoundError: No module named 'sentence_transformers'\n"
    )

    def fake_run(cmd, description, env=None):
        # Mirror what the real helper does on failure: show the child's
        # captured output, then raise.
        print(child_err)
        raise subprocess.CalledProcessError(1, cmd, child_err)

    monkeypatch.setattr(embeddings, "_run_subprocess_with_progress", fake_run)

    assert embeddings.ensure_semantic_deps(auto_install=True) is False

    out = capsys.readouterr().out
    assert "Failed to auto-install" in out
    # The child interpreter's actual error is surfaced, not swallowed.
    assert "No module named 'sentence_transformers'" in out
