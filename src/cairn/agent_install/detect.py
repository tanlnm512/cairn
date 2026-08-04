"""Client detection: which AI coding clients are present for a workspace.

Also owns `claude_desktop_config_path` (the OS-specific global Desktop config
path), which detection needs. `resolve_cg_command`/`resolve_cg_str` live in
``_common`` because client config generators need them too and must not import
detect (which would pull detection into every client module); they are
re-exported here for callers that import them from the detection module.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path



def claude_desktop_config_path() -> Path:
    """Location of Claude Desktop's global MCP config file, per-OS.

    Claude Desktop (the GUI app) reads a single global config rather than a
    per-workspace file:
      macOS:   ~/Library/Application Support/Claude/claude_desktop_config.json
      Windows: %APPDATA%/Claude/claude_desktop_config.json
      Linux:   ~/.config/Claude/claude_desktop_config.json
    """
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", str(home)))
        return base / "Claude" / "claude_desktop_config.json"
    return home / ".config" / "Claude" / "claude_desktop_config.json"


@dataclass
class Detection:
    client: str
    detected: bool
    reason: str  # human-readable why/why-not


def detect_clients(workspace: str) -> list[Detection]:
    """Detect which AI coding clients are present for this workspace."""
    ws = Path(workspace)
    home = Path.home()
    results: list[Detection] = []

    # Claude Code: CLI on PATH, or user config dir, or workspace .claude/
    claude = bool(shutil.which("claude")) or (home / ".claude").is_dir() or (ws / ".claude").is_dir()
    results.append(Detection("claude", claude,
                             "claude CLI on PATH" if shutil.which("claude")
                             else ("~/.claude exists" if (home / ".claude").is_dir()
                                   else ".claude/ in workspace" if (ws / ".claude").is_dir()
                                   else "not found")))

    # Cursor: CLI, app bundle, or workspace .cursor/
    cursor = (bool(shutil.which("cursor"))
              or Path("/Applications/Cursor.app").exists()
              or (ws / ".cursor").is_dir())
    reason = ("cursor CLI on PATH" if shutil.which("cursor")
              else "Cursor.app in /Applications" if Path("/Applications/Cursor.app").exists()
              else ".cursor/ in workspace" if (ws / ".cursor").is_dir()
              else "not found")
    results.append(Detection("cursor", cursor, reason))

    # Droid/Factory: CLI, or workspace .factory/
    droid = bool(shutil.which("droid")) or (ws / ".factory").is_dir()
    results.append(Detection("droid", droid,
                             "droid CLI on PATH" if shutil.which("droid")
                             else ".factory/ in workspace" if (ws / ".factory").is_dir()
                             else "not found"))

    # ZCode: user config dir
    zcode = (home / ".zcode").is_dir()
    results.append(Detection("zcode", zcode,
                             "~/.zcode exists" if zcode else "not found"))

    # Claude Desktop: GUI app with a single global MCP config. Detected if its
    # config file exists or the app-support "Claude" directory is present.
    cd_cfg = claude_desktop_config_path()
    claude_desktop = cd_cfg.exists() or cd_cfg.parent.is_dir()
    results.append(Detection("claude-desktop", claude_desktop,
                             "config file exists" if cd_cfg.exists()
                             else f"{cd_cfg.parent.name}/ app dir exists" if cd_cfg.parent.is_dir()
                             else "not found"))

    # agy TUI CLI
    agy = bool(shutil.which("agy")) or (home / ".gemini" / "antigravity-cli").is_dir()
    results.append(Detection("agy", agy,
                             "agy CLI on PATH" if shutil.which("agy")
                             else "~/.gemini/antigravity-cli exists" if (home / ".gemini" / "antigravity-cli").is_dir()
                             else "not found"))

    # OpenCode: CLI, ~/.config/opencode, or workspace .opencode/
    opencode = bool(shutil.which("opencode")) or (ws / ".opencode").is_dir() or (home / ".config" / "opencode").is_dir()
    results.append(Detection("opencode", opencode,
                             "opencode CLI on PATH" if shutil.which("opencode")
                             else ".opencode/ in workspace" if (ws / ".opencode").is_dir()
                             else "~/.config/opencode exists" if (home / ".config" / "opencode").is_dir()
                             else "not found"))

    return results


def _json_has_cairn(path: Path) -> bool:
    """True if a JSON file has a 'cairn' key in mcpServers or top-level."""
    try:
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False
        # Check mcpServers.cairn (Claude Desktop, .mcp.json shape)
        servers = data.get("mcpServers", {})
        if isinstance(servers, dict) and "cairn" in servers:
            return True
        # Check top-level cairn key (some configs)
        if "cairn" in data:
            return True
        # ZCode nested shape: mcp.servers.cairn
        mcp = data.get("mcp", {})
        if isinstance(mcp, dict):
            nested_servers = mcp.get("servers", {})
            if isinstance(nested_servers, dict) and "cairn" in nested_servers:
                return True
        # opencode flat shape: mcp.cairn
        if isinstance(mcp, dict) and "cairn" in mcp:
            return True
        return False
    except (OSError, ValueError, KeyError):
        return False


def check_installed(workspace: str) -> dict[str, bool]:
    """For each client, True if cairn is already wired in.

    Checks the client's config file(s) and skill directories for cairn
    entries. Used by `cairn install-agents` to show which clients already have
    cairn vs which need it, so the user isn't blindly re-installing.
    """
    ws = Path(workspace)
    home = Path.home()
    result: dict[str, bool] = {}

    # claude: .mcp.json has cairn OR .claude/skills/cairn/ exists
    result["claude"] = (
        _json_has_cairn(ws / ".mcp.json")
        or (ws / ".claude" / "skills" / "cairn" / "SKILL.md").exists()
        or _json_has_cairn(home / ".claude" / "settings.json")
    )

    # claude-desktop: global config has cairn in mcpServers
    result["claude-desktop"] = _json_has_cairn(claude_desktop_config_path())

    # cursor: .cursor/mcp.json has cairn OR .cursor/rules/cairn.mdc exists
    result["cursor"] = (
        _json_has_cairn(ws / ".cursor" / "mcp.json")
        or (ws / ".cursor" / "rules" / "cairn.mdc").exists()
        or _json_has_cairn(home / ".cursor" / "mcp.json")
    )

    # droid: .factory/skills/cairn/ exists
    result["droid"] = (ws / ".factory" / "skills" / "cairn" / "SKILL.md").exists()

    # zcode: .zcode/config.json has cairn entry
    result["zcode"] = (
        _json_has_cairn(ws / ".zcode" / "config.json")
        or _json_has_cairn(home / ".zcode" / "config.json")
    )

    # agy: ~/.gemini/config/mcp_config.json has cairn
    result["agy"] = _json_has_cairn(home / ".gemini" / "config" / "mcp_config.json")

    # opencode: opencode.json has cairn MCP entry
    result["opencode"] = (
        _json_has_cairn(ws / "opencode.json")
        or _json_has_cairn(home / ".config" / "opencode" / "opencode.json")
    )

    return result
