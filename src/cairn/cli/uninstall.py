"""Uninstall CLI: ``cairn uninstall`` — full teardown, native (no shell script).

Removes, in order:
  1. agent wiring     — MCP configs / skills / commands (cairn uninstall-agents)
  2. git hooks        — post-commit hooks (cairn hooks uninstall)
  3. graph + store    — the current workspace's store dir, or all of
                        ``~/.cairn`` with --full
  4. cairn binary        — via uv / pipx / pip, plus stale in-tree build artifacts

Mirrors scripts/uninstall.sh but runs natively so it works from a wheel/pipx/uv
install (the shell script is not packaged). Store resolution reuses paths.py,
which is the single correct source of truth — the shell script's bugs came from
reimplementing this in bash.

CLAUDE.md / AGENTS.md are never removed (created create-if-absent only).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import click

from .main import main
from ._helpers import _human_bytes
from ..paths import store_key


def _home() -> Path:
    """CAIRN_HOME read lazily, so tests/processes that set the env var
    after import are honored. (paths.py binds it once at import time.)"""
    return Path(os.environ.get("CAIRN_HOME", str(Path.home() / ".cairn")))


# ─── detection helpers ─────────────────────────────────────────────────────

def _detect_install_method() -> str:
    """How cairn-intel was installed: 'uv', 'pipx', 'pip', 'venv', or 'unknown'.

    Same logic as upgrade.py but extended with the venv (source checkout) case.
    """
    exe = sys.executable
    try:
        r = subprocess.run(
            ["uv", "tool", "list"], capture_output=True, text=True, timeout=10,
        )
        if "cairn-intel" in r.stdout:
            return "uv"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        r = subprocess.run(
            ["pipx", "list"], capture_output=True, text=True, timeout=10,
        )
        if "cairn-intel" in r.stdout:
            return "pipx"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Source checkout's in-tree venv (.venv at the package root).
    try:
        from importlib.util import find_spec
        pkg_root = Path(find_spec("cairn").origin).resolve().parents[2]
        if (pkg_root / ".venv").exists() and pkg_root / ".venv" in Path(exe).resolve().parents:
            return "venv"
    except Exception:
        pass
    if "venv" in exe or "virtualenv" in exe or ".local" in exe:
        return "pip"
    return "unknown"


def _stale_artifacts() -> list[Path]:
    """Leftover build/, dist/, *.egg-info in the source tree (pip install -e debris)."""
    try:
        from importlib.util import find_spec
        pkg_root = Path(find_spec("cairn").origin).resolve().parents[2]
    except Exception:
        return []
    found: list[Path] = []
    for name in ("build", "dist"):
        p = pkg_root / name
        if p.exists():
            found.append(p)
    found.extend(sorted(pkg_root.glob("*.egg-info")))
    return found


# ─── removal steps ─────────────────────────────────────────────────────────

def _remove_agents(ws: str, clients: list[str] | None, dry_run: bool) -> None:
    click.echo("➜ Agent integrations")
    from ..agent_install import uninstall

    report = uninstall(ws, clients=clients or None)
    targeted = {r.client for r in report.results}
    if not targeted:
        click.echo("  (no clients detected — nothing to remove)")
        return
    for res in report.results:
        click.echo(f"  {res.client}:")
        if res.written:
            for p in res.written:
                click.echo(f"    removed: {p}")
        else:
            click.echo("    (nothing to remove)")
    if report.cross_tool and report.cross_tool.written:
        click.echo("  cross-tool (.agents/):")
        for p in report.cross_tool.written:
            click.echo(f"    removed: {p}")
    click.echo("  ✓ done")


def _remove_hooks(ws: str, dry_run: bool) -> None:
    click.echo("➜ Git hooks")
    try:
        from ..graph import scanner as scanner_mod
        from ..hooks.git_hooks import uninstall_hooks
        repos = [r.name for r in scanner_mod.discover_repos(ws)]
    except Exception as e:
        click.echo(f"  (skipped: {e})")
        return
    if dry_run:
        click.echo(f"  would scan {len(repos)} repo(s) for post-commit hooks")
        return
    removed = uninstall_hooks(repos, ws)
    if removed:
        click.echo(f"  ✓ removed from {len(removed)}: {', '.join(removed)}")
    else:
        click.echo("  (none found)")


def _resolve_store_target(ws: str, full: bool) -> tuple[Path, bool] | None:
    """What gets deleted in step 3.

    Returns ``(directory, whole_home)`` or None if nothing is on disk.

    - --full                                    -> (home, True)
    - workspace has a pinned store              -> (that store, False)
    - workspace not pinned, but home has stores -> (home, True)
      (mirrors scripts/uninstall.sh: don't say "nothing to remove" when
      ~/.cairn clearly holds stores — the user is running from the tool
      repo or an unregistered dir.)
    """
    home = _home()
    if full or not (home / store_key(Path(ws))).exists():
        if home.exists() and _home_has_stores(home):
            return (home, True)
        return None
    return (home / store_key(Path(ws)), False)


def _home_has_stores(home: Path) -> bool:
    """True if home contains anything beyond workspaces.json / .DS_Store."""
    if not home.exists():
        return False
    for p in home.iterdir():
        if p.name in ("workspaces.json", ".DS_Store"):
            continue
        return True
    return False


def _remove_store(ws: str, full: bool, dry_run: bool) -> None:
    click.echo("➜ Graph and knowledge data")
    resolved = _resolve_store_target(ws, full)

    if resolved is None:
        click.echo("  (no cairn store found — nothing to remove)")
        return

    target, whole_home = resolved

    if whole_home:
        n = sum(1 for p in target.iterdir() if p.is_dir())
        label = f"{target} (entire cairn home, {n} workspace(s))"
    else:
        label = str(target)

    try:
        size_str = _human_bytes(_dir_size(target))
    except Exception:
        size_str = "unknown"
    click.echo(f"  removing: {label}")
    click.echo(f"  size:     {size_str}")

    if dry_run:
        click.echo(f"  would: rm -rf {target}")
        return

    shutil.rmtree(target, ignore_errors=True)

    # Prune the registry: whole-home removal takes workspaces.json with it;
    # single-store removal drops just that workspace's entry.
    if not whole_home:
        reg_path = _home() / "workspaces.json"
        if reg_path.exists():
            try:
                import json
                reg = json.loads(reg_path.read_text(encoding="utf-8"))
                reg.pop(str(Path(ws).resolve()), None)
                reg_path.write_text(json.dumps(reg, indent=2, sort_keys=True), encoding="utf-8")
            except Exception:
                pass
    click.echo("  ✓ done")


def _remove_binary(installed_via: str, dry_run: bool) -> None:
    click.echo("➜ cairn binary")
    if installed_via == "unknown":
        stale = _stale_artifacts()
        if not stale:
            click.echo("  (cairn not found via uv/pipx/pip/venv — nothing to remove)")
            return
        click.echo("  installed via: unknown (stale build artifacts only)")
    else:
        click.echo(f"  installed via: {installed_via}")

    stale = _stale_artifacts()
    for p in stale:
        click.echo(f"  stale: {p}")

    if dry_run:
        cmds = {
            "uv": ["uv", "tool", "uninstall", "cairn-intel"],
            "pipx": ["pipx", "uninstall", "cairn-intel"],
            "pip": [sys.executable, "-m", "pip", "uninstall", "-y", "cairn-intel"],
            "venv": ["rm -rf <pkg>/.venv"],
        }
        c = cmds.get(installed_via)
        if c:
            click.echo(f"  would: {' '.join(c)}")
        for p in stale:
            click.echo(f"  would: rm -rf {p}")
        return

    if installed_via == "uv":
        subprocess.run(["uv", "tool", "uninstall", "cairn-intel"], check=False)
    elif installed_via == "pipx":
        subprocess.run(["pipx", "uninstall", "cairn-intel"], check=False)
    elif installed_via == "pip":
        subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "cairn-intel"], check=False)
    elif installed_via == "venv":
        try:
            from importlib.util import find_spec
            pkg_root = Path(find_spec("cairn").origin).resolve().parents[2]
            shutil.rmtree(pkg_root / ".venv", ignore_errors=True)
        except Exception:
            pass

    for p in stale:
        shutil.rmtree(p, ignore_errors=True)
    click.echo("  ✓ done")


# ─── small utils ───────────────────────────────────────────────────────────

def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            fp = Path(root, f)
            try:
                total += fp.stat().st_size
            except OSError:
                pass
    return total


# ─── the command ───────────────────────────────────────────────────────────

@main.command()
@click.option("--full", "full", is_flag=True,
              help="Remove everything for ALL workspaces (the entire ~/.cairn).")
@click.option("--agents-only", is_flag=True, help="Remove agent wiring only.")
@click.option("--hooks-only", is_flag=True, help="Remove git hooks only.")
@click.option("--graph-only", is_flag=True, help="Remove graph + knowledge data only.")
@click.option("--package-only", is_flag=True, help="Remove the cairn binary only.")
@click.option("--client", "clients", multiple=True,
              type=click.Choice(["claude", "claude-desktop", "cursor", "droid", "zcode", "agy", "all"]),
              help="Limit agent removal to these clients (repeatable).")
@click.option("--workspace", "ws_arg", default=None, help="Workspace root (default: resolved).")
@click.option("--dry-run", is_flag=True, help="Show what would be removed; change nothing.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmations (implied by --full).")
def uninstall(full, agents_only, hooks_only, graph_only, package_only, clients, ws_arg, dry_run, yes):
    """Uninstall cairn: agent wiring, hooks, graph store, and the cairn binary.

    By default removes everything for the CURRENT workspace. Use --full to wipe
    the entire ~/.cairn (all workspaces' stores). CLAUDE.md / AGENTS.md and
    your source repos are never touched.

    Equivalent to scripts/uninstall.sh, but native — works from any install
    (uv / pipx / pip / venv), not just a source checkout.

    \b
    Examples:
      cairn uninstall                    # interactive, current workspace
      cairn uninstall --full             # everything, all workspaces, no prompts
      cairn uninstall --graph-only -y    # just the store
      cairn uninstall --dry-run          # preview only
    """
    # Resolve workspace the same way install-agents / uninstall-agents do:
    # explicit flag > env > cwd. NOT the ancestor walk (which can wrongly
    # resolve to a parent like ~/Projects).
    if ws_arg:
        ws = ws_arg
    elif os.environ.get("CAIRN_WORKSPACE"):
        ws = os.environ["CAIRN_WORKSPACE"]
    else:
        ws = str(Path.cwd())

    # Decide which steps run. A bare `cairn uninstall` runs all four; any --*-only
    # flag restricts to just that step. --full implies all four and is mutually
    # exclusive with --*-only (combining them previously silently ran all four
    # because the `or full` term dominated every do_* line, defeating the
    # --*-only intent on a destructive command).
    if full and (agents_only or hooks_only or graph_only or package_only):
        raise click.UsageError("--full cannot be combined with --agents-only/--hooks-only/--graph-only/--package-only.")
    any_only = agents_only or hooks_only or graph_only or package_only
    do_agents = full or agents_only or not any_only
    do_hooks = full or hooks_only or not any_only
    do_graph = full or graph_only or not any_only
    do_package = full or package_only or not any_only

    # --full targets the entire CAIRN_HOME regardless of which workspace
    # we're in. Otherwise the resolved workspace's own store (or, if it has
    # none, the whole home — see _resolve_store_target).

    click.echo("")
    click.secho("Cairn Uninstaller", bold=True)
    if dry_run:
        click.secho("(dry-run: nothing will be deleted)", dim=True)
    click.echo("")

    def confirm(step: str) -> bool:
        if yes or full:
            return True
        return click.confirm(f"  Remove {step}?", default=False)

    installed_via = _detect_install_method() if do_package else "unknown"

    if do_agents and (dry_run or confirm("agent integrations")):
        _remove_agents(ws, list(clients) or None, dry_run)
        click.echo("")

    if do_hooks and (dry_run or confirm("git hooks")):
        _remove_hooks(ws, dry_run)
        click.echo("")

    if do_graph and (dry_run or confirm("graph and knowledge data")):
        _remove_store(ws, full, dry_run)
        click.echo("")

    if do_package and (dry_run or confirm("cairn binary")):
        _remove_binary(installed_via, dry_run)
        click.echo("")

    click.echo("")
    if dry_run:
        click.secho("Dry-run complete — nothing was deleted", bold=True)
    else:
        click.secho("Uninstall complete", bold=True)
    click.echo("")
    click.echo("  Remaining (never removed):")
    click.echo("    CLAUDE.md, AGENTS.md  — instruction files (created create-if-absent only)")
    click.echo("    Source code            — your repos are untouched")
    click.echo("")
    click.echo("  To reinstall: pip install cairn-intel  (or ./scripts/install.sh)")
