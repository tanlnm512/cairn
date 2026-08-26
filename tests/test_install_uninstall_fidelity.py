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
        no --transport flag (regression pin for the pre-SSE behavior)."""
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
        no --type flag (regression pin)."""
        _cli_at(monkeypatch, "droid")
        calls = _spy_subprocess(monkeypatch)
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["droid"], transport="stdio")

        add = [c for c in calls if c[:3] == ["droid", "mcp", "add"]]
        assert len(add) == 1
        assert "--type" not in add[0]
        assert "serve" in add[0]
