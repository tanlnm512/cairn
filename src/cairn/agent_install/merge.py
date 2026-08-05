"""JSON merge primitives, file writers, and uninstall strip helpers.

Schema-agnostic: these functions know how to deep-merge, idempotently write,
and strip cairn entries from JSON config files for ANY client. Each
client's config *shape* lives in its own ``clients/<name>.py`` module; the
shape-specific bits are passed in via the ``config_key`` argument.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ._common import (
    InstallResult,
    _claude_hook_command,
    _hook_markers,
    _HOOK_ENTRYPOINTS,
)


# --------------------------------------------------------------------------
# File writers
# --------------------------------------------------------------------------

def _write_file(path: Path, content: str, force: bool, result: InstallResult,
                dry_run: bool = False) -> None:
    """Write a file unless it exists and !force. Records into result.

    In dry-run mode, records the would-be action without touching the disk.
    """
    if dry_run:
        result.written.append(f"would write {path}")
        return
    if path.exists() and not force:
        result.add(path, existed=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if path.suffix in (".py", ".sh") and "scripts" in path.parts:
        path.chmod(path.stat().st_mode | 0o111)
    result.add(path, existed=False)


def _write_tree(dest_dir: Path, src_dir: Path, force: bool, result: InstallResult,
                dry_run: bool = False) -> None:
    """Recursively write every file under `src_dir` to the matching path under
    `dest_dir`, so skill packages (SKILL.md + references/ + scripts/ + evals/)
    ship as a whole instead of one hand-picked file at a time.

    Each file goes through `_write_file`, so per-file force/dry-run/existed
    semantics are unchanged -- only files that don't already exist (or when
    `force=True`) are (re)written. Scripts (.py/.sh under a `scripts/`
    subdirectory) get their executable bit set.
    """
    if not src_dir.is_dir():
        raise FileNotFoundError(f"template directory missing: {src_dir}")
    for src_path in sorted(src_dir.rglob("*")):
        if src_path.is_dir():
            continue
        # Skip bytecode caches and other non-text artifacts that may have been
        # left behind by test runs or editor tooling inside the package tree.
        if src_path.suffix in (".pyc", ".pyo") or src_path.name == "__pycache__":
            continue
        rel = src_path.relative_to(src_dir)
        _write_file(dest_dir / rel, src_path.read_text(encoding="utf-8"), force, result,
                    dry_run=dry_run)


def _merge_json_file(path: Path, merger: dict, force: bool, result: InstallResult, *,
                     config_key: str = "mcpServers", dry_run: bool = False) -> None:
    """Merge `merger` into a JSON file at `path` (deep-merge top-level keys).

    For .mcp.json: overwrites only the "cairn" entry (config_key="mcpServers").
    For .zcode/config.json: overwrites the cairn server under mcp.servers
    (config_key="zcode").
    For .claude/settings.json: merges hooks.<Event> lists, appending cairn
    hook entries (skipping duplicates that already mention cairn).

    In dry-run mode, records the would-be action without touching the disk.
    """
    if dry_run:
        result.written.append(f"would merge into {path}")
        return
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except (json.JSONDecodeError, OSError):
            existing = {}

    # Detect whether our entry is already present.
    if _already_installed(existing, merger, config_key=config_key) and not force:
        result.add(path, existed=True)
        return

    merged = _deep_merge(existing, merger, config_key=config_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    result.add(path, existed=False)


# --------------------------------------------------------------------------
# Merge / idempotency logic
# --------------------------------------------------------------------------

def _already_installed(existing: dict, merger: dict, *, config_key: str = "mcpServers") -> bool:
    """Has the cairn entry already been written into `existing` *and matches*?

    For MCP (config_key="mcpServers"): checks mcpServers.cairn.
    For ZCode (config_key="zcode"): checks mcp.servers.cairn.
    For hooks: checks hooks.<Event> for cairn hook commands.
    """
    if config_key == "zcode" and "mcp" in merger:
        cur = existing.get("mcp", {}).get("servers", {}).get("cairn")
        if not cur:
            return False
        new = merger["mcp"]["servers"]["cairn"]
        cur_cmd = cur.get("command", "") + " " + " ".join(cur.get("args", []))
        new_cmd = new.get("command", "") + " " + " ".join(new.get("args", []))
        return cur_cmd.strip() == new_cmd.strip()
    if config_key == "opencode" and "mcp" in merger:
        # opencode: mcp.<name> = {type, command:[...], enabled}. The command is
        # a single array (no separate args), so compare it joined. Remote
        # servers carry `url` instead; compare that.
        cur = existing.get("mcp", {}).get("cairn")
        if not cur:
            return False
        new = merger["mcp"]["cairn"]
        if new.get("type") == "remote":
            return cur.get("url") == new.get("url")
        cur_cmd = " ".join(cur.get("command", []))
        new_cmd = " ".join(new.get("command", []))
        return cur_cmd.strip() == new_cmd.strip()
    if "mcpServers" in merger:
        cur = existing.get("mcpServers", {}).get("cairn")
        if not cur:
            return False
        new = merger["mcpServers"]["cairn"]
        # Compare the full command (command + args joined) so path changes register.
        cur_cmd = cur.get("command", "") + " " + " ".join(cur.get("args", []))
        new_cmd = new.get("command", "") + " " + " ".join(new.get("args", []))
        if cur_cmd.strip() != new_cmd.strip():
            return False
        # Also compare env (Claude Desktop pins CAIRN_WORKSPACE here). Absent
        # env == {}, so this is a no-op for clients that don't set one.
        return (cur.get("env") or {}) == (new.get("env") or {})
    # Hooks shape: any of our hook commands present in the event lists?
    if "hooks" in merger:
        our_cmds = {_claude_hook_command("post_edit"), _claude_hook_command("session_end")}
        for event, entries in existing.get("hooks", {}).items():
            for entry in entries if isinstance(entries, list) else []:
                for h in entry.get("hooks", []) if isinstance(entry, dict) else []:
                    cmd = h.get("command", "") if isinstance(h, dict) else ""
                    if any(oc in cmd for oc in our_cmds):
                        return True
    return False


def _deep_merge(existing: dict, addition: dict, *, config_key: str = "mcpServers") -> dict:
    """Deep-merge addition into existing. For lists under hooks.<Event>, append
    new entries (dedup by command substring). For dicts, recurse. For mcpServers
    or mcp.servers (ZCode), replace the named server."""
    out = dict(existing)
    for key, val in addition.items():
        if key == "mcpServers" and isinstance(val, dict):
            servers = dict(out.get("mcpServers", {}))
            servers.update(val)
            out["mcpServers"] = servers
        elif key == "mcp" and config_key == "zcode" and isinstance(val, dict):
            mcp_out = dict(out.get("mcp", {}))
            servers = dict(mcp_out.get("servers", {}))
            servers.update(val.get("servers", {}))
            mcp_out["servers"] = servers
            out["mcp"] = mcp_out
            # Remove stale mcpServers key if present (ZCode doesn't use it).
            out.pop("mcpServers", None)
        elif key == "mcp" and config_key == "opencode" and isinstance(val, dict):
            # opencode: mcp.<name> = {...}; replace the named server in place.
            # Unlike ZCode there is no nested "servers" sub-key.
            mcp_out = dict(out.get("mcp", {}))
            mcp_out.update(val)
            out["mcp"] = mcp_out
        elif key == "hooks" and isinstance(val, dict):
            out_hooks = dict(out.get("hooks", {}))
            for event, entries in val.items():
                if not isinstance(entries, list):
                    continue
                cur = out_hooks.get(event, [])
                if not isinstance(cur, list):
                    cur = []
                # Append entries whose commands aren't already present.
                for entry in entries:
                    if not _entry_present(cur, entry):
                        cur.append(entry)
                out_hooks[event] = cur
            out["hooks"] = out_hooks
        elif isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def _entry_present(entries: list, candidate: dict) -> bool:
    """Is `candidate` (a hook entry) already in `entries`?

    Matches on the cairn hook entrypoint name (post_edit / session_end)
    rather than the full command string, so re-installs after a path change
    don't create duplicates.
    """
    cand_eps = {ep for ep in _HOOK_ENTRYPOINTS
                for h in candidate.get("hooks", []) if isinstance(h, dict)
                if ep in h.get("command", "")}
    if not cand_eps:
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for h in entry.get("hooks", []):
            if isinstance(h, dict):
                cmd = h.get("command", "")
                if any(ep in cmd for ep in cand_eps):
                    return True
    return False


# --------------------------------------------------------------------------
# Uninstall: file/dir removal + per-shape strip helpers
# --------------------------------------------------------------------------

def _rm_if_exists(path: Path, res: InstallResult) -> None:
    if path.exists():
        path.unlink()
        res.written.append(f"removed {path}")


def _rm_tree_if_cairn(path: Path, res: InstallResult) -> None:
    """Remove a dir tree only if it contains cairn content."""
    if path.is_dir() and path.exists():
        shutil.rmtree(path)
        res.written.append(f"removed {path}/")


def _rm_if_cairn(path: Path, res: InstallResult) -> None:
    if path.exists():
        path.unlink()
        res.written.append(f"removed {path}")


def _strip_mcp(path: Path, res) -> None:
    """Remove the cairn server from an mcp.json, leaving others intact."""
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    servers = data.get("mcpServers", {})
    if "cairn" in servers:
        del servers["cairn"]
        data["mcpServers"] = servers
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        res.written.append(f"stripped cairn from {path}")


def _strip_mcp_zcode(path: Path, res) -> None:
    """Remove the cairn server from a .zcode/config.json, leaving others intact.

    Cleans up empty ``mcp`` and ``servers`` keys when cairn was the only
    server, so the file stays tidy.
    """
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    servers = data.get("mcp", {}).get("servers", {})
    if "cairn" in servers:
        del servers["cairn"]
        mcp = data.get("mcp", {})
        if servers:
            mcp["servers"] = servers
        else:
            mcp.pop("servers", None)
        if mcp:
            data["mcp"] = mcp
        else:
            data.pop("mcp", None)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        res.written.append(f"stripped cairn from {path}")


def _strip_mcp_opencode(path: Path, res) -> None:
    """Remove the cairn server from an opencode.json, leaving others intact.

    opencode's MCP shape is a flat ``mcp.<name>`` dict (no nested ``servers``
    key like ZCode). Cleans up the empty ``mcp`` key when cairn was the
    only server. Also strips a stray ``.opencode/mcp.json`` if an earlier
    installer wrote one (opencode itself does not read it).
    """
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        mcp = data.get("mcp", {})
        if "cairn" in mcp:
            del mcp["cairn"]
            if mcp:
                data["mcp"] = mcp
            else:
                data.pop("mcp", None)
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            res.written.append(f"stripped cairn from {path}")
    # Cleanup: an earlier installer wrote .opencode/mcp.json (wrong path,
    # never read by opencode). Remove it if present so uninstall is complete.
    stray = path.parent / ".opencode" / "mcp.json"
    if stray.exists():
        # Only treat it as ours if it carries our server.
        try:
            stray_data = json.loads(stray.read_text(encoding="utf-8"))
            if "cairn" in (stray_data.get("mcpServers") or {}):
                stray.unlink()
                res.written.append(f"removed stray {stray}")
        except (json.JSONDecodeError, OSError):
            pass


def _strip_hooks(path: Path, res: InstallResult) -> None:
    """Remove cairn hook entries from .claude/settings.json.

    Matches on `cairn.hooks.claude_hooks <entrypoint>` so it strips entries
    regardless of how the python path was written (absolute, venv-relative,
    cd-prefixed).
    """
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    hooks = data.get("hooks", {})
    markers = _hook_markers()
    changed = False
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        kept = []
        for entry in entries:
            if not isinstance(entry, dict):
                kept.append(entry)
                continue
            inner = entry.get("hooks", [])
            if any(isinstance(h, dict)
                   and any(m in h.get("command", "") for m in markers)
                   for h in inner):
                changed = True
                continue  # drop this cairn entry
            kept.append(entry)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
            changed = True
    if changed:
        if hooks:
            data["hooks"] = hooks
        else:
            data.pop("hooks", None)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        res.written.append(f"stripped cairn hooks from {path}")


def _strip_cursor_hooks(path: Path, res: InstallResult) -> None:
    """Remove cairn entries from .cursor/hooks.json."""
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    hooks = data.get("hooks", {})
    markers = _hook_markers()
    changed = False
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        kept = [e for e in entries
                if not (isinstance(e, dict) and any(m in e.get("command", "") for m in markers))]
        if len(kept) != len(entries):
            changed = True
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if changed:
        if hooks:
            data["hooks"] = hooks
        else:
            data.pop("hooks", None)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        res.written.append(f"stripped cairn hooks from {path}")
