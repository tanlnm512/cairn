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
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from cairn.agent_install import check_installed, install, uninstall

# Independent truth (FR-005 default port, D-007 single /mcp path).
DAEMON_URL = "http://127.0.0.1:9876/mcp"

_TEST_KEY = "test-key-k1-0123456789abcdef"

# Every module the generators may bind the key reader through; patching them
# all keeps the pins independent of the generators' import style.
_KEY_MODULE_NAMES = [
    "cairn.mcp_server.auth",
    "cairn.agent_install._common",
    *[
        f"cairn.agent_install.clients.{n}"
        for n in (
            "claude",
            "claude_desktop",
            "cursor",
            "droid",
            "zcode",
            "agy",
            "opencode",
            "kilo",
            "omp",
        )
    ],
]


@pytest.fixture
def pinned_api_key(monkeypatch):
    """Pin the persisted-key reader so the embedded credential in every
    generated config is exactly this key."""
    import importlib

    for name in _KEY_MODULE_NAMES:
        monkeypatch.setattr(
            importlib.import_module(name),
            "persisted_api_key",
            lambda: _TEST_KEY,
            raising=False,
        )
    return _TEST_KEY


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


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
    monkeypatch.setattr(
        shutil,
        "which",
        lambda cmd, path=None: f"{binpath}/{cmd}" if cmd == name else None,
    )


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
            install(str(ws), clients=["cursor"])
        data = json.loads((ws / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
        assert len(data["hooks"]["afterFileEdit"]) == 1
        assert len(data["hooks"]["afterSessionEnd"]) == 1

    def test_claude_partial_hooks_reheal(self, tmp_path, monkeypatch):
        """The old ANY-present check made a partial config read as installed;
        now BOTH entrypoints are required so the reinstall heals it."""
        _no_cli(monkeypatch, "claude")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude"])
        p = ws / ".claude" / "settings.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["hooks"]["Stop"] = []
        p.write_text(json.dumps(data))
        install(str(ws), clients=["claude"])
        healed = json.loads(p.read_text(encoding="utf-8"))
        assert len(healed["hooks"]["Stop"]) == 1
        assert len(healed["hooks"]["PostToolUse"]) == 2

    def test_claude_reinstall_replaces_stale_hook_command(self, tmp_path, monkeypatch):
        import shlex
        import sys

        from cairn.agent_install._common import _claude_hook_command

        _no_cli(monkeypatch, "claude")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude"])
        p = ws / ".claude" / "settings.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        stale = "/tmp/Cairn Application Support/bin/python"
        for entries in data["hooks"].values():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    command = hook["command"]
                    hook["command"] = command.replace(
                        shlex.quote(sys.executable), stale
                    )
        p.write_text(json.dumps(data))

        install(str(ws), clients=["claude"])
        healed = json.loads(p.read_text(encoding="utf-8"))
        commands = [
            hook["command"]
            for entries in healed["hooks"].values()
            for entry in entries
            for hook in entry["hooks"]
        ]
        assert stale not in " ".join(commands)
        assert set(commands) == {
            _claude_hook_command("post_edit"),
            _claude_hook_command("post_tool_failure"),
            _claude_hook_command("session_end"),
            _claude_hook_command("session_start"),
        }


# --------------------------------------------------------------------------
# MCP registration re-heal: url/headers/enabled drift must read as
# not-installed so the reinstall rewrites the entry
# --------------------------------------------------------------------------


class TestMcpRegistrationReheal:
    def test_reinstall_rewrites_stale_daemon_url(self, tmp_path, monkeypatch):
        """A URL-based mcpServers entry whose daemon url changed must be
        rewritten by the next install instead of reading as already-installed."""
        _no_cli(monkeypatch, "claude")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude"])
        mcp = ws / ".mcp.json"
        data = json.loads(mcp.read_text(encoding="utf-8"))
        data["mcpServers"]["cairn"]["url"] = "http://127.0.0.1:1/mcp"
        mcp.write_text(json.dumps(data), encoding="utf-8")

        res = next(
            r for r in install(str(ws), clients=["claude"]).results
            if r.client == "claude"
        )

        healed = json.loads(mcp.read_text(encoding="utf-8"))
        assert healed["mcpServers"]["cairn"]["url"] == DAEMON_URL
        assert str(mcp) in res.written


# --------------------------------------------------------------------------
# Hook command strings embed the CAIRN_HOME assignment iff non-default
# --------------------------------------------------------------------------


class TestHookCairnHomePrefix:
    """Hook env contract: generated hook command strings
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

    def test_custom_home_install_writes_prefixed_hook_commands(
        self, tmp_path, monkeypatch
    ):
        env = self._custom_home_env(monkeypatch)
        _no_cli(monkeypatch, "claude", "cursor")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude", "cursor"])

        claude = json.loads(
            (ws / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        assert claude["hooks"]["PostToolUse"][0]["hooks"][0]["command"].startswith(
            f"CAIRN_HOME={env['CAIRN_HOME']} "
        )
        assert claude["hooks"]["Stop"][0]["hooks"][0]["command"].startswith(
            f"CAIRN_HOME={env['CAIRN_HOME']} "
        )
        cursor = json.loads((ws / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
        assert cursor["hooks"]["afterFileEdit"][0]["command"].startswith(
            f"CAIRN_HOME={env['CAIRN_HOME']} "
        )
        assert cursor["hooks"]["afterSessionEnd"][0]["command"].startswith(
            f"CAIRN_HOME={env['CAIRN_HOME']} "
        )

    def test_custom_home_git_hook_gains_quoted_export_line_after_shebang(
        self, tmp_path, monkeypatch
    ):
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
        assert [ln for ln in lines if "CAIRN_HOME" in ln] == [lines[1]], (
            "the export line must appear exactly once"
        )
        assert 'cairn update --repo "repo"' in hook
        assert "cairn validate-paths --mark" in hook

    # -- custom home: matching/idempotency survive the prefix -----------------

    def test_custom_home_prefixed_hooks_stay_idempotent_and_uninstallable(
        self, tmp_path, monkeypatch
    ):
        """With the prefix present in every written command (the shape this
        spec generates on a custom home), a reinstall must not duplicate
        entries and uninstall must still strip them -- matching is
        entrypoint-substring based, not full-command based."""
        self._custom_home_env(monkeypatch)
        _no_cli(monkeypatch, "claude", "cursor")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude", "cursor"])
        install(str(ws), clients=["claude", "cursor"])

        claude = json.loads(
            (ws / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        post_tool_use = claude["hooks"]["PostToolUse"]
        assert len(post_tool_use) == 2
        entrypoints = [
            h["command"].rsplit(" ", 1)[-1]
            for entry in post_tool_use
            for h in entry["hooks"]
        ]
        assert sorted(entrypoints) == ["post_edit", "post_tool_failure"]
        assert len(claude["hooks"]["Stop"]) == 1
        session_start = claude["hooks"]["SessionStart"]
        assert len(session_start) == 1
        assert session_start[0]["matcher"] == "startup"
        assert session_start[0]["hooks"][0]["command"].endswith(
            "claude_hooks session_start"
        )
        cursor = json.loads((ws / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
        assert len(cursor["hooks"]["afterFileEdit"]) == 1
        assert len(cursor["hooks"]["afterSessionEnd"]) == 1

        uninstall(str(ws), clients=["claude", "cursor"])
        claude = json.loads(
            (ws / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        assert "hooks" not in claude
        cursor = json.loads((ws / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
        assert "hooks" not in cursor

    # -- default home: nothing is added ---------------------------------------

    def test_default_home_hook_commands_stay_env_less(self, monkeypatch):
        monkeypatch.delenv("CAIRN_HOME", raising=False)
        import sys
        import shlex

        from cairn.agent_install._common import _claude_hook_command
        from cairn.agent_install.clients.claude import claude_hooks_block
        from cairn.agent_install.clients.cursor import cursor_hooks_json

        assert _claude_hook_command("post_edit") == (
            f"{shlex.quote(sys.executable)} -m cairn.hooks.claude_hooks post_edit"
        )
        assert _claude_hook_command("session_end") == (
            f"{shlex.quote(sys.executable)} -m cairn.hooks.claude_hooks session_end"
        )

        claude = claude_hooks_block()
        for event in ("PostToolUse", "Stop", "SessionStart"):
            for entry in claude[event]:
                for h in entry["hooks"]:
                    assert "CAIRN_HOME" not in h["command"]
        cursor = cursor_hooks_json()["hooks"]
        for event in ("afterFileEdit", "afterSessionEnd"):
            assert "CAIRN_HOME" not in cursor[event][0]["command"]

    def test_hook_interpreter_path_with_spaces_stays_one_shell_token(self, monkeypatch):
        import shlex
        import sys

        from cairn.agent_install._common import _claude_hook_command

        python = "/tmp/Cairn Application Support/bin/python"
        monkeypatch.delenv("CAIRN_HOME", raising=False)
        monkeypatch.setattr(sys, "executable", python)
        command = _claude_hook_command("post_edit")
        assert shlex.split(command, posix=True)[0] == python

    def test_default_home_git_hook_stays_env_less(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CAIRN_HOME", raising=False)
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".git").mkdir()
        from cairn.hooks.git_hooks import POST_COMMIT_TEMPLATE, install_hooks

        assert install_hooks(["repo"], str(ws)) == ["repo"]

        hook = (ws / ".git" / "hooks" / "post-commit").read_text(encoding="utf-8")
        assert hook == POST_COMMIT_TEMPLATE.format(repo="repo"), (
            "default home must keep the hook byte-identical to the template"
        )
        assert "CAIRN_HOME" not in hook


# --------------------------------------------------------------------------
# freshness-hooks FR-002: post-commit hook diffs the committed range
# --------------------------------------------------------------------------


class TestPostCommitRangeHook:
    """The post-commit hook backgrounds a range-scoped update over the
    committed span; a fresh repo's first commit (no HEAD@{1} reflog entry)
    falls back to an empty-tree range."""

    @staticmethod
    def _render() -> str:
        from cairn.hooks.git_hooks import POST_COMMIT_TEMPLATE

        return POST_COMMIT_TEMPLATE.format(repo="repo")

    def test_update_is_range_scoped_backgrounded_and_discarded(self):
        hook = self._render()
        assert (
            'cairn update --repo "repo" --diff-ref "$range" > /dev/null 2>&1 &'
        ) in hook
        assert "cairn validate-paths --mark" in hook

    def test_range_resolves_reflog_then_empty_tree_on_first_commit(self):
        hook = self._render()
        assert "git rev-parse -q --verify 'HEAD@{1}'" in hook
        assert "$(git hash-object -t tree /dev/null)..HEAD" in hook


# --------------------------------------------------------------------------
# freshness-hooks FR-003: post-checkout hook updates on branch switches only
# --------------------------------------------------------------------------


class TestPostCheckoutGuardHook:
    """The post-checkout hook updates old..new only when git reports a branch
    checkout ($3 = 1); a file checkout ($3 = 0) runs nothing."""

    @staticmethod
    def _render() -> str:
        from cairn.hooks.git_hooks import POST_CHECKOUT_TEMPLATE

        return POST_CHECKOUT_TEMPLATE.format(repo="repo")

    def test_branch_checkout_updates_the_ranged_span_in_background(self):
        hook = self._render()
        assert 'if [ "$3" = "1" ]; then' in hook
        assert (
            'cairn update --repo "repo" --diff-ref "$1..$2" > /dev/null 2>&1 &'
        ) in hook
        assert (
            hook.index('if [ "$3" = "1" ]; then')
            < hook.index('cairn update --repo "repo" --diff-ref "$1..$2"')
            < hook.index("fi")
        ), "the update must run inside the branch guard"

    def test_default_home_render_is_byte_identical_to_template(self, monkeypatch):
        monkeypatch.delenv("CAIRN_HOME", raising=False)
        from cairn.hooks.git_hooks import (
            POST_CHECKOUT_TEMPLATE,
            _render_post_checkout,
        )

        assert _render_post_checkout("repo") == POST_CHECKOUT_TEMPLATE.format(
            repo="repo"
        )
        assert "CAIRN_HOME" not in _render_post_checkout("repo")

    def test_custom_home_export_line_follows_the_shebang(self, tmp_path, monkeypatch):
        custom = tmp_path / "custom_home"
        monkeypatch.setenv("CAIRN_HOME", str(custom))
        from cairn.hooks.git_hooks import _render_post_checkout

        lines = _render_post_checkout("repo").splitlines()
        assert lines[0] == "#!/bin/bash"
        assert lines[1] == f'export CAIRN_HOME="{custom}"'
        assert [ln for ln in lines if "CAIRN_HOME" in ln] == [lines[1]]


# --------------------------------------------------------------------------
# freshness-hooks FR-004: install/uninstall manage both hook files per repo
# --------------------------------------------------------------------------


class TestGitHookInstallCoverage:
    """install_hooks/uninstall_hooks manage post-commit AND post-checkout per
    repo: the refuse-to-clobber guard and the cairn-marker removal rule apply
    per hook file, an existing cairn hook is rewritten in place, and the
    signature/return contract stays a list of repo names."""

    @staticmethod
    def _hooks_dir(ws: Path) -> Path:
        return ws / ".git" / "hooks"

    @staticmethod
    def _install(ws: Path) -> list[str]:
        from cairn.hooks.git_hooks import install_hooks

        return install_hooks(["repo"], str(ws))

    def test_install_writes_both_hooks_executable_and_uninstall_removes_both(
        self, tmp_path, monkeypatch
    ):
        import os

        monkeypatch.delenv("CAIRN_HOME", raising=False)
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".git").mkdir()

        assert self._install(ws) == ["repo"]

        for name in ("post-commit", "post-checkout"):
            hook = self._hooks_dir(ws) / name
            assert 'cairn update --repo "repo"' in hook.read_text(encoding="utf-8")
            assert os.access(hook, os.X_OK), "hooks must be executable for git"

        from cairn.hooks.git_hooks import uninstall_hooks

        assert uninstall_hooks(["repo"], str(ws)) == ["repo"]
        assert not (self._hooks_dir(ws) / "post-commit").exists()
        assert not (self._hooks_dir(ws) / "post-checkout").exists()
        assert uninstall_hooks(["repo"], str(ws)) == [], (
            "a second uninstall has nothing cairn-marked left to remove"
        )

    def test_foreign_post_commit_skipped_but_post_checkout_still_installed(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("CAIRN_HOME", raising=False)
        ws = tmp_path / "ws"
        ws.mkdir()
        hooks = self._hooks_dir(ws)
        hooks.mkdir(parents=True)
        foreign = "#!/bin/sh\necho mine\n"
        (hooks / "post-commit").write_text(foreign, encoding="utf-8")

        assert self._install(ws) == ["repo"], (
            "a repo with one foreign hook still gets its other cairn hook"
        )

        assert (hooks / "post-commit").read_text(encoding="utf-8") == foreign
        assert "cairn" in (hooks / "post-checkout").read_text(encoding="utf-8")

    def test_all_foreign_hooks_skip_the_repo_entirely(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CAIRN_HOME", raising=False)
        ws = tmp_path / "ws"
        ws.mkdir()
        hooks = self._hooks_dir(ws)
        hooks.mkdir(parents=True)
        foreign_pc = "#!/bin/sh\necho mine\n"
        foreign_pco = "#!/bin/sh\necho also-mine\n"
        (hooks / "post-commit").write_text(foreign_pc, encoding="utf-8")
        (hooks / "post-checkout").write_text(foreign_pco, encoding="utf-8")

        assert self._install(ws) == []
        assert (hooks / "post-commit").read_text(encoding="utf-8") == foreign_pc
        assert (hooks / "post-checkout").read_text(encoding="utf-8") == foreign_pco

    def test_existing_cairn_hook_is_upgraded_in_place(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CAIRN_HOME", raising=False)
        ws = tmp_path / "ws"
        ws.mkdir()
        hooks = self._hooks_dir(ws)
        hooks.mkdir(parents=True)
        (hooks / "post-commit").write_text(
            "#!/bin/bash\n# Auto-generated by cairn (cairn hooks install)\n"
            'cairn update --repo "repo" > /dev/null 2>&1 &\n',
            encoding="utf-8",
        )

        assert self._install(ws) == ["repo"]

        from cairn.hooks.git_hooks import POST_COMMIT_TEMPLATE

        assert (hooks / "post-commit").read_text(
            encoding="utf-8"
        ) == POST_COMMIT_TEMPLATE.format(repo="repo")

    def test_uninstall_removes_only_cairn_marked_files(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CAIRN_HOME", raising=False)
        ws = tmp_path / "ws"
        ws.mkdir()
        hooks = self._hooks_dir(ws)
        hooks.mkdir(parents=True)
        foreign = "#!/bin/sh\necho mine\n"
        (hooks / "post-commit").write_text(foreign, encoding="utf-8")
        self._install(ws)

        from cairn.hooks.git_hooks import uninstall_hooks

        assert uninstall_hooks(["repo"], str(ws)) == ["repo"]
        assert (hooks / "post-commit").read_text(encoding="utf-8") == foreign, (
            "a foreign hook must survive uninstall"
        )
        assert not (hooks / "post-checkout").exists()

    def test_user_hook_mentioning_cairn_is_never_touched(self, tmp_path, monkeypatch):
        """Ownership keys on the cairn-generated marker line, not a bare
        'cairn' substring: a user hook that merely mentions cairn must
        survive both install (no overwrite) and uninstall (no delete)."""
        monkeypatch.delenv("CAIRN_HOME", raising=False)
        ws = tmp_path / "ws"
        ws.mkdir()
        hooks = self._hooks_dir(ws)
        hooks.mkdir(parents=True)
        user_hook = "#!/bin/sh\n# runs cairn aside, then my checks\necho mine\n"
        (hooks / "post-commit").write_text(user_hook, encoding="utf-8")

        assert self._install(ws) == ["repo"], (
            "the post-checkout slot still gets installed"
        )
        assert (hooks / "post-commit").read_text(encoding="utf-8") == user_hook, (
            "a user hook mentioning cairn must not be overwritten"
        )

        from cairn.hooks.git_hooks import uninstall_hooks

        assert uninstall_hooks(["repo"], str(ws)) == ["repo"]
        assert (hooks / "post-commit").read_text(encoding="utf-8") == user_hook, (
            "a user hook mentioning cairn must not be removed"
        )

    def test_unsafe_cairn_home_is_rejected_before_any_write(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CAIRN_HOME", str(tmp_path / 'ho"me$(touch pwned)'))
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".git").mkdir()

        with pytest.raises(ValueError, match="unsafe to embed"):
            self._install(ws)
        assert not (self._hooks_dir(ws) / "post-commit").exists()
        assert not (self._hooks_dir(ws) / "post-checkout").exists()
        assert not list(tmp_path.glob("pwned")), "no shell metachar may execute"

    def test_invalid_repo_name_writes_no_hooks(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".git").mkdir()
        from cairn.hooks.git_hooks import install_hooks

        with pytest.raises(ValueError):
            install_hooks(["repo; rm -rf /"], str(ws))
        assert not (self._hooks_dir(ws) / "post-commit").exists()
        assert not (self._hooks_dir(ws) / "post-checkout").exists()


class TestGitHooksInstallGate:
    """install()'s git-hooks gate: hooks land only when explicitly requested
    (include_git_hooks), a client is targeted, and the run is not a dry-run --
    a plain or dry-run install must change nothing under .git/hooks."""

    @staticmethod
    def _git_ws(tmp_path: Path) -> Path:
        ws = tmp_path / "ws"
        (ws / ".git").mkdir(parents=True)
        return ws

    @staticmethod
    def _hook_files(ws: Path) -> list[Path]:
        return [ws / ".git" / "hooks" / name for name in ("post-commit", "post-checkout")]

    def test_plain_install_without_the_flag_writes_no_git_hooks(self, tmp_path):
        ws = self._git_ws(tmp_path)

        rep = install(str(ws), clients=["cursor"])

        assert rep.git_hooks_installed == []
        assert not any(p.exists() for p in self._hook_files(ws)), (
            "an install without --git-hooks must not touch .git/hooks"
        )

    def test_dry_run_with_a_targeted_client_writes_no_git_hooks(self, tmp_path):
        ws = self._git_ws(tmp_path)

        rep = install(str(ws), clients=["cursor"], dry_run=True)

        assert rep.git_hooks_installed == []
        assert not any(p.exists() for p in self._hook_files(ws)), (
            "dry-run must change nothing, git hooks included"
        )

    def test_flag_with_target_outside_dry_run_installs_git_hooks(self, tmp_path):
        ws = self._git_ws(tmp_path)

        rep = install(str(ws), clients=["cursor"], include_git_hooks=True)

        assert rep.git_hooks_installed == ["ws"]
        assert all(p.exists() for p in self._hook_files(ws))


# --------------------------------------------------------------------------
# freshness-hooks FR-005/FR-006: init --with-hooks + non-git clean failure
# --------------------------------------------------------------------------


class TestInitWithHooks:
    """`cairn init --with-hooks` installs both git hooks with the same rules
    as `cairn hooks install`; in a non-git workspace `hooks install` exits 1
    with guidance while `init --with-hooks` warns and still completes —
    zero hook files written either way."""

    @staticmethod
    def _invoke(argv):
        from cairn.cli import main

        return CliRunner().invoke(main, argv)

    @staticmethod
    def _git_workspace(tmp_path: Path) -> Path:
        ws = tmp_path / "ws"
        (ws / ".git").mkdir(parents=True)
        return ws

    def test_init_with_hooks_installs_both_hooks_idempotently(self, tmp_path):
        ws = self._git_workspace(tmp_path)
        argv = ["init", "--no-build", "--with-hooks", "--workspace", str(ws)]

        first = self._invoke(argv)
        assert first.exit_code == 0, first.output
        hooks_dir = ws / ".git" / "hooks"
        contents = {
            name: (hooks_dir / name).read_text(encoding="utf-8")
            for name in ("post-commit", "post-checkout")
        }
        assert all("cairn" in body for body in contents.values())
        assert "Git hooks" in first.output
        assert "ws" in first.output, "the rail must name the repo hooks landed in"

        second = self._invoke(argv)
        assert second.exit_code == 0, second.output
        for name, body in contents.items():
            assert (hooks_dir / name).read_text(encoding="utf-8") == body, (
                "re-running init --with-hooks must not duplicate or clobber hooks"
            )

    def test_hooks_install_surface_text_covers_both_hooks(self, tmp_path):
        ws = self._git_workspace(tmp_path)

        result = self._invoke(["hooks", "install", "--workspace", str(ws)])

        assert result.exit_code == 0, result.output
        assert "post-commit" in result.output
        assert "post-checkout" in result.output
        for name in ("post-commit", "post-checkout"):
            hook = ws / ".git" / "hooks" / name
            assert hook.exists()
            assert os.access(hook, os.X_OK), "hooks must be executable for git"

    def test_hooks_install_non_git_exits_1_with_guidance_and_no_files(self, tmp_path):
        ws = tmp_path / "plain"
        ws.mkdir()

        result = self._invoke(["hooks", "install", "--workspace", str(ws)])

        assert result.exit_code == 1
        assert "No git repositories found" in result.output
        assert list(ws.iterdir()) == [], "a failed install must write zero files"

    def test_init_with_hooks_warns_and_still_completes_on_non_git(self, tmp_path):
        ws = tmp_path / "plain"
        ws.mkdir()

        result = self._invoke(
            ["init", "--no-build", "--with-hooks", "--workspace", str(ws)]
        )

        assert result.exit_code == 0
        assert "No git repositories found" in result.output
        assert list(ws.iterdir()) == [], "init must write zero hook files on non-git"
        from cairn.paths import is_registered

        assert is_registered(ws), "init's store registration must still succeed"

    def test_init_survives_hook_name_guard_hit(self, tmp_path):
        """A repo id failing the shell-injection allowlist must not crash init
        after its store is registered: warn, skip hooks, keep exit 0."""
        ws = tmp_path / "multi"
        repo = ws / "my repo"
        (repo / ".git").mkdir(parents=True)

        result = self._invoke(
            ["init", "--no-build", "--with-hooks", "--workspace", str(ws)]
        )

        assert result.exit_code == 0, result.output
        assert "git hooks not installed" in result.output
        assert not (repo / ".git" / "hooks" / "post-commit").exists()
        assert not (repo / ".git" / "hooks" / "post-checkout").exists()

    def test_hooks_install_repo_with_space_fails_clean(self, tmp_path):
        """A repo id failing the shell-injection allowlist maps to a clean
        ClickException from `hooks install`, never a raw ValueError traceback."""
        ws = tmp_path / "multi"
        repo = ws / "my repo"
        (repo / ".git").mkdir(parents=True)

        result = self._invoke(["hooks", "install", "--workspace", str(ws)])

        assert result.exit_code == 1
        assert "Invalid repo name" in result.output
        assert not isinstance(result.exception, ValueError), (
            "the guard must surface as a ClickException, not an uncaught ValueError"
        )
        assert not (repo / ".git" / "hooks" / "post-commit").exists()


# --------------------------------------------------------------------------
# F2: global-scope uninstall
# --------------------------------------------------------------------------


class TestGlobalScopeUninstall:
    def test_global_install_uninstall_removes_home_tree(
        self, fake_home, tmp_path, monkeypatch
    ):
        _no_cli(monkeypatch, "claude", "cursor", "zcode", "droid", "opencode", "agy")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude", "cursor", "zcode"], scope="global")

        assert (fake_home / ".claude" / "skills" / "cairn").is_dir()
        assert (fake_home / ".cursor" / "hooks.json").exists()
        assert (fake_home / ".zcode" / "cli" / "config.json").exists()

        uninstall(str(ws), clients=["claude", "cursor", "zcode"], scope="global")

        assert not (fake_home / ".claude" / "skills" / "cairn").exists()
        assert not any((fake_home / ".claude" / "commands").glob("*.md"))
        assert not any((fake_home / ".claude" / "agents").glob("*.md"))
        st = json.loads(
            (fake_home / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        assert "hooks" not in st, (
            "cairn hooks must be stripped from ~/.claude/settings.json"
        )
        cur = json.loads(
            (fake_home / ".cursor" / "hooks.json").read_text(encoding="utf-8")
        )
        assert "hooks" not in cur, (
            "cairn hooks must be stripped from ~/.cursor/hooks.json"
        )
        zc = json.loads(
            (fake_home / ".zcode" / "cli" / "config.json").read_text(encoding="utf-8")
        )
        assert "cairn" not in zc.get("mcp", {}).get("servers", {})

    def test_zcode_global_install_migrates_legacy_config(
        self, fake_home, tmp_path, monkeypatch, pinned_api_key
    ):
        """A legacy global install left a stdio cairn entry in
        ~/.zcode/config.json (which the ZCode CLI does not read for MCP).
        Re-installing must move the entry to ~/.zcode/cli/config.json in the
        URL+key shape and strip the legacy one."""
        _no_cli(monkeypatch, "zcode")
        ws = tmp_path / "ws"
        ws.mkdir()
        legacy = fake_home / ".zcode" / "config.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(
            json.dumps(
                {
                    "mcp": {
                        "servers": {
                            "cairn": {
                                "type": "stdio",
                                "command": "/old/cairn",
                                "args": ["serve"],
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        install(str(ws), clients=["zcode"], scope="global")

        new = json.loads(
            (fake_home / ".zcode" / "cli" / "config.json").read_text(encoding="utf-8")
        )
        assert new["mcp"]["servers"]["cairn"] == {
            "type": "http",
            "url": DAEMON_URL,
            "headers": _bearer(pinned_api_key),
        }
        leg = json.loads(legacy.read_text(encoding="utf-8"))
        assert "cairn" not in leg.get("mcp", {}).get("servers", {}), (
            "legacy entry must be stripped so only one source of truth remains"
        )

    def test_global_uninstall_preserves_user_entries(
        self, fake_home, tmp_path, monkeypatch
    ):
        _no_cli(monkeypatch, "claude")
        ws = tmp_path / "ws"
        ws.mkdir()
        settings = fake_home / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(
            json.dumps(
                {
                    "hooks": {"UserEvent": [{"hooks": [{"command": "echo mine"}]}]},
                }
            )
        )
        install(str(ws), clients=["claude"], scope="global")
        uninstall(str(ws), clients=["claude"], scope="global")
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["hooks"]["UserEvent"][0]["hooks"][0]["command"] == "echo mine"

    def test_workspace_default_uninstall_leaves_global_alone(
        self, fake_home, tmp_path, monkeypatch
    ):
        """Byte-identical workspace default: plain uninstall(ws) must not
        touch a global install (opt in with scope= to remove it)."""
        _no_cli(monkeypatch, "claude", "zcode")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude"], scope="global")
        uninstall(str(ws), clients=["claude"])  # default scope="workspace"
        assert (fake_home / ".claude" / "skills" / "cairn").is_dir()
        st = json.loads(
            (fake_home / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        assert "PostToolUse" in st.get("hooks", {})

    def test_global_uninstall_invokes_claude_mcp_remove(
        self, fake_home, tmp_path, monkeypatch
    ):
        _cli_at(monkeypatch, "claude")
        calls = _spy_subprocess(monkeypatch)
        ws = tmp_path / "ws"
        ws.mkdir()
        uninstall(str(ws), clients=["claude"], scope="global")
        assert ["claude", "mcp", "remove", "cairn", "--scope", "user"] in calls

    def test_global_uninstall_reports_claude_mcp_remove_failure(
        self, fake_home, tmp_path, monkeypatch
    ):
        """A non-zero `claude mcp remove` must surface a WARNING, not claim
        the registration was removed."""
        _cli_at(monkeypatch, "claude")

        class _R:
            returncode = 1
            stdout = ""
            stderr = "No MCP server found"

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
        ws = tmp_path / "ws"
        ws.mkdir()
        rep = uninstall(str(ws), clients=["claude"], scope="global")
        claude = next(r for r in rep.results if r.client == "claude")
        assert any("exited 1" in n for n in claude.notes)
        assert not any(n.startswith("Removed user-scope") for n in claude.notes)

    def test_workspace_uninstall_never_invokes_claude_mcp_remove(
        self, fake_home, tmp_path, monkeypatch
    ):
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
        install(str(ws), clients=["cursor"])

        from cairn.agent_install._common import _claude_hook_command

        # Cursor's own hook content covers only post_edit/session_end; seed the
        # remaining _HOOK_ENTRYPOINTS entrypoints so the fixture reads as a
        # fully-wired install (a partially-wired one must re-merge).
        p = ws / ".cursor" / "hooks.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["hooks"]["afterFileEdit"].append(
            {"command": _claude_hook_command("post_tool_failure"), "timeout": 10000}
        )
        data["hooks"]["afterFileEdit"].append(
            {"command": _claude_hook_command("session_start"), "timeout": 10000}
        )
        p.write_text(json.dumps(data))

        rep = install(str(ws), clients=["cursor"], dry_run=True)
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
        install(str(ws), clients=["claude"])
        skill = ws / ".agents" / "skills" / "cairn"
        subdirs = {p.name for p in skill.iterdir() if p.is_dir()}
        assert {"references", "scripts", "evals"} <= subdirs
        uninstall(str(ws), clients=["claude"])
        assert not skill.exists()

    def test_uninstall_without_targets_leaves_cross_tool_tree_alone(
        self, tmp_path, monkeypatch
    ):
        """No detections and no --client: uninstall is a no-op — .agents/ must
        survive and the report must not deny or claim cross-tool work."""
        _no_cli(monkeypatch, "claude")
        from cairn.agent_install._common import _read_template

        ws = tmp_path / "ws"
        ws.mkdir()
        cmd = ws / ".agents" / "commands" / "cairn.md"
        cmd.parent.mkdir(parents=True)
        cmd.write_text(_read_template("commands/cairn.md"), encoding="utf-8")

        rep = uninstall(str(ws))

        assert rep.results == []
        assert rep.cross_tool is None
        assert cmd.exists(), "no-target uninstall must not delete cross-tool assets"


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

        rep = install(str(ws), clients=["claude"])
        claude = next(r for r in rep.results if r.client == "claude")
        assert any(str(user_cmd) in s for s in claude.skipped), (
            "install must skip the user file"
        )

        un_rep = uninstall(str(ws), clients=["claude"])
        un_claude = next(r for r in un_rep.results if r.client == "claude")
        assert user_cmd.exists(), "uninstall must not delete a user-authored file"
        assert any("differs from the current cairn template" in s
                   for s in un_claude.skipped)

    def test_installer_written_command_file_removed(self, tmp_path, monkeypatch):
        _no_cli(monkeypatch, "claude")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["claude"])
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

        install(str(ws), clients=["zcode"])  # must not raise

        assert cfg.with_suffix(".json.bak").exists(), (
            "user's broken value must be backed up"
        )
        data = json.loads(cfg.read_text(encoding="utf-8"))
        assert "cairn" in data["mcp"]["servers"]

    def test_claude_non_object_hooks_backed_up(self, tmp_path, monkeypatch):
        _no_cli(monkeypatch, "claude")
        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = ws / ".claude" / "settings.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"hooks": []}), encoding="utf-8")

        install(str(ws), clients=["claude"])  # must not raise

        assert cfg.with_suffix(".json.bak").exists()
        data = json.loads(cfg.read_text(encoding="utf-8"))
        assert "PostToolUse" in data["hooks"]

    # -- uninstall-side strip helpers (S1) ------------------------------------

    @pytest.mark.parametrize(
        "client,relpath,payload",
        [
            ("claude", ".mcp.json", {"mcpServers": "cairn"}),
            ("claude", ".claude/settings.json", {"hooks": "nope"}),
            ("cursor", ".cursor/hooks.json", {"hooks": 7}),
            ("zcode", ".zcode/config.json", {"mcp": 5}),
            ("opencode", "opencode.json", {"mcp": ["cairn"]}),
            ("kilo", "kilo.json", {"mcp": "cairn"}),
        ],
    )
    def test_uninstall_tolerates_non_object_values(
        self, fake_home, tmp_path, monkeypatch, client, relpath, payload
    ):
        """Uninstall strip helpers must no-op on non-dict values under
        mcpServers/mcp/hooks instead of crashing on them."""
        _no_cli(
            monkeypatch,
            "claude",
            "cursor",
            "zcode",
            "droid",
            "opencode",
            "agy",
            "kilo",
            "omp",
        )
        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = ws / relpath
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(payload), encoding="utf-8")

        uninstall(str(ws), clients=[client])  # must not raise

        assert json.loads(cfg.read_text(encoding="utf-8")) == payload, (
            "nothing cairn-shaped to strip; the user's value must survive untouched"
        )

    def test_zcode_install_tolerates_non_dict_mcp_servers(
        self, fake_home, tmp_path, monkeypatch
    ):
        """_already_installed's mcp.servers chain must not crash on a
        non-dict nested value."""
        _no_cli(monkeypatch, "zcode")
        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = ws / ".zcode" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"mcp": {"servers": 5}}), encoding="utf-8")

        install(str(ws), clients=["zcode"])  # must not raise

        data = json.loads(cfg.read_text(encoding="utf-8"))
        assert "cairn" in data["mcp"]["servers"]

    # -- .bak rotation (S2) ----------------------------------------------------

    def test_backup_rotation_preserves_the_existing_bak(
        self, fake_home, tmp_path, monkeypatch
    ):
        """_backup_to_bak must never overwrite an existing .bak — it may be
        the only preserved copy of the user's earlier config."""
        _no_cli(monkeypatch, "zcode")
        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = ws / ".zcode" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"mcp": True, "gen": 1}), encoding="utf-8")

        install(str(ws), clients=["zcode"])
        bak = cfg.with_suffix(".json.bak")
        first = bak.read_bytes()

        cfg.write_text(json.dumps({"mcp": False, "gen": 2}), encoding="utf-8")
        install(str(ws), clients=["zcode"])

        assert bak.read_bytes() == first, "the existing .bak must survive untouched"
        rotated = cfg.with_suffix(".json.bak.1")
        assert json.loads(rotated.read_text(encoding="utf-8")) == {
            "mcp": False,
            "gen": 2,
        }


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
            json.dumps({str(ws.resolve()): key}), encoding="utf-8"
        )

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
        assert (home / "workspaces.json") in seen, (
            "registry rewrite must go through _atomic_write_text"
        )
        assert json.loads((home / "workspaces.json").read_text(encoding="utf-8")) == {}
        assert not (home / key).exists()


# --------------------------------------------------------------------------
# F8: opencode scope
# --------------------------------------------------------------------------


class TestOpencodeScope:
    def test_global_install_lands_in_global_path(
        self, fake_home, tmp_path, monkeypatch
    ):
        _no_cli(monkeypatch, "opencode")
        ws = tmp_path / "ws"
        ws.mkdir()
        install(str(ws), clients=["opencode"], scope="global")

        global_cfg = fake_home / ".config" / "opencode" / "opencode.json"
        assert global_cfg.exists(), (
            "scope=global must write ~/.config/opencode/opencode.json"
        )
        assert not (ws / "opencode.json").exists(), (
            "scope=global must not write the workspace"
        )
        assert "cairn" in json.loads(global_cfg.read_text(encoding="utf-8"))["mcp"]
        assert check_installed(str(ws))["opencode"], (
            "a global install must be detected by check_installed (it probes the global path)"
        )


# --------------------------------------------------------------------------
# CLI wiring: --scope plumbing and the --dry-run gate on `cairn uninstall`
# --------------------------------------------------------------------------


class TestCliScopeWiring:
    def test_full_implies_scope_all_in_dry_run_output(
        self, fake_home, tmp_path, monkeypatch
    ):
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
        install(str(ws), clients=["cursor"])
        before = (ws / ".cursor" / "hooks.json").read_text(encoding="utf-8")

        result = CliRunner().invoke(
            main,
            ["uninstall", "--agents-only", "--dry-run", "-y", "--workspace", str(ws)],
            env={"CAIRN_HOME": str(tmp_path / "cairn_home4")},
        )
        assert result.exit_code == 0, result.output
        assert "would" in result.output
        assert (ws / ".cursor" / "hooks.json").read_text(encoding="utf-8") == before
        assert (ws / ".agents" / "skills" / "cairn").exists(), (
            "dry-run must not remove the cross-tool copies either"
        )


# --------------------------------------------------------------------------
# Store targeting (resolved-workspace key; -y never widens a miss)
# --------------------------------------------------------------------------


class TestStoreTargeting:
    """`uninstall --graph-only` must key the store on the RESOLVED workspace
    path (the form registration writes), and a -y run with no matching store
    must never widen to the whole home."""

    def _graph_only(self, ws: Path, home: Path, extra: list[str] | None = None):
        from cairn.cli import main

        return CliRunner().invoke(
            main,
            ["uninstall", "--graph-only", "-y", "--workspace", str(ws), *(extra or [])],
            env={"CAIRN_HOME": str(home)},
        )

    @pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
    def test_symlinked_workspace_targets_its_own_store(self, tmp_path):
        from cairn.paths import store_key

        home = tmp_path / "home"
        real = tmp_path / "real_ws"
        real.mkdir()
        (home / store_key(real)).mkdir(parents=True)
        (home / store_key(real) / ".kg").write_bytes(b"")
        other = tmp_path / "other_ws"
        other.mkdir()
        (home / store_key(other)).mkdir(parents=True)
        (home / store_key(other) / ".kg").write_bytes(b"")
        link = tmp_path / "link_ws"
        link.symlink_to(real, target_is_directory=True)

        result = self._graph_only(link, home)

        assert result.exit_code == 0, result.output
        assert not (home / store_key(real)).exists(), (
            "the symlinked workspace's own store must be removed"
        )
        assert (home / store_key(other)).exists(), (
            "a symlinked invocation must not widen the delete to the whole home"
        )

    def test_y_skips_whole_home_fallback_on_store_miss(self, tmp_path):
        from cairn.paths import store_key

        home = tmp_path / "home"
        unregistered = tmp_path / "unregistered_ws"
        unregistered.mkdir()
        other = home / store_key(tmp_path / "some_other_ws")
        (other / ".kg").mkdir(parents=True)

        result = self._graph_only(unregistered, home)

        assert result.exit_code == 0, result.output
        assert "nothing to remove" in result.output
        assert str(unregistered) in result.output
        assert other.exists(), (
            "-y must not widen a store miss to every workspace's store"
        )

    def test_store_miss_fails_closed_and_names_workspace(self, tmp_path):
        """A store miss never offers the whole home and names the workspace."""
        from cairn.cli import main

        home = tmp_path / "home"
        unregistered = tmp_path / "unregistered_ws"
        unregistered.mkdir()
        other = home / "abcd1234abcd1234"
        (other / ".kg").mkdir(parents=True)

        result = CliRunner().invoke(
            main,
            ["uninstall", "--graph-only", "--workspace", str(unregistered)],
            env={"CAIRN_HOME": str(home)},
            input="y\n",
        )

        assert result.exit_code == 0, result.output
        assert str(unregistered) in result.output
        assert "entire cairn home" not in result.output
        assert other.exists(), "a confirmed store miss must keep every other store"


# --------------------------------------------------------------------------
# agy probe path (installer and probe must resolve the same config path)
# --------------------------------------------------------------------------


class TestAgyProbePath:
    def test_check_installed_probes_the_installer_config_path(
        self, fake_home, tmp_path, monkeypatch
    ):
        """The agy probe must read the path the installer writes (XDG-aware),
        not a hardcoded ~/.gemini — otherwise a Linux/XDG install reads as
        not installed."""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / "xdg"))
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["agy"])

        assert check_installed(str(ws))["agy"] is True


# --------------------------------------------------------------------------
# Transport default: SSE everywhere except Claude Desktop (stdio-only)
# --------------------------------------------------------------------------


class TestUrlKeyDefaultInstall:
    def test_default_install_writes_url_key_entries(
        self, tmp_path, monkeypatch, pinned_api_key
    ):
        """No transport flags exist anymore: a plain install writes the
        URL+key registration every supported client reads."""
        _no_cli(monkeypatch, "claude", "zcode")
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["zcode", "claude"])

        expected = {
            "type": "http",
            "url": DAEMON_URL,
            "headers": _bearer(pinned_api_key),
        }
        zc = json.loads((ws / ".zcode" / "config.json").read_text(encoding="utf-8"))
        assert zc["mcp"]["servers"]["cairn"] == expected
        cl = json.loads((ws / ".mcp.json").read_text(encoding="utf-8"))
        assert cl["mcpServers"]["cairn"] == expected

    def test_droid_cli_registers_http_url_with_key(
        self, tmp_path, monkeypatch, pinned_api_key
    ):
        """With the droid CLI present, install registers the daemon URL via
        `--type http` with the key riding along as a header."""
        _cli_at(monkeypatch, "droid")
        calls = _spy_subprocess(monkeypatch)
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["droid"])

        assert [
            "droid",
            "mcp",
            "add",
            "cairn",
            DAEMON_URL,
            "--type",
            "http",
            "--header",
            f"Authorization: Bearer {pinned_api_key}",
        ] in calls

    def test_claude_global_registers_http_url_with_key(
        self, fake_home, tmp_path, monkeypatch, pinned_api_key
    ):
        """Global scope registers the daemon URL via the claude CLI's http
        transport with the key as a header -- no command spawn, no env."""
        _cli_at(monkeypatch, "claude")
        calls = _spy_subprocess(monkeypatch)
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["claude"], scope="global")

        add = [c for c in calls if c[:3] == ["claude", "mcp", "add"]]
        assert len(add) == 1
        assert add[0] == [
            "claude",
            "mcp",
            "add",
            "--transport",
            "http",
            "--scope",
            "user",
            "cairn",
            DAEMON_URL,
            "--header",
            f"Authorization: Bearer {pinned_api_key}",
        ]


# --------------------------------------------------------------------------
# kilo (Kilo Code CLI): opencode-format config, kilo.json paths
# --------------------------------------------------------------------------


class TestKiloClient:
    def test_default_install_writes_remote_url_key_entry(
        self, tmp_path, monkeypatch, pinned_api_key
    ):
        """Default install: kilo.json gets mcp.cairn = {type: remote, url at
        /mcp, enabled, headers with the key} (kilo's documented remote shape --
        same schema as opencode)."""
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["kilo"])

        cfg = json.loads((ws / "kilo.json").read_text(encoding="utf-8"))
        assert cfg["mcp"]["cairn"] == {
            "type": "remote",
            "url": DAEMON_URL,
            "enabled": True,
            "headers": _bearer(pinned_api_key),
        }


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

        explorer = (ws / ".omp" / "agents" / "cairn-explorer.md").read_text(
            encoding="utf-8"
        )
        assert explorer.startswith("---\nname: cairn-explorer\n")
        assert "description:" in explorer
        assert "mcp__cairn_explore" in explorer
        assert "mcp__cairn__explore" not in explorer, (
            "omp uses single underscore, not Claude's double"
        )

        steward = (ws / ".omp" / "agents" / "knowledge-steward.md").read_text(
            encoding="utf-8"
        )
        assert steward.startswith("---\nname: knowledge-steward\n")

    def test_uninstall_strips_cairn_preserves_others_and_removes_agents(
        self, fake_home, tmp_path, monkeypatch
    ):
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

    def test_dry_run_note_does_not_claim_written(self, tmp_path, monkeypatch):
        """A dry-run merge must report what would happen, not 'MCP server
        written to ...'."""
        _no_cli(monkeypatch, "omp")
        ws = tmp_path / "ws"
        ws.mkdir()

        res = next(
            r for r in install(str(ws), clients=["omp"], dry_run=True).results
            if r.client == "omp"
        )

        assert not any(n.startswith("MCP server written") for n in res.notes)
        assert any("Would merge" in n for n in res.notes)


# --------------------------------------------------------------------------
# Install-time per-client verification probes /healthz, never a process
# --------------------------------------------------------------------------


class TestInstallVerification:
    """Registration verification tracks daemon health: the probe targets the
    registration's host:port /healthz endpoint and no subprocess is ever
    spawned to verify a registration."""

    @staticmethod
    def _probe(monkeypatch, healthy: bool) -> list:
        from cairn.mcp_server import lifecycle as lc

        seen: list = []

        def fake(*args, **kwargs):
            seen.append((*args, *kwargs.values()))
            return healthy

        monkeypatch.setattr(lc, "healthz_responds", fake, raising=False)
        return seen

    def test_healthy_daemon_verifies_pass_probing_registration_host_port(
        self, tmp_path, monkeypatch, pinned_api_key
    ):
        seen = self._probe(monkeypatch, healthy=True)
        ws = tmp_path / "ws"
        ws.mkdir()

        rep = install(str(ws), clients=["cursor", "zcode", "opencode"])

        for res in rep.results:
            assert res.written, f"{res.client}: premise -- file-written client"
            assert res.verification_status == "pass", (
                f"{res.client}: healthy daemon must verify PASS, got "
                f"{res.verification_status!r} ({res.verification_detail})"
            )
        probed = {v for call in seen for v in call}
        assert {"127.0.0.1", 9876} <= probed, (
            "verification must derive host:port from the registration's URL"
        )

    def test_dead_daemon_verifies_unreachable(
        self, tmp_path, monkeypatch, pinned_api_key
    ):
        self._probe(monkeypatch, healthy=False)
        ws = tmp_path / "ws"
        ws.mkdir()

        rep = install(str(ws), clients=["cursor"])

        res = next(r for r in rep.results if r.client == "cursor")
        assert res.verification_status == "fail"
        assert re.search(
            r"unreach|not running|unhealthy|down|unavailable",
            res.verification_detail,
            re.I,
        ), res.verification_detail

    def test_verification_spawns_no_process(
        self, tmp_path, monkeypatch, pinned_api_key
    ):
        calls = _spy_subprocess(monkeypatch)
        ws = tmp_path / "ws"
        ws.mkdir()

        install(str(ws), clients=["cursor", "zcode", "opencode", "claude-desktop"])

        assert calls == [], "verification must probe healthz, never spawn"


# --------------------------------------------------------------------------
# Claude Code global MCP registration (`claude mcp add --scope user`)
# --------------------------------------------------------------------------

# Pristine shutil.which, captured at import time -- before the hermetic
# fixture swaps in its agent-CLI-blocking replacement.
_PRISTINE_WHICH = shutil.which


class TestClaudeGlobalMcpRegistration:
    """`install-agents --scope global` registers Claude Code's MCP via the
    `claude` CLI (Claude Code reads no global ~/.mcp.json file). The spawned
    argv carries the http transport, the daemon URL, and the key header; the
    outcome must be reported honestly."""

    def test_nonzero_exit_reports_warning_not_success(
        self, fake_home, tmp_path, monkeypatch
    ):
        """A failed `claude mcp add` (non-zero exit) must surface as a
        WARNING naming the exit code and the CLI's stderr -- never as the
        success note."""
        _cli_at(monkeypatch, "claude")

        class _R:
            returncode = 1
            stdout = ""
            stderr = "error: unknown option '-m'"

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())

        ws = tmp_path / "ws"
        ws.mkdir()
        rep = install(str(ws), clients=["claude"], scope="global")

        res = next(r for r in rep.results if r.client == "claude")
        assert not any("Registered MCP globally" in n for n in res.notes)
        assert any("exited 1" in n and "unknown option '-m'" in n for n in res.notes)

    def test_doctor_reads_claude_user_scope_registration(self, fake_home, tmp_path):
        """The user-scope file `claude mcp add --scope user` writes
        (~/.claude.json) is one of the configs the registration audit reads,
        and it alone marks claude installed."""
        from cairn.agent_install import check_installed
        from cairn.cli.system import _enumerate_registrations

        ws = tmp_path / "ws"
        ws.mkdir()
        entry = {"type": "stdio", "command": "/bin/echo", "args": ["serve"]}
        user_cfg = fake_home / ".claude.json"
        user_cfg.write_text(
            json.dumps({"mcpServers": {"cairn": entry}}), encoding="utf-8"
        )

        assert check_installed(str(ws))["claude"] is True

        claude_hits = [
            (c, p, d, e, ws_owned)
            for c, p, d, e, ws_owned in _enumerate_registrations()
            if c == "claude"
        ]
        assert ("claude", user_cfg, "~/.claude.json", entry, False) in claude_hits

    def test_doctor_reads_droid_user_scope_registration(self, fake_home, tmp_path):
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
        user_cfg.write_text(
            json.dumps({"mcpServers": {"cairn": entry}}), encoding="utf-8"
        )

        assert check_installed(str(ws))["droid"] is True

        droid_hits = [
            (c, p, d, e, ws_owned)
            for c, p, d, e, ws_owned in _enumerate_registrations()
            if c == "droid"
        ]
        assert ("droid", user_cfg, "~/.factory/mcp.json", entry, False) in droid_hits

    def test_claude_probe_reads_the_hooks_cairn_writes(self, fake_home, tmp_path):
        """check_installed's ~/.claude/settings.json probe must look at the
        hooks block cairn writes there — MCP keys never land in settings.json."""
        ws = tmp_path / "ws"
        ws.mkdir()
        settings = fake_home / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "/usr/bin/python3 -m cairn.hooks.claude_hooks session_end",
                                    },
                                ]
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        assert check_installed(str(ws))["claude"] is True

    @pytest.mark.skipif(
        not os.environ.get("CAIRN_TEST_CLAUDE_CLI") or not shutil.which("claude"),
        reason="opt-in end-to-end probe (CAIRN_TEST_CLAUDE_CLI=1); needs the real claude CLI",
    )
    def test_claude_cli_global_registration_end_to_end(
        self, tmp_path, monkeypatch, pinned_api_key
    ):
        """The real `claude mcp add` accepts the spawned argv and stores the
        http URL registration with the key header. HOME is pointed at a
        throwaway dir, so the real ~/.claude is never touched."""
        # Unblock the hermetic fixture's which() for claude only; the
        # throwaway-HOME sandbox still applies to everything else.
        monkeypatch.setattr(
            shutil,
            "which",
            lambda name, *a, **k: (
                _PRISTINE_WHICH(name, *a, **k) if name == "claude" else None
            ),
        )

        home = tmp_path / "claude_home"
        home.mkdir()
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setattr(Path, "home", lambda *a, **k: home)
        monkeypatch.setenv("HOME", str(home))

        install(str(ws), clients=["claude"], scope="global")

        cfg = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
        entry = cfg["mcpServers"]["cairn"]
        assert entry["type"] == "http"
        assert entry["url"] == DAEMON_URL
        assert entry["headers"]["Authorization"] == f"Bearer {pinned_api_key}"


# --------------------------------------------------------------------------
# Droid CLI registration (`droid mcp add`)
# --------------------------------------------------------------------------


class TestDroidCliRegistration:
    """`droid mcp add` is a subprocess whose outcome must be reported
    honestly and whose argv registers the daemon URL with the key header. A
    failed add falls back to the .factory/mcp.json file so the registration
    lands either way."""

    def test_failed_add_warns_and_falls_back_to_url_key_file(
        self, tmp_path, monkeypatch, pinned_api_key
    ):
        _cli_at(monkeypatch, "droid")

        class _R:
            returncode = 1
            stdout = ""
            stderr = "error: server cairn already exists"

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
        ws = tmp_path / "ws"
        ws.mkdir()

        rep = install(str(ws), clients=["droid"])

        res = next(r for r in rep.results if r.client == "droid")
        assert not any("Registered MCP via" in n for n in res.notes)
        assert any("exited 1" in n and "already exists" in n for n in res.notes)
        fallback = json.loads(
            (ws / ".factory" / "mcp.json").read_text(encoding="utf-8")
        )
        assert fallback["mcpServers"]["cairn"] == {
            "type": "http",
            "url": DAEMON_URL,
            "headers": _bearer(pinned_api_key),
        }

    def test_dry_run_reports_the_registration_a_real_run_performs(
        self, tmp_path, monkeypatch
    ):
        """With the droid CLI on PATH, dry-run must still report the
        `droid mcp add` registration a real run performs (and spawn nothing)."""
        _cli_at(monkeypatch, "droid")
        calls = _spy_subprocess(monkeypatch)
        ws = tmp_path / "ws"
        ws.mkdir()

        rep = install(str(ws), clients=["droid"], dry_run=True)

        res = next(r for r in rep.results if r.client == "droid")
        assert any("droid mcp add" in n for n in res.notes), (
            "dry-run must report the registration a real run performs"
        )
        assert calls == [], "dry-run must not spawn the droid CLI"

    def test_remove_nonzero_exit_reports_warning(
        self, fake_home, tmp_path, monkeypatch
    ):
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


# --------------------------------------------------------------------------
# Uninstall CLI: registry prune failures are visible
# --------------------------------------------------------------------------


def test_uninstall_cli_warns_when_registry_prune_fails(tmp_path, monkeypatch):
    """A failing workspaces.json prune is reported, never swallowed, so the
    registry cannot silently keep pointing at the removed store."""
    from cairn.cli.uninstall import uninstall
    from cairn.paths import store_key

    home = tmp_path / "cairn_home"
    ws = tmp_path / "ws"
    ws.mkdir()
    key = store_key(ws.resolve())
    (home / key).mkdir(parents=True)
    registry = home / "workspaces.json"
    registry.write_text(json.dumps({str(ws.resolve()): key}), encoding="utf-8")
    monkeypatch.setenv("CAIRN_HOME", str(home))

    def boom(path, text):
        raise OSError("disk full")

    monkeypatch.setattr("cairn.agent_install.merge._atomic_write_text", boom)

    result = CliRunner().invoke(
        uninstall, ["--graph-only", "-y", "--workspace", str(ws)]
    )

    assert result.exit_code == 0, result.output
    assert "registry prune failed" in result.output
    assert "still references the removed store" in result.output


def test_install_agents_client_all_does_not_imply_git_hooks(tmp_path, monkeypatch):
    """`--client all` installs client configs only; git hooks ride on the
    explicit --git-hooks flag alone."""
    from cairn.agent_install import InstallReport, InstallResult
    from cairn.cli.agents import install_agents

    captured = {}

    def fake_install(ws, **kwargs):
        captured.update(kwargs)
        return InstallReport(
            detections=[],
            results=[InstallResult(client="claude")],
            cross_tool=None,
        )

    monkeypatch.setattr("cairn.agent_install.install", fake_install)
    monkeypatch.setattr("cairn.mcp_server.lifecycle.healthz_responds", lambda: True)

    ws = tmp_path / "ws"
    ws.mkdir()
    plain = CliRunner().invoke(
        install_agents,
        ["--client", "all", "--scope", "workspace", "--workspace", str(ws)],
    )
    assert plain.exit_code == 0, plain.output
    assert captured["include_git_hooks"] is False

    flagged = CliRunner().invoke(
        install_agents,
        ["--client", "all", "--scope", "workspace", "--workspace", str(ws),
         "--git-hooks"],
    )
    assert flagged.exit_code == 0, flagged.output
    assert captured["include_git_hooks"] is True
