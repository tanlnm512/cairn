#!/usr/bin/env python3
"""Validate skill eval frontmatter: required keys, and every tool is a real MCP tool, `cairn` command, or script."""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(
        "ERROR: PyYAML is required to parse eval frontmatter; install it with "
        "``pip install pyyaml`` then re-run.\n"
    )
    raise SystemExit(2) from exc


# Repo root = parent of this scripts/ directory.
ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "rust" / "crates" / "cairn-cli" / "templates" / "agent_integration" / "skill"
EVALS_DIR = SKILL_DIR / "evals"
MCP_TOOLS_FILE = ROOT / "rust" / "crates" / "cairn-daemon" / "src" / "tools.rs"
CLI_DIR = ROOT / "rust" / "crates" / "cairn-cli" / "src"
# The clap families cli.rs flattens into the top level.
TOP_LEVEL_ENUMS = ("CoreCommand", "KnowledgeCommand", "SystemCommand")
SCRIPTS_DIRS = [ROOT / "scripts"]

# Frontmatter schema. ``expected_calls`` and ``wrong_calls`` are lists of dicts.
REQUIRED_KEYS = ["id", "rule", "title", "scenario", "expected_calls", "wrong_calls"]
EXPECTED_CALL_FIELDS = ["tool", "reason"]
WRONG_CALL_FIELDS = ["tool", "why"]


# ---------------------------------------------------------------------------
# Discovery: scrape the universe of real tool / command names from source.
# ---------------------------------------------------------------------------

# Every registered MCP tool is one `text_tool(` entry in the daemon catalog,
# its name on the next line.
_TOOL_ENTRY_RE = re.compile(r'\b\w+_tool\(\s*\n\s*"([a-z_]+)"')


def discover_mcp_tools() -> set[str]:
    """Names of every tool the daemon's ``catalog()`` registers."""
    return set(_TOOL_ENTRY_RE.findall(MCP_TOOLS_FILE.read_text(encoding="utf-8")))


_ENUM_RE = re.compile(r"^pub enum (\w+)\s*\{", re.M)
_VARIANT_RE = re.compile(r"^\s{4}([A-Z]\w*)\s*(\(|\{|,)")
_NAME_ATTR_RE = re.compile(r'#\[command\([^\]]*name\s*=\s*"([^"]+)"')
_SUBCOMMAND_RE = re.compile(r"#\[command\(subcommand\)\]\s*(?:pub\s+)?\w+:\s*(?:Option<)?(\w+)")


def _brace_body(text: str, open_idx: int) -> str:
    """Text between the brace at ``open_idx`` and its match."""
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i]
    raise ValueError("unbalanced braces")


def _enum_bodies() -> dict[str, str]:
    bodies: dict[str, str] = {}
    for path in sorted(CLI_DIR.glob("cmds_*.rs")):
        text = path.read_text(encoding="utf-8")
        for m in _ENUM_RE.finditer(text):
            bodies[m.group(1)] = _brace_body(text, m.end() - 1)
    return bodies


def _kebab(ident: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", ident).lower()


def _variants(body: str) -> list[tuple[str, str | None]]:
    """``(command name, subcommand enum)`` per variant of one clap enum body."""
    out: list[tuple[str, str | None]] = []
    pending_attrs: list[str] = []
    pos = 0
    while pos < len(body):
        nl = body.find("\n", pos)
        line = body[pos:] if nl == -1 else body[pos:nl]
        stripped = line.strip()
        if stripped.startswith("#["):
            pending_attrs.append(stripped)
        m = _VARIANT_RE.match(line)
        if m:
            ident, opener = m.group(1), m.group(2)
            explicit = next((n.group(1) for n in map(_NAME_ATTR_RE.search, pending_attrs) if n), None)
            sub_enum = None
            if opener == "{":
                variant_body = _brace_body(body, pos + line.index("{"))
                sub = _SUBCOMMAND_RE.search(variant_body)
                sub_enum = sub.group(1) if sub else None
                pos += line.index("{") + len(variant_body) + 2
                pending_attrs = []
                out.append((explicit or _kebab(ident), sub_enum))
                continue
            pending_attrs = []
            out.append((explicit or _kebab(ident), None))
        elif not stripped.startswith("///"):
            pending_attrs = [a for a in pending_attrs if stripped.startswith("#[")]
        pos = len(body) if nl == -1 else nl + 1
    return out


def discover_cli_commands() -> dict[str, set[str]]:
    """Map of ``cairn``-invokable command names: ``{"<group>": {...}, "main": {...}}``.

    Group names map to their subcommand names; top-level commands live under
    the ``"main"`` key. So ``cairn dataflow build`` is found as
    ``commands["dataflow"] == {"build", "lookup"}`` and ``cairn update`` as
    ``"update" in commands["main"]``.
    """
    bodies = _enum_bodies()
    commands: dict[str, set[str]] = {"main": set()}

    def walk(enum_name: str, bucket: str) -> None:
        for name, sub_enum in _variants(bodies[enum_name]):
            commands.setdefault(bucket, set()).add(name)
            if sub_enum and sub_enum in bodies:
                walk(sub_enum, name)

    for enum_name in TOP_LEVEL_ENUMS:
        walk(enum_name, "main")
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
    allow_unregistered_mcp: bool = False,
) -> tuple[bool, str]:
    """Return ``(ok, reason)``: whether ``tool`` is a registered MCP tool, a `cairn` command (flags ignored), or a script."""
    if not isinstance(tool, str) or not tool.strip():
        return False, "empty/missing tool name"

    t = tool.strip()

    # Fully-qualified MCP name, e.g. mcp__cairn__rebuild_graph. The tail must
    # be a registered tool; an unregistered tail is accepted only for
    # wrong_calls entries, which document tools that do not exist.
    mq = _MCP_REMOTE_PREFIX_RE.match(t)
    if mq:
        tail = mq.group(1)
        if tail in mcp_tools:
            return True, f"MCP tool (fully-qualified, registered: {tail})"
        if allow_unregistered_mcp:
            return True, f"MCP tool name (documented-as-nonexistent: {tail})"
        return False, (
            f"unregistered MCP tool: {t!r} (tail {tail!r} matches no "
            f"catalog tool; expected_calls must name real tools)"
        )

    # Bare MCP tool name.
    if t in mcp_tools:
        return True, f"MCP tool ({t})"

    # CLI: accept "cairn <group> <cmd>", "cairn <cmd>", "<group> <cmd>" (no prefix).
    # Also accept the legacy "cg " prefix for eval specs written before the rename.
    if t.startswith("cairn "):
        cleaned = t[len("cairn "):].strip()
    elif t.startswith("cg "):
        cleaned = t[3:].strip()
    else:
        cleaned = t
    parts = [p for p in cleaned.split() if not p.startswith("-")]
    if len(parts) == 2:
        grp, cmd = parts
        if grp in cli_commands and cmd in cli_commands[grp]:
            return True, f"CLI command (cairn {grp} {cmd})"
        # hyphenated subcommands are sometimes written with a space
        hyphen = f"{grp}-{cmd}"
        if grp in cli_commands and hyphen in cli_commands[grp]:
            return True, f"CLI command (cairn {grp} {hyphen})"
    if len(parts) == 1 and parts[0] in cli_commands["main"]:
        return True, f"CLI command (cairn {parts[0]})"

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
                ok, why = resolve_tool(
                    tool,
                    mcp_tools,
                    cli_commands,
                    scripts,
                    allow_unregistered_mcp=(field == "wrong_calls"),
                )
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
