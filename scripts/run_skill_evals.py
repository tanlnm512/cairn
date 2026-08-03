#!/usr/bin/env python3
"""Structural validation runner for the skill eval specs.

The evals in ``src/agent_integration/skill/evals/*.md`` are scenario
descriptions (prose), not executable assertions. This runner turns them into a
CI-checkable artifact by validating the *structured frontmatter* each spec now
declares at its top:

  1. every spec has the required frontmatter keys;
  2. every ``expected_calls`` / ``wrong_calls`` entry has its required fields;
  3. every referenced ``tool`` is a real registered MCP tool
     (``@mcp.tool()`` in ``src/mcp_server/tools_*.py``), a real ``cg`` CLI
     command (scraped from ``src/cli/*.py``), or a shipped script under
     ``scripts/`` / ``src/agent_integration/skill/scripts/``.

It is a STRUCTURAL validator only -- it does not (cannot, cheaply) run an agent
and grade its behavior. Its value is catching drift: a spec whose frontmatter
references a tool that was renamed/removed, a spec missing required fields, or
a command in the prose that no longer exists in the CLI.

Run::

    python3 scripts/run_skill_evals.py
    make evals

Exits non-zero if any spec fails validation.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

# PyYAML is a core dependency (see pyproject.toml ``pyyaml>=6.0``), but guard
# anyway so a minimal environment gets a clear, actionable message rather than
# an ImportError deep in the stack.
try:
    import yaml
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(
        "ERROR: PyYAML is required to parse eval frontmatter, but it is not "
        "installed. It is listed as a core dependency in pyproject.toml "
        "(``pyyaml>=6.0``); install it with ``pip install pyyaml`` or "
        "``pip install -e .`` then re-run.\n"
    )
    raise SystemExit(2) from exc


# Repo root = parent of this scripts/ directory.
ROOT = Path(__file__).resolve().parent.parent
EVALS_DIR = ROOT / "src" / "codegraph" / "agent_integration" / "skill" / "evals"
MCP_TOOLS_GLOB = ROOT / "src" / "codegraph" / "mcp_server" / "tools_*.py"
CLI_DIR = ROOT / "src" / "codegraph" / "cli"
SCRIPTS_DIRS = [ROOT / "scripts", ROOT / "src" / "codegraph" / "agent_integration" / "skill" / "scripts"]

# Frontmatter schema. ``expected_calls`` and ``wrong_calls`` are lists of dicts.
REQUIRED_KEYS = ["id", "rule", "title", "scenario", "expected_calls", "wrong_calls"]
EXPECTED_CALL_FIELDS = ["tool", "reason"]
WRONG_CALL_FIELDS = ["tool", "why"]


# ---------------------------------------------------------------------------
# Discovery: scrape the universe of real tool / command names from source.
# ---------------------------------------------------------------------------

_MCP_DECOR_RE = re.compile(r"^@mcp\.tool\(\)\s*$")
_DEF_RE = re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def discover_mcp_tools() -> set[str]:
    """Names of every ``@mcp.tool()``-registered function across tools_*.py.

    The decorator is always immediately followed (after zero or more plain
    decorators like ``@instrument``) by the ``def name(...)`` line; we scan
    forward a few lines to find it.
    """
    names: set[str] = set()
    for path in sorted(MCP_TOOLS_GLOB.parent.glob("tools_*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if not _MCP_DECOR_RE.match(line):
                continue
            for j in range(i + 1, min(i + 4, len(lines))):
                m = _DEF_RE.match(lines[j])
                if m:
                    names.add(m.group(1))
                    break
    return names


# Decorator forms we recognize when scraping the click CLI:
#   @main.command()                 -> top-level command (name = func name)
#   @main.command(name="foo")       -> top-level command, explicit name
#   @main.group()                   -> group definition (name = func name)
#   @<grp>.command()                -> subcommand (name = func name, prefix stripped)
#   @<grp>.command("foo")           -> subcommand, positional name
#   @<grp>.command(name="foo")      -> subcommand, keyword name
_GROUP_DEF_RE = re.compile(r"^@main\.group\(\s*\)\s*$")
_MAIN_CMD_RE = re.compile(r'^@main\.command\(\s*(?:name="([^"]+)")?\s*\)\s*$')
_SUB_CMD_RE = re.compile(
    r'^@([A-Za-z_][A-Za-z0-9_]*)\.command\(\s*(?:"([^"]+)"|name="([^"]+)")?\s*\)\s*$'
)


def _peek_def(lines: list[str], i: int) -> str | None:
    # Scan forward past any intervening decorators (``@click.option(...)`` etc.)
    # and their continuation lines to the first ``def``. Decorators can span
    # multiple physical lines (e.g. ``@click.option("-v", "--verbose",\n  help=...)``),
    # so we keep going while we see decorator lines (start with ``@``) or
    # indented continuation lines (leading whitespace, not a ``def``).
    for j in range(i + 1, min(i + 60, len(lines))):
        line = lines[j]
        if line.startswith("@"):  # another decorator line
            continue
        m = _DEF_RE.match(line)
        if m:
            return m.group(1)
        if line.startswith(" ") or line.startswith("\t"):
            # indented continuation of a multi-line decorator / option
            continue
        # A non-indented, non-def, non-blank line means we've passed the def
        # position without finding it -- give up.
        if line.strip():
            break
    return None


def discover_cli_commands() -> dict[str, set[str]]:
    """Map of ``cg``-invokable command names: ``{"<group>": {...}, "main": {...}}``.

    Group names map to their subcommand names; top-level commands live under
    the ``"main"`` key. So ``cg dataflow build`` is found as
    ``commands["dataflow"] == {"build", "lookup"}`` and ``cg update`` as
    ``"update" in commands["main"]``.
    """
    commands: dict[str, set[str]] = {"main": set()}
    for path in sorted(CLI_DIR.glob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            s = line.strip()
            if _GROUP_DEF_RE.match(line):
                gname = _peek_def(lines, i)
                if gname:
                    commands.setdefault(gname, set())
                continue
            msub = _SUB_CMD_RE.match(line)
            if msub and msub.group(1) != "main":
                grp = msub.group(1)
                explicit = msub.group(2) or msub.group(3)
                fname = explicit or _peek_def(lines, i)
                if not fname:
                    continue
                # default name: strip a leading "<group>_" prefix from the func
                if not explicit and fname.startswith(grp + "_"):
                    fname = fname[len(grp) + 1 :]
                commands.setdefault(grp, set()).add(fname)
                continue
            mmain = _MAIN_CMD_RE.match(line)
            if mmain:
                explicit = mmain.group(1)
                fname = explicit or _peek_def(lines, i)
                if fname:
                    commands["main"].add(fname)
    return commands


def discover_scripts() -> set[str]:
    """Names of runnable scripts (``*.py`` / ``*.sh``) shipped under scripts/."""
    names: set[str] = set()
    for d in SCRIPTS_DIRS:
        if not d.is_dir():
            continue
        for path in d.iterdir():
            if path.is_file() and path.suffix in (".py", ".sh"):
                names.add(path.name)
    return names


# ---------------------------------------------------------------------------
# Tool-name resolution: is a frontmatter ``tool`` value real?
# ---------------------------------------------------------------------------

_MCP_REMOTE_PREFIX_RE = re.compile(r"^mcp__(?:[A-Za-z0-9_]+)__(.+)$")


def resolve_tool(
    tool: str,
    mcp_tools: set[str],
    cli_commands: dict[str, set[str]],
    scripts: set[str],
) -> tuple[bool, str]:
    """Return ``(ok, reason)`` describing whether ``tool`` is a known surface.

    Recognized forms:
      * bare MCP tool name        -> ``impact_analysis``
      * fully-qualified MCP name  -> ``mcp__codegraph__rebuild_graph`` (the
        tail after the last ``__`` must match a registered tool; we also allow
        it to be *unregistered*, which is legitimate for a ``wrong_calls``
        entry that documents a nonexistent tool an agent might wrongly invoke)
      * ``cg <cmd>`` / ``cg <group> <cmd>`` CLI commands
      * shipped script filenames  -> ``scripts/impact_guard.py``

    The ``reason`` string classifies which surface matched (or why not) so the
    report is legible.
    """
    if not isinstance(tool, str) or not tool.strip():
        return False, "empty/missing tool name"

    t = tool.strip()

    # Fully-qualified MCP name, e.g. mcp__codegraph__rebuild_graph. The tail is
    # the real tool name; if it is NOT registered that is expected for a
    # wrong_calls entry (it documents a tool that does not exist), so we report
    # it as a known *documented-as-nonexistent* MCP surface.
    mq = _MCP_REMOTE_PREFIX_RE.match(t)
    if mq:
        tail = mq.group(1)
        if tail in mcp_tools:
            return True, f"MCP tool (fully-qualified, registered: {tail})"
        return True, f"MCP tool name (documented-as-nonexistent: {tail})"

    # Bare MCP tool name.
    if t in mcp_tools:
        return True, f"MCP tool ({t})"

    # CLI: accept "cg <group> <cmd>", "cg <cmd>", "<group> <cmd>" (no cg prefix).
    cleaned = t[3:].strip() if t.startswith("cg ") else t
    parts = cleaned.split()
    if len(parts) == 2:
        grp, cmd = parts
        if grp in cli_commands and cmd in cli_commands[grp]:
            return True, f"CLI command (cg {grp} {cmd})"
        # dataflow-lookup style (hyphenated) sometimes written with space
        hyphen = f"{grp}-{cmd}"
        if grp in cli_commands and hyphen in cli_commands[grp]:
            return True, f"CLI command (cg {grp} {hyphen})"
    if len(parts) == 1 and parts[0] in cli_commands["main"]:
        return True, f"CLI command (cg {parts[0]})"

    # Shipped script (allow with or without a leading scripts/ dir).
    script_name = Path(t).name
    if script_name in scripts:
        return True, f"script ({script_name})"

    return False, f"unknown tool/command/script: {t!r}"


# ---------------------------------------------------------------------------
# Frontmatter parsing.
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict | None, str | None]:
    """Split a leading ``--- ... ---`` YAML block from a markdown file.

    Returns ``(frontmatter_dict_or_None, error_or_None)``.
    """
    if not text.startswith("---"):
        return None, "file does not start with a '---' frontmatter block"
    # Find the closing delimiter on its own line.
    m = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.DOTALL)
    if not m:
        return None, "frontmatter block has no closing '---' delimiter"
    body = m.group(1)
    try:
        data = yaml.safe_load(body)
    except yaml.YAMLError as e:  # malformed YAML
        return None, f"YAML parse error: {e}"
    if not isinstance(data, dict):
        return None, "frontmatter did not parse to a YAML mapping"
    return data, None


# ---------------------------------------------------------------------------
# Per-spec validation.
# ---------------------------------------------------------------------------

def validate_spec(path: Path, mcp_tools, cli_commands, scripts) -> list[str]:
    """Return a list of problem strings for one spec (empty == valid)."""
    problems: list[str] = []
    text = path.read_text(encoding="utf-8")

    fm, err = parse_frontmatter(text)
    if err:
        problems.append(f"frontmatter: {err}")
        return problems  # nothing else to check without frontmatter

    spec_id = fm.get("id", path.stem)
    missing = [k for k in REQUIRED_KEYS if k not in fm]
    if missing:
        problems.append(f"missing required key(s): {', '.join(missing)}")

    # Type/value sanity on scalar fields.
    if "rule" in fm and not isinstance(fm["rule"], int):
        problems.append(f"'rule' must be an integer, got {type(fm['rule']).__name__}")
    for scalar in ("id", "title", "scenario"):
        if scalar in fm and not isinstance(fm[scalar], str):
            problems.append(f"'{scalar}' must be a string")

    def _check_call_list(field: str, required_fields: list[str]):
        calls = fm.get(field)
        if field not in fm:
            return  # already reported as missing key
        if calls is None:
            problems.append(f"'{field}' is null; expected a list")
            return
        if not isinstance(calls, list):
            problems.append(f"'{field}' must be a list, got {type(calls).__name__}")
            return
        for idx, entry in enumerate(calls):
            tag = f"{field}[{idx}]"
            if not isinstance(entry, dict):
                problems.append(f"{tag}: entry must be a mapping, got {type(entry).__name__}")
                continue
            for rf in required_fields:
                if rf not in entry:
                    problems.append(f"{tag}: missing required field '{rf}'")
                elif not isinstance(entry[rf], str) or not entry[rf].strip():
                    problems.append(f"{tag}: field '{rf}' must be a non-empty string")
            tool = entry.get("tool")
            if tool is not None:
                ok, why = resolve_tool(tool, mcp_tools, cli_commands, scripts)
                if not ok:
                    problems.append(f"{tag}: {why}")

    _check_call_list("expected_calls", EXPECTED_CALL_FIELDS)
    _check_call_list("wrong_calls", WRONG_CALL_FIELDS)

    # Cross-check: the 'id' should match the filename stem, to keep discovery
    # and reporting in sync.
    if isinstance(fm.get("id"), str) and fm["id"] != path.stem:
        problems.append(f"'id' ({fm['id']!r}) does not match filename stem ({path.stem!r})")

    _ = spec_id  # currently only used in reporting; kept for clarity
    return problems


# ---------------------------------------------------------------------------
# Reporting.
# ---------------------------------------------------------------------------

def _print_header(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    if not EVALS_DIR.is_dir():
        sys.stderr.write(f"ERROR: evals directory not found: {EVALS_DIR}\n")
        return 2

    specs = sorted(EVALS_DIR.glob("rule*.md"))
    if not specs:
        sys.stderr.write(f"ERROR: no rule*.md specs found in {EVALS_DIR}\n")
        return 2

    print("Discovering registered surfaces...")
    mcp_tools = discover_mcp_tools()
    cli_commands = discover_cli_commands()
    scripts = discover_scripts()
    n_cli = sum(len(v) for v in cli_commands.values())
    print(
        f"  MCP tools : {len(mcp_tools)} registered "
        f"({', '.join(sorted(mcp_tools)[:8])}{'...' if len(mcp_tools) > 8 else ''})"
    )
    print(f"  CLI       : {len(cli_commands) - 1} groups, {n_cli} commands total")
    print(f"  scripts   : {len(scripts)} ({', '.join(sorted(scripts))})")

    _print_header(f"Validating {len(specs)} eval spec(s)")
    total_problems = 0
    failing: list[tuple[Path, list[str]]] = []
    for spec in specs:
        problems = validate_spec(spec, mcp_tools, cli_commands, scripts)
        status = "OK" if not problems else f"{len(problems)} problem(s)"
        print(f"  [{'PASS' if not problems else 'FAIL'}] {spec.name}  ({status})")
        for p in problems:
            print(f"        - {p}")
        total_problems += len(problems)
        if problems:
            failing.append((spec, problems))

    _print_header("Summary")
    valid = len(specs) - len(failing)
    print(f"  specs scanned : {len(specs)}")
    print(f"  valid         : {valid}")
    print(f"  failing       : {len(failing)}")
    print(f"  total problems: {total_problems}")

    if failing:
        print("\nFailing specs:")
        for spec, _ in failing:
            print(f"  - {spec.relative_to(ROOT)}")
        print(
            "\nThis is a STRUCTURAL validator: it checks that each spec's "
            "frontmatter\nis well-formed and that every referenced tool/command "
            "actually exists.\nA failure usually means a tool was renamed/removed "
            "or the frontmatter is\nincomplete -- fix the spec, not the runner."
        )
        return 1

    print("\nAll specs structurally valid. (Agent behavior itself is still manual.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
