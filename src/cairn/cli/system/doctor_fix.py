"""Safe remediation of stale agent-client MCP registrations."""
from __future__ import annotations

import json
import time
from pathlib import Path


_FRESHNESS_WINDOW_S = 60.0
_REPOINTED = "repointed-to-stdio"
_REFUSED_RECENT = "refused-recent-write"
_SKIPPED_WORKSPACE = "skipped-workspace-owned"


def _stdio_config(client: str, workspace: str | None = None) -> dict | None:
    from ...agent_install import (
        kilo_mcp_config_json,
        mcp_config_json,
        mcp_config_json_desktop,
        opencode_mcp_config_json,
        zcode_mcp_config_json,
    )
    from ...agent_install.clients.agy import (
        agy_mcp_config_json,
    )

    if client in {"claude", "cursor", "droid", "omp"}:
        return mcp_config_json()
    if client == "agy":
        return agy_mcp_config_json()
    if client == "claude-desktop":
        return mcp_config_json_desktop(workspace or str(Path.cwd()))
    if client == "zcode":
        return zcode_mcp_config_json()
    if client == "opencode":
        return opencode_mcp_config_json()
    if client == "kilo":
        return kilo_mcp_config_json()
    return None


def _replace_registration(data: dict, client: str) -> bool:
    try:
        if client == "zcode":
            parent = data["mcp"]["servers"]
        elif client in {"opencode", "kilo"}:
            parent = data["mcp"]
        else:
            parent = data["mcpServers"]
    except (KeyError, TypeError):
        return False
    if not isinstance(parent, dict):
        return False
    workspace = _pinned_workspace(parent.get("cairn"))
    generated = _stdio_config(client, workspace)
    if generated is None:
        return False
    try:
        if client == "zcode":
            replacement = generated["mcp"]["servers"]["cairn"]
        elif client in {"opencode", "kilo"}:
            replacement = generated["mcp"]["cairn"]
        else:
            replacement = generated["mcpServers"]["cairn"]
    except (KeyError, TypeError):
        return False
    if not isinstance(replacement, dict):
        return False
    parent["cairn"] = replacement
    return True


def _pinned_workspace(entry: object) -> str | None:
    if not isinstance(entry, dict):
        return None
    env = entry.get("env")
    if not isinstance(env, dict):
        return None
    pinned = env.get("CAIRN_WORKSPACE")
    return pinned if isinstance(pinned, str) and pinned else None


def apply_doctor_fixes(db: str) -> list[dict]:
    """Repoint dead user-owned SSE registrations and return action records."""
    from ...agent_install import InstallResult
    from ...agent_install.merge import (
        _atomic_write_text,
        _backup_to_bak,
        _load_json_or_none,
    )
    from ...mcp_server import lifecycle
    from .doctor import _enumerate_registrations, _sse_endpoint, _sse_host_port

    actions: list[dict] = []
    for client, path, display, entry, workspace_owned in (
            _enumerate_registrations()):
        if "command" in entry:
            continue
        url = _sse_endpoint(entry)
        if url is None:
            continue
        host_port = _sse_host_port(url)
        if host_port is not None and lifecycle.sse_responds(
            host=host_port[0], port=host_port[1]
        ):
            continue

        def action(action_name: str) -> dict:
            return {
                "client": client,
                "config": display,
                "action": action_name,
                "url": url,
            }

        if workspace_owned:
            actions.append(action(_SKIPPED_WORKSPACE))
            continue
        try:
            recent = path.stat().st_mtime > time.time() - _FRESHNESS_WINDOW_S
        except OSError:
            continue
        if recent:
            actions.append(action(_REFUSED_RECENT))
            continue

        data = _load_json_or_none(path)
        if not isinstance(data, dict):
            continue
        if not _replace_registration(data, client):
            continue
        _backup_to_bak(path, "doctor-fix", InstallResult(client))
        _atomic_write_text(path, json.dumps(data, indent=2) + "\n")
        actions.append(action(_REPOINTED))
    return actions
