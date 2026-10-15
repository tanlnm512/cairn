"""Agent integration installer: wires cairn into AI coding clients."""
from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ._common import (
    CLIENTS,
    InstallResult,
    _SLASH_COMMANDS,
    mcp_config_json,
    resolve_cg_command,
)
from .detect import (
    Detection,
    check_installed,
    claude_desktop_config_path,
    detect_clients,
)
from .merge import (
    _already_installed,
    _deep_merge,
    _merge_json_file,
    _write_file,
    _write_tree,
)
from .clients import claude as _claude
from .clients import cursor as _cursor
from .clients import droid as _droid
from .clients import zcode as _zcode
from .clients import agy as _agy
from .clients import opencode as _opencode
from .clients import kilo as _kilo
from .clients import omp as _omp
from .clients import claude_desktop as _claude_desktop
from .clients.zcode import zcode_mcp_config_json
from .clients.opencode import opencode_mcp_config_json
from .clients.kilo import kilo_mcp_config_json
from .clients.claude import claude_hooks_block
from .clients.cursor import cursor_hooks_json
from .clients.claude import install_claude
from .clients.claude_desktop import install_claude_desktop
from .clients.cursor import install_cursor
from .clients.droid import install_droid
from .clients.zcode import install_zcode
from .clients.agy import install_agy
from .clients.opencode import install_opencode
from .clients.kilo import install_kilo
from .clients.omp import install_omp
from ._common import (
    _INSTRUCTIONS_BODY,
    _agents_instructions,
    _claude_instructions,
)


__all__ = [
    "install",
    "uninstall",
    "detect_clients",
    "check_installed",
    "Detection",
    "CLIENTS",
    "InstallReport",
    "InstallResult",
    "resolve_cg_command",
    "claude_desktop_config_path",
    "mcp_config_json",
    "zcode_mcp_config_json",
    "opencode_mcp_config_json",
    "kilo_mcp_config_json",
    "claude_hooks_block",
    "cursor_hooks_json",
    "install_claude",
    "install_claude_desktop",
    "install_cursor",
    "install_droid",
    "install_zcode",
    "install_agy",
    "install_opencode",
    "install_kilo",
    "install_omp",
    "_SLASH_COMMANDS",
    "_already_installed",
    "_deep_merge",
    "_merge_json_file",
    "_write_file",
    "_write_tree",
    "_INSTRUCTIONS_BODY",
    "_agents_instructions",
    "_claude_instructions",
]


@dataclass
class InstallReport:
    """Report summarizing client integrations installed and detections."""
    detections: list[Detection]
    results: list[InstallResult]
    cross_tool: Optional[InstallResult]
    git_hooks_installed: list[str] = field(default_factory=list)


def install_cross_tool(workspace: str, force: bool, dry_run: bool = False) -> InstallResult:
    """Write the shared workspace .agents skill, commands, and agent files."""
    from ._common import (
        _TEMPLATE_DIR,
        _claude_agent_md,
        _read_template,
    )

    ws = Path(workspace)
    res = InstallResult("cross-tool")
    _write_tree(ws / ".agents" / "skills" / "cairn", _TEMPLATE_DIR / "skill", force, res, dry_run=dry_run)
    for name in _SLASH_COMMANDS:
        _write_file(ws / ".agents" / "commands" / f"{name}.md",
                    _read_template(f"commands/{name}.md"), force, res, dry_run=dry_run)
    _write_file(ws / ".agents" / "agents" / "cairn-explorer.md",
                _claude_agent_md(), force, res, dry_run=dry_run)
    _write_file(ws / ".agents" / "agents" / "knowledge-steward.md",
                _claude_agent_md("cursor/knowledge-steward.json"), force, res, dry_run=dry_run)
    return res


_INSTALLERS = {
    "claude": _claude.install_claude,
    "claude-desktop": _claude_desktop.install_claude_desktop,
    "cursor": _cursor.install_cursor,
    "droid": _droid.install_droid,
    "zcode": _zcode.install_zcode,
    "agy": _agy.install_agy,
    "opencode": _opencode.install_opencode,
    "kilo": _kilo.install_kilo,
    "omp": _omp.install_omp,
}

_UNINSTALLERS = {
    "claude": _claude.uninstall,
    "claude-desktop": _claude_desktop.uninstall,
    "cursor": _cursor.uninstall,
    "droid": _droid.uninstall,
    "zcode": _zcode.uninstall,
    "agy": _agy.uninstall,
    "opencode": _opencode.uninstall,
    "kilo": _kilo.uninstall,
    "omp": _omp.uninstall,
}


_PROBE_TIMEOUT_S = 5.0


def _registration_entry(config_path: str) -> Optional[dict]:
    """Return the cairn registration entry from any supported client config shape."""
    if not config_path.endswith(".json"):
        return None
    try:
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    candidates: list[object] = []
    servers = data.get("mcpServers")
    if isinstance(servers, dict):
        candidates.append(servers.get("cairn"))
    mcp = data.get("mcp")
    if isinstance(mcp, dict):
        nested = mcp.get("servers")
        if isinstance(nested, dict):
            candidates.append(nested.get("cairn"))
        candidates.append(mcp.get("cairn"))
    candidates.append(data.get("cairn"))
    for entry in candidates:
        if isinstance(entry, dict):
            return entry
    return None


def _registration_url(entry: dict) -> Optional[str]:
    """Return the daemon URL from a registration entry's client-specific URL field."""
    for key in ("url", "serverUrl"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _url_host_port(url: str) -> Optional[tuple[str, int]]:
    """Return (host, port) from an http(s) URL; None when not probeable."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.port is not None:
        return (parts.hostname, parts.port)
    return (parts.hostname, 443 if parts.scheme == "https" else 80)


def _verify_results(results: list[InstallResult]) -> None:
    """Record healthz probe verdicts for every file-written URL registration."""
    from ..mcp_server import lifecycle

    for res in results:
        entry = None
        for written in res.written:
            entry = _registration_entry(written)
            if entry is not None:
                break
        if entry is None:
            res.notes.append(
                "verification skipped: no file-written MCP registration this "
                "run (CLI-registered or unchanged)")
            continue
        host_port = _url_host_port(_registration_url(entry) or "")
        if host_port is None:
            res.notes.append("verification skipped: registration carries no daemon URL to probe")
            continue
        host, port = host_port
        if lifecycle.healthz_responds(port=port, host=host, timeout=_PROBE_TIMEOUT_S):
            res.verification_status, res.verification_detail = (
                "pass", f"healthz answered at {host}:{port}")
        else:
            res.verification_status, res.verification_detail = (
                "fail", f"daemon at {host}:{port} unreachable — run `cairn connect` to start it")


def install(
    workspace: str,
    clients: Optional[list[str]] = None,
    force: bool = False,
    dry_run: bool = False,
    include_git_hooks: bool = False,
    scope: str = "workspace",
) -> InstallReport:
    """Install selected agent clients and verify their registrations."""
    detections = detect_clients(workspace)
    if clients:
        bad = [c for c in clients if c not in CLIENTS + ["all"]]
        if bad:
            raise ValueError(f"Unknown clients: {bad}. Valid: {CLIENTS + ['all']}")
        target = set(CLIENTS) if "all" in clients else set(clients)
    else:
        target = {d.client for d in detections if d.detected}

    results: list[InstallResult] = []
    for client in [c for c in CLIENTS if c in target]:
        results.append(_INSTALLERS[client](workspace, force, dry_run, scope=scope))

    if not dry_run:
        _verify_results(results)

    cross = install_cross_tool(workspace, force, dry_run=dry_run) if target else None

    git_installed: list[str] = []
    if include_git_hooks and target and not dry_run:
        try:
            from ..graph import scanner as scanner_mod
            from ..hooks.git_hooks import install_hooks

            repos = [
                scanner_mod.repository_id(r)
                for r in scanner_mod.discover_repos(workspace)
            ]
            git_installed = install_hooks(repos, workspace)
        except ValueError as e:
            print(f"warning: git hooks skipped — {e}")
        except Exception as e:
            print(f"warning: git hooks skipped — {e!r}")

    return InstallReport(detections, results, cross, git_installed)


def uninstall(workspace: str, clients: Optional[list[str]] = None,
              scope: str = "workspace") -> InstallReport:
    """Remove cairn entries for the install scope; the operation is idempotent."""
    from ._common import _claude_agent_md, _read_template
    from .merge import _rm_if_ours, _rm_tree_if_cairn

    detections = detect_clients(workspace)
    if clients:
        bad = [c for c in clients if c not in CLIENTS + ["all"]]
        if bad:
            raise ValueError(f"Unknown clients: {bad}. Valid: {CLIENTS + ['all']}")
        target = set(CLIENTS) if "all" in clients else set(clients)
    else:
        target = {d.client for d in detections if d.detected}

    ws = Path(workspace)
    results: list[InstallResult] = []
    for client in [c for c in CLIENTS if c in target]:
        res = InstallResult(client)
        _UNINSTALLERS[client](ws, res, scope=scope)
        results.append(res)

    cross = InstallResult("cross-tool")
    if target:
        _rm_tree_if_cairn(ws / ".agents" / "skills" / "cairn", cross)
        for name in _SLASH_COMMANDS:
            _rm_if_ours(ws / ".agents" / "commands" / f"{name}.md",
                        _read_template(f"commands/{name}.md"), cross)
        _rm_if_ours(ws / ".agents" / "agents" / "cairn-explorer.md",
                    _claude_agent_md(), cross)
        _rm_if_ours(ws / ".agents" / "agents" / "knowledge-steward.md",
                    _claude_agent_md("cursor/knowledge-steward.json"), cross)

    return InstallReport(detections, results, cross if target else None)
