"""Agents CLI: install-agents / uninstall-agents."""
from __future__ import annotations

import click
import os
import sys

from .main import main
from ..agent_install import CLIENTS

# "all" is a valid --client value meaning every registered client.
_CLIENT_CHOICES = [*CLIENTS, "all"]

@main.command(name="install-agents")
@click.option("--client", "clients", multiple=True,
              type=click.Choice(_CLIENT_CHOICES),
              help="Override detection: install for these clients (repeatable). Skips the interactive prompt.")
@click.option("--workspace", "ws_arg", default=None, help="Workspace root (default: cwd).")
@click.option("--scope", "scope_arg", type=click.Choice(["workspace", "global"]), default=None,
              help="Where to write configs: 'workspace' (./.claude/) or 'global' (~/.claude/). If omitted, prompts interactively.")
@click.option("--force", is_flag=True, help="Overwrite existing cairn files.")
@click.option("--dry-run", is_flag=True, help="Show what would be written; change nothing.")
@click.option("--git-hooks", is_flag=True, help="Also install git hooks (post-commit + post-checkout) in repos.")
@click.option("--yes", "-y", is_flag=True,
              help="Skip the interactive prompt; install for detected clients that don't already have cairn.")
def install_agents(clients, ws_arg, scope_arg, force, dry_run, git_hooks, yes):
    """Wire cairn into AI coding clients (Claude Code, Cursor, Droid, ZCode, etc.); prompts unless --client/--yes."""
    from ..agent_install import install, detect_clients, check_installed

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

            # An explicitly empty selection installs nothing; install() would
            # otherwise fall back to the detected defaults.
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

    include_git = git_hooks

    click.echo(f"Scope: {scope}")
    report = install(ws, clients=target_clients, force=force, dry_run=dry_run,
                     include_git_hooks=include_git,
                     scope=scope)

    # Daemon gate: probe healthz so registrations are traced to a live daemon.
    from ..mcp_server.lifecycle import healthz_responds, mcp_url

    url = mcp_url()
    click.echo(f"MCP server: {url} (URL + key registrations)")
    if not healthz_responds():
        click.echo(f"  note: daemon at {url} unreachable — run `cairn connect` to start it.")
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
        # Verification verdict (set by install()'s verify loop; dry-run,
        # URL-based, and CLI-registered clients stay "skipped" and print no line).
        if res.verification_status == "pass":
            click.echo(f"  verify: PASS ({res.verification_detail})")
        elif res.verification_status == "fail":
            click.echo(f"  verify: FAIL — {res.verification_detail}")
        click.echo("")

    if report.cross_tool:
        click.echo("=== cross-tool (.agents/) ===")
        click.echo(f"  wrote: {len(report.cross_tool.written)}")

    if report.git_hooks_installed:
        click.echo("")
        click.echo(f"Git hooks installed in: {', '.join(report.git_hooks_installed)}")

    click.echo("")
    click.echo("Done. Registrations point at the shared daemon; run `cairn connect` if it is not running.")


@main.command(name="uninstall-agents")
@click.option("--client", "clients", multiple=True,
              type=click.Choice(_CLIENT_CHOICES),
              help="Which clients to remove from (repeatable). Default: detected.")
@click.option("--scope", "scope", type=click.Choice(["workspace", "global", "all"]), default="workspace",
              show_default=True,
              help="Which install scope to remove: 'workspace' (./.claude/ etc.), 'global' "
                   "(~/.claude/ etc. + user-scope MCP registrations), or 'all'. "
                   "Match this to the --scope you installed with.")
@click.option("--workspace", "ws_arg", default=None, help="Workspace root (default: resolved).")
def uninstall_agents(clients, scope, ws_arg):
    """Remove cairn entries from AI client configs, idempotently; --scope global|all also removes user-level writes."""
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
