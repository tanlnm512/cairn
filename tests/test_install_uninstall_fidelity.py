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
ever executes. The one opt-in CLI integration test runs the real `claude`
binary with HOME pointed at a throwaway dir (same guarantees).
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


# --------------------------------------------------------------------------
# CLI wiring: --scope plumbing and the --dry-run gate on `cairn uninstall`
# --------------------------------------------------------------------------

class TestCliScopeWiring:

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
        # -e entries precede the `--` separator; the server command follows it.
        assert f"CAIRN_HOME={custom_home}" in add[1]
        assert add[1].index(f"CAIRN_HOME={custom_home}") < add[1].index("--")
        assert add[1][-1] == "serve"


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


# --------------------------------------------------------------------------
# omp (oh-my-pi CLI): native mcpServers config + native .omp/agents/*.md subagents
# --------------------------------------------------------------------------

class TestOmpClient:

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
    """Real-subprocess install verification: a healthy install marks every
    written-client file PASS; a PATH-shadowed cairn names both stores in the
    FAIL verdict."""

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


# --------------------------------------------------------------------------
# Claude Code global MCP registration (`claude mcp add --scope user`)
# --------------------------------------------------------------------------

# Pristine shutil.which, captured at import time -- before the hermetic
# fixture swaps in its agent-CLI-blocking replacement.
_PRISTINE_WHICH = shutil.which


class TestClaudeGlobalMcpRegistration:
    """`install-agents --scope global` registers Claude Code's MCP via the
    `claude` CLI (Claude Code reads no global ~/.mcp.json file). The spawned
    argv must survive the CLI's own option parser and the outcome must be
    reported honestly."""

    def test_stdio_argv_ends_option_parsing_before_server_command(
            self, fake_home, tmp_path, monkeypatch):
        """Module-fallback command shape (`python -m cairn.cli.main serve`):
        `--` must separate it from claude's own options, or the CLI parses
        the server command's flags as its own and the add fails."""
        from cairn.agent_install.clients import claude as claude_mod

        monkeypatch.delenv("CAIRN_HOME", raising=False)
        _cli_at(monkeypatch, "claude")
        calls = _spy_subprocess(monkeypatch)
        monkeypatch.setattr(claude_mod, "resolve_cg_command",
                            lambda: ["/usr/bin/python3", "-m", "cairn.cli.main"])

        ws = tmp_path / "ws"
        ws.mkdir()
        rep = install(str(ws), clients=["claude"], scope="global", transport="stdio")

        assert calls == [["claude", "mcp", "add", "cairn", "--scope", "user",
                          "--", "/usr/bin/python3", "-m", "cairn.cli.main",
                          "serve"]]
        res = next(r for r in rep.results if r.client == "claude")
        assert any("Registered MCP globally" in n for n in res.notes)

    def test_stdio_argv_pins_home_env_before_separator(
            self, fake_home, tmp_path, monkeypatch):
        """A non-default home rides along as -e entries placed before `--`;
        everything after `--` is the server command verbatim."""
        from cairn.agent_install.clients import claude as claude_mod

        _cli_at(monkeypatch, "claude")
        calls = _spy_subprocess(monkeypatch)
        monkeypatch.setattr(claude_mod, "resolve_cg_command", lambda: ["/fake/cairn"])
        monkeypatch.setattr(claude_mod, "cairn_home_env",
                            lambda: {"CAIRN_HOME": "/custom/home"})

        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude"], scope="global", transport="stdio")

        assert calls == [["claude", "mcp", "add", "cairn", "--scope", "user",
                          "-e", "CAIRN_HOME=/custom/home",
                          "--", "/fake/cairn", "serve"]]

    def test_stdio_nonzero_exit_reports_warning_not_success(
            self, fake_home, tmp_path, monkeypatch):
        """A failed `claude mcp add` (non-zero exit) must surface as a
        WARNING naming the exit code and the CLI's stderr -- never as the
        success note."""
        from cairn.agent_install.clients import claude as claude_mod

        _cli_at(monkeypatch, "claude")
        monkeypatch.setattr(claude_mod, "resolve_cg_command",
                            lambda: ["/usr/bin/python3", "-m", "cairn.cli.main"])

        class _R:
            returncode = 1
            stdout = ""
            stderr = "error: unknown option '-m'"

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())

        ws = tmp_path / "ws"
        ws.mkdir()
        rep = install(str(ws), clients=["claude"], scope="global", transport="stdio")

        res = next(r for r in rep.results if r.client == "claude")
        assert not any("Registered MCP globally" in n for n in res.notes)
        assert any("exited 1" in n and "unknown option '-m'" in n
                   for n in res.notes)

    def test_doctor_reads_claude_user_scope_registration(
            self, fake_home, tmp_path):
        """The user-scope file `claude mcp add --scope user` writes
        (~/.claude.json) is one of the configs the registration audit reads,
        and it alone marks claude installed."""
        from cairn.agent_install import check_installed
        from cairn.cli.system import _enumerate_registrations

        ws = tmp_path / "ws"
        ws.mkdir()
        entry = {"type": "stdio", "command": "/bin/echo", "args": ["serve"]}
        user_cfg = fake_home / ".claude.json"
        user_cfg.write_text(json.dumps({"mcpServers": {"cairn": entry}}),
                            encoding="utf-8")

        assert check_installed(str(ws))["claude"] is True

        claude_hits = [(c, d, e) for c, d, e in _enumerate_registrations()
                       if c == "claude"]
        assert ("claude", "~/.claude.json", entry) in claude_hits

    def test_doctor_reads_droid_user_scope_registration(
            self, fake_home, tmp_path):
        """The user-scope file `droid mcp add` writes (~/.factory/mcp.json)
        is one of the configs the registration audit reads, and it alone
        marks droid installed."""
        from cairn.agent_install import check_installed
        from cairn.cli.system import _enumerate_registrations

        ws = tmp_path / "ws"
        ws.mkdir()
        entry = {"type": "stdio", "command": "/bin/echo", "args": ["serve"]}
        user_cfg = fake_home / ".factory" / "mcp.json"
        user_cfg.parent.mkdir(parents=True)
        user_cfg.write_text(json.dumps({"mcpServers": {"cairn": entry}}),
                            encoding="utf-8")

        assert check_installed(str(ws))["droid"] is True

        droid_hits = [(c, d, e) for c, d, e in _enumerate_registrations()
                      if c == "droid"]
        assert ("droid", "~/.factory/mcp.json", entry) in droid_hits

    @pytest.mark.skipif(
        not os.environ.get("CAIRN_TEST_CLAUDE_CLI") or not shutil.which("claude"),
        reason="opt-in end-to-end probe (CAIRN_TEST_CLAUDE_CLI=1); needs the real claude CLI",
    )
    def test_claude_cli_global_registration_end_to_end(
            self, tmp_path, monkeypatch):
        """The real `claude mcp add` accepts the spawned argv and stores the
        full server command (with its flags) as the registration. HOME is
        pointed at a throwaway dir, so the real ~/.claude is never touched."""
        from cairn.agent_install._common import resolve_cg_command

        # Unblock the hermetic fixture's which() for claude only; the
        # throwaway-HOME sandbox still applies to everything else.
        monkeypatch.setattr(
            shutil, "which",
            lambda name, *a, **k: _PRISTINE_WHICH(name, *a, **k) if name == "claude" else None)

        home = tmp_path / "claude_home"
        home.mkdir()
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setattr(Path, "home", lambda *a, **k: home)
        monkeypatch.setenv("HOME", str(home))

        install(str(ws), clients=["claude"], scope="global", transport="stdio")

        cfg = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
        entry = cfg["mcpServers"]["cairn"]
        cmd = resolve_cg_command()
        assert entry["type"] == "stdio"
        assert entry["command"] == cmd[0]
        assert entry["args"] == [*cmd[1:], "serve"]


# --------------------------------------------------------------------------
# Droid CLI registration (`droid mcp add`)
# --------------------------------------------------------------------------

class TestDroidCliRegistration:
    """`droid mcp add` is a subprocess whose outcome must be reported
    honestly and whose argv must match the documented stdio shape (the full
    server command as ONE argument). A failed add falls back to the
    .factory/mcp.json file so the registration lands either way."""

    def test_stdio_argv_passes_server_command_as_one_argument(
            self, tmp_path, monkeypatch):
        from cairn.agent_install.clients import droid as droid_mod

        _cli_at(monkeypatch, "droid")
        calls = _spy_subprocess(monkeypatch)
        monkeypatch.delenv("CAIRN_HOME", raising=False)
        monkeypatch.setattr(droid_mod, "resolve_cg_command",
                            lambda: ["/usr/bin/python3", "-m", "cairn.cli.main"])
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["droid"], transport="stdio")

        assert calls == [["droid", "mcp", "add", "cairn",
                          "/usr/bin/python3 -m cairn.cli.main serve",
                          "--type", "stdio"]]
        assert not (ws / ".factory" / "mcp.json").exists()

    def test_stdio_argv_pins_home_env(self, tmp_path, monkeypatch):
        from cairn.agent_install.clients import droid as droid_mod

        _cli_at(monkeypatch, "droid")
        calls = _spy_subprocess(monkeypatch)
        monkeypatch.setattr(droid_mod, "resolve_cg_command", lambda: ["/fake/cairn"])
        monkeypatch.setattr(droid_mod, "cairn_home_env",
                            lambda: {"CAIRN_HOME": "/custom/home"})
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["droid"], transport="stdio")

        add = calls[0]
        assert add[-2:] == ["--env", "CAIRN_HOME=/custom/home"]

    def test_failed_add_warns_and_falls_back_to_file(
            self, tmp_path, monkeypatch):
        import cairn.agent_install._common as common_mod
        from cairn.agent_install.clients import droid as droid_mod

        _cli_at(monkeypatch, "droid")
        monkeypatch.delenv("CAIRN_HOME", raising=False)
        # Both argv build and file fallback resolve the command independently.
        monkeypatch.setattr(common_mod, "resolve_cg_command", lambda: ["/fake/cairn"])
        monkeypatch.setattr(droid_mod, "resolve_cg_command", lambda: ["/fake/cairn"])

        class _R:
            returncode = 1
            stdout = ""
            stderr = "error: server cairn already exists"

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
        ws = tmp_path / "ws"
        ws.mkdir()

        rep = install(str(ws), clients=["droid"], transport="stdio")

        res = next(r for r in rep.results if r.client == "droid")
        assert not any("Registered MCP via" in n for n in res.notes)
        assert any("exited 1" in n and "already exists" in n for n in res.notes)
        fallback = json.loads((ws / ".factory" / "mcp.json").read_text(encoding="utf-8"))
        assert fallback["mcpServers"]["cairn"]["command"] == "/fake/cairn"

    def test_remove_nonzero_exit_reports_warning(
            self, fake_home, tmp_path, monkeypatch):
        _cli_at(monkeypatch, "droid")

        class _R:
            returncode = 1
            stdout = ""
            stderr = "error: server cairn not found"

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
        ws = tmp_path / "ws"
        ws.mkdir()

        rep = uninstall(str(ws), clients=["droid"])

        res = next(r for r in rep.results if r.client == "droid")
        assert not any("Removed MCP registration" in n for n in res.notes)
        assert any("exited 1" in n and "not found" in n for n in res.notes)
