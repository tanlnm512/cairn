"""Agent integration installer: wires cairn into AI coding clients.

Detects installed clients (Claude Code, Cursor, Droid/Factory, ZCode, agy,
opencode, kilo, omp, Claude Desktop) and writes their per-client configs — MCP server,
skills, slash commands, subagents/droids, rules, and hooks — with paths
resolved at install time. Configs are *generated* (not copied), pointing at
the installed `cairn` binary, so there are no hardcoded paths and no dependence
on cwd at runtime.

The knowledge model (per-workspace graph + .knowledge in ~/.cairn) is
unaffected; this package only writes client-facing config files.

Package layout (per the agent_install split):
- ``_common``   — constants (CLIENTS, _SLASH_COMMANDS), shared helpers,
                  InstallResult, the shared mcp_config_json generator.
- ``detect``    — Detection, detect_clients, claude_desktop_config_path.
- ``merge``     — _deep_merge / _already_installed / _entry_present /
                  _merge_json_file / _write_file / _write_tree + strip helpers.
- ``clients/``  — one module per client, each owning its config schema +
                  install + uninstall (no client imports a sibling client).
- this module   — the public ``install()`` / ``uninstall()`` dispatch +
                  cross-tool fallback, re-exporting the public surface so
                  ``from cairn.agent_install import install, uninstall,
                  detect_clients, CLIENTS, Detection`` keeps working.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import paths

# Public re-exports (single source of truth for the public API).
from ._common import (
    CLIENTS,
    InstallResult,
    _SLASH_COMMANDS,
    mcp_config_json,
    resolve_cg_command,
    resolve_cg_str,
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
    _entry_present,
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

# Per-client config generators (re-exported for backward compat — some callers
# and tests import them by name from the top-level module).
from .clients.zcode import zcode_mcp_config_json
from .clients.opencode import opencode_mcp_config_json
from .clients.kilo import kilo_mcp_config_json
from .clients.claude_desktop import mcp_config_json_desktop
from .clients.claude import claude_hooks_block
from .clients.cursor import cursor_hooks_json

# Per-client installers (importable from the top level).
from .clients.claude import install_claude
from .clients.claude_desktop import install_claude_desktop
from .clients.cursor import install_cursor
from .clients.droid import install_droid
from .clients.zcode import install_zcode
from .clients.agy import install_agy
from .clients.opencode import install_opencode
from .clients.kilo import install_kilo
from .clients.omp import install_omp

# Instruction-file builders (re-exported; test_agent_surface imports
# _agents_instructions and also parses _INSTRUCTIONS_BODY from source).
from ._common import (
    _INSTRUCTIONS_BODY,
    _agents_instructions,
    _claude_instructions,
)


__all__ = [
    # Public API
    "install",
    "uninstall",
    "detect_clients",
    "check_installed",
    "Detection",
    "CLIENTS",
    "InstallReport",
    "InstallResult",
    # Path/command resolution
    "resolve_cg_command",
    "resolve_cg_str",
    "claude_desktop_config_path",
    "verify_registration",
    # MCP config generators
    "mcp_config_json",
    "zcode_mcp_config_json",
    "opencode_mcp_config_json",
    "kilo_mcp_config_json",
    "mcp_config_json_desktop",
    # Hooks
    "claude_hooks_block",
    "cursor_hooks_json",
    # Per-client installers
    "install_claude",
    "install_claude_desktop",
    "install_cursor",
    "install_droid",
    "install_zcode",
    "install_agy",
    "install_opencode",
    "install_kilo",
    "install_omp",
    # Private helpers re-exported for internal callers / tests
    "_SLASH_COMMANDS",
    "_already_installed",
    "_deep_merge",
    "_entry_present",
    "_merge_json_file",
    "_write_file",
    "_write_tree",
    "_INSTRUCTIONS_BODY",
    "_agents_instructions",
    "_claude_instructions",
]


# --- Per-client install reach (what `cairn install-agents` wires natively) ---
# Verified against each client's documented discovery paths. The cross-tool
# `.agents/` fallback (install_cross_tool, always written) fills gaps for the
# clients whose docs confirm `.agents/` discovery.
#
#   MCP   = MCP server config written to a path the client actually reads
#   Skill = full skill package (SKILL.md + references/ + scripts/ + evals/)
#   Cmds  = slash commands    Subs = subagents    Hooks = lifecycle hooks
#
#   claude          : MCP YES | Skill YES (.claude/skills/) | Cmds YES | Subs YES | Hooks YES   [FULL]
#   droid           : MCP YES | Skill YES (.factory/skills/) | Cmds YES | Subs YES | Hooks YES  [FULL]
#   zcode           : MCP YES | Skill YES (.zcode/skills/) | Cmds YES | Subs YES | Hooks via git [FULL-ish]
#   cursor          : MCP YES | Skill FALLBACK (.agents/skills/ + .cursor/skills/ discovered;
#                     native .mdc rules written too) | Subs YES (.cursor/subagents/) | Hooks YES  [rules-rich]
#   opencode        : MCP YES (opencode.json, `mcp` key) | Skill FALLBACK (.agents/skills/
#                     discovered) | Cmds/Subs NOT discovered (reads .opencode/commands/ +
#                     opencode.json agents)  [MCP + skill-via-fallback]
#   kilo            : MCP YES (kilo.json, opencode format) | Skill/Cmds/Subs/Hooks NOT
#                     documented for the CLI (config format is opencode-derived; skill
#                     may reach it via the .agents/ fallback)  [MCP-only]
#   omp             : MCP YES (.omp/mcp.json, native mcpServers schema) | Subs YES
#                     (.omp/agents/*.md, native task-agent format) | Skill FALLBACK
#                     (.agents/skills/ discovered via omp's `.agent[s]/skills`) |
#                     Cmds/Hooks NOT wired (no documented cairn-relevant hook surface)
#                     [MCP + Subs, skill-via-fallback]
#   agy             : MCP YES (~/.gemini/config/mcp_config.json) -- Skill/Cmds/Subs/Hooks NOT
#                     discovered (agy has no skill/command dirs)  [MCP-only]
#   claude-desktop  : MCP YES (stdio only) -- no Skill/Cmds/Subs/Hooks (app is MCP-only)  [MCP-only]
#
# Net: the golden rules + tool-behaviors table reach claude/droid/zcode
# natively, cursor/opencode/omp via the .agents/ skill fallback, and
# claude-desktop/agy NOT AT ALL (MCP tools work, but the agent gets no skill).


@dataclass
class InstallReport:
    detections: list[Detection]
    results: list[InstallResult]
    cross_tool: Optional[InstallResult]
    git_hooks_installed: list[str] = field(default_factory=list)
    transport: str = "stdio"  # the transport actually used (after auto-detect)


def sse_daemon_reachable(sse_url: str | None = None) -> bool:
    """Probe whether the shared SSE daemon is accepting connections.

    Connects to the host:port parsed out of the SSE URL (default
    ``http://127.0.0.1:{DEFAULT_PORT}/sse``). Installs default to SSE
    transport for every client except Claude Desktop (stdio-only), so this
    is used only to warn when the daemon is not yet running — not to pick
    the transport.
    """
    import socket

    from ..mcp_server import lifecycle as lc

    url = sse_url or f"http://127.0.0.1:{lc.DEFAULT_PORT}/sse"
    # Parse host:port out of the URL (minimal — no urllib to avoid edge cases).
    # Expected forms: http://HOST:PORT/sse
    try:
        host_part = url.split("://", 1)[1].split("/", 1)[0]
        host, port_s = host_part.rsplit(":", 1)
        port = int(port_s)
    except (IndexError, ValueError):
        return False
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def install_cross_tool(workspace: str, force: bool, dry_run: bool = False) -> InstallResult:
    """Always write the cross-tool .agents/ copies (shared skill + commands + agent).

    These are discovered by both Claude Code, ZCode, and agy CLI as a fallback,
    maximizing compatibility.
    """
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


# Dispatch tables. The client key ("claude-desktop") maps to the per-client
# module; each module owns install_<name> + uninstall(ws, res).
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


# --- FR-006/D-005: install-time registration verification ------------------
#
# Ceiling for one probe spawn. A warm probe costs ~0.5s (the spec assumes
# ~1s per client); the ceiling is deliberately generous because a timeout is
# reported as FAIL and a healthy install must never flake on it.

_PROBE_TIMEOUT_S = 5.0


def _registration_entry(config_path: str) -> Optional[dict]:
    """Return the cairn MCP entry inside a written JSON config, or None.

    Shape-aware exactly like ``detect._json_has_cairn``: flat
    ``mcpServers.cairn`` (claude/cursor/droid/omp/agy/claude-desktop),
    zcode's ``mcp.servers.cairn``, opencode/kilo's ``mcp.cairn``, and a
    top-level ``cairn`` key. Non-object containers are tolerated (the F6
    backup shapes) and files that do not parse are simply not registrations.
    """
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


def _registration_argv(entry: dict) -> list[str]:
    """The registration's full argv from its written config entry.

    Handles both written shapes: ``command`` + ``args`` (claude, cursor,
    zcode, agy, omp, droid fallback, claude-desktop) and opencode/kilo's
    single ``command`` array.
    """
    cmd = entry.get("command")
    if isinstance(cmd, list):
        return [str(part) for part in cmd]
    if isinstance(cmd, str):
        args = entry.get("args")
        if isinstance(args, list):
            return [cmd, *[str(a) for a in args]]
        return [cmd]
    return []


def verify_registration(
    command: list[str],
    env: dict[str, str],
    cwd: Path,
    expected: dict[str, str],
) -> tuple[str, str]:
    """Spawn one written registration and compare its resolved store with the
    install target (FR-006, D-005; TC-010/TC-011).

    ``command`` is the registration's exact argv as written (its ``command``
    plus ``args``); the trailing ``"serve"`` is replaced by the read-only
    probe args ``["config", "--json"]`` (the registration's own args would
    start a long-lived MCP server). ``env`` is the registration's written
    env block, merged over the invoking process's environment; the intended
    ``CAIRN_HOME`` and workspace are then pinned so the probe answers
    exactly one question: does this exact binary, pointed at the intended
    store, resolve exactly that store? The intended home is derived from
    ``expected["db"]`` per the StorePaths layout (``<CAIRN_HOME>/<store
    key>/.kg``). Pinning happens after the written env merges in, so a
    spawned wrapper that drops ``CAIRN_HOME`` (TC-011's PATH-shadowed
    binary) is still caught, while a long-lived installer process's
    binding-vs-env divergence is bridged deterministically. ``cwd`` is the
    target workspace so cwd-based resolution matches what the client does.

    Returns ``(status, detail)``: ``("pass", <one-line confirmation>)`` or
    ``("fail", <reason naming both stores>)``. Spawn failure, timeout
    (``_PROBE_TIMEOUT_S``), non-zero exit, and unparseable output all FAIL
    naming the intended store. Reused by the doctor's consistency audit
    (FR-007) instead of duplicating the mechanism.
    """
    argv = list(command)
    if argv and argv[-1] == "serve":
        argv = argv[:-1]
    probe_argv = [*argv, "config", "--json"]

    spawn_env = {**os.environ, **env}
    intended_db = str(expected.get("db", ""))
    intended_ws = str(expected.get("workspace", ""))
    if intended_db:
        # StorePaths invariant: db = <CAIRN_HOME>/<store key>/.kg, so the
        # intended CAIRN_HOME is the db's grandparent.
        spawn_env["CAIRN_HOME"] = str(Path(intended_db).parent.parent)
    if intended_ws:
        spawn_env["CAIRN_WORKSPACE"] = intended_ws
    intended = f"db={intended_db or '?'} workspace={intended_ws or '?'}"

    try:
        proc = subprocess.run(
            probe_argv, env=spawn_env, cwd=str(cwd),
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return "fail", (f"probe timed out after {_PROBE_TIMEOUT_S:g}s; "
                        f"intended store: {intended}")
    except OSError as exc:
        return "fail", (f"probe could not spawn {probe_argv[0]!r}: {exc}; "
                        f"intended store: {intended}")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return "fail", (f"probe exited {proc.returncode}: {err[:200]}; "
                        f"intended store: {intended}")
    try:
        resolved = json.loads(proc.stdout)
    except ValueError:
        resolved = None
    if not isinstance(resolved, dict):
        return "fail", f"probe printed no JSON object; intended store: {intended}"
    resolved_db = str(resolved.get("db", ""))
    resolved_ws = str(resolved.get("workspace", ""))
    if resolved_db == intended_db and resolved_ws == intended_ws:
        return "pass", f"resolved db={resolved_db}"
    return "fail", (f"registration resolves db={resolved_db} "
                    f"workspace={resolved_ws}; install target "
                    f"db={intended_db} workspace={intended_ws}")


def _verify_results(results: list[InstallResult], workspace: str) -> None:
    """FR-006 verify loop: record per-client spawn-probe verdicts in place.

    For every result carrying a file-written stdio registration (located by
    scanning the files this installer actually wrote, per client shape), the
    registration's exact binary+env is spawned with probe args from inside
    the workspace and compared against this process's ``resolve_store()``.
    dry_run never reaches this (guarded by the caller). SSE registrations
    (URL-based, nothing to spawn) and CLI-registered clients (no file-written
    registration this run) stay "skipped" and get a note (D-006).
    """
    store = paths.resolve_store(workspace)
    expected = {"db": str(store.db), "workspace": str(store.workspace)}
    for res in results:
        entry = None
        for written in res.written:
            entry = _registration_entry(written)
            if entry is not None:
                break
        if entry is None:
            res.notes.append(
                "verification skipped: no file-written MCP registration this "
                "run (CLI-registered or unchanged); not spawn-verified")
            continue
        if "command" not in entry:
            res.notes.append(
                "verification skipped: SSE registration is URL-based "
                "(nothing to spawn)")
            continue
        res.verification_status, res.verification_detail = verify_registration(
            _registration_argv(entry), entry.get("env") or {},
            Path(workspace), expected)


def install(
    workspace: str,
    clients: Optional[list[str]] = None,
    force: bool = False,
    dry_run: bool = False,
    include_git_hooks: bool = False,
    transport: str | None = None,
    sse_url: str | None = None,
    scope: str = "workspace",
) -> InstallReport:
    """Install cairn agent integration.

    ``scope`` controls where dual-scope clients (claude, cursor, zcode) write:
    ``"workspace"`` (default) writes to ``./.claude/``, ``./.cursor/`` etc.;
    ``"global"`` writes to ``~/.claude/``, ``~/.cursor/`` etc. Single-scope
    clients (claude-desktop, agy) ignore the parameter.

    ``transport`` defaults to ``"sse"`` — one shared daemon started with
    ``cairn serve start`` (the SSE URL derives from ``lifecycle.DEFAULT_PORT``).
    Claude Desktop is stdio-only and always gets a stdio config regardless;
    pass ``transport="stdio"`` to opt out everywhere.

    After writing (unless ``dry_run``), every file-written stdio registration
    is spawn-verified against the install-time store (FR-006/D-005); the
    per-client verdict lands on ``InstallResult.verification_status`` /
    ``verification_detail``.
    """
    if transport is None:
        transport = "sse"

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
        results.append(_INSTALLERS[client](
            workspace, force, dry_run, transport=transport, sse_url=sse_url,
            scope=scope,
        ))

    # FR-006/D-005: verify each file-written stdio registration by spawning
    # its exact binary+env with probe args (cwd = target workspace) and
    # comparing the resolved store against this process's resolve_store().
    # Lives here — not the CLI layer — so every install() caller gets
    # verification; agents.py only renders the verdicts. dry_run never
    # spawns; SSE and CLI-registered clients stay "skipped" (D-006).
    if not dry_run:
        _verify_results(results, workspace)

    # Cross-tool .agents/ copies: always write when any client is targeted.
    cross = install_cross_tool(workspace, force, dry_run=dry_run) if target else None

    # Git hooks (optional; under --client all or explicit flag).
    git_installed: list[str] = []
    if include_git_hooks and target and not dry_run:
        try:
            from ..graph import scanner as scanner_mod
            from ..hooks.git_hooks import install_hooks

            repos = [r.name for r in scanner_mod.discover_repos(workspace)]
            git_installed = install_hooks(repos, workspace)
        except ValueError as e:
            # Security guard (shell-injection repo-name check) rejected a repo.
            # Surface it: otherwise a single bad name silently installs zero hooks.
            print(f"warning: git hooks skipped — {e}")
        except Exception as e:
            # Filesystem/other errors are genuinely best-effort, but don't hide them.
            print(f"warning: git hooks skipped — {e!r}")

    return InstallReport(detections, results, cross, git_installed, transport=transport)


def uninstall(workspace: str, clients: Optional[list[str]] = None,
              scope: str = "workspace") -> InstallReport:
    """Remove cairn entries from client configs. Idempotent.

    ``scope`` must mirror the install scope so global installs are actually
    removed: ``"workspace"`` (default, the historical behavior) strips only
    the workspace paths; ``"global"`` strips the home-dir trees a
    ``--scope global`` install wrote (``~/.claude/``, ``~/.cursor/``,
    ``~/.zcode/``, ``~/.config/opencode/``) and undoes the user-scope MCP
    registrations (``claude mcp remove --scope user``, ``droid mcp remove``);
    ``"all"`` does both. The cross-tool ``.agents/`` copies are always
    workspace-scoped and removed from the workspace regardless.
    """
    from ._common import _claude_agent_md, _read_template
    from .merge import _rm_if_ours, _rm_tree_if_cairn

    detections = detect_clients(workspace)
    if clients:
        target = set(CLIENTS) if "all" in clients else set(clients)
    else:
        target = {d.client for d in detections if d.detected}

    ws = Path(workspace)
    results: list[InstallResult] = []
    for client in [c for c in CLIENTS if c in target]:
        res = InstallResult(client)
        _UNINSTALLERS[client](ws, res, scope=scope)
        results.append(res)

    # Cross-tool .agents/ copies (workspace-only). The skill package goes as
    # a whole tree -- SKILL.md plus references/scripts/evals -- and the
    # commands/agents only when byte-identical to what install writes, so a
    # user's own file at the same path survives.
    cross = InstallResult("cross-tool")
    _rm_tree_if_cairn(ws / ".agents" / "skills" / "cairn", cross)
    for name in _SLASH_COMMANDS:
        _rm_if_ours(ws / ".agents" / "commands" / f"{name}.md",
                    _read_template(f"commands/{name}.md"), cross)
    _rm_if_ours(ws / ".agents" / "agents" / "cairn-explorer.md",
                _claude_agent_md(), cross)
    _rm_if_ours(ws / ".agents" / "agents" / "knowledge-steward.md",
                _claude_agent_md("cursor/knowledge-steward.json"), cross)

    return InstallReport(detections, results, cross if target else None)
