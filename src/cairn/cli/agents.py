"""Agents CLI: install-agents / uninstall-agents."""
from __future__ import annotations

import click
import os
import sys

from .main import main

@main.command(name="install-agents")
@click.option("--client", "clients", multiple=True,
              type=click.Choice(["claude", "claude-desktop", "cursor", "droid", "zcode", "agy", "opencode", "kilo", "all"]),
              help="Override detection: install for these clients (repeatable). Skips the interactive prompt.")
@click.option("--workspace", "ws_arg", default=None, help="Workspace root (default: cwd).")
@click.option("--scope", "scope_arg", type=click.Choice(["workspace", "global"]), default=None,
              help="Where to write configs: 'workspace' (./.claude/) or 'global' (~/.claude/). If omitted, prompts interactively.")
@click.option("--force", is_flag=True, help="Overwrite existing cairn files.")
@click.option("--dry-run", is_flag=True, help="Show what would be written; change nothing.")
@click.option("--git-hooks", is_flag=True, help="Also install git post-commit hooks in repos.")
@click.option("--sse", "sse", is_flag=True,
              help="Use SSE configs (shared daemon) — this is the default; the flag is kept for explicitness. Run `cairn serve start` first.")
@click.option("--stdio", "stdio", is_flag=True,
              help="Use stdio configs (one process per client) instead of the SSE default. Claude Desktop is always stdio either way.")
@click.option("--sse-url", "sse_url", default=None,
              help="SSE URL (default http://127.0.0.1:9876/sse).")
@click.option("--yes", "-y", is_flag=True,
              help="Skip the interactive prompt; install for detected clients that don't already have cairn.")
def install_agents(clients, ws_arg, scope_arg, force, dry_run, git_hooks, sse, stdio, sse_url, yes):
    """Wire cairn into AI coding clients (Claude Code/Desktop, Cursor, Droid, ZCode, etc.).

    Detects which clients are installed and shows whether cairn is already
    wired in. By default, prompts you to choose which clients to install for
    (skipping those that already have cairn). Use --client to bypass the
    prompt, or --yes to auto-install for all detected-and-not-yet-installed.

    Scope: --scope workspace (default) writes to ./.claude/, ./.cursor/ etc.
    --scope global writes to ~/.claude/, ~/.cursor/ etc. (all projects inherit).
    """
    from ..agent_install import install, detect_clients, check_installed

    if sse and stdio:
        click.echo("Error: --sse and --stdio are mutually exclusive.")
        sys.exit(1)

    if ws_arg:
        ws = ws_arg
    elif os.environ.get("CAIRN_WORKSPACE"):
        ws = os.environ["CAIRN_WORKSPACE"]
    else:
        ws = os.getcwd()

    # Detect + check installed state.
    detections = detect_clients(ws)
    installed_map = check_installed(ws)

    # Show detection + install state table.
    click.echo("Client detection:")
    for d in detections:
        mark = "✓" if d.detected else " "
        inst = "installed" if installed_map.get(d.client) else "not installed"
        inst_mark = "✓" if installed_map.get(d.client) else " "
        click.echo(f"  [{mark}] {d.client:16} {d.reason:30}  cairn: [{inst_mark}] {inst}")
    click.echo("")

    # Determine target clients + scope.
    cl = list(clients) if clients else None
    scope = scope_arg  # None if not explicitly passed
    if cl:
        # --client was passed: bypass prompts entirely.
        target_clients = cl
    else:
        detected = [d.client for d in detections if d.detected]
        not_yet_installed = [c for c in detected if not installed_map.get(c)]

        if not detected:
            click.echo("No clients detected. Use --client <name> or --client all to force.")
            return

        if not not_yet_installed:
            click.echo("All detected clients already have cairn installed.")
            click.echo("Use --force to overwrite, or --client <name> to target a specific one.")
            return

        if yes or not sys.stdin.isatty():
            # Non-interactive (--yes or piped): auto-install detected-not-installed.
            target_clients = not_yet_installed
            click.echo(f"Installing for: {', '.join(target_clients)} (detected, not yet installed)")
        else:
            # Interactive prompt: ask for clients (checkbox multi-select).
            import questionary
            from prompt_toolkit.styles import Style as PtStyle

            from .display import PROMPT_TOOLKIT_COLORS as C

            choices = [
                {"name": c, "checked": c in not_yet_installed, "value": c}
                for c in detected
            ]
            # Custom style aligned with the cairn CLI theme (see display.py).
            cb_style = PtStyle([
                ("qmark", f"fg:{C['info']} bold"),
                ("question", f"fg:{C['info']} bold"),
                ("pointer", f"fg:{C['warning']} bold"),
                ("selected", f"fg:{C['success']} bold"),    # ● checked item
                ("unselected", f"fg:{C['dim']}"),           # ○ unchecked item
                ("highlighted", f"fg:{C['warning']} bold"), # » current row
                ("answer", f"fg:{C['success']} bold"),
                ("instruction", f"fg:{C['dim']} italic"),
            ])
            # Suppress questionary's own "Aborted." so a single consistent
            # message prints for both Ctrl+C (returns None) and Ctrl+D (raises
            # EOFError, which questionary does not catch).
            try:
                answer = questionary.checkbox(
                    "Select clients to install cairn for:",
                    choices=choices,
                    style=cb_style,
                    instruction="(↑↓ navigate · space toggle · a all · i invert · enter confirm)",
                ).ask(kbi_msg="")
            except (KeyboardInterrupt, EOFError):
                answer = None

            if answer is None:  # Ctrl+C or Ctrl+D
                click.echo("Aborted.")
                return

            # Respect the selection as-is. An explicitly empty selection
            # installs nothing, rather than silently falling back to the
            # detected defaults (which install() would do for an empty/None
            # clients list).
            if not answer:
                click.echo("Nothing selected. Use --client <name> to target a specific client.")
                return
            target_clients = answer

            # Interactive prompt: ask for scope (only if --scope wasn't passed).
            if scope is None:
                click.echo("")
                click.echo("Config scope:")
                click.echo("  workspace  — write to ./.claude/, ./.cursor/ etc. (per-project)")
                click.echo("  global     — write to ~/.claude/, ~/.cursor/ etc. (all projects inherit)")
                try:
                    scope = click.prompt("Scope", type=click.Choice(["workspace", "global"]),
                                         default="workspace", show_default=True)
                except click.exceptions.Abort:
                    click.echo("\nAborted.")
                    return

    # Fall back to workspace scope if still unset (non-interactive without --scope).
    if scope is None:
        scope = "workspace"

    include_git = git_hooks or (target_clients is not None and "all" in target_clients)
    transport = "sse" if sse else ("stdio" if stdio else None)

    click.echo(f"Scope: {scope}")
    report = install(ws, clients=target_clients, force=force, dry_run=dry_run,
                     include_git_hooks=include_git,
                     transport=transport,
                     sse_url=sse_url,
                     scope=scope)

    # Transport summary.
    tp_note = (
        "SSE daemon (shared instance)" if report.transport == "sse"
        else "stdio (one process per client)"
    )
    click.echo(f"MCP transport: {tp_note}")
    if report.transport == "sse":
        from ..agent_install import sse_daemon_reachable
        if not sse_daemon_reachable(sse_url):
            click.echo("  note: SSE daemon not reachable — run `cairn serve start` "
                       "(installs a launchd agent), or pass --stdio for one process per client.")
    click.echo("")

    targeted = {r.client for r in report.results}
    if not targeted:
        click.echo("Nothing to install. Use --client <name> or --client all.")
        return

    click.echo(f"Installing for: {', '.join(sorted(targeted))}" + (" (dry-run)" if dry_run else ""))
    click.echo("")
    for res in report.results:
        click.echo(f"=== {res.client} ===")
        if res.written:
            click.echo(f"  wrote:   {len(res.written)}")
            for p in res.written[:8]:
                click.echo(f"    {p}")
            if len(res.written) > 8:
                click.echo(f"    ... and {len(res.written) - 8} more")
        if res.skipped:
            click.echo(f"  skipped: {len(res.skipped)} (use --force to overwrite)")
        for note in res.notes:
            click.echo(f"  note: {note}")
        click.echo("")

    if report.cross_tool:
        click.echo("=== cross-tool (.agents/) ===")
        click.echo(f"  wrote: {len(report.cross_tool.written)}")

    if report.git_hooks_installed:
        click.echo("")
        click.echo(f"Git hooks installed in: {', '.join(report.git_hooks_installed)}")

    click.echo("")
    click.echo("Done. The MCP server resolves its own store paths via `cairn serve`.")


@main.command(name="uninstall-agents")
@click.option("--client", "clients", multiple=True,
              type=click.Choice(["claude", "claude-desktop", "cursor", "droid", "zcode", "agy", "opencode", "kilo", "all"]),
              help="Which clients to remove from (repeatable). Default: detected.")
@click.option("--scope", "scope", type=click.Choice(["workspace", "global", "all"]), default="workspace",
              show_default=True,
              help="Which install scope to remove: 'workspace' (./.claude/ etc.), 'global' "
                   "(~/.claude/ etc. + user-scope MCP registrations), or 'all'. "
                   "Match this to the --scope you installed with.")
@click.option("--workspace", "ws_arg", default=None, help="Workspace root (default: resolved).")
def uninstall_agents(clients, scope, ws_arg):
    """Remove cairn entries from AI client configs. Idempotent.

    Strips the cairn MCP server and hooks from config files (preserving
    other entries) and deletes cairn skill/command/subagent files. Use
    --scope global (or all) to also remove what a `install-agents --scope
    global` wrote under ~.
    """
    from ..agent_install import uninstall

    # Same as install-agents: default to cwd, not ancestor walk.
    if ws_arg:
        ws = ws_arg
    elif os.environ.get("CAIRN_WORKSPACE"):
        ws = os.environ["CAIRN_WORKSPACE"]
    else:
        ws = os.getcwd()
    cl = list(clients) or None
    report = uninstall(ws, clients=cl, scope=scope)

    targeted = {r.client for r in report.results}
    if not targeted:
        click.echo("No clients detected to uninstall from. Use --client <name> or --client all.")
        return

    for res in report.results:
        click.echo(f"=== {res.client} ===")
        for p in res.written:
            click.echo(f"  {p}")
        for p in res.skipped:
            click.echo(f"  skipped: {p}")
        for note in res.notes:
            click.echo(f"  note: {note}")
        if not (res.written or res.skipped or res.notes):
            click.echo("  (nothing to remove)")
        click.echo("")

    if report.cross_tool:
        click.echo("=== cross-tool (.agents/) ===")
        for p in report.cross_tool.written:
            click.echo(f"  {p}")
        for p in report.cross_tool.skipped:
            click.echo(f"  skipped: {p}")

    click.echo("")
    click.echo("Done. Git hooks: run `cairn hooks uninstall` separately if needed.")

