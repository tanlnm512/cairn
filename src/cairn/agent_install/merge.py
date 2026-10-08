"""JSON merge primitives, file writers, and uninstall strip helpers."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ._common import (
    InstallResult,
    _hook_markers,
    _HOOK_ENTRYPOINTS,
)


# --------------------------------------------------------------------------
# File writers
# --------------------------------------------------------------------------

def _atomic_write_text(path: Path, content: str) -> None:
    """Atomically replace path with content using a same-directory temp file."""
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        # Clean up the temp file on any failure; never leave a dangling .tmp.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _load_json_or_none(path: Path):
    """Return parsed JSON at path, or None when missing or malformed."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_file(path: Path, content: str, force: bool, result: InstallResult,
                dry_run: bool = False) -> None:
    """Write path unless it exists and force is false, recording the result."""
    if path.exists() and not force:
        result.add(path, existed=True)
        return
    if dry_run:
        result.written.append(f"would write {path}")
        return
    _atomic_write_text(path, content)
    if path.suffix in (".py", ".sh") and "scripts" in path.parts:
        path.chmod(path.stat().st_mode | 0o111)
    result.add(path, existed=False)


def _write_tree(dest_dir: Path, src_dir: Path, force: bool, result: InstallResult,
                dry_run: bool = False) -> None:
    """Recursively install a skill package with per-file install semantics."""
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


def _backup_to_bak(path: Path, reason: str, result: InstallResult,
                   dry_run: bool = False) -> dict:
    """Back up path beside itself and return an empty merge target."""
    backup = path.with_suffix(path.suffix + ".bak")
    if backup.exists():
        # The existing .bak may be the only preserved copy of an earlier
        # state: never overwrite it, slot the new backup beside it instead.
        n = 1
        backup = path.with_suffix(f"{path.suffix}.bak.{n}")
        while backup.exists():
            n += 1
            backup = path.with_suffix(f"{path.suffix}.bak.{n}")
    if dry_run:
        result.written.append(f"would back up {reason} {path} -> {backup}")
        return {}
    try:
        backup.write_bytes(path.read_bytes())
    except OSError:
        # If even the backup fails, refuse to overwrite rather than destroy data.
        raise RuntimeError(
            f"refusing to overwrite {reason} config {path}: could not "
            f"back it up. Fix or remove the file and re-run."
        )
    result.written.append(f"backed up {reason} {path} -> {backup}")
    return {}


# Top-level keys the merge writes into; a non-object value under any of them
# (e.g. ``"mcp": true`` or ``"hooks": []``) makes the merge crash or destroy
# the user's value, so the file is treated like a malformed config.
_MERGE_TOUCHED_KEYS = ("mcpServers", "mcp", "hooks")


def _merge_keys_non_object(existing: dict, merger: dict) -> bool:
    """True if a top-level key the merge writes holds a non-object value."""
    return any(
        k in merger and k in existing and not isinstance(existing[k], dict)
        for k in _MERGE_TOUCHED_KEYS
    )


def _merge_json_file(path: Path, merger: dict, force: bool, result: InstallResult, *,
                     config_key: str = "mcpServers", dry_run: bool = False) -> None:
    """Merge cairn configuration into a JSON file with dry-run fidelity."""
    existing: dict = {}
    if path.exists():
        loaded = _load_json_or_none(path)
        if loaded is None:
            # Malformed/unreadable JSON: do NOT clobber the user's config. Back
            # it up so the data is recoverable, then start fresh.
            existing = _backup_to_bak(path, "malformed", result, dry_run)
        elif isinstance(loaded, dict):
            if _merge_keys_non_object(loaded, merger):
                # Valid object, but a key we must merge into (mcpServers/mcp/
                # hooks) holds a non-object value. Same discipline as malformed:
                # back up, then start fresh.
                existing = _backup_to_bak(path, "non-object", result, dry_run)
            else:
                existing = loaded
        else:
            # Valid JSON but not an object (e.g. a bare array/string). Preserve
            # it as-is by backing up and starting fresh.
            existing = _backup_to_bak(path, "non-object", result, dry_run)

    # Detect whether our entry is already present.
    if _already_installed(existing, merger, config_key=config_key) and not force:
        result.add(path, existed=True)
        return

    if dry_run:
        result.written.append(f"would merge into {path}")
        return

    merged = _deep_merge(existing, merger, config_key=config_key)
    _atomic_write_text(path, json.dumps(merged, indent=2) + "\n")
    result.add(path, existed=False)


# --------------------------------------------------------------------------
# Merge / idempotency logic
# --------------------------------------------------------------------------

def _already_installed(existing: dict, merger: dict, *, config_key: str = "mcpServers") -> bool:
    """Return True when every required cairn entry already exists and matches."""
    if config_key == "zcode" and "mcp" in merger:
        mcp = existing.get("mcp")
        servers = mcp.get("servers") if isinstance(mcp, dict) else None
        cur = servers.get("cairn") if isinstance(servers, dict) else None
        if not isinstance(cur, dict):
            return False
        new = merger["mcp"]["servers"]["cairn"]
        cur_cmd = cur.get("command", "") + " " + " ".join(cur.get("args", []))
        new_cmd = new.get("command", "") + " " + " ".join(new.get("args", []))
        if cur_cmd.strip() != new_cmd.strip():
            return False
        # Same env rule as the flat mcpServers branch: a changed env block
        # (e.g. CAIRN_HOME after moving the store) must read as not-installed
        # so the reinstall rewrites it.
        return (cur.get("env") or {}) == (new.get("env") or {})
    if config_key in ("opencode", "kilo") and "mcp" in merger:
        # opencode/kilo: mcp.<name> = {type, command:[...], enabled}. The
        # command is a single array (no separate args), so compare it joined.
        # Remote servers carry `url` instead; compare that.
        mcp = existing.get("mcp")
        cur = mcp.get("cairn") if isinstance(mcp, dict) else None
        if not isinstance(cur, dict):
            return False
        new = merger["mcp"]["cairn"]
        if new.get("type") == "remote":
            return (cur.get("url") == new.get("url")
                    and (cur.get("env") or {}) == (new.get("env") or {}))
        cur_cmd = " ".join(cur.get("command", []))
        new_cmd = " ".join(new.get("command", []))
        if cur_cmd.strip() != new_cmd.strip():
            return False
        # Same env rule as the flat mcpServers branch: a changed env block
        # (e.g. CAIRN_HOME after moving the store) must read as not-installed
        # so the reinstall rewrites it.
        return (cur.get("env") or {}) == (new.get("env") or {})
    if "mcpServers" in merger:
        servers = existing.get("mcpServers")
        cur = servers.get("cairn") if isinstance(servers, dict) else None
        if not isinstance(cur, dict):
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
    # Hooks shape: BOTH of our hook entrypoints present in the event lists?
    if "hooks" in merger:
        hooks = existing.get("hooks")
        found: set[str] = set()
        commands: set[str] = set()
        if isinstance(hooks, dict):
            for entries in hooks.values():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if isinstance(entry, dict):
                        found |= _entry_entrypoints(entry)
                        commands |= _entry_commands(entry)
        expected = {
            command
            for entries in merger["hooks"].values()
            if isinstance(entries, list)
            for entry in entries
            if isinstance(entry, dict)
            for command in _entry_commands(entry)
        }
        if not expected <= commands:
            return False
        return _HOOK_ENTRYPOINTS <= found
    return False


def _entry_commands(entry: dict) -> set[str]:
    """Return the command strings carried by a Claude or Cursor hook entry."""
    commands: set[str] = set()
    inner = entry.get("hooks", [])
    if isinstance(inner, list):
        for hook in inner:
            if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                commands.add(hook["command"])
    if isinstance(entry.get("command"), str):
        commands.add(entry["command"])
    return commands


def _entry_entrypoints(entry: dict) -> set[str]:
    """Return cairn hook entrypoint names carried by a client hook entry."""
    cmds: list[str] = []
    inner = entry.get("hooks", [])
    if isinstance(inner, list):
        for h in inner:
            if isinstance(h, dict) and isinstance(h.get("command"), str):
                cmds.append(h["command"])
    if isinstance(entry.get("command"), str):
        cmds.append(entry["command"])
    eps: set[str] = set()
    for cmd in cmds:
        for ep in _HOOK_ENTRYPOINTS:
            if (f"cairn.hooks.claude_hooks {ep}" in cmd
                    or f"src.hooks.claude_hooks {ep}" in cmd):
                eps.add(ep)
    return eps


def _deep_merge(existing: dict, addition: dict, *, config_key: str = "mcpServers") -> dict:
    """Deep-merge configuration while replacing named MCP servers."""
    out = dict(existing)
    for key, val in addition.items():
        if key == "mcpServers" and isinstance(val, dict):
            prev = out.get("mcpServers")
            servers = dict(prev) if isinstance(prev, dict) else {}
            servers.update(val)
            out["mcpServers"] = servers
        elif key == "mcp" and config_key == "zcode" and isinstance(val, dict):
            prev = out.get("mcp")
            mcp_out = dict(prev) if isinstance(prev, dict) else {}
            prev_servers = mcp_out.get("servers")
            servers = dict(prev_servers) if isinstance(prev_servers, dict) else {}
            servers.update(val.get("servers", {}))
            mcp_out["servers"] = servers
            out["mcp"] = mcp_out
            # Remove stale mcpServers key if present (ZCode doesn't use it).
            out.pop("mcpServers", None)
        elif key == "mcp" and config_key in ("opencode", "kilo") and isinstance(val, dict):
            # opencode/kilo: mcp.<name> = {...}; replace the named server in
            # place. Unlike ZCode there is no nested "servers" sub-key.
            prev = out.get("mcp")
            mcp_out = dict(prev) if isinstance(prev, dict) else {}
            mcp_out.update(val)
            out["mcp"] = mcp_out
        elif key == "hooks" and isinstance(val, dict):
            prev = out.get("hooks")
            out_hooks = dict(prev) if isinstance(prev, dict) else {}
            for event, entries in val.items():
                if not isinstance(entries, list):
                    continue
                cur = out_hooks.get(event, [])
                if not isinstance(cur, list):
                    cur = []
                cur = [e for e in cur if not _entry_entrypoints(e)]
                cur.extend(entries)
                out_hooks[event] = cur
            out["hooks"] = out_hooks
        elif isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val, config_key=config_key)
        else:
            out[key] = val
    return out


# --------------------------------------------------------------------------
# Uninstall: file/dir removal + per-shape strip helpers
# --------------------------------------------------------------------------

def _rm_if_exists(path: Path, res: InstallResult) -> None:
    if path.exists():
        path.unlink()
        res.written.append(f"removed {path}")


def _rm_tree_if_cairn(path: Path, res: InstallResult) -> None:
    """Remove a cairn-named directory tree, refusing any broader path."""
    if not (path.is_dir() and path.exists()):
        return
    if path.name != "cairn":
        res.notes.append(
            f"refused to remove {path}/: not cairn-scoped (directory must be "
            f"named 'cairn'). Remove manually if intended."
        )
        return
    shutil.rmtree(path)
    res.written.append(f"removed {path}/")


def _rm_if_ours(path: Path, expected: str, res: InstallResult) -> None:
    """Remove path only when its bytes equal generated installer content."""
    if not path.exists():
        return
    try:
        ours = path.read_text(encoding="utf-8") == expected
    except (OSError, UnicodeDecodeError):
        ours = False
    if not ours:
        res.skipped.append(f"{path} (not cairn-written; left in place)")
        return
    path.unlink()
    res.written.append(f"removed {path}")


def _strip_mcp(path: Path, res) -> None:
    """Remove the cairn server from an mcp.json, leaving others intact."""
    data = _load_json_or_none(path)
    if not isinstance(data, dict):
        return
    servers = data.get("mcpServers")
    if isinstance(servers, dict) and "cairn" in servers:
        del servers["cairn"]
        data["mcpServers"] = servers
        _atomic_write_text(path, json.dumps(data, indent=2) + "\n")
        res.written.append(f"stripped cairn from {path}")


def _strip_mcp_zcode(path: Path, res) -> None:
    """Remove cairn from ZCode config and prune emptied MCP containers."""
    data = _load_json_or_none(path)
    if not isinstance(data, dict):
        return
    mcp = data.get("mcp")
    if not isinstance(mcp, dict):
        return
    servers = mcp.get("servers")
    if not (isinstance(servers, dict) and "cairn" in servers):
        return
    del servers["cairn"]
    if servers:
        mcp["servers"] = servers
    else:
        mcp.pop("servers", None)
    if mcp:
        data["mcp"] = mcp
    else:
        data.pop("mcp", None)
    _atomic_write_text(path, json.dumps(data, indent=2) + "\n")
    res.written.append(f"stripped cairn from {path}")


def _strip_mcp_kilo(path: Path, res) -> None:
    """Remove the cairn server from an opencode-format ``mcp`` key (opencode/kilo)."""
    data = _load_json_or_none(path)
    if not isinstance(data, dict):
        return
    mcp = data.get("mcp")
    if not (isinstance(mcp, dict) and "cairn" in mcp):
        return
    del mcp["cairn"]
    if mcp:
        data["mcp"] = mcp
    else:
        data.pop("mcp", None)
    _atomic_write_text(path, json.dumps(data, indent=2) + "\n")
    res.written.append(f"stripped cairn from {path}")


def _strip_mcp_opencode(path: Path, res) -> None:
    """Remove cairn from OpenCode config and its legacy MCP file."""
    _strip_mcp_kilo(path, res)
    # Cleanup: remove a stray .opencode/mcp.json if it carries our server.
    stray = path.parent / ".opencode" / "mcp.json"
    if stray.exists():
        stray_data = _load_json_or_none(stray)
        stray_servers = (
            stray_data.get("mcpServers") if isinstance(stray_data, dict) else None
        )
        if isinstance(stray_servers, dict) and "cairn" in stray_servers:
            stray.unlink()
            res.written.append(f"removed stray {stray}")


def _strip_hooks(path: Path, res: InstallResult) -> None:
    """Remove cairn hook entries across every historical command spelling."""
    data = _load_json_or_none(path)
    if not isinstance(data, dict):
        return
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return
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
        _atomic_write_text(path, json.dumps(data, indent=2) + "\n")
        res.written.append(f"stripped cairn hooks from {path}")


def _strip_cursor_hooks(path: Path, res: InstallResult) -> None:
    """Remove cairn entries from .cursor/hooks.json."""
    data = _load_json_or_none(path)
    if not isinstance(data, dict):
        return
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return
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
        _atomic_write_text(path, json.dumps(data, indent=2) + "\n")
        res.written.append(f"stripped cairn hooks from {path}")
