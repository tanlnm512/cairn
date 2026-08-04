"""Doc-drift prevention tests for the cairn agent surface.

These tests guard against doc/code drift on the agent surface: they turn the
entire class of "skill docs / slash commands disagree with the code" bugs into
CI failures. Each test below targets a specific kind of drift:

  1. ``test_cli_commands_in_skill_docs_exist`` -- slash-command docs only
     reference ``cairn`` subcommands that actually exist in the Click registry.
  2. ``test_tool_count_string_matches_server`` -- the "N tools across M layers"
     string emitted by the installer (and written into AGENTS.md) matches the
     authoritative ``_EXPECTED_TOOL_COUNT`` in the server.
  3. ``test_skill_tool_index_lists_all_registered_tools`` -- the SKILL.md name
     index and references/tools.md document exactly the set of tools the MCP
     server registers (no missing, no phantom tools).
  4. ``test_tools_md_default_args_match_live_signatures`` -- every documented
     default argument in references/tools.md matches the live tool signature
     obtained via ``inspect.signature``.
  5. ``test_no_invented_promotion_gate_in_steward`` -- the knowledge-steward
     subagent prompt must not regress to the invented "confidence >= 0.5"
     promotion gate.

The tests prefer source-scraping (regex / ``ast``) over importing heavy
modules, so they stay fast and dependency-free even when optional deps
(torch, sentence-transformers) are absent. ``inspect.signature`` is used only
on the lightweight tool functions in ``tools_*.py`` and is guarded per-import
so a missing optional dependency skips the single affected assertion rather
than erroring the whole file.
"""
from __future__ import annotations

import ast
import inspect
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "cairn"
CMD_DIR = SRC / "agent_integration" / "commands"
SKILL_MD = SRC / "agent_integration" / "skill" / "SKILL.md"
TOOLS_MD = SRC / "agent_integration" / "skill" / "references" / "tools.md"
CLI_FALLBACK_MD = SRC / "agent_integration" / "skill" / "references" / "cli-fallback.md"
STEWARD_JSON = SRC / "agent_integration" / "cursor" / "knowledge-steward.json"
AGENTS_MD = REPO_ROOT / "AGENTS.md"


# ---------------------------------------------------------------------------
# Helpers: CLI command registry scraped from source (no `cairn` invocation needed)
# ---------------------------------------------------------------------------

def _scrape_click_registry() -> tuple[set[str], dict[str, set[str]]]:
    """Return ``(top_level, groups)`` for the ``cairn`` Click CLI.

    ``top_level`` is the set of names usable as ``cairn <name>``. ``groups`` maps a
    group name (e.g. ``memory``) to the set of its subcommand names (e.g.
    ``{"record", "stats", ...}``).

    Building this by importing ``src.cli`` would require Click (and transitively
    the graph stack) to be installed in CI, which is not guaranteed. Instead we
    parse each ``src/cli/*.py`` with ``ast`` and read the decorator structure:

      * a function decorated with ``@<owner>.group(...)`` defines a new group
        named after the function;
      * a function decorated with ``@<owner>.command(...)`` registers a command
        on ``<owner>``. The command name is either the explicit string literal
        argument (positional first arg or ``name="..."`` keyword) or, when the
        decorator is bare, the function's own name.

    ``ast`` is used (rather than a line regex) because ``@click.option(...)``
    decorators are frequently split across multiple continuation lines, which
    defeats naive forward line-scanning.
    """
    top: set[str] = set()
    groups: dict[str, set[str]] = {}

    cli_files = sorted((SRC / "cli").glob("*.py"))

    # Pass 1: collect every group name (function name under a .group() call).
    group_names: set[str] = set()
    for f in cli_files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if (isinstance(dec, ast.Call)
                            and isinstance(dec.func, ast.Attribute)
                            and dec.func.attr == "group"):
                        group_names.add(node.name)

    # Pass 2: collect commands, attaching to main (top-level) or to a group.
    for f in cli_files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr == "command"):
                    continue
                owner_node = dec.func.value
                if not isinstance(owner_node, ast.Name):
                    continue  # chained/complex owner we don't model
                owner = owner_node.id

                explicit: str | None = None
                if dec.args:
                    a0 = dec.args[0]
                    if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                        explicit = a0.value
                for kw in dec.keywords:
                    if (kw.arg == "name"
                            and isinstance(kw.value, ast.Constant)
                            and isinstance(kw.value.value, str)):
                        explicit = kw.value.value

                cmd = explicit or node.name
                if owner == "main":
                    top.add(cmd)
                elif owner in group_names:
                    groups.setdefault(owner, set()).add(cmd)
    return top, groups


# ---------------------------------------------------------------------------
# Helpers: MCP tool names scraped from source (import-free)
# ---------------------------------------------------------------------------

# ``@mcp.tool()`` may be immediately followed by other decorators (e.g.
# ``@instrument``) before the ``def``. We allow only decorator lines and blank
# lines to appear between them, so a plain helper like ``_clamp`` defined later
# in the same file is never mistaken for a tool. The call may carry keyword
# arguments such as ``annotations=ToolAnnotations(...)`` (Phase 3.1), so the
# parenthesized body is matched permissively rather than requiring an empty
# ``@mcp.tool()``.
_TOOL_DEF_RE = re.compile(
    r'@mcp\.tool\((?P<kw>[^\n]*)\)\s*\n((?:[ \t]*@\w[^\n]*\n|[ \t]*\n)*?)'
    r'(?:async\s+)?def (?P<name>\w+)\(',
    re.MULTILINE,
)


def _scrape_mcp_tool_names() -> set[str]:
    """Collect the names of every ``@mcp.tool()``-decorated function.

    Source-scraped (not imported) so the test has no dependency on the model /
    embedding stack. The function name under ``@mcp.tool()`` IS the tool name
    FastMCP exposes.
    """
    names: set[str] = set()
    for f in sorted((SRC / "mcp_server").glob("tools_*.py")):
        for m in _TOOL_DEF_RE.finditer(f.read_text(encoding="utf-8")):
            names.add(m.group("name"))
    return names


def _live_defaults_from_source(tool_name: str) -> dict[str, object] | None:
    """Parse a tool function's ``def`` in ``tools_*.py`` and return its
    ``{param: default}`` mapping, evaluated with :func:`ast.literal_eval`.

    Dep-free fallback for :func:`inspect.signature` when the ``mcp`` package
    (pulled in by importing the server) is unavailable. Defaults align
    right-most against positional args, per Python's call signature rules.
    """
    for f in sorted((SRC / "mcp_server").glob("tools_*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name != tool_name:
                continue
            args = node.args
            pos = args.args
            defaults = args.defaults
            out: dict[str, object] = {}
            n_default = len(defaults)
            for i, default in enumerate(defaults):
                param = pos[len(pos) - n_default + i].arg
                try:
                    out[param] = ast.literal_eval(default)
                except (ValueError, SyntaxError):
                    continue
            for k_arg, k_default in zip(args.kwonlyargs, args.kw_defaults):
                if k_default is None:
                    continue
                try:
                    out[k_arg.arg] = ast.literal_eval(k_default)
                except (ValueError, SyntaxError):
                    continue
            return out
    return None


def _render_agents_instructions_from_source() -> str:
    """Reconstruct ``_agents_instructions()`` output by reading its source.

    Dep-free fallback when importing ``cairn.agent_install`` fails (it pulls
    the ``mcp`` package transitively). We locate the ``_agents_instructions``
    function and the module-level ``_INSTRUCTIONS_BODY`` string by scanning the
    ``agent_install`` package (after the Phase 1.3 split they live in
    ``agent_install/_common.py``) and concatenate the two header string literals
    with the body, mirroring what the function returns. This is enough to check
    the tool-count blurb -- we are not validating the full rendering here.
    """
    pkg_dir = SRC / "agent_install"
    body_value: str | None = None
    agents_value: str | None = None
    for src_path in sorted(pkg_dir.rglob("*.py")):
        try:
            tree = ast.parse(src_path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            # ``_INSTRUCTIONS_BODY = \"\"\"...\"\"\"`` at module level.
            if (isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "_INSTRUCTIONS_BODY"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                body_value = node.value.value
            # ``_agents_instructions`` returns the concatenation; we only need its
            # returned string, but the header literal is inlined inside it. Find the
            # first string constant inside the function body to use as the header.
        for node in tree.body:
            if (isinstance(node, ast.FunctionDef) and node.name == "_agents_instructions"):
                for inner in ast.walk(node):
                    if (isinstance(inner, ast.Constant) and isinstance(inner.value, str)
                            and "tools across 5 layers" in inner.value):
                        agents_value = inner.value
                        break
                break
        if agents_value is not None and body_value is not None:
            break
    if agents_value is not None and body_value is not None:
        return agents_value + body_value
    return ""


# ---------------------------------------------------------------------------
# Helpers: parse documented tool signatures in references/tools.md
# ---------------------------------------------------------------------------

# A documented signature looks like:  `tool_name(p1, p2=default, p3="x")`
# captured as a single backtick-quoted span. We match the opening `` `name(``
# then read up to the matching close paren, then a closing backtick.
_DOC_SIG_RE = re.compile(r"`([A-Za-z_]\w*)\((.*?)\)`", re.DOTALL)


def _split_top_level(s: str) -> list[str]:
    """Split a signature's parameter list on top-level commas only.

    Respects double/single quotes and nested parentheses/brackets so defaults
    like ``"mermaid"`` or function-call defaults are not split mid-value.
    """
    parts: list[str] = []
    depth = 0
    cur = []
    quote: str | None = None
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            cur.append(ch)
        elif ch in "([{":
            depth += 1
            cur.append(ch)
        elif ch in ")]}":
            depth = max(0, depth - 1)
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_doc_defaults(sig_body: str) -> dict[str, object]:
    """Given the text inside a documented ``tool(...)`` signature, return the
    ``{param: default}`` mapping for parameters that document a default.

    Defaults are evaluated with :func:`ast.literal_eval` so ``"mermaid"``,
    ``False``, ``500``, ``""`` become the matching Python values. A parameter
    without ``=`` (a required param, or one whose default is intentionally
    omitted from the docs) is skipped.
    """
    defaults: dict[str, object] = {}
    for part in _split_top_level(sig_body):
        if "=" not in part:
            continue
        name, _, raw = part.partition("=")
        name = name.strip().split(":")[0].strip()  # drop any `: type` annotation
        raw = raw.strip()
        if not name or not raw:
            continue
        try:
            value = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            # Unrecognized default literal -- skip rather than guess.
            continue
        defaults[name] = value
    return defaults


def _parse_tools_md_signatures() -> dict[str, dict[str, object]]:
    """Return ``{tool_name: {param: default}}`` for every signature in tools.md."""
    text = TOOLS_MD.read_text(encoding="utf-8")
    out: dict[str, dict[str, object]] = {}
    for m in _DOC_SIG_RE.finditer(text):
        name = m.group(1)
        defaults = _parse_doc_defaults(m.group(2))
        # First occurrence wins (canonical header lines); later inline mentions
        # of the same name are usually prose, not signatures.
        out.setdefault(name, defaults)
    return out


# ---------------------------------------------------------------------------
# Test 1
# ---------------------------------------------------------------------------

def test_cli_commands_in_skill_docs_exist():
    """Every ``cairn <name>`` / ``cairn <group> <sub>`` reference in the slash-command
    docs and the CLI-fallback reference resolves to a real Click command.

    Catches drift where a doc names a command that was renamed or removed (the
    classic ``cairn status`` vs ``cairn stats`` confusion). We build the command
    registry by AST-scraping ``src/cli/*.py`` rather than invoking ``cairn``, so
    this works in CI without the package installed.
    """
    top, groups = _scrape_click_registry()

    # Sanity: the registry scraper must have found the structural anchors we
    # rely on. If these are missing, the scraper itself is broken (and a false
    # PASS would be worse than a loud failure).
    assert "stats" in top, "scraper failed to find top-level `cairn stats`"
    assert "status" in top, (
        "scraper failed to find top-level `cairn status` (defined in system.py)"
    )
    assert "status" in groups.get("serve", set()), (
        "scraper failed to find `cairn serve status` subcommand"
    )

    # Collect every backtick-quoted `cairn <name>` and `cairn <group> <sub>` reference.
    ref_re = re.compile(r"`cairn ([a-zA-Z][\w-]*)(?:\s+([a-zA-Z][\w-]*))?")
    refs: dict[tuple[str, ...], set[str]] = {}
    doc_files = sorted(CMD_DIR.glob("*.md")) + [CLI_FALLBACK_MD]
    for f in doc_files:
        for m in ref_re.finditer(f.read_text(encoding="utf-8")):
            first, second = m.group(1), m.group(2)
            key = (first, second) if second else (first,)
            refs.setdefault(key, set()).add(f.name)

    assert refs, "no `cairn ...` references found in docs (regex too strict?)"

    failures: list[str] = []
    for (first, *rest), where in sorted(refs.items()):
        if rest:
            second = rest[0]
            if first not in groups:
                failures.append(f"`cairn {first} {second}` -> `{first}` is not a group "
                                f"(in {sorted(where)})")
            elif second not in groups[first]:
                failures.append(f"`cairn {first} {second}` -> no `{second}` subcommand "
                                f"under `{first}` (in {sorted(where)})")
        else:
            if first not in top:
                failures.append(f"`cairn {first}` -> no such top-level command "
                                f"(in {sorted(where)})")

    assert not failures, (
        "doc references non-existent cairn commands:\n  " + "\n  ".join(failures)
    )


# ---------------------------------------------------------------------------
# Test 2
# ---------------------------------------------------------------------------

def test_tool_count_string_matches_server():
    """The "N tools across M layers" string must match the server's
    ``_EXPECTED_TOOL_COUNT``.

    Catches drift where a tool is added/removed and the installer blurb (and the
    on-disk AGENTS.md) still advertise the old count. The previous regression
    was "24 tools" lingering after the count became 26.
    """
    # Authoritative count lives in the server source.
    server_src = (SRC / "mcp_server" / "server.py").read_text(encoding="utf-8")
    m = re.search(r"_EXPECTED_TOOL_COUNT\s*=\s*(\d+)", server_src)
    assert m, "could not find _EXPECTED_TOOL_COUNT in src/mcp_server/server.py"
    expected = int(m.group(1))

    # The installer must emit a count string that agrees with the server.
    # Importing ``src.agent_install`` can transitively pull the ``mcp`` package
    # (via the server subpackage); when that optional dep is absent, fall back to
    # rendering the instruction text from source so the test still runs.
    instructions = ""
    try:
        from cairn.agent_install import _agents_instructions
        instructions = _agents_instructions()
    except Exception as exc:  # optional deps (mcp/...) missing
        instructions = _render_agents_instructions_from_source()
        assert instructions, (
            f"could not import _agents_instructions ({exc!r}) and the source "
            "fallback also failed to render the instruction text"
        )
    expected_phrase = f"{expected} tools across 5 layers"
    assert expected_phrase in instructions, (
        f"_agents_instructions() output does not contain {expected_phrase!r}; "
        f"the installer blurb drifted from _EXPECTED_TOOL_COUNT={expected}"
    )
    # The previous stale count must not appear.
    stale = f"{expected - 1} tools"
    assert stale not in instructions, (
        f"_agents_instructions() still references the stale {stale!r} string"
    )

    # The on-disk AGENTS.md must agree too (it is generated from the installer).
    assert AGENTS_MD.exists(), f"{AGENTS_MD} not found"
    agents_text = AGENTS_MD.read_text(encoding="utf-8")
    assert f"{expected} tools" in agents_text, (
        f"{AGENTS_MD} does not contain '{expected} tools'"
    )
    assert stale not in agents_text, (
        f"{AGENTS_MD} still references the stale {stale!r} string"
    )


# ---------------------------------------------------------------------------
# Test 3
# ---------------------------------------------------------------------------

def test_skill_tool_index_lists_all_registered_tools():
    """The SKILL.md name index and references/tools.md must each list exactly
    the set of tools the MCP server registers -- no missing tools, no phantoms.

    Catches drift like a new tool (e.g. ``memory_digest``, ``trace_workflow``)
    being registered but never added to the docs, or a doc lingering after a
    tool is removed. The authoritative set is scraped from ``@mcp.tool()``
    decorators so the test needs no model/embedding deps.
    """
    registered = _scrape_mcp_tool_names()
    assert registered, "no @mcp.tool() functions scraped from tools_*.py"
    assert len(registered) == 27, (
        f"expected 27 registered tools, scraper found {len(registered)}: "
        f"{sorted(registered)}"
    )

    # --- SKILL.md "Available MCP Tools" name index ---
    # The index is a bulleted list. Each bullet lists comma-separated tool names
    # (possibly with a `**Layer:**` lead-in). Collect every bare identifier.
    skill_text = SKILL_MD.read_text(encoding="utf-8")
    skill_index_section = skill_text.split("## Available MCP Tools", 1)[-1]
    # Stop at the next markdown section header so we only scan the index.
    skill_index_section = skill_index_section.split("\n## ", 1)[0]
    skill_names: set[str] = set()
    for line in skill_index_section.splitlines():
        # Strip the leading `- ` list marker, the `**Label:**` lead-in, and
        # backticks, then pull identifiers. (Each bullet's first tool name
        # follows the marker/label, so all three must be removed or it keeps
        # a leading `- ` and fails the identifier match.)
        cleaned = re.sub(r"^\s*-\s+", "", line)
        cleaned = re.sub(r"\*\*[^*]*:\*\*", "", cleaned)
        cleaned = cleaned.replace("`", "")
        for tok in cleaned.split(","):
            tok = tok.strip()
            if re.fullmatch(r"[A-Za-z_]\w*", tok):
                skill_names.add(tok)

    missing_from_skill = registered - skill_names
    phantom_in_skill = skill_names - registered
    assert not missing_from_skill, (
        f"SKILL.md index is missing registered tools: {sorted(missing_from_skill)}"
    )
    assert not phantom_in_skill, (
        f"SKILL.md index lists non-existent (phantom) tools: {sorted(phantom_in_skill)}"
    )

    # --- references/tools.md signature blocks ---
    # Every tool appears as a backtick-quoted signature `` `tool_name(... ``.
    tools_md_text = TOOLS_MD.read_text(encoding="utf-8")
    tools_md_names: set[str] = set()
    for m in re.finditer(r"`([A-Za-z_]\w*)\(", tools_md_text):
        tools_md_names.add(m.group(1))

    missing_from_tools_md = registered - tools_md_names
    phantom_in_tools_md = tools_md_names - registered
    assert not missing_from_tools_md, (
        f"references/tools.md is missing registered tools: "
        f"{sorted(missing_from_tools_md)}"
    )
    assert not phantom_in_tools_md, (
        f"references/tools.md documents phantom tools: {sorted(phantom_in_tools_md)}"
    )


# ---------------------------------------------------------------------------
# Test 4
# ---------------------------------------------------------------------------

# Map tool name -> (module path, function name) for the tools whose defaults we
# verify against the live signature. These are the high-leverage ones
# (impact_analysis's `cached` default and the various `limit` defaults), but
# we verify *every* documented default we can resolve, so new drift in any
# documented default is caught automatically.
_LIVE_TOOL_MODULES = {
    "explore": "cairn.mcp_server.tools_graph",
    "semantic_search": "cairn.mcp_server.tools_graph",
    "find_definition": "cairn.mcp_server.tools_graph",
    "get_callers": "cairn.mcp_server.tools_graph",
    "get_callees": "cairn.mcp_server.tools_graph",
    "impact_analysis": "cairn.mcp_server.tools_graph",
    "search_symbols": "cairn.mcp_server.tools_graph",
    "cross_repo_deps": "cairn.mcp_server.tools_graph",
    "visualize_graph": "cairn.mcp_server.tools_graph",
    "search_knowledge": "cairn.mcp_server.tools_compass",
    "get_compass": "cairn.mcp_server.tools_compass",
    "ask_compass": "cairn.mcp_server.tools_compass",
    "memory_digest": "cairn.mcp_server.tools_memory",
    "recall_memory": "cairn.mcp_server.tools_memory",
    "record_memory": "cairn.mcp_server.tools_memory",
    "memory_promote": "cairn.mcp_server.tools_memory",
    "memory_demote": "cairn.mcp_server.tools_memory",
    "memory_delete": "cairn.mcp_server.tools_memory",
    "memory_decay": "cairn.mcp_server.tools_memory",
    "knowledge_add": "cairn.mcp_server.tools_knowledge",
    "knowledge_search": "cairn.mcp_server.tools_knowledge",
    "knowledge_delete": "cairn.mcp_server.tools_knowledge",
    "knowledge_status": "cairn.mcp_server.tools_knowledge",
    "trace_workflow": "cairn.mcp_server.tools_knowledge",
}


def _load_live_tool(name: str):
    """Import a tool function by name, returning ``(fn, module_path)`` or
    ``(None, module_path)`` if its module can't be imported (optional deps).

    The MCP tool functions are plain ``def``s decorated with ``@mcp.tool()``
    (plus ``@instrument``); the decorators do not rewrite signatures, so
    ``inspect.signature`` on the imported object returns the real defaults.
    """
    mod_path = _LIVE_TOOL_MODULES.get(name)
    if not mod_path:
        return None, None
    mod = sys.modules.get(mod_path)
    if mod is None:
        try:
            mod = __import__(mod_path, fromlist=[name])
        except Exception:  # optional deps missing, etc.
            return None, mod_path
    return getattr(mod, name, None), mod_path


def _resolve_live_defaults(name: str) -> tuple[dict[str, object] | None, str]:
    """Return ``(defaults, source)`` for the live defaults of tool ``name``.

    Prefers ``inspect.signature`` on the imported function (returns a dict keyed
    by param name, where params without a default are omitted). If the import
    fails (the ``mcp`` dep is absent), falls back to AST-parsing the function's
    ``def`` in source -- so the comparison still checks real defaults without
    requiring the optional dependency. ``source`` is a short human-readable note
    for skip messages. Returns ``(None, reason)`` if neither path resolves.
    """
    fn, mod_path = _load_live_tool(name)
    if fn is not None:
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError) as exc:
            # Fall through to the source-based path.
            fn = None
            mod_path = f"signature unavailable: {exc}"
        else:
            defaults: dict[str, object] = {}
            for pname, param in sig.parameters.items():
                if param.default is not inspect.Parameter.empty:
                    defaults[pname] = param.default
            return defaults, "inspect.signature"
    src_defaults = _live_defaults_from_source(name)
    if src_defaults is not None:
        return src_defaults, "source def"
    return None, f"module {mod_path} not importable and no source def found"


def test_tools_md_default_args_match_live_signatures():
    """Every documented default argument in references/tools.md must match the
    live tool function's actual default.

    This is the highest-value test: it catches the precise bug where docs lie
    about behavior -- e.g. documenting ``impact_analysis(cached=True)`` when the
    live default is ``cached=False``. For each signature in tools.md we parse
    the ``param=default`` pairs, then resolve the live defaults -- preferring
    ``inspect.signature`` on the imported tool function, and falling back to
    AST-parsing the function's ``def`` in source when the ``mcp`` package (pulled
    in by importing the server) is unavailable, so the test still checks real
    defaults in a minimal CI. A tool is only skipped when neither path resolves.
    """
    doc_sigs = _parse_tools_md_signatures()
    assert doc_sigs, "no tool signatures parsed from references/tools.md"

    # Spot-check that the headline defaults are actually documented with the
    # expected values -- this anchors the test against a doc rewrite that
    # silently drops the defaults (turning the comparison into a vacuous pass).
    impact = doc_sigs.get("impact_analysis", {})
    assert impact.get("cached") is False, (
        f"impact_analysis must document cached=False, got {impact.get('cached')!r}"
    )
    assert impact.get("depth") == 5, (
        f"impact_analysis must document depth=5, got {impact.get('depth')!r}"
    )
    assert impact.get("limit") == 500, (
        f"impact_analysis must document limit=500, got {impact.get('limit')!r}"
    )
    assert doc_sigs.get("get_callers", {}).get("limit") == 200, (
        f"get_callers must document limit=200, got "
        f"{doc_sigs.get('get_callers', {}).get('limit')!r}"
    )
    assert doc_sigs.get("semantic_search", {}).get("limit") == 20, (
        f"semantic_search must document limit=20, got "
        f"{doc_sigs.get('semantic_search', {}).get('limit')!r}"
    )
    assert doc_sigs.get("semantic_search", {}).get("include_callers") is False, (
        f"semantic_search must document include_callers=False, got "
        f"{doc_sigs.get('semantic_search', {}).get('include_callers')!r}"
    )
    assert doc_sigs.get("cross_repo_deps", {}).get("limit") == 50, (
        f"cross_repo_deps must document limit=50, got "
        f"{doc_sigs.get('cross_repo_deps', {}).get('limit')!r}"
    )

    mismatches: list[str] = []
    skipped: list[str] = []
    checked = 0
    for tool_name, doc_defaults in sorted(doc_sigs.items()):
        if not doc_defaults:
            continue  # signature documents no defaults -> nothing to compare

        live_defaults, live_source = _resolve_live_defaults(tool_name)
        if live_defaults is None:
            skipped.append(f"{tool_name} ({live_source})")
            continue

        for param, doc_default in doc_defaults.items():
            if param not in live_defaults:
                mismatches.append(
                    f"{tool_name}: docs reference parameter `{param}` "
                    f"which is absent from the live signature"
                )
                continue
            checked += 1
            live_default = live_defaults[param]
            if live_default != doc_default:
                mismatches.append(
                    f"{tool_name}.{param}: docs say ={doc_default!r} "
                    f"but live default is {live_default!r}"
                )

    # Surface skips so a silent "nothing checked" pass is visible in the report,
    # but do not fail on them (CI may legitimately lack optional deps).
    skip_note = (f" (skipped {len(skipped)} tools: {skipped})") if skipped else ""
    assert not mismatches, (
        f"documented defaults disagree with live signatures{skip_note}:\n  "
        + "\n  ".join(mismatches)
    )
    # Guard against the comparison becoming vacuous: we must have actually
    # checked real parameters.
    assert checked >= 10, (
        f"only verified {checked} documented default(s){skip_note}; "
        "the parser may have regressed"
    )


# ---------------------------------------------------------------------------
# Test 5
# ---------------------------------------------------------------------------

def test_no_invented_promotion_gate_in_steward():
    """The knowledge-steward subagent prompt must not contain the invented
    "confidence >= 0.5" promotion gate that was removed.

    Promotion is unconditional (the steward verifies quality itself); a hardcoded
    confidence floor is an invented rule. This test prevents that text from
    creeping back in.
    """
    assert STEWARD_JSON.exists(), f"{STEWARD_JSON} not found"
    text = STEWARD_JSON.read_text(encoding="utf-8")
    assert "confidence >= 0.5" not in text, (
        "knowledge-steward prompt reintroduced the invented 'confidence >= 0.5' "
        "promotion gate (promotion is unconditional)"
    )
    assert "confidence >=0.5" not in text, (
        "knowledge-steward prompt reintroduced the invented 'confidence >=0.5' "
        "promotion gate (promotion is unconditional)"
    )


# ---------------------------------------------------------------------------
# Test 6
# ---------------------------------------------------------------------------

def test_empty_result_strings_offer_a_next_step():
    """Bare "No X found" empty-results contradict the skill's core doctrine.

    The skill teaches "empty precise != unused" and "don't conclude nothing
    exists" -- but if a tool returns a bare ``"No definition found for 'X'."``
    with no remediation hint, an agent hits it and stops, which is exactly the
    failure mode the doctrine warns against. Every tool whose miss is a dead
    end must point at the next thing to try (a sibling tool, a broader query,
    or an index/embed step).

    This test reads the empty-result string literals from the tool source and
    asserts each carries a next-step hint. Source-scraped (no heavy imports)
    so it runs in minimal CI.
    """
    REPO = Path(__file__).resolve().parent.parent
    cases = [
        # (tool module path, function name, substring that MUST appear in an
        #  empty-result return string in that function)
        ("src/cairn/mcp_server/tools_graph.py", "find_definition",
         "search_symbols"),
        # Phase 3.3 moved search_symbols' empty-result prose into a dedicated
        # _render_search_symbols helper (the tool now returns structured data
        # or delegates rendering to the helper). The hint invariant still
        # holds; it just lives in the renderer now.
        ("src/cairn/mcp_server/tools_graph.py", "_render_search_symbols",
         "semantic_search"),
        ("src/cairn/mcp_server/tools_memory.py", "recall_memory",
         "memory_digest"),
        ("src/cairn/mcp_server/tools_knowledge.py", "knowledge_search",
         "broader"),
    ]
    problems = []
    for rel, fn, required_hint in cases:
        src = (REPO / rel).read_text(encoding="utf-8")
        # Slice from the function def to the next top-level def.
        m = re.search(
            rf"^def {fn}\b.*?(?=^def |\Z)",
            src, re.DOTALL | re.MULTILINE,
        )
        assert m, f"could not locate def {fn} in {rel}"
        body = m.group(0)
        # The empty-result return is a (possibly multi-line) f-string beginning
        # with "No ...". It may be written as `return (...)` with parenthesized
        # adjacent f-strings, OR as a bare `return f"..." f"..." f"..."` chain.
        # Rather than parse Python string syntax with regex, grab the whole
        # span from each `return` to the next blank line (end of statement) and
        # check the hint is anywhere in that span. Robust to either form.
        return_spans = re.findall(
            r"return\s+(.*?)(?=\n\s*\n|\n    [a-z]|\Z)",
            body, re.DOTALL,
        )
        miss_spans = [s for s in return_spans if re.search(r'"No ', s)]
        if not miss_spans:
            problems.append(f"{rel}::{fn}: no empty-result return block found")
            continue
        hinted = any(required_hint in s for s in miss_spans)
        if not hinted:
            _ws = re.compile(r"\s+")
            previews = [_ws.sub(" ", s)[:90] for s in miss_spans]
            problems.append(
                f"{rel}::{fn}: no empty-result return block contains "
                f"'{required_hint}'. Spans: {previews}"
            )
    assert not problems, (
        "An empty-result string lost its next-step hint. The skill's "
        "'don't conclude nothing exists' doctrine depends on these pointers:\n  "
        + "\n  ".join(problems)
    )


# ---------------------------------------------------------------------------
# Test 7
# ---------------------------------------------------------------------------

def test_ask_compass_surfaces_all_layers_empty():
    """ask_compass must not return a bare header when every layer is empty.

    The router computes ``route["empty"]`` (compass/router.py), but for a long
    time ask_compass discarded it: an all-empty query printed just the
    "Intent: ..." header followed by nothing, forcing the agent to infer failure
    from absence -- the exact trap the skill warns against. The fix appends an
    explicit "(No results from any layer ...)" line when ``empty`` is true.

    This asserts the empty-signal branch exists in ask_compass (source-scraped,
    no router/db needed). Regression guard for the surfacing fix.
    """
    REPO = Path(__file__).resolve().parent.parent
    src = (REPO / "src/cairn/mcp_server/tools_compass.py").read_text(encoding="utf-8")
    m = re.search(r"^def ask_compass\b.*?(?=^def |\Z)", src, re.DOTALL | re.MULTILINE)
    assert m, "could not locate def ask_compass in tools_compass.py"
    body = m.group(0)
    # The fix reads result.get("empty") and prints an explicit empty-line.
    assert 'result.get("empty")' in body or 'result.get(\'empty\')' in body, (
        "ask_compass no longer reads the router's 'empty' flag -- the "
        "all-layers-empty signal was dropped (agents can't tell a thin-coverage "
        "miss from 'no info exists')."
    )
    assert "No results from any layer" in body, (
        "ask_compass no longer prints the explicit all-layers-empty line."
    )

