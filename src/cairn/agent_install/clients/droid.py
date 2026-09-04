"""Droid/Factory integration: config + install + uninstall, together.

Droid reads the ``.factory/`` tree (skills, commands, droids) and registers
MCP via ``droid mcp add`` when the CLI is present, falling back to a
``.factory/mcp.json`` file when the CLI is absent or the add fails.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .._common import (
    InstallResult,
    _TEMPLATE_DIR,
    _SLASH_COMMANDS,
    _claude_agent_md,
    _read_template,
    default_sse_url,
    mcp_config_json,
    resolve_cg_command,
)
from ..merge import (
    _merge_json_file,
    _rm_if_exists,
    _rm_if_ours,
    _rm_tree_if_cairn,
    _strip_mcp,
    _write_file,
    _write_tree,
)
from ...paths import cairn_home_env


def install_droid(workspace: str, force: bool, dry_run: bool,
                  transport: str = "stdio", sse_url: str | None = None,
                  scope: str = "workspace") -> InstallResult:
    """Wire cairn into Droid/Factory (.factory/ tree + droid mcp add)."""
    ws = Path(workspace)
    res = InstallResult("droid")

    _write_tree(ws / ".factory" / "skills" / "cairn", _TEMPLATE_DIR / "skill", force, res, dry_run=dry_run)
    for name in _SLASH_COMMANDS:
        _write_file(ws / ".factory" / "commands" / f"{name}.md",
                    _read_template(f"commands/{name}.md"), force, res, dry_run=dry_run)
    # Single shared explorer agent (same definition as Claude Code), so every
    # client wires the one cairn-explorer agent rather than a separate one.
    _write_file(ws / ".factory" / "droids" / "cairn-explorer.md",
                _claude_agent_md(), force, res, dry_run=dry_run)
    _write_file(ws / ".factory" / "droids" / "knowledge-steward.md",
                _claude_agent_md("cursor/knowledge-steward.json"), force, res, dry_run=dry_run)

    # MCP: prefer `droid mcp add` if the CLI is available; a failed CLI add
    # falls back to the .factory/mcp.json file so the registration lands
    # either way (droid reads both; uninstall strips both).
    if shutil.which("droid") and not dry_run:
        if transport == "sse":
            url = default_sse_url(sse_url)
            argv = ["droid", "mcp", "add", "cairn", url, "--type", "sse"]
        else:
            # Documented stdio shape: the full server command arrives as ONE
            # argument (droid splits it), so server-command flags are never
            # parsed as droid options. `--env` entries persist into the
            # registration; a non-default home must ride along or the
            # spawned server resolves the default store.
            argv = ["droid", "mcp", "add", "cairn",
                    " ".join([*resolve_cg_command(), "serve"]),
                    "--type", "stdio"]
            for key, value in cairn_home_env().items():
                argv += ["--env", f"{key}={value}"]
        registered = False
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=10, check=False,
                                  text=True, errors="replace")
            registered = proc.returncode == 0
            if registered:
                res.notes.append("Registered MCP via `droid mcp add`.")
            else:
                err = (proc.stderr or proc.stdout or "").strip()
                res.notes.append(
                    f"WARNING: `droid mcp add` exited {proc.returncode}: "
                    f"{err[:200]}.")
        except (subprocess.SubprocessError, OSError) as e:
            res.notes.append(f"WARNING: `droid mcp add` failed ({e}).")
        if not registered:
            _merge_json_file(ws / ".factory" / "mcp.json",
                             mcp_config_json(transport, sse_url), force, res,
                             dry_run=False)
            res.notes.append(
                "Wrote .factory/mcp.json so the registration is present on "
                "the next droid run.")
    elif not shutil.which("droid"):
        # No droid CLI: write a .factory/mcp.json so it's present when droid is installed.
        _merge_json_file(ws / ".factory" / "mcp.json", mcp_config_json(transport, sse_url), force, res, dry_run=dry_run)
        res.notes.append("droid CLI not found; wrote .factory/mcp.json (registers on next droid run).")

    res.notes.append("Cron automation (e.g. `droid cron create`) requires the droid CLI on PATH.")
    return res


def _mcp_remove_droid(res: InstallResult) -> None:
    """Undo install's ``droid mcp add`` registration when the CLI is present.

    The registration lives outside the workspace (droid's own user config), so
    stripping ``.factory/`` alone leaves a stale server entry. Mirrors the
    install subprocess pattern (list-args, capture_output, timeout,
    check=False); a missing CLI means install never registered (it wrote the
    file fallback instead), so there is nothing to remove.
    """
    if not shutil.which("droid"):
        return
    try:
        proc = subprocess.run(
            ["droid", "mcp", "remove", "cairn"],
            capture_output=True, timeout=10, check=False,
            text=True, errors="replace",
        )
    except (subprocess.SubprocessError, OSError) as e:
        res.notes.append(f"WARNING: `droid mcp remove cairn` failed ({e}); the registration may remain.")
        return
    if proc.returncode == 0:
        res.notes.append("Removed MCP registration via `droid mcp remove cairn`.")
    else:
        err = (proc.stderr or proc.stdout or "").strip()
        res.notes.append(
            f"WARNING: `droid mcp remove cairn` exited {proc.returncode}: "
            f"{err[:200]}; the registration may remain.")


def uninstall(ws: Path, res: InstallResult, scope: str = "workspace") -> None:
    """Remove cairn files/entries for Droid/Factory.

    Droid's file wiring is workspace-scoped (install ignores ``scope`` for
    files), so ``scope`` is accepted only for signature parity with the other
    clients. The MCP registration is NOT file-scoped -- install runs
    ``droid mcp add`` at any scope when the CLI is present -- so the matching
    ``droid mcp remove`` runs at any scope too.

    Commands/droids are only removed when byte-identical to what the
    installer writes, so a user's own file at the same path survives.
    """
    _rm_tree_if_cairn(ws / ".factory" / "skills" / "cairn", res)
    for n in _SLASH_COMMANDS:
        _rm_if_ours(ws / ".factory" / "commands" / f"{n}.md",
                    _read_template(f"commands/{n}.md"), res)
    _rm_if_ours(ws / ".factory" / "droids" / "cairn-explorer.md",
                _claude_agent_md(), res)
    _rm_if_ours(ws / ".factory" / "droids" / "knowledge-steward.md",
                _claude_agent_md("cursor/knowledge-steward.json"), res)
    _rm_if_exists(ws / ".factory" / "droids" / "cairn-agent.md", res)  # legacy filename cleanup
    _strip_mcp(ws / ".factory" / "mcp.json", res)
    _mcp_remove_droid(res)
