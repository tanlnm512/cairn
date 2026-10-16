"""Round-2 audit regressions for agent_install (batch A9, G003-G045)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cairn.agent_install._common import InstallResult, _TEMPLATE_DIR, daemon_mcp_url, mcp_config_json
from cairn.agent_install.detect import _json_has_cairn
from cairn.agent_install.merge import (
    _deep_merge,
    _merge_json_file,
    _rm_tree_if_cairn,
    _rmdir_if_empty,
    _strip_cursor_hooks,
    _strip_hooks,
    _write_tree,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "fake_home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda *a, **k: home)
    return home


def _no_cli(monkeypatch, *names):
    real_which = shutil.which

    def fake(cmd, path=None):
        return None if cmd in names else real_which(cmd)

    monkeypatch.setattr(shutil, "which", fake)


@pytest.fixture
def default_bind(monkeypatch):
    """Pin the daemon bind discovery so URL assertions are environment-independent."""
    from cairn.mcp_server import lifecycle as lc

    monkeypatch.setattr(lc, "daemon_bind", lambda: (None, None), raising=False)


@pytest.fixture
def no_healthz(monkeypatch):
    from cairn.mcp_server import lifecycle as lc

    monkeypatch.setattr(lc, "healthz_responds", lambda **k: False, raising=False)


# G003: registrations follow the daemon's actual bind


class TestDaemonUrlBind:
    def test_url_reflects_discovered_bind(self, monkeypatch):
        from cairn.mcp_server import lifecycle as lc

        monkeypatch.setattr(lc, "daemon_bind", lambda: (9123, "127.0.0.1"))
        assert daemon_mcp_url() == "http://127.0.0.1:9123/mcp"
        assert mcp_config_json()["mcpServers"]["cairn"]["url"] == "http://127.0.0.1:9123/mcp"

    def test_url_falls_back_to_default_when_undiscoverable(self, monkeypatch):
        from cairn.mcp_server import lifecycle as lc

        monkeypatch.setattr(lc, "daemon_bind", lambda: (None, None))
        assert daemon_mcp_url() == "http://127.0.0.1:9876/mcp"


# G004: configs embedding the persisted key carry a commit warning


class TestEmbeddedKeyNote:
    def test_written_config_with_headers_gets_commit_warning(self, tmp_path, monkeypatch):
        from cairn.agent_install import _note_embedded_key

        monkeypatch.setattr(
            "cairn.agent_install._common.auth_headers",
            lambda: {"Authorization": "Bearer k"},
        )
        cfg = tmp_path / ".mcp.json"
        cfg.write_text(json.dumps(
            {"mcpServers": {"cairn": {"type": "http", "url": "http://h/mcp",
                                      "headers": {"Authorization": "Bearer k"}}}}))
        res = InstallResult("claude")
        res.written.append(str(cfg))
        _note_embedded_key([res])
        assert any("embeds the daemon API key" in n for n in res.notes)

    def test_no_key_no_warning(self, tmp_path, monkeypatch):
        from cairn.agent_install import _note_embedded_key

        monkeypatch.setattr("cairn.agent_install._common.auth_headers", lambda: {})
        cfg = tmp_path / ".mcp.json"
        cfg.write_text(json.dumps({"mcpServers": {}}))
        res = InstallResult("claude")
        res.written.append(str(cfg))
        _note_embedded_key([res])
        assert not any("API key" in n for n in res.notes)


# G005: a non-object value under a merged key costs only that key


class TestNonObjectMergePreservesSiblings:
    def test_merge_keeps_unrelated_keys_and_writes_bak(self, tmp_path):
        from cairn.agent_install.clients.opencode import opencode_mcp_config_json

        p = tmp_path / "opencode.json"
        p.write_text(json.dumps({"mcp": True, "theme": "dark", "keybinds": {"a": 1}}))
        _merge_json_file(p, opencode_mcp_config_json(), False, InstallResult("t"),
                         config_key="opencode")
        data = json.loads(p.read_text())
        assert data["theme"] == "dark"
        assert data["keybinds"] == {"a": 1}
        assert "cairn" in data["mcp"]
        assert p.with_suffix(".json.bak").exists()


# G035: one failing client must not abort the whole run


class TestInstallerErrorBoundary:
    def test_install_continues_past_a_failing_client(self, tmp_path, monkeypatch):
        import cairn.agent_install as ai

        def boom(*a, **k):
            raise RuntimeError("backup failed")

        monkeypatch.setitem(ai._INSTALLERS, "kilo", boom)
        ws = tmp_path / "ws"
        ws.mkdir()
        rep = ai.install(str(ws), clients=["kilo", "cursor"], dry_run=True)
        by_client = {r.client: r for r in rep.results}
        assert {"kilo", "cursor"} <= set(by_client)
        assert any("install failed" in n for n in by_client["kilo"].notes)
        assert by_client["cursor"].notes  # cursor ran normally

    def test_uninstall_continues_past_a_failing_client(self, tmp_path, monkeypatch):
        import cairn.agent_install as ai

        def boom(ws, res, scope="workspace"):
            raise RuntimeError("strip failed")

        monkeypatch.setitem(ai._UNINSTALLERS, "kilo", boom)
        ws = tmp_path / "ws"
        ws.mkdir()
        rep = ai.uninstall(str(ws), clients=["kilo", "cursor"])
        by_client = {r.client: r for r in rep.results}
        assert any("uninstall failed" in n for n in by_client["kilo"].notes)
        assert by_client["cursor"] is not None


# G036: git-hook failures land in the report


class TestGitHookErrorReported:
    def test_hook_failure_recorded_in_report(self, tmp_path, monkeypatch):
        from cairn.graph import scanner as scanner_mod
        from cairn.hooks import git_hooks

        monkeypatch.setattr(scanner_mod, "discover_repos", lambda ws: ["repo"])
        monkeypatch.setattr(scanner_mod, "repository_id", lambda r: "repo")

        def boom(repos, workspace):
            raise ValueError("no git")

        monkeypatch.setattr(git_hooks, "install_hooks", boom)
        ws = tmp_path / "ws"
        ws.mkdir()
        rep = __import__("cairn.agent_install", fromlist=["install"]).install(
            str(ws), clients=["cursor"], include_git_hooks=True)
        assert rep.git_hooks_error == "git hooks skipped — no git"
        assert rep.git_hooks_installed == []


# G037: empty directories left by install are pruned on uninstall


class TestEmptyDirPruning:
    def test_rmdir_if_empty(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        res = InstallResult("t")
        _rmdir_if_empty(empty, res)
        assert not empty.exists()
        full = tmp_path / "full"
        full.mkdir()
        (full / "f.txt").write_text("x")
        _rmdir_if_empty(full, res)
        assert full.exists()

    def test_uninstall_removes_empty_agents_and_client_dirs(
        self, tmp_path, monkeypatch, default_bind, no_healthz
    ):
        _no_cli(monkeypatch, "claude")
        from cairn.agent_install import install, uninstall

        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude"])
        uninstall(str(ws), clients=["claude"])
        assert not (ws / ".agents").exists(), "empty .agents/ skeleton must be pruned"
        assert not (ws / ".claude" / "commands").exists()
        assert not (ws / ".claude" / "skills").exists()


# G038/G039: hook stripping tolerates non-string commands


class TestHookStripToleratesNonStringCommands:
    def test_claude_strip_survives_null_command(self, tmp_path):
        p = tmp_path / "settings.json"
        payload = {"hooks": {"PostToolUse": [
            {"hooks": [{"type": "command", "command": None}]}]}}
        p.write_text(json.dumps(payload))
        _strip_hooks(p, InstallResult("t"))
        assert json.loads(p.read_text()) == payload

    def test_claude_strip_survives_null_inner_hooks(self, tmp_path):
        p = tmp_path / "settings.json"
        payload = {"hooks": {"Stop": [{"hooks": None, "command": "echo mine"}]}}
        p.write_text(json.dumps(payload))
        _strip_hooks(p, InstallResult("t"))
        assert json.loads(p.read_text()) == payload

    def test_cursor_strip_survives_null_command(self, tmp_path):
        p = tmp_path / "hooks.json"
        payload = {"hooks": {"afterFileEdit": [{"command": None, "timeout": 5}]}}
        p.write_text(json.dumps(payload))
        _strip_cursor_hooks(p, InstallResult("t"))
        assert json.loads(p.read_text()) == payload

    def test_real_cairn_hooks_still_stripped(self, tmp_path):
        from cairn.agent_install._common import _claude_hook_command

        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": _claude_hook_command("session_end")}]}]}}))
        _strip_hooks(p, InstallResult("t"))
        assert "hooks" not in json.loads(p.read_text())


# G040: kilo scope='all' installs where scope='global' does


class TestKiloScopeAll:
    def test_all_resolves_to_global_path(self, fake_home):
        from cairn.agent_install.clients.kilo import _kilo_config_path

        assert _kilo_config_path("/ws", "all") == fake_home / ".config" / "kilo" / "kilo.json"
        assert _kilo_config_path("/ws", "global") == fake_home / ".config" / "kilo" / "kilo.json"
        assert _kilo_config_path("/ws", "workspace") == Path("/ws") / "kilo.json"


# G041: dry-run previews the legacy zcode strip


class TestZcodeLegacyStripDryRun:
    def test_dry_run_reports_and_skips_legacy_strip(
        self, fake_home, tmp_path, monkeypatch, default_bind
    ):
        _no_cli(monkeypatch, "zcode")
        from cairn.agent_install.clients.zcode import install_zcode

        legacy = fake_home / ".zcode" / "config.json"
        legacy.parent.mkdir(parents=True)
        before = {"mcp": {"servers": {"cairn": {"command": "/old"}}}}
        legacy.write_text(json.dumps(before))
        ws = tmp_path / "ws"
        ws.mkdir()

        res = install_zcode(str(ws), force=False, dry_run=True, scope="global")

        assert any("would strip cairn from" in w for w in res.written)
        assert json.loads(legacy.read_text()) == before

    def test_real_run_still_strips_legacy(
        self, fake_home, tmp_path, monkeypatch, default_bind
    ):
        _no_cli(monkeypatch, "zcode")
        from cairn.agent_install.clients.zcode import install_zcode

        legacy = fake_home / ".zcode" / "config.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps({"mcp": {"servers": {"cairn": {"command": "/old"}}}}))
        ws = tmp_path / "ws"
        ws.mkdir()

        install_zcode(str(ws), force=False, dry_run=False, scope="global")

        assert "cairn" not in json.loads(legacy.read_text()).get("mcp", {}).get("servers", {})


# G042: the installer-written AGENTS.md is removed on uninstall


class TestAgentsMdUninstall:
    def test_install_written_agents_md_removed(self, tmp_path, monkeypatch,
                                               default_bind, no_healthz):
        _no_cli(monkeypatch, "zcode")
        from cairn.agent_install import install, uninstall
        from cairn.agent_install._common import _agents_instructions

        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["zcode"])
        agents_md = ws / "AGENTS.md"
        assert agents_md.read_text() == _agents_instructions()

        uninstall(str(ws), clients=["zcode"])
        assert not agents_md.exists()

    def test_customized_agents_md_kept_with_note(self, tmp_path, monkeypatch,
                                                 default_bind, no_healthz):
        _no_cli(monkeypatch, "zcode")
        from cairn.agent_install import install, uninstall

        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["zcode"])
        agents_md = ws / "AGENTS.md"
        agents_md.write_text("# my own agents instructions\n")

        rep = uninstall(str(ws), clients=["zcode"])
        assert agents_md.read_text() == "# my own agents instructions\n"
        zc = next(r for r in rep.results if r.client == "zcode")
        assert any("differs from the current cairn template" in s for s in zc.skipped)


# G043: a top-level cairn key must look like a registration


class TestJsonHasCairnShape:
    def test_unrelated_top_level_cairn_key_is_not_installed(self, tmp_path):
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps({"cairn": {"enabled": "some-other-tool"}, "other": 1}))
        assert _json_has_cairn(p) is False

    def test_registration_shaped_top_level_key_counts(self, tmp_path):
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps(
            {"cairn": {"type": "http", "url": "http://127.0.0.1:9876/mcp"}}))
        assert _json_has_cairn(p) is True


# G044: the zcode merge only removes a cairn-stamped mcpServers entry


class TestZcodeMcpServersScopedPop:
    MERGER = {"mcp": {"servers": {"cairn": {"type": "http", "url": "http://h/mcp"}}}}

    def test_user_servers_in_mcpServers_survive(self):
        merged = _deep_merge({"mcpServers": {"other": {"command": "x"}}, "mcp": {}},
                             self.MERGER, config_key="zcode")
        assert merged["mcpServers"] == {"other": {"command": "x"}}

    def test_stale_cairn_entry_still_removed(self):
        merged = _deep_merge(
            {"mcpServers": {"cairn": {"command": "/old"}, "other": {"command": "x"}}, "mcp": {}},
            self.MERGER, config_key="zcode")
        assert "cairn" not in merged.get("mcpServers", {})
        assert merged["mcpServers"] == {"other": {"command": "x"}}

    def test_lone_stale_cairn_entry_drops_the_key(self):
        merged = _deep_merge({"mcpServers": {"cairn": {"command": "/old"}}, "mcp": {}},
                             self.MERGER, config_key="zcode")
        assert "mcpServers" not in merged

    def test_non_dict_mcpServers_untouched(self):
        merged = _deep_merge({"mcpServers": "user-data", "mcp": {}},
                             self.MERGER, config_key="zcode")
        assert merged["mcpServers"] == "user-data"


# G045: tree removal deletes only template-matching files


class TestTreeRemovalProvenance:
    def _install_tree(self, dest: Path) -> None:
        _write_tree(dest, _TEMPLATE_DIR / "skill", True, InstallResult("t"))

    def test_pristine_tree_fully_removed(self, tmp_path):
        skill = tmp_path / "skills" / "cairn"
        self._install_tree(skill)
        res = InstallResult("t")
        _rm_tree_if_cairn(skill, res)
        assert not skill.exists()
        assert any("removed" in w for w in res.written)

    def test_user_added_file_survives_with_note(self, tmp_path):
        skill = tmp_path / "skills" / "cairn"
        self._install_tree(skill)
        user_file = skill / "references" / "my-notes.md"
        user_file.write_text("# mine\n")
        res = InstallResult("t")
        _rm_tree_if_cairn(skill, res)
        assert user_file.exists(), "user-added file must survive"
        assert not (skill / "SKILL.md").exists(), "template files still go"
        assert any("non-template file" in n for n in res.notes)

    def test_customized_template_file_survives(self, tmp_path):
        skill = tmp_path / "skills" / "cairn"
        self._install_tree(skill)
        skill_md = skill / "SKILL.md"
        skill_md.write_text("# heavily customized\n")
        res = InstallResult("t")
        _rm_tree_if_cairn(skill, res)
        assert skill_md.exists()
        assert any("non-template file" in n for n in res.notes)

    def test_non_cairn_dir_still_refused(self, tmp_path):
        victim = tmp_path / "skills" / "not-cairn"
        victim.mkdir(parents=True)
        (victim / "f.txt").write_text("x")
        res = InstallResult("t")
        _rm_tree_if_cairn(victim, res)
        assert victim.exists()
        assert any("not cairn-scoped" in n for n in res.notes)
