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
    _hook_markers,
    _HOOK_ENTRYPOINTS,
)


# --------------------------------------------------------------------------
# File writers
# --------------------------------------------------------------------------

def _atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically.

    Writes to a sibling temp file then ``os.replace``s it into place, which is
    atomic on POSIX (and same-volume Windows). A crash (OOM, SIGKILL, full
    disk) between truncate and full write would otherwise leave a zero-byte or
    truncated config file that breaks the user's agent client entirely. The
    temp file lives in the same directory so the rename never crosses a
    filesystem boundary.
    """
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
    """Load JSON from ``path``.

    Returns the parsed value, or ``None`` if the file is missing or its JSON is
    malformed. Callers MUST check for ``None`` and decide whether to skip,
    back up, or raise -- silently overwriting a malformed user config with a
    fresh cairn-only one loses their hand-edited data.
    """
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_file(path: Path, content: str, force: bool, result: InstallResult,
                dry_run: bool = False) -> None:
    """Write a file unless it exists and !force. Records into result.

    In dry-run mode, records the would-be action without touching the disk.
    The exists-check runs BEFORE the dry-run decision so the report matches a
    real run: a file that is already present (and not forced) shows up as
    skipped, not as a phantom "would write".
    """
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


def _backup_to_bak(path: Path, reason: str, result: InstallResult,
                   dry_run: bool = False) -> dict:
    """Back up ``path`` beside itself as ``<name>.bak`` and return ``{}``.

    Used when the existing config cannot be merged into safely (malformed
    JSON, a non-object file, or a non-object value under a top-level key the
    merge touches): the user's data is preserved in the backup while the
    merge starts from a fresh object. In dry-run mode nothing is written --
    the would-be backup is only reported.
    """
    backup = path.with_suffix(path.suffix + ".bak")
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
    """Merge `merger` into a JSON file at `path` (deep-merge top-level keys).

    For .mcp.json: overwrites only the "cairn" entry (config_key="mcpServers").
    For .zcode/config.json: overwrites the cairn server under mcp.servers
    (config_key="zcode").
    For .claude/settings.json: merges hooks.<Event> lists, appending cairn
    hook entries (skipping duplicates that already mention cairn).

    In dry-run mode, records the would-be action without touching the disk.
    The idempotency checks run BEFORE the dry-run decision so the report
    matches a real run: an already-installed (or malformed-to-be-backed-up)
    config is reported as such, not as a blanket "would merge".
    """
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
    """Has the cairn entry already been written into `existing` *and matches*?

    For MCP (config_key="mcpServers"): checks mcpServers.cairn.
    For ZCode (config_key="zcode"): checks mcp.servers.cairn.
    For hooks: checks hooks.<Event> for BOTH cairn hook entrypoints
    (post_edit AND session_end), in either on-disk shape (Claude's nested
    entries or Cursor's flat ones). Requiring both means a partially-stripped
    hooks config reads as absent and the next install re-heals it.
    """
    if config_key == "zcode" and "mcp" in merger:
        mcp = existing.get("mcp")
        cur = mcp.get("servers", {}).get("cairn") if isinstance(mcp, dict) else None
        if not isinstance(cur, dict):
            return False
        new = merger["mcp"]["servers"]["cairn"]
        cur_cmd = cur.get("command", "") + " " + " ".join(cur.get("args", []))
        new_cmd = new.get("command", "") + " " + " ".join(new.get("args", []))
        return cur_cmd.strip() == new_cmd.strip()
    if config_key == "opencode" and "mcp" in merger:
        # opencode: mcp.<name> = {type, command:[...], enabled}. The command is
        # a single array (no separate args), so compare it joined. Remote
        # servers carry `url` instead; compare that.
        mcp = existing.get("mcp")
        cur = mcp.get("cairn") if isinstance(mcp, dict) else None
        if not isinstance(cur, dict):
            return False
        new = merger["mcp"]["cairn"]
        if new.get("type") == "remote":
            return cur.get("url") == new.get("url")
        cur_cmd = " ".join(cur.get("command", []))
        new_cmd = " ".join(new.get("command", []))
        return cur_cmd.strip() == new_cmd.strip()
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
        if isinstance(hooks, dict):
            for entries in hooks.values():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if isinstance(entry, dict):
                        found |= _entry_entrypoints(entry)
        return _HOOK_ENTRYPOINTS <= found
    return False


def _entry_entrypoints(entry: dict) -> set[str]:
    """Which cairn hook entrypoints does this hook entry carry?

    Handles both on-disk shapes: Claude Code's nested
    ``{"matcher": ..., "hooks": [{"command": ...}]}`` and Cursor's flat
    ``{"command": ..., "timeout": ...}``. Matches on the module-qualified
    ``cairn.hooks.claude_hooks <entrypoint>`` marker (and the legacy
    ``src.hooks.claude_hooks`` one), so the python path it was written with
    doesn't matter.
    """
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
    """Deep-merge addition into existing. For lists under hooks.<Event>, append
    new entries (dedup by command substring). For dicts, recurse. For mcpServers
    or mcp.servers (ZCode), replace the named server.

    Non-object values under the keys we merge into (a user's ``"mcp": true``,
    say) are treated as absent rather than crashed on -- though
    ``_merge_json_file`` backs such a file up before we ever get here.
    """
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
        elif key == "mcp" and config_key == "opencode" and isinstance(val, dict):
            # opencode: mcp.<name> = {...}; replace the named server in place.
            # Unlike ZCode there is no nested "servers" sub-key.
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
                # Append entries whose entrypoints aren't already present.
                for entry in entries:
                    if not _entry_present(cur, entry):
                        cur.append(entry)
                out_hooks[event] = cur
            out["hooks"] = out_hooks
        elif isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val, config_key=config_key)
        else:
            out[key] = val
    return out


def _entry_present(entries: list, candidate: dict) -> bool:
    """Is `candidate` (a hook entry) already in `entries`?

    Matches on the cairn hook entrypoint (post_edit / session_end) rather than
    the full command string, so re-installs after a path change don't create
    duplicates. Both on-disk shapes are recognized -- a flat Cursor entry
    carrying an entrypoint suppresses the re-append just like a nested Claude
    one, so Cursor's hooks.json stays at one entry per event across re-installs.
    """
    cand_eps = _entry_entrypoints(candidate)
    if not cand_eps:
        return False
    return any(isinstance(e, dict) and cand_eps & _entry_entrypoints(e)
               for e in entries)


# --------------------------------------------------------------------------
# Uninstall: file/dir removal + per-shape strip helpers
# --------------------------------------------------------------------------

def _rm_if_exists(path: Path, res: InstallResult) -> None:
    if path.exists():
        path.unlink()
        res.written.append(f"removed {path}")


def _rm_tree_if_cairn(path: Path, res: InstallResult) -> None:
    """Remove a dir tree only if it is cairn-scoped.

    All callers target a directory *named* ``cairn`` (e.g.
    ``.claude/skills/cairn``). The name check is the guard the function's name
    promises: without it a future caller passing a broader path (say
    ``.claude`` itself) would ``rmtree`` the user's whole directory. If the
    final path component isn't ``cairn``, refuse and record a note rather than
    delete something that isn't ours.
    """
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
    """Remove ``path`` only if it is byte-identical to what the installer writes.

    Install skips files that already exist (unless --force), so a user file
    that merely shares a cairn filename was never ours. Comparing against the
    installer's generated content keeps uninstall from deleting a file the
    installer itself declined to overwrite. A mismatch (user-edited, or
    written by an older cairn version) is left in place and recorded in
    ``skipped`` -- remove it manually if it really is cairn's.
    """
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
    servers = data.get("mcpServers", {})
    if "cairn" in servers:
        del servers["cairn"]
        data["mcpServers"] = servers
        _atomic_write_text(path, json.dumps(data, indent=2) + "\n")
        res.written.append(f"stripped cairn from {path}")


def _strip_mcp_zcode(path: Path, res) -> None:
    """Remove the cairn server from a .zcode/config.json, leaving others intact.

    Cleans up empty ``mcp`` and ``servers`` keys when cairn was the only
    server, so the file stays tidy.
    """
    data = _load_json_or_none(path)
    if not isinstance(data, dict):
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
        _atomic_write_text(path, json.dumps(data, indent=2) + "\n")
        res.written.append(f"stripped cairn from {path}")


def _strip_mcp_opencode(path: Path, res) -> None:
    """Remove the cairn server from an opencode.json, leaving others intact.

    Also strips a stray ``.opencode/mcp.json`` if an earlier installer wrote one
    (opencode itself does not read it).
    """
    data = _load_json_or_none(path)
    if isinstance(data, dict):
        mcp = data.get("mcp", {})
        if "cairn" in mcp:
            del mcp["cairn"]
            if mcp:
                data["mcp"] = mcp
            else:
                data.pop("mcp", None)
            _atomic_write_text(path, json.dumps(data, indent=2) + "\n")
            res.written.append(f"stripped cairn from {path}")
    # Cleanup: remove a stray .opencode/mcp.json if it carries our server.
    stray = path.parent / ".opencode" / "mcp.json"
    if stray.exists():
        # Only treat it as ours if it carries our server.
        stray_data = _load_json_or_none(stray)
        if isinstance(stray_data, dict) and "cairn" in (stray_data.get("mcpServers") or {}):
            stray.unlink()
            res.written.append(f"removed stray {stray}")


def _strip_hooks(path: Path, res: InstallResult) -> None:
    """Remove cairn hook entries from .claude/settings.json.

    Matches on `cairn.hooks.claude_hooks <entrypoint>` so it strips entries
    regardless of how the python path was written (absolute, venv-relative,
    cd-prefixed).
    """
    data = _load_json_or_none(path)
    if not isinstance(data, dict):
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
        _atomic_write_text(path, json.dumps(data, indent=2) + "\n")
        res.written.append(f"stripped cairn hooks from {path}")


def _strip_cursor_hooks(path: Path, res: InstallResult) -> None:
    """Remove cairn entries from .cursor/hooks.json."""
    data = _load_json_or_none(path)
    if not isinstance(data, dict):
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
        _atomic_write_text(path, json.dumps(data, indent=2) + "\n")
        res.written.append(f"stripped cairn hooks from {path}")
