"""Install/uninstall fidelity tests (audit wave C1: findings F1-F8).

Scope of this module:
- F1: cursor hooks.json idempotency (flat entry shape) + partial-hook re-heal
  (both entrypoints required) + claude nested shape stays idempotent.
- F2: global-scope installs are actually uninstalled (scope plumbed through
  uninstall; `claude mcp remove --scope user` / `droid mcp remove` invoked).
- F3: dry-run reports match a real run (no phantom "would write" for files
  that a real run would skip).
- F4: cross-tool uninstall removes the whole .agents/skills/cairn tree.
- F5: uninstall only removes files the installer actually wrote.
- F6: `mcp`/`mcpServers`/`hooks` holding non-object values are backed up,
  not crashed on.
- F7: workspaces.json registry rewrite is atomic.
- F8: opencode honors --scope (global lands in ~/.config/opencode/).

Safety: every global-scope test monkeypatches Path.home to a throwaway dir
and every subprocess-spawning CLI (claude/droid) is mocked -- the real
~/.claude, ~/.cursor, ~/.zcode are never touched and no real `mcp add/remove`
ever executes.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from cairn.agent_install import check_installed, install, uninstall
from cairn.agent_install._common import InstallResult


# --------------------------------------------------------------------------
# Helpers / fixtures
# --------------------------------------------------------------------------

@pytest.fixture
def fake_home(tmp_path, monkeypatch) -> Path:
    """Throwaway home dir: Path.home() resolves here for the test's duration."""
    home = tmp_path / "fake_home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda *a, **k: home)
    return home


def _no_cli(monkeypatch, *names):
    """Pretend the named CLIs are absent (blocks real subprocess paths)."""
    real_which = shutil.which

    def fake(cmd, path=None):
        return None if cmd in names else real_which(cmd)

    monkeypatch.setattr(shutil, "which", fake)


def _cli_at(monkeypatch, name, binpath="/fake/bin"):
    """Pretend exactly one named CLI exists at a fake path."""
    monkeypatch.setattr(shutil, "which",
                        lambda cmd, path=None: f"{binpath}/{cmd}" if cmd == name else None)


def _spy_subprocess(monkeypatch):
    """Record every subprocess.run argv; return the recording list."""
    calls: list[list[str]] = []

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, *a, **k):
        calls.append(list(cmd))
        return _R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _repoint_home_bindings(monkeypatch, home: Path) -> None:
    """Re-point paths.py's import-time CAIRN_HOME bindings into the sandbox.

    Same pit test_config_probe.py fixes for the probe: paths.py binds
    CAIRN_HOME / REGISTRY_FILE from os.environ at import time (under pytest,
    collection time), while resolve_store and _load_registry read the globals
    at call time. Without the re-point an in-process install-time comparison
    would use the collector's real ~/.cairn instead of the test's custom home.
    """
    from cairn import paths

    monkeypatch.setattr(paths, "CAIRN_HOME", home)
    monkeypatch.setattr(paths, "REGISTRY_FILE", home / "workspaces.json")


def _shim_on_path(tmp_path: Path, monkeypatch, name: str, body: str) -> Path:
    """Install an executable shim script called `name` first on PATH.

    Real-subprocess shadowing (no subprocess patching, C-04): anything that
    resolves `name` off PATH -- the installer's resolve_cg_command, a spawned
    probe -- runs the shim. Returns the shim path.
    """
    shim_dir = tmp_path / f"_shim_{name}"
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / name
    shim.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return shim


# --------------------------------------------------------------------------
# F1: cursor hooks idempotency + both-entrypoints requirement
# --------------------------------------------------------------------------

class TestHookIdempotency:
    def test_cursor_reinstall_does_not_duplicate(self, tmp_path, monkeypatch):
        """Cursor's flat hook entries must be recognized as already-present:
        three installs leave exactly one entry per event (each duplicate would
        spawn an extra python process per editor event)."""
        _no_cli(monkeypatch, "cursor")
        ws = tmp_path / "ws"
        ws.mkdir()
        for _ in range(3):
            install(str(ws), clients=["cursor"], transport="stdio")
        data = json.loads((ws / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
        assert len(data["hooks"]["afterFileEdit"]) == 1
        assert len(data["hooks"]["afterSessionEnd"]) == 1

    def test_cursor_partial_hooks_reheal(self, tmp_path, monkeypatch):
        """One entrypoint stripped -> config reads as absent -> reinstall
        re-heals it without duplicating the surviving entry."""
        _no_cli(monkeypatch, "cursor")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["cursor"], transport="stdio")
        p = ws / ".cursor" / "hooks.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        del data["hooks"]["afterSessionEnd"]
        p.write_text(json.dumps(data))
        install(str(ws), clients=["cursor"], transport="stdio")
        healed = json.loads(p.read_text(encoding="utf-8"))
        assert len(healed["hooks"]["afterSessionEnd"]) == 1
        assert len(healed["hooks"]["afterFileEdit"]) == 1

    def test_claude_nested_shape_still_idempotent(self, tmp_path, monkeypatch):
        _no_cli(monkeypatch, "claude")
        ws = tmp_path / "ws"
        ws.mkdir()
        for _ in range(2):
            install(str(ws), clients=["claude"], transport="stdio")
        data = json.loads((ws / ".claude" / "settings.json").read_text(encoding="utf-8"))
        assert len(data["hooks"]["PostToolUse"]) == 1
        assert len(data["hooks"]["Stop"]) == 1

    def test_claude_partial_hooks_reheal(self, tmp_path, monkeypatch):
        """The old ANY-present check made a partial config read as installed;
        now BOTH entrypoints are required so the reinstall heals it."""
        _no_cli(monkeypatch, "claude")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude"], transport="stdio")
        p = ws / ".claude" / "settings.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["hooks"]["Stop"] = []
        p.write_text(json.dumps(data))
        install(str(ws), clients=["claude"], transport="stdio")
        healed = json.loads(p.read_text(encoding="utf-8"))
        assert len(healed["hooks"]["Stop"]) == 1
        assert len(healed["hooks"]["PostToolUse"]) == 1

    def test_user_hooks_survive_install_and_uninstall(self, tmp_path, monkeypatch):
        _no_cli(monkeypatch, "claude")
        ws = tmp_path / "ws"
        ws.mkdir()
        settings = ws / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "hooks": {"UserEvent": [{"hooks": [{"command": "echo user"}]}]},
        }))
        install(str(ws), clients=["claude"], transport="stdio")
        uninstall(str(ws), clients=["claude"])
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["hooks"]["UserEvent"][0]["hooks"][0]["command"] == "echo user"
        assert "PostToolUse" not in data["hooks"] and "Stop" not in data["hooks"]

    def test_already_installed_requires_both_entrypoints(self):
        from cairn.agent_install.clients.claude import claude_hooks_block
        from cairn.agent_install.merge import _already_installed

        merger = {"hooks": claude_hooks_block()}
        flat_one = {"hooks": {
            "afterFileEdit": [
                {"command": "/py -m cairn.hooks.claude_hooks post_edit", "timeout": 10000}],
        }}
        flat_both = {"hooks": {
            "afterFileEdit": [
                {"command": "/py -m cairn.hooks.claude_hooks post_edit", "timeout": 10000}],
            "afterSessionEnd": [
                {"command": "/py -m cairn.hooks.claude_hooks session_end", "timeout": 60000}],
        }}
        nested_one = {"hooks": {
            "PostToolUse": [{"matcher": "Edit", "hooks": [
                {"command": "/py -m cairn.hooks.claude_hooks post_edit"}]}],
        }}
        assert not _already_installed(flat_one, merger), "one flat entrypoint is partial"
        assert _already_installed(flat_both, merger), "both flat entrypoints = installed"
        assert not _already_installed(nested_one, merger), "one nested entrypoint is partial"
        assert not _already_installed({}, merger), "empty config is not installed"

    def test_entry_present_matches_flat_shape(self):
        from cairn.agent_install.merge import _entry_present

        cur = [{"command": "/a/py -m cairn.hooks.claude_hooks post_edit", "timeout": 10000}]
        same_ep_other_path = {"command": "/b/py -m cairn.hooks.claude_hooks post_edit",
                              "timeout": 10000}
        unrelated = {"command": "echo hi", "timeout": 1}
        assert _entry_present(cur, same_ep_other_path), "path change must not duplicate"
        assert not _entry_present(cur, unrelated)


# --------------------------------------------------------------------------
# FR-002: hook command strings embed the CAIRN_HOME assignment iff non-default
# --------------------------------------------------------------------------

class TestHookCairnHomePrefix:
    """Hook env contract (tech-spec D-009): generated hook command strings
    carry a `CAIRN_HOME=<path> ` prefix when CAIRN_HOME resolves to a
    non-default home and stay byte-identical to today's env-less commands when
    it is default (unset, or explicitly set to Path.home()/".cairn"). The git
    post-commit hook gains one quoted `export CAIRN_HOME="<path>"` line right
    after the shebang. Uninstall/idempotency matching keys on the
    `cairn.hooks.claude_hooks <entrypoint>` substring, so the prefix must not
    break recognition -- pinned here with hand-prefixed commands."""

    @staticmethod
    def _custom_home_env(monkeypatch) -> dict[str, str]:
        monkeypatch.setenv("CAIRN_HOME", "~/custom-cairn-home")
        return {"CAIRN_HOME": str(Path.home() / "custom-cairn-home")}

    # -- custom home: the prefix is present ----------------------------------

    def test_claude_shape_hook_commands_carry_prefix(self, monkeypatch):
        env = self._custom_home_env(monkeypatch)
        from cairn.agent_install.clients.claude import claude_hooks_block

        block = claude_hooks_block()
        post_edit = block["PostToolUse"][0]["hooks"][0]["command"]
        session_end = block["Stop"][0]["hooks"][0]["command"]
        for cmd, ep in ((post_edit, "post_edit"), (session_end, "session_end")):
            assert cmd.startswith(f"CAIRN_HOME={env['CAIRN_HOME']} "), (
                f"{ep} hook command must carry the CAIRN_HOME prefix, got: {cmd}"
            )
            assert f"-m cairn.hooks.claude_hooks {ep}" in cmd

    def test_cursor_shape_hook_commands_carry_prefix(self, monkeypatch):
        env = self._custom_home_env(monkeypatch)
        from cairn.agent_install.clients.cursor import cursor_hooks_json

        hooks = cursor_hooks_json()["hooks"]
        for event, ep in (("afterFileEdit", "post_edit"),
                          ("afterSessionEnd", "session_end")):
            cmd = hooks[event][0]["command"]
            assert cmd.startswith(f"CAIRN_HOME={env['CAIRN_HOME']} "), (
                f"{event} hook command must carry the CAIRN_HOME prefix, got: {cmd}"
            )
            assert f"-m cairn.hooks.claude_hooks {ep}" in cmd

    def test_custom_home_install_writes_prefixed_hook_commands(self, tmp_path, monkeypatch):
        env = self._custom_home_env(monkeypatch)
        _no_cli(monkeypatch, "claude", "cursor")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude", "cursor"], transport="stdio")

        claude = json.loads((ws / ".claude" / "settings.json").read_text(encoding="utf-8"))
        assert claude["hooks"]["PostToolUse"][0]["hooks"][0]["command"].startswith(
            f"CAIRN_HOME={env['CAIRN_HOME']} ")
        assert claude["hooks"]["Stop"][0]["hooks"][0]["command"].startswith(
            f"CAIRN_HOME={env['CAIRN_HOME']} ")
        cursor = json.loads((ws / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
        assert cursor["hooks"]["afterFileEdit"][0]["command"].startswith(
            f"CAIRN_HOME={env['CAIRN_HOME']} ")
        assert cursor["hooks"]["afterSessionEnd"][0]["command"].startswith(
            f"CAIRN_HOME={env['CAIRN_HOME']} ")

    def test_custom_home_git_hook_gains_quoted_export_line_after_shebang(
            self, tmp_path, monkeypatch):
        custom = tmp_path / "custom_home"
        monkeypatch.setenv("CAIRN_HOME", str(custom))
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".git").mkdir()
        from cairn.hooks.git_hooks import install_hooks

        assert install_hooks(["repo"], str(ws)) == ["repo"]

        hook = (ws / ".git" / "hooks" / "post-commit").read_text(encoding="utf-8")
        lines = hook.splitlines()
        assert lines[0] == "#!/bin/bash"
        assert lines[1] == f'export CAIRN_HOME="{custom}"', (
            "exactly one quoted export line must follow the shebang"
        )
        assert [ln for ln in lines if "CAIRN_HOME" in ln] == [lines[1]], \
            "the export line must appear exactly once"
        assert 'cairn update --repo "repo"' in hook
        assert "cairn validate-paths --mark" in hook

    # -- custom home: matching/idempotency survive the prefix -----------------

    def test_custom_home_prefixed_hooks_stay_idempotent_and_uninstallable(
            self, tmp_path, monkeypatch):
        """With the prefix present in every written command (the shape this
        spec generates on a custom home), a reinstall must not duplicate
        entries and uninstall must still strip them -- matching is
        entrypoint-substring based, not full-command based."""
        self._custom_home_env(monkeypatch)
        _no_cli(monkeypatch, "claude", "cursor")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude", "cursor"], transport="stdio")
        install(str(ws), clients=["claude", "cursor"], transport="stdio")

        claude = json.loads((ws / ".claude" / "settings.json").read_text(encoding="utf-8"))
        assert len(claude["hooks"]["PostToolUse"]) == 1
        assert len(claude["hooks"]["Stop"]) == 1
        cursor = json.loads((ws / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
        assert len(cursor["hooks"]["afterFileEdit"]) == 1
        assert len(cursor["hooks"]["afterSessionEnd"]) == 1

        uninstall(str(ws), clients=["claude", "cursor"])
        claude = json.loads((ws / ".claude" / "settings.json").read_text(encoding="utf-8"))
        assert "hooks" not in claude
        cursor = json.loads((ws / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
        assert "hooks" not in cursor

    def test_hook_marker_matching_survives_cairn_home_prefix(self, tmp_path):
        """Hand-prefixed commands (the D-009 shape) are recognized by the same
        machinery that uninstall/idempotency use: _already_installed,
        _entry_present, _strip_hooks (claude nested) and _strip_cursor_hooks
        (cursor flat) all key on the `cairn.hooks.claude_hooks <entrypoint>`
        substring, which the prefix leaves intact."""
        from cairn.agent_install.clients.claude import claude_hooks_block
        from cairn.agent_install.merge import (
            _already_installed,
            _entry_present,
            _strip_cursor_hooks,
            _strip_hooks,
        )

        def prefixed(ep: str) -> str:
            return f"CAIRN_HOME=/custom/cairn/home /py -m cairn.hooks.claude_hooks {ep}"

        claude_cfg = {"hooks": {
            "PostToolUse": [{"matcher": "Edit|Write|MultiEdit", "hooks": [
                {"type": "command", "command": prefixed("post_edit")}]}],
            "Stop": [{"hooks": [
                {"type": "command", "command": prefixed("session_end")}]}],
        }}
        assert _already_installed(claude_cfg, {"hooks": claude_hooks_block()}), \
            "prefixed commands must still read as installed"
        assert _entry_present(claude_cfg["hooks"]["PostToolUse"],
                              claude_hooks_block()["PostToolUse"][0]), \
            "a reinstall onto prefixed entries must not duplicate"

        cursor_cfg = {"hooks": {
            "afterFileEdit": [{"command": prefixed("post_edit"), "timeout": 10000}],
            "afterSessionEnd": [{"command": prefixed("session_end"), "timeout": 60000}],
        }}
        assert _entry_present(cursor_cfg["hooks"]["afterFileEdit"],
                              {"command": prefixed("post_edit"), "timeout": 10000})

        claude_cfg["hooks"]["UserEvent"] = [{"hooks": [{"command": "echo mine"}]}]
        claude_path = tmp_path / "settings.json"
        claude_path.write_text(json.dumps(claude_cfg), encoding="utf-8")
        _strip_hooks(claude_path, InstallResult("claude"))
        data = json.loads(claude_path.read_text(encoding="utf-8"))
        assert "PostToolUse" not in data["hooks"] and "Stop" not in data["hooks"]
        assert data["hooks"]["UserEvent"][0]["hooks"][0]["command"] == "echo mine"

        cursor_path = tmp_path / "hooks.json"
        cursor_path.write_text(json.dumps(cursor_cfg), encoding="utf-8")
        _strip_cursor_hooks(cursor_path, InstallResult("cursor"))
        assert "hooks" not in json.loads(cursor_path.read_text(encoding="utf-8"))

    # -- default home: nothing is added ---------------------------------------

    def test_default_home_hook_commands_stay_env_less(self, monkeypatch):
        monkeypatch.delenv("CAIRN_HOME", raising=False)
        import sys

        from cairn.agent_install._common import _claude_hook_command
        from cairn.agent_install.clients.claude import claude_hooks_block
        from cairn.agent_install.clients.cursor import cursor_hooks_json

        assert _claude_hook_command("post_edit") == (
            f"{sys.executable} -m cairn.hooks.claude_hooks post_edit")
        assert _claude_hook_command("session_end") == (
            f"{sys.executable} -m cairn.hooks.claude_hooks session_end")

        claude = claude_hooks_block()
        for event in ("PostToolUse", "Stop"):
            for entry in claude[event]:
                for h in entry["hooks"]:
                    assert "CAIRN_HOME" not in h["command"]
        cursor = cursor_hooks_json()["hooks"]
        for event in ("afterFileEdit", "afterSessionEnd"):
            assert "CAIRN_HOME" not in cursor[event][0]["command"]

    def test_home_set_to_default_hook_commands_match_unset(self, monkeypatch):
        from cairn.agent_install.clients.claude import claude_hooks_block
        from cairn.agent_install.clients.cursor import cursor_hooks_json

        generators = (claude_hooks_block, cursor_hooks_json)
        monkeypatch.delenv("CAIRN_HOME", raising=False)
        unset = [json.dumps(gen(), sort_keys=True) for gen in generators]
        monkeypatch.setenv("CAIRN_HOME", str(Path.home() / ".cairn"))
        defaulted = [json.dumps(gen(), sort_keys=True) for gen in generators]
        assert defaulted == unset, \
            "a CAIRN_HOME set to the default path counts as default (no prefix)"

    def test_default_home_git_hook_stays_env_less(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CAIRN_HOME", raising=False)
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".git").mkdir()
        from cairn.hooks.git_hooks import POST_COMMIT_TEMPLATE, install_hooks

        assert install_hooks(["repo"], str(ws)) == ["repo"]

        hook = (ws / ".git" / "hooks" / "post-commit").read_text(encoding="utf-8")
        assert hook == POST_COMMIT_TEMPLATE.format(repo="repo"), \
            "default home must keep the hook byte-identical to the template"
        assert "CAIRN_HOME" not in hook


# --------------------------------------------------------------------------
# F2: global-scope uninstall
# --------------------------------------------------------------------------

class TestGlobalScopeUninstall:
    def test_global_install_uninstall_removes_home_tree(self, fake_home, tmp_path, monkeypatch):
        _no_cli(monkeypatch, "claude", "cursor", "zcode", "droid", "opencode", "agy")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude", "cursor", "zcode"], scope="global",
                transport="stdio")

        assert (fake_home / ".claude" / "skills" / "cairn").is_dir()
        assert (fake_home / ".cursor" / "hooks.json").exists()
        assert (fake_home / ".zcode" / "cli" / "config.json").exists()

        uninstall(str(ws), clients=["claude", "cursor", "zcode"], scope="global")

        assert not (fake_home / ".claude" / "skills" / "cairn").exists()
        assert not any((fake_home / ".claude" / "commands").glob("*.md"))
        assert not any((fake_home / ".claude" / "agents").glob("*.md"))
        st = json.loads((fake_home / ".claude" / "settings.json").read_text(encoding="utf-8"))
        assert "hooks" not in st, "cairn hooks must be stripped from ~/.claude/settings.json"
        cur = json.loads((fake_home / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
        assert "hooks" not in cur, "cairn hooks must be stripped from ~/.cursor/hooks.json"
        zc = json.loads((fake_home / ".zcode" / "cli" / "config.json").read_text(encoding="utf-8"))
        assert "cairn" not in zc.get("mcp", {}).get("servers", {})

    def test_zcode_global_install_migrates_legacy_config(self, fake_home, tmp_path, monkeypatch):
        """A pre-fix global install left cairn in ~/.zcode/config.json (which the
        ZCode CLI does not read for MCP). Re-installing must move the entry to
        ~/.zcode/cli/config.json and strip the legacy one."""
        _no_cli(monkeypatch, "zcode")
        ws = tmp_path / "ws"
        ws.mkdir()
        legacy = fake_home / ".zcode" / "config.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps(
            {"mcp": {"servers": {"cairn": {"type": "stdio", "command": "/old/cairn",
                                           "args": ["serve"]}}}}), encoding="utf-8")

        install(str(ws), clients=["zcode"], scope="global", transport="sse",
                sse_url="http://127.0.0.1:9876/sse")

        new = json.loads((fake_home / ".zcode" / "cli" / "config.json").read_text(encoding="utf-8"))
        assert new["mcp"]["servers"]["cairn"]["type"] == "sse"
        leg = json.loads(legacy.read_text(encoding="utf-8"))
        assert "cairn" not in leg.get("mcp", {}).get("servers", {}), \
            "legacy entry must be stripped so only one source of truth remains"

    def test_global_uninstall_preserves_user_entries(self, fake_home, tmp_path, monkeypatch):
        _no_cli(monkeypatch, "claude")
        ws = tmp_path / "ws"
        ws.mkdir()
        settings = fake_home / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "hooks": {"UserEvent": [{"hooks": [{"command": "echo mine"}]}]},
        }))
        install(str(ws), clients=["claude"], scope="global", transport="stdio")
        uninstall(str(ws), clients=["claude"], scope="global")
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["hooks"]["UserEvent"][0]["hooks"][0]["command"] == "echo mine"

    def test_workspace_default_uninstall_leaves_global_alone(self, fake_home, tmp_path, monkeypatch):
        """Byte-identical workspace default: plain uninstall(ws) must not
        touch a global install (opt in with scope= to remove it)."""
        _no_cli(monkeypatch, "claude", "zcode")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude"], scope="global", transport="stdio")
        uninstall(str(ws), clients=["claude"])  # default scope="workspace"
        assert (fake_home / ".claude" / "skills" / "cairn").is_dir()
        st = json.loads((fake_home / ".claude" / "settings.json").read_text(encoding="utf-8"))
        assert "PostToolUse" in st.get("hooks", {})

    def test_scope_all_covers_workspace_and_global(self, fake_home, tmp_path, monkeypatch):
        _no_cli(monkeypatch, "claude")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude"], transport="stdio")
        install(str(ws), clients=["claude"], scope="global", transport="stdio")
        uninstall(str(ws), clients=["claude"], scope="all")
        assert not (ws / ".claude" / "skills" / "cairn").exists()
        assert not (fake_home / ".claude" / "skills" / "cairn").exists()

    def test_global_uninstall_invokes_claude_mcp_remove(self, fake_home, tmp_path, monkeypatch):
        _cli_at(monkeypatch, "claude")
        calls = _spy_subprocess(monkeypatch)
        ws = tmp_path / "ws"
        ws.mkdir()
        uninstall(str(ws), clients=["claude"], scope="global")
        assert ["claude", "mcp", "remove", "cairn", "--scope", "user"] in calls

    def test_workspace_uninstall_never_invokes_claude_mcp_remove(self, fake_home, tmp_path, monkeypatch):
        """The historical workspace path spawns no subprocesses (it never
        registered one)."""
        _cli_at(monkeypatch, "claude")
        calls = _spy_subprocess(monkeypatch)
        ws = tmp_path / "ws"
        ws.mkdir()
        uninstall(str(ws), clients=["claude"])
        assert calls == []

    def test_droid_uninstall_invokes_mcp_remove(self, tmp_path, monkeypatch):
        """Install registers via `droid mcp add` at ANY scope; uninstall must
        run the matching remove -- stripping the file fallback alone leaves a
        stale registration."""
        _cli_at(monkeypatch, "droid")
        calls = _spy_subprocess(monkeypatch)
        from cairn.agent_install.clients.droid import uninstall as uninstall_droid
        res = InstallResult("droid")
        uninstall_droid(tmp_path, res)
        assert ["droid", "mcp", "remove", "cairn"] in calls

    def test_droid_uninstall_without_cli_spawns_nothing(self, tmp_path, monkeypatch):
        _no_cli(monkeypatch, "droid")
        calls = _spy_subprocess(monkeypatch)
        from cairn.agent_install.clients.droid import uninstall as uninstall_droid
        uninstall_droid(tmp_path, InstallResult("droid"))
        assert calls == []


# --------------------------------------------------------------------------
# F3: dry-run fidelity
# --------------------------------------------------------------------------

class TestDryRunFidelity:
    def test_dry_run_on_installed_workspace_reports_skips(self, tmp_path, monkeypatch):
        """A real second install would skip everything; dry-run must say so
        instead of over-reporting phantom 'would write/merge' actions."""
        _no_cli(monkeypatch, "cursor")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["cursor"], transport="stdio")
        rep = install(str(ws), clients=["cursor"], transport="stdio", dry_run=True)
        cur = next(r for r in rep.results if r.client == "cursor")
        assert not any("would" in w for w in cur.written), cur.written
        assert cur.skipped, "already-installed entries should be reported as skipped"

    def test_dry_run_on_fresh_workspace_reports_would(self, tmp_path, monkeypatch):
        _no_cli(monkeypatch, "cursor")
        ws = tmp_path / "ws"
        ws.mkdir()
        rep = install(str(ws), clients=["cursor"], transport="stdio", dry_run=True)
        cur = next(r for r in rep.results if r.client == "cursor")
        assert any("would merge into" in w for w in cur.written)
        assert any("would write" in w for w in cur.written)
        assert not (ws / ".cursor").exists(), "dry-run must not touch the disk"


# --------------------------------------------------------------------------
# F4: cross-tool .agents/ tree removal
# --------------------------------------------------------------------------

class TestCrossToolTreeRemoval:
    def test_uninstall_removes_whole_skill_tree(self, tmp_path, monkeypatch):
        """The skill package ships references/ + scripts/ + evals/ alongside
        SKILL.md; uninstall must take the whole tree, not just SKILL.md."""
        _no_cli(monkeypatch, "claude")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude"], transport="stdio")
        skill = ws / ".agents" / "skills" / "cairn"
        subdirs = {p.name for p in skill.iterdir() if p.is_dir()}
        assert {"references", "scripts", "evals"} <= subdirs
        uninstall(str(ws), clients=["claude"])
        assert not skill.exists()


# --------------------------------------------------------------------------
# F5: uninstall only removes files the installer wrote
# --------------------------------------------------------------------------

class TestProvenanceCheckedRemoval:
    def test_user_command_file_survives_uninstall(self, tmp_path, monkeypatch):
        """Install skips a pre-existing .claude/commands/cairn.md; uninstall
        must not delete the user's file it never wrote."""
        _no_cli(monkeypatch, "claude")
        ws = tmp_path / "ws"
        ws.mkdir()
        user_cmd = ws / ".claude" / "commands" / "cairn.md"
        user_cmd.parent.mkdir(parents=True)
        user_cmd.write_text("# my own command\n\nuser-authored\n", encoding="utf-8")

        rep = install(str(ws), clients=["claude"], transport="stdio")
        claude = next(r for r in rep.results if r.client == "claude")
        assert any(str(user_cmd) in s for s in claude.skipped), "install must skip the user file"

        un_rep = uninstall(str(ws), clients=["claude"])
        un_claude = next(r for r in un_rep.results if r.client == "claude")
        assert user_cmd.exists(), "uninstall must not delete a user-authored file"
        assert any("not cairn-written" in s for s in un_claude.skipped)

    def test_installer_written_command_file_removed(self, tmp_path, monkeypatch):
        _no_cli(monkeypatch, "claude")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude"], transport="stdio")
        ours = ws / ".claude" / "commands" / "cairn-prep.md"
        assert ours.exists()
        uninstall(str(ws), clients=["claude"])
        assert not ours.exists()

    def test_cross_tool_user_command_file_survives(self, tmp_path, monkeypatch):
        _no_cli(monkeypatch, "claude")
        ws = tmp_path / "ws"
        ws.mkdir()
        user_cmd = ws / ".agents" / "commands" / "cairn.md"
        user_cmd.parent.mkdir(parents=True)
        user_cmd.write_text("user-authored\n", encoding="utf-8")
        install(str(ws), clients=["claude"], transport="stdio")
        uninstall(str(ws), clients=["claude"])
        assert user_cmd.exists()


# --------------------------------------------------------------------------
# F6: non-object values under merged keys
# --------------------------------------------------------------------------

class TestNonObjectKeyHandling:
    def test_zcode_non_object_mcp_backed_up(self, tmp_path, monkeypatch):
        _no_cli(monkeypatch, "claude")
        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = ws / ".zcode" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"mcp": True}), encoding="utf-8")

        install(str(ws), clients=["zcode"], transport="stdio")  # must not raise

        assert cfg.with_suffix(".json.bak").exists(), "user's broken value must be backed up"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        assert "cairn" in data["mcp"]["servers"]

    def test_cursor_non_object_mcp_servers_backed_up(self, tmp_path, monkeypatch):
        _no_cli(monkeypatch, "cursor")
        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = ws / ".cursor" / "mcp.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"mcpServers": "nope"}), encoding="utf-8")

        install(str(ws), clients=["cursor"], transport="stdio")  # must not raise

        assert cfg.with_suffix(".json.bak").exists()
        data = json.loads(cfg.read_text(encoding="utf-8"))
        assert "cairn" in data["mcpServers"]

    def test_claude_non_object_hooks_backed_up(self, tmp_path, monkeypatch):
        _no_cli(monkeypatch, "claude")
        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = ws / ".claude" / "settings.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"hooks": []}), encoding="utf-8")

        install(str(ws), clients=["claude"], transport="stdio")  # must not raise

        assert cfg.with_suffix(".json.bak").exists()
        data = json.loads(cfg.read_text(encoding="utf-8"))
        assert "PostToolUse" in data["hooks"]

    def test_merge_helpers_tolerate_non_dict_values(self):
        """_already_installed / _deep_merge never raise on non-object values
        under the keys they inspect."""
        from cairn.agent_install.merge import _already_installed, _deep_merge

        merger_mcp = {"mcpServers": {"cairn": {"command": "x", "args": ["serve"]}}}
        assert _already_installed({"mcpServers": []}, merger_mcp) is False
        assert _already_installed({"mcpServers": "str"}, merger_mcp) is False
        merged = _deep_merge({"mcpServers": []}, merger_mcp)
        assert merged["mcpServers"] == merger_mcp["mcpServers"]

        merger_zcode = {"mcp": {"servers": {"cairn": {"command": "x"}}}}
        assert _already_installed({"mcp": True}, merger_zcode, config_key="zcode") is False
        assert _deep_merge({"mcp": True}, merger_zcode, config_key="zcode")["mcp"] == \
            merger_zcode["mcp"]

        merger_oc = {"mcp": {"cairn": {"type": "local", "command": ["x"]}}}
        assert _already_installed({"mcp": [1]}, merger_oc, config_key="opencode") is False
        assert _deep_merge({"mcp": 3}, merger_oc, config_key="opencode")["mcp"] == \
            merger_oc["mcp"]

        merger_hooks = {"hooks": {"Stop": [{"hooks": [{"command": "x"}]}]}}
        assert _already_installed({"hooks": []}, merger_hooks) is False
        assert _deep_merge({"hooks": 1}, merger_hooks)["hooks"] == merger_hooks["hooks"]


# --------------------------------------------------------------------------
# F7: atomic workspaces.json rewrite
# --------------------------------------------------------------------------

class TestAtomicRegistryRewrite:
    def test_registry_prune_goes_through_atomic_write(self, tmp_path, monkeypatch):
        from cairn.agent_install import merge as merge_mod
        from cairn.cli import main
        from cairn.paths import store_key

        home = tmp_path / "cairn_home"
        home.mkdir()
        ws = tmp_path / "ws"
        ws.mkdir()
        key = store_key(ws)
        (home / key).mkdir()
        (home / key / "store.db").write_bytes(b"x")
        (home / "workspaces.json").write_text(
            json.dumps({str(ws.resolve()): key}), encoding="utf-8")

        seen: list[Path] = []
        orig = merge_mod._atomic_write_text

        def spy(path, content):
            seen.append(path)
            return orig(path, content)

        monkeypatch.setattr(merge_mod, "_atomic_write_text", spy)

        result = CliRunner().invoke(
            main,
            ["uninstall", "--graph-only", "-y", "--workspace", str(ws)],
            env={"CAIRN_HOME": str(home)},
        )
        assert result.exit_code == 0, result.output
        assert (home / "workspaces.json") in seen, \
            "registry rewrite must go through _atomic_write_text"
        assert json.loads((home / "workspaces.json").read_text(encoding="utf-8")) == {}
        assert not (home / key).exists()


# --------------------------------------------------------------------------
# F8: opencode scope
# --------------------------------------------------------------------------

class TestOpencodeScope:
    def test_global_install_lands_in_global_path(self, fake_home, tmp_path, monkeypatch):
        _no_cli(monkeypatch, "opencode")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["opencode"], scope="global", transport="stdio")

        global_cfg = fake_home / ".config" / "opencode" / "opencode.json"
        assert global_cfg.exists(), "scope=global must write ~/.config/opencode/opencode.json"
        assert not (ws / "opencode.json").exists(), "scope=global must not write the workspace"
        assert "cairn" in json.loads(global_cfg.read_text(encoding="utf-8"))["mcp"]
        assert check_installed(str(ws))["opencode"], \
            "a global install must be detected by check_installed (it probes the global path)"

    def test_global_uninstall_strips_global_path(self, fake_home, tmp_path, monkeypatch):
        _no_cli(monkeypatch, "opencode")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["opencode"], scope="global", transport="stdio")
        uninstall(str(ws), clients=["opencode"], scope="global")
        global_cfg = fake_home / ".config" / "opencode" / "opencode.json"
        assert "cairn" not in json.loads(global_cfg.read_text(encoding="utf-8")).get("mcp", {})

    def test_workspace_scope_still_writes_workspace_file(self, tmp_path, monkeypatch):
        _no_cli(monkeypatch, "opencode")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["opencode"], transport="stdio")
        assert (ws / "opencode.json").exists()


# --------------------------------------------------------------------------
# CLI wiring: --scope plumbing and the --dry-run gate on `cairn uninstall`
# --------------------------------------------------------------------------

class TestCliScopeWiring:
    def test_uninstall_cmd_help_lists_scope(self):
        from cairn.cli import main
        result = CliRunner().invoke(main, ["uninstall", "--help"])
        assert result.exit_code == 0
        assert "--scope" in result.output

    def test_full_implies_scope_all_in_dry_run_output(self, fake_home, tmp_path, monkeypatch):
        """`cairn uninstall --full` must tear down global wiring too; the
        dry-run preview reports the effective agent scope."""
        from cairn.cli import main
        ws = tmp_path / "ws"
        ws.mkdir()
        result = CliRunner().invoke(
            main,
            ["uninstall", "--full", "--dry-run", "-y", "--workspace", str(ws)],
            env={"CAIRN_HOME": str(tmp_path / "cairn_home2")},
        )
        assert result.exit_code == 0, result.output
        assert "(scope: all)" in result.output

    def test_explicit_scope_global_shown_in_dry_run(self, fake_home, tmp_path, monkeypatch):
        from cairn.cli import main
        ws = tmp_path / "ws"
        ws.mkdir()
        result = CliRunner().invoke(
            main,
            ["uninstall", "--agents-only", "--scope", "global", "--dry-run", "-y",
             "--workspace", str(ws), "--client", "cursor"],
            env={"CAIRN_HOME": str(tmp_path / "cairn_home3")},
        )
        assert result.exit_code == 0, result.output
        assert "(scope: global)" in result.output

    def test_uninstall_agents_cli_removes_global_scope(self, fake_home, tmp_path, monkeypatch):
        """End-to-end: install-agents --scope global then uninstall-agents
        --scope global leaves no cairn entry in ~/.cursor/."""
        _no_cli(monkeypatch, "cursor")
        from cairn.cli import main
        ws = tmp_path / "ws"
        ws.mkdir()
        r1 = CliRunner().invoke(main, [
            "install-agents", "--client", "cursor", "--scope", "global",
            "--stdio", "--workspace", str(ws),
        ])
        assert r1.exit_code == 0, r1.output
        assert (fake_home / ".cursor" / "hooks.json").exists()

        r2 = CliRunner().invoke(main, [
            "uninstall-agents", "--client", "cursor", "--scope", "global",
            "--workspace", str(ws),
        ])
        assert r2.exit_code == 0, r2.output
        hooks = json.loads((fake_home / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
        assert "hooks" not in hooks

    def test_cli_dry_run_deletes_no_agent_wiring(self, tmp_path, monkeypatch):
        """Regression: `cairn uninstall --dry-run` used to pass dry_run into
        _remove_agents, which accepted but ignored it -- a 'preview' run
        actually deleted agent configs."""
        _no_cli(monkeypatch, "cursor")
        from cairn.cli import main
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["cursor"], transport="stdio")
        before = (ws / ".cursor" / "hooks.json").read_text(encoding="utf-8")

        result = CliRunner().invoke(
            main,
            ["uninstall", "--agents-only", "--dry-run", "-y", "--workspace", str(ws)],
            env={"CAIRN_HOME": str(tmp_path / "cairn_home4")},
        )
        assert result.exit_code == 0, result.output
        assert "would" in result.output
        assert (ws / ".cursor" / "hooks.json").read_text(encoding="utf-8") == before
        assert (ws / ".agents" / "skills" / "cairn").exists(), \
            "dry-run must not remove the cross-tool copies either"


# --------------------------------------------------------------------------
# Transport default: SSE everywhere except Claude Desktop (stdio-only)
# --------------------------------------------------------------------------

class TestTransportDefault:
    def test_install_defaults_to_sse_except_claude_desktop(self, fake_home, tmp_path, monkeypatch):
        """No explicit transport: clients get an SSE config pointing at the
        shared daemon; Claude Desktop stays stdio (the app is stdio-only)."""
        _no_cli(monkeypatch, "claude", "zcode")
        from cairn.mcp_server import lifecycle as lc
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["zcode", "claude", "claude-desktop"])

        expected_url = f"http://127.0.0.1:{lc.DEFAULT_PORT}/sse"
        # zcode (workspace scope): nested mcp.servers shape, SSE.
        zc = json.loads((ws / ".zcode" / "config.json").read_text(encoding="utf-8"))
        assert zc["mcp"]["servers"]["cairn"]["type"] == "sse"
        assert zc["mcp"]["servers"]["cairn"]["url"] == expected_url
        # claude code (workspace scope): flat mcpServers shape, SSE.
        cl = json.loads((ws / ".mcp.json").read_text(encoding="utf-8"))
        assert cl["mcpServers"]["cairn"]["type"] == "sse"
        assert cl["mcpServers"]["cairn"]["url"] == expected_url
        # claude-desktop: ALWAYS stdio, even under the SSE default.
        from cairn.agent_install.detect import claude_desktop_config_path
        cd = json.loads(claude_desktop_config_path().read_text(encoding="utf-8"))
        assert "command" in cd["mcpServers"]["cairn"]
        assert "url" not in cd["mcpServers"]["cairn"]

    def test_claude_global_sse_registers_sse_transport(self, fake_home, tmp_path, monkeypatch):
        """Global-scope claude install must honor the SSE default: the
        `claude mcp add --scope user` registration uses --transport sse with
        the daemon URL, not a stdio command spawn."""
        _cli_at(monkeypatch, "claude")
        calls = _spy_subprocess(monkeypatch)
        from cairn.mcp_server import lifecycle as lc
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["claude"], scope="global")

        expected_url = f"http://127.0.0.1:{lc.DEFAULT_PORT}/sse"
        assert ["claude", "mcp", "add", "--transport", "sse", "--scope", "user",
                "cairn", expected_url] in calls

    def test_claude_global_sse_custom_url(self, fake_home, tmp_path, monkeypatch):
        """--sse-url propagates to the global claude registration."""
        _cli_at(monkeypatch, "claude")
        calls = _spy_subprocess(monkeypatch)
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["claude"], scope="global",
                sse_url="http://localhost:9999/sse")

        assert ["claude", "mcp", "add", "--transport", "sse", "--scope", "user",
                "cairn", "http://localhost:9999/sse"] in calls

    def test_claude_global_stdio_keeps_stdio_registration(self, fake_home, tmp_path, monkeypatch):
        """transport=stdio global install registers the command spawn, with
        no --transport flag (regression pin for the pre-SSE behavior). A
        non-default CAIRN_HOME rides along as `-e CAIRN_HOME=<abs>`; an unset
        home keeps the argv env-less."""
        monkeypatch.delenv("CAIRN_HOME", raising=False)
        _cli_at(monkeypatch, "claude")
        calls = _spy_subprocess(monkeypatch)
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["claude"], scope="global", transport="stdio")

        add = [c for c in calls if c[:3] == ["claude", "mcp", "add"]]
        assert len(add) == 1
        assert "--transport" not in add[0]
        assert add[0][3] == "cairn"  # name positional, then --scope user
        assert "serve" in add[0]
        assert "-e" not in add[0], "default home must stay env-less"

        custom_home = tmp_path / "custom_home"
        monkeypatch.setenv("CAIRN_HOME", str(custom_home))
        install(str(ws), clients=["claude"], scope="global", transport="stdio")

        add = [c for c in calls if c[:3] == ["claude", "mcp", "add"]]
        assert len(add) == 2
        assert add[1][-2:] == ["-e", f"CAIRN_HOME={custom_home}"]

    def test_droid_cli_sse_registers_sse_type(self, tmp_path, monkeypatch):
        """With the droid CLI present, the default SSE transport must
        register the daemon URL via `--type sse`, not a stdio command spawn."""
        _cli_at(monkeypatch, "droid")
        calls = _spy_subprocess(monkeypatch)
        from cairn.mcp_server import lifecycle as lc
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["droid"])

        expected_url = f"http://127.0.0.1:{lc.DEFAULT_PORT}/sse"
        assert ["droid", "mcp", "add", "cairn", expected_url, "--type", "sse"] in calls

    def test_droid_cli_stdio_keeps_stdio_registration(self, tmp_path, monkeypatch):
        """transport=stdio droid install registers the command spawn, with
        no --type flag (regression pin). The argv stays env-less even on a
        custom home; the install notes WARN and point at the workspace-scope
        file registration instead."""
        monkeypatch.delenv("CAIRN_HOME", raising=False)
        _cli_at(monkeypatch, "droid")
        calls = _spy_subprocess(monkeypatch)
        ws = tmp_path / "ws"
        ws.mkdir()

        rep_default = install(str(ws), clients=["droid"], transport="stdio")

        add = [c for c in calls if c[:3] == ["droid", "mcp", "add"]]
        assert len(add) == 1
        assert "--type" not in add[0]
        assert "serve" in add[0]
        default_res = next(r for r in rep_default.results if r.client == "droid")
        assert not any("CAIRN_HOME" in n for n in default_res.notes)

        custom_home = tmp_path / "custom_home"
        monkeypatch.setenv("CAIRN_HOME", str(custom_home))
        rep_custom = install(str(ws), clients=["droid"], transport="stdio")

        add = [c for c in calls if c[:3] == ["droid", "mcp", "add"]]
        assert len(add) == 2
        assert not any("CAIRN_HOME" in part for part in add[1]), \
            "argv stays env-less: `droid mcp add` has no verified env mechanism"
        custom_res = next(r for r in rep_custom.results if r.client == "droid")
        warns = [n for n in custom_res.notes
                 if n.startswith("WARNING") and "CAIRN_HOME" in n]
        assert warns, "custom home must WARN that the registration embeds no env"
        assert any("workspace" in n for n in warns), \
            "the WARN must point at the workspace-scope file registration"

    def test_agy_sse_uses_serverurl_shape(self, fake_home, tmp_path, monkeypatch):
        """agy (Antigravity) remote servers use the `serverUrl` field; the
        official docs state legacy `url`/`httpUrl` fields are NOT supported
        and there is no `type` field — transport is implied by the field."""
        from cairn.mcp_server import lifecycle as lc
        from cairn.agent_install.clients.agy import agy_config_path
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["agy"])

        cfg = json.loads(agy_config_path().read_text(encoding="utf-8"))
        entry = cfg["mcpServers"]["cairn"]
        assert entry["serverUrl"] == f"http://127.0.0.1:{lc.DEFAULT_PORT}/sse"
        assert "url" not in entry
        assert "type" not in entry

    def test_agy_stdio_keeps_command_args_shape(self, fake_home, tmp_path, monkeypatch):
        """stdio pin: command/args shape (agy's documented stdio form)."""
        from cairn.agent_install.clients.agy import agy_config_path
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["agy"], transport="stdio")

        cfg = json.loads(agy_config_path().read_text(encoding="utf-8"))
        entry = cfg["mcpServers"]["cairn"]
        assert "command" in entry
        assert "serverUrl" not in entry


# --------------------------------------------------------------------------
# kilo (Kilo Code CLI): opencode-format config, kilo.json paths
# --------------------------------------------------------------------------

class TestKiloClient:
    def test_sse_default_writes_remote_entry(self, fake_home, tmp_path, monkeypatch):
        """Default transport: kilo.json gets mcp.cairn = {type: remote, url}
        (kilo's documented remote shape — same schema as opencode)."""
        from cairn.mcp_server import lifecycle as lc
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["kilo"])

        cfg = json.loads((ws / "kilo.json").read_text(encoding="utf-8"))
        entry = cfg["mcp"]["cairn"]
        assert entry["type"] == "remote"
        assert entry["url"] == f"http://127.0.0.1:{lc.DEFAULT_PORT}/sse"
        assert entry["enabled"] is True

    def test_stdio_writes_local_command_array(self, fake_home, tmp_path, monkeypatch):
        """stdio: mcp.cairn = {type: local, command: [...]} — command is a
        single array per kilo's schema, ending in `serve`."""
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["kilo"], transport="stdio")

        cfg = json.loads((ws / "kilo.json").read_text(encoding="utf-8"))
        entry = cfg["mcp"]["cairn"]
        assert entry["type"] == "local"
        assert isinstance(entry["command"], list)
        assert entry["command"][-1] == "serve"

    def test_global_scope_writes_global_config(self, fake_home, tmp_path, monkeypatch):
        """scope=global lands in ~/.config/kilo/kilo.json (kilo's global
        config path) and is detected by check_installed."""
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["kilo"], scope="global")

        global_cfg = fake_home / ".config" / "kilo" / "kilo.json"
        assert global_cfg.exists(), "scope=global must write ~/.config/kilo/kilo.json"
        assert not (ws / "kilo.json").exists(), "scope=global must not write the workspace"
        assert "cairn" in json.loads(global_cfg.read_text(encoding="utf-8"))["mcp"]
        assert check_installed(str(ws))["kilo"], \
            "a global install must be detected by check_installed (it probes the global path)"

    def test_workspace_install_detected(self, fake_home, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["kilo"])
        assert check_installed(str(ws))["kilo"]

    def test_uninstall_strips_cairn_preserves_others(self, fake_home, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["kilo"])

        p = ws / "kilo.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["mcp"]["other-server"] = {"type": "local", "command": ["echo"]}
        p.write_text(json.dumps(data))

        uninstall(str(ws), clients=["kilo"])

        after = json.loads(p.read_text(encoding="utf-8"))
        assert "cairn" not in after.get("mcp", {})
        assert after["mcp"]["other-server"] == {"type": "local", "command": ["echo"]}


# --------------------------------------------------------------------------
# omp (oh-my-pi CLI): native mcpServers config + native .omp/agents/*.md subagents
# --------------------------------------------------------------------------

class TestOmpClient:
    def test_sse_default_writes_mcpservers_entry(self, fake_home, tmp_path, monkeypatch):
        """Default transport: .omp/mcp.json gets mcpServers.cairn = {type: sse, url}
        (omp's schema matches the shared shape used by claude/cursor/droid)."""
        from cairn.mcp_server import lifecycle as lc
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["omp"])

        cfg = json.loads((ws / ".omp" / "mcp.json").read_text(encoding="utf-8"))
        entry = cfg["mcpServers"]["cairn"]
        assert entry["type"] == "sse"
        assert entry["url"] == f"http://127.0.0.1:{lc.DEFAULT_PORT}/sse"

    def test_stdio_writes_command_args_shape(self, fake_home, tmp_path, monkeypatch):
        """stdio: mcpServers.cairn = {command, args: [..., "serve"]}."""
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["omp"], transport="stdio")

        cfg = json.loads((ws / ".omp" / "mcp.json").read_text(encoding="utf-8"))
        entry = cfg["mcpServers"]["cairn"]
        assert "command" in entry
        assert entry["args"][-1] == "serve"

    def test_writes_native_subagent_files(self, fake_home, tmp_path, monkeypatch):
        """Subagents are written as omp's native .omp/agents/<name>.md task-agent
        files (not just the cross-tool .agents/ fallback), with name/description
        frontmatter and MCP tools referenced as mcp__cairn_<tool> (single
        underscore, per omp's tool-registry naming)."""
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["omp"])

        explorer = (ws / ".omp" / "agents" / "cairn-explorer.md").read_text(encoding="utf-8")
        assert explorer.startswith("---\nname: cairn-explorer\n")
        assert "description:" in explorer
        assert "mcp__cairn_explore" in explorer
        assert "mcp__cairn__explore" not in explorer, "omp uses single underscore, not Claude's double"

        steward = (ws / ".omp" / "agents" / "knowledge-steward.md").read_text(encoding="utf-8")
        assert steward.startswith("---\nname: knowledge-steward\n")

    def test_global_scope_writes_global_config_and_agents(self, fake_home, tmp_path, monkeypatch):
        """scope=global lands in ~/.omp/agent/mcp.json + ~/.omp/agent/agents/
        (omp's documented user-level paths) and is detected by check_installed."""
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["omp"], scope="global")

        global_cfg = fake_home / ".omp" / "agent" / "mcp.json"
        assert global_cfg.exists(), "scope=global must write ~/.omp/agent/mcp.json"
        assert not (ws / ".omp" / "mcp.json").exists(), "scope=global must not write the workspace"
        assert (fake_home / ".omp" / "agent" / "agents" / "cairn-explorer.md").exists()
        assert check_installed(str(ws))["omp"], \
            "a global install must be detected by check_installed (it probes the global path)"

    def test_workspace_install_detected(self, fake_home, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["omp"])
        assert check_installed(str(ws))["omp"]

    def test_uninstall_strips_cairn_preserves_others_and_removes_agents(self, fake_home, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["omp"])

        p = ws / ".omp" / "mcp.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["mcpServers"]["other-server"] = {"command": "echo"}
        p.write_text(json.dumps(data))

        uninstall(str(ws), clients=["omp"])

        after = json.loads(p.read_text(encoding="utf-8"))
        assert "cairn" not in after.get("mcpServers", {})
        assert after["mcpServers"]["other-server"] == {"command": "echo"}
        assert not (ws / ".omp" / "agents" / "cairn-explorer.md").exists()
        assert not (ws / ".omp" / "agents" / "knowledge-steward.md").exists()


# --------------------------------------------------------------------------
# FR-001: stdio registrations embed env.CAIRN_HOME iff the home is non-default
# --------------------------------------------------------------------------

class TestCairnHomeEnvBlock:
    """stdio env-block contract: generated configs carry env.CAIRN_HOME (the
    expanded absolute path) when CAIRN_HOME resolves to a non-default home,
    and stay byte-identical to the env-less shapes when it is default --
    unset, or explicitly set to Path.home()/".cairn"."""

    @pytest.fixture(autouse=True)
    def _pin_bin(self, monkeypatch):
        _cli_at(monkeypatch, "cairn")

    @staticmethod
    def _custom_home_env(monkeypatch) -> dict[str, str]:
        monkeypatch.setenv("CAIRN_HOME", "~/custom-cairn-home")
        return {"CAIRN_HOME": str(Path.home() / "custom-cairn-home")}

    @staticmethod
    def _set_default_home(monkeypatch) -> None:
        monkeypatch.setenv("CAIRN_HOME", str(Path.home() / ".cairn"))

    def test_custom_home_generators_embed_env(self, monkeypatch):
        env = self._custom_home_env(monkeypatch)
        from cairn.agent_install._common import mcp_config_json
        from cairn.agent_install.clients.agy import agy_mcp_config_json
        from cairn.agent_install.clients.zcode import zcode_mcp_config_json

        assert mcp_config_json(transport="stdio") == {
            "mcpServers": {"cairn": {"command": "/fake/bin/cairn", "args": ["serve"],
                                     "env": env}}}
        assert zcode_mcp_config_json(transport="stdio") == {
            "mcp": {"servers": {"cairn": {"type": "stdio", "command": "/fake/bin/cairn",
                                          "args": ["serve"], "env": env}}}}
        assert agy_mcp_config_json(transport="stdio") == {
            "mcpServers": {"cairn": {"command": "/fake/bin/cairn", "args": ["serve"],
                                     "env": env}}}

    def test_custom_home_install_writes_env_into_generated_files(self, tmp_path, monkeypatch):
        env = self._custom_home_env(monkeypatch)
        from cairn.agent_install.clients.agy import agy_config_path

        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude", "cursor", "zcode", "agy"], transport="stdio")

        claude = json.loads((ws / ".mcp.json").read_text(encoding="utf-8"))
        cursor = json.loads((ws / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
        zcode = json.loads((ws / ".zcode" / "config.json").read_text(encoding="utf-8"))
        agy = json.loads(agy_config_path().read_text(encoding="utf-8"))
        assert claude["mcpServers"]["cairn"]["env"] == env
        assert cursor["mcpServers"]["cairn"]["env"] == env
        assert zcode["mcp"]["servers"]["cairn"]["env"] == env
        assert agy["mcpServers"]["cairn"]["env"] == env

    def test_default_home_generators_stay_env_less(self, monkeypatch):
        monkeypatch.delenv("CAIRN_HOME")
        from cairn.agent_install._common import mcp_config_json
        from cairn.agent_install.clients.agy import agy_mcp_config_json
        from cairn.agent_install.clients.zcode import zcode_mcp_config_json

        assert mcp_config_json(transport="stdio") == {
            "mcpServers": {"cairn": {"command": "/fake/bin/cairn", "args": ["serve"]}}}
        assert zcode_mcp_config_json(transport="stdio") == {
            "mcp": {"servers": {"cairn": {"type": "stdio", "command": "/fake/bin/cairn",
                                          "args": ["serve"]}}}}
        assert agy_mcp_config_json(transport="stdio") == {
            "mcpServers": {"cairn": {"command": "/fake/bin/cairn", "args": ["serve"]}}}

    def test_home_set_to_default_generators_match_unset(self, monkeypatch):
        from cairn.agent_install._common import mcp_config_json
        from cairn.agent_install.clients.agy import agy_mcp_config_json
        from cairn.agent_install.clients.zcode import zcode_mcp_config_json

        generators = (mcp_config_json, zcode_mcp_config_json, agy_mcp_config_json)
        monkeypatch.delenv("CAIRN_HOME")
        unset = [json.dumps(gen(transport="stdio"), sort_keys=True) for gen in generators]
        self._set_default_home(monkeypatch)
        defaulted = [json.dumps(gen(transport="stdio"), sort_keys=True) for gen in generators]
        assert defaulted == unset

    def test_default_home_install_writes_no_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CAIRN_HOME")
        from cairn.agent_install.clients.agy import agy_config_path

        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude", "cursor", "zcode", "agy"], transport="stdio")

        flat_claude = json.loads((ws / ".mcp.json").read_text(encoding="utf-8"))
        flat_cursor = json.loads((ws / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
        nested_zcode = json.loads((ws / ".zcode" / "config.json").read_text(encoding="utf-8"))
        agy = json.loads(agy_config_path().read_text(encoding="utf-8"))
        assert "env" not in flat_claude["mcpServers"]["cairn"]
        assert "env" not in flat_cursor["mcpServers"]["cairn"]
        assert "env" not in nested_zcode["mcp"]["servers"]["cairn"]
        assert "env" not in agy["mcpServers"]["cairn"]

    def test_home_set_to_default_files_byte_identical_to_unset(self, tmp_path, monkeypatch):
        from cairn.agent_install.clients.agy import agy_config_path

        monkeypatch.delenv("CAIRN_HOME")
        ws_unset = tmp_path / "ws_unset"
        ws_unset.mkdir()
        install(str(ws_unset), clients=["claude", "cursor", "zcode", "agy"],
                transport="stdio")
        unset = {
            ".mcp.json": (ws_unset / ".mcp.json").read_bytes(),
            ".cursor/mcp.json": (ws_unset / ".cursor" / "mcp.json").read_bytes(),
            ".zcode/config.json": (ws_unset / ".zcode" / "config.json").read_bytes(),
            "agy": agy_config_path().read_bytes(),
        }
        agy_config_path().unlink()

        self._set_default_home(monkeypatch)
        ws_default = tmp_path / "ws_default"
        ws_default.mkdir()
        install(str(ws_default), clients=["claude", "cursor", "zcode", "agy"],
                transport="stdio")

        assert (ws_default / ".mcp.json").read_bytes() == unset[".mcp.json"]
        assert (ws_default / ".cursor" / "mcp.json").read_bytes() == unset[".cursor/mcp.json"]
        assert (ws_default / ".zcode" / "config.json").read_bytes() == unset[".zcode/config.json"]
        assert agy_config_path().read_bytes() == unset["agy"]


# --------------------------------------------------------------------------
# FR-006: install-time per-client verification (TC-010 / TC-011; D-005, D-006)
# --------------------------------------------------------------------------

class TestInstallVerification:
    """After a stdio install under a custom CAIRN_HOME, every file-written
    client carries a verification verdict: the registration's exact binary+env
    is spawned with probe args (`config --json`, D-005) from inside the
    workspace and its resolved store compared with the install target
    (TC-010 healthy PASS, TC-011 FAIL naming both stores). dry_run never
    spawns, and SSE / CLI-registered clients get no verdict (D-006 scope).

    TDD state: the healthy-PASS and FAIL tests are RED until T018 (the
    defaulted `verification_status` / `verification_detail` fields on
    InstallResult) and T019 (the spawn-probe loop in install()) land — they
    must fail on the missing verdict, never on anything else. The dry_run and
    skip guards are written with getattr(..., "skipped") so they hold both
    before (field absent) and after (verdict stays "skipped") T018/T019, and
    only fail if a wrong implementation starts verifying what D-005/D-006
    exempt.

    Hermeticity (C-04): workspaces live in tmp_path only; spawns are observed
    through real PATH shims, never by patching subprocess globals.
    """

    def _custom_home(self, tmp_path, monkeypatch) -> tuple[Path, Path]:
        """A custom CAIRN_HOME + workspace with the import-time bindings
        re-pointed; returns (home, ws)."""
        home = tmp_path / "cairn_home"
        ws = tmp_path / "ws"
        ws.mkdir()
        _repoint_home_bindings(monkeypatch, home)
        monkeypatch.setenv("CAIRN_WORKSPACE", str(ws))
        return home, ws

    def test_healthy_install_marks_every_file_written_client_pass(
            self, tmp_path, monkeypatch):
        """TC-010: a stdio install under a custom CAIRN_HOME with a built
        store on a healthy machine verifies every file-written client PASS —
        no client missing a verdict."""
        from cairn.paths import store_key

        real = shutil.which("cairn")
        assert real, "verification spawns the real cairn binary; it must be on PATH"
        home, ws = self._custom_home(tmp_path, monkeypatch)
        key = store_key(ws.resolve())
        (home / key).mkdir(parents=True)  # TC-010 Given: a built store
        (home / key / ".kg").write_bytes(b"")

        rep = install(str(ws), clients=["claude", "cursor", "zcode"],
                      transport="stdio")

        for res in rep.results:
            assert res.written, f"{res.client}: premise — file-written stdio client"
            assert res.verification_status == "pass", (
                f"{res.client}: healthy install must verify PASS, got "
                f"{res.verification_status!r} ({res.verification_detail})")

    def test_path_shadowed_cairn_fails_naming_both_stores(
            self, tmp_path, monkeypatch):
        """TC-011: a PATH-shadowed cairn that drops CAIRN_HOME makes the
        registration resolve the default store; the affected client's verdict
        is FAIL naming both the resolved and the intended store — per client,
        not one overall pass/fail."""
        from cairn.paths import store_key

        real = shutil.which("cairn")
        assert real, "the shim execs the real cairn binary; it must be on PATH"
        home, ws = self._custom_home(tmp_path, monkeypatch)
        key = store_key(ws.resolve())

        # Drop CAIRN_HOME (and pin HOME so the default the probe resolves is
        # deterministic regardless of which env the verifier passes it).
        shim = _shim_on_path(tmp_path, monkeypatch, "cairn",
                             f'exec env -u CAIRN_HOME HOME="{Path.home()}" '
                             f'"{real}" "$@"\n')

        rep = install(str(ws), clients=["claude", "cursor"], transport="stdio")

        written = json.loads((ws / ".mcp.json").read_text(encoding="utf-8"))
        assert written["mcpServers"]["cairn"]["command"] == str(shim), \
            "premise: the shadow bit — registrations point at the shimmed cairn"

        intended = home / key
        resolved = Path.home() / ".cairn" / key
        for res in rep.results:
            assert res.verification_status == "fail", (
                f"{res.client}: env-dropping registration must verify FAIL, got "
                f"{res.verification_status!r}")
            assert str(intended) in res.verification_detail, (
                f"{res.client}: FAIL must name the intended store {intended}")
            assert str(resolved) in res.verification_detail, (
                f"{res.client}: FAIL must name the store it actually "
                f"resolved ({resolved})")

    def test_dry_run_never_spawns_the_probe(self, tmp_path, monkeypatch):
        """D-005: dry_run never spawns — even for clients a real run would
        verify. Observed with a real PATH shim that logs any invocation."""
        _, ws = self._custom_home(tmp_path, monkeypatch)
        log = tmp_path / "shim_spawns.log"
        shim = _shim_on_path(tmp_path, monkeypatch, "cairn",
                             f'echo "$0 $*" >> "{log}"\nexit 127\n')
        assert shutil.which("cairn") == str(shim), \
            "premise: the shim shadows cairn"

        rep = install(str(ws), clients=["claude", "cursor"], transport="stdio",
                      dry_run=True)

        assert not log.exists(), \
            "dry_run must not spawn the registration binary (D-005)"
        for res in rep.results:
            assert getattr(res, "verification_status", "skipped") == "skipped", \
                "dry-run results must not claim a verification verdict"

    def test_sse_registrations_get_no_verification_verdict(
            self, tmp_path, monkeypatch):
        """D-006: SSE registrations are URL-based — nothing to spawn, no
        verdict (today: field absent; after T019: stays "skipped")."""
        ws = tmp_path / "ws"
        ws.mkdir()

        rep = install(str(ws), clients=["claude"], transport="sse")

        entry = json.loads((ws / ".mcp.json").read_text(encoding="utf-8"))
        cairn_entry = entry["mcpServers"]["cairn"]
        assert "url" in cairn_entry and "command" not in cairn_entry, \
            "premise: SSE registration is URL-based"
        res = next(r for r in rep.results if r.client == "claude")
        assert getattr(res, "verification_status", "skipped") == "skipped", \
            "SSE clients must not carry a spawn verdict (D-006)"

    def test_cli_registered_clients_get_no_verification_verdict(
            self, fake_home, tmp_path, monkeypatch):
        """D-006: global-scope claude registers through `claude mcp add` —
        cairn never writes the registration file, so there is nothing to
        read back and spawn-verify: no verdict, no probe spawn."""
        _, ws = self._custom_home(tmp_path, monkeypatch)
        log = tmp_path / "shim_spawns.log"
        cairn_shim = _shim_on_path(tmp_path, monkeypatch, "cairn",
                                   f'echo "$0 $*" >> "{log}"\nexit 127\n')
        claude_shim = _shim_on_path(tmp_path, monkeypatch, "claude",
                                    "exit 0\n")

        # conftest blocks agent CLIs suite-wide; this test explicitly creates
        # one (same philosophy as _cli_at) on top of the blocker, while cairn
        # resolves through the real PATH lookup to the shim above.
        blocked_which = shutil.which

        def _shimmed_which(cmd, *a, **k):
            if cmd == "claude":
                return str(claude_shim)
            return blocked_which(cmd, *a, **k)

        monkeypatch.setattr(shutil, "which", _shimmed_which)

        rep = install(str(ws), clients=["claude"], scope="global",
                      transport="stdio")

        assert shutil.which("cairn") == str(cairn_shim), \
            "premise: the cairn shim is what a probe would spawn"
        res = next(r for r in rep.results if r.client == "claude")
        assert getattr(res, "verification_status", "skipped") == "skipped", \
            "CLI-registered clients must not carry a spawn verdict (D-006)"
        assert not log.exists(), \
            "CLI-registered registrations must not be spawn-verified"
