"""Safe remediation of stale agent-client MCP registrations."""
from __future__ import annotations

import json
import time


_FRESHNESS_WINDOW_S = 60.0
_REPOINTED = "repointed-to-url"
_REFUSED_RECENT = "refused-recent-write"
_SKIPPED_WORKSPACE = "skipped-workspace-owned"
_UNSUPPORTED_DESKTOP = "remove-unsupported-desktop"
_UNREADABLE_CONFIG = "unreadable-config"
_MALFORMED_CONFIG = "malformed-config"
_UNREPLACEABLE = "unrecognized-shape"
_WRITE_FAILED = "write-failed"


def _url_config(client: str) -> dict | None:
    from ...agent_install import (
        kilo_mcp_config_json,
        mcp_config_json,
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
    generated = _url_config(client)
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


def apply_doctor_fixes(db: str) -> list[dict]:
    """Repoint stale user-owned registrations to the daemon URL and return action records."""
    from ...agent_install import InstallResult
    from ...agent_install._common import daemon_mcp_url
    from ...agent_install.merge import (
        _atomic_write_text,
        _backup_to_bak,
        _load_json_or_none,
    )
    from .doctor import (
        _enumerate_registrations,
        _registration_url,
        _stale_registration,
    )

    destination = daemon_mcp_url()
    actions: list[dict] = []
    for client, path, display, entry, workspace_owned in (
            _enumerate_registrations()):
        if client == "claude-desktop":
            actions.append({
                "client": client,
                "config": display,
                "action": _UNSUPPORTED_DESKTOP,
            })
            continue
        if not _stale_registration(entry, destination):
            continue

        def action(action_name: str, url: str | None) -> dict:
            return {
                "client": client,
                "config": display,
                "action": action_name,
                "url": url,
            }

        if workspace_owned:
            actions.append(action(_SKIPPED_WORKSPACE, _registration_url(entry)))
            continue
        try:
            recent = path.stat().st_mtime > time.time() - _FRESHNESS_WINDOW_S
        except OSError:
            actions.append(action(_UNREADABLE_CONFIG, _registration_url(entry)))
            continue
        if recent:
            actions.append(action(_REFUSED_RECENT, _registration_url(entry)))
            continue

        data = _load_json_or_none(path)
        if not isinstance(data, dict):
            actions.append(action(_MALFORMED_CONFIG, _registration_url(entry)))
            continue
        if not _replace_registration(data, client):
            actions.append(action(_UNREPLACEABLE, _registration_url(entry)))
            continue
        try:
            _backup_to_bak(path, "doctor-fix", InstallResult(client))
            _atomic_write_text(path, json.dumps(data, indent=2) + "\n")
        except (OSError, RuntimeError):
            actions.append(action(_WRITE_FAILED, _registration_url(entry)))
            continue
        actions.append(action(_REPOINTED, destination))
    return actions
