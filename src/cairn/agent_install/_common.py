"""Shared constants, helpers, and result types for the agent_install package.

Kept separate from detect/merge/clients so no module imports a sibling client.
"""
from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

# All supported clients. Order matters only for display.
# "claude" is Claude Code (CLI, workspace-scoped); "claude-desktop" is the
# Claude Desktop GUI app (global config, MCP-only).
CLIENTS = ["claude", "claude-desktop", "cursor", "droid", "zcode", "agy", "opencode"]

# Slash commands provided by cairn (single source of truth for all client modules).
_SLASH_COMMANDS = [
    "cairn",
    "cairn-prep",
    "cairn-ship",
    "cairn-audit",
    "cairn-refresh",
]

# Resources bundled under src/agent_integration/.
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "agent_integration"
_MARKER = "cairn"  # used to identify our entries when merging/uninstalling


# --------------------------------------------------------------------------
# Result tracking
# --------------------------------------------------------------------------

@dataclass
class InstallResult:
    client: str
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, path: Path, existed: bool):
        (self.skipped if existed else self.written).append(str(path))


# --------------------------------------------------------------------------
# Path / command resolution
# --------------------------------------------------------------------------

def resolve_cg_command() -> list[str]:
    """Resolve a cairn invocation for generated configs.

    Prefers the absolute path of a `cairn` binary on PATH; falls back to
    `python -m cairn.cli.main`.
    """
    cairn_bin = shutil.which("cairn")
    if cairn_bin:
        return [cairn_bin]
    return [sys.executable, "-m", "cairn.cli.main"]


def resolve_cg_str() -> str:
    """Single-string form for config files (command + args joined)."""
    cmd = resolve_cg_command()
    return " ".join(cmd)


def mcp_config_json(transport: str = "stdio", sse_url: str | None = None) -> dict:
    """MCP server config pointing at `cairn serve` (shared mcpServers shape).

    Used by claude, cursor, droid, agy, and (via mcp_config_json_desktop) the
    Claude Desktop app. Client-specific MCP shapes live in their own client
    modules.

    Args:
        transport: "stdio" (default, one process per client) or "sse" (one
            shared daemon, requires `cairn serve start` to be running).
        sse_url: when transport="sse", the URL clients should connect to.
            Defaults to http://127.0.0.1:{lc.DEFAULT_PORT}/sse.
    """
    from ..mcp_server import lifecycle as lc

    if transport == "sse":
        url = sse_url or f"http://127.0.0.1:{lc.DEFAULT_PORT}/sse"
        # Include "type": "sse" explicitly for maximum cross-client compat.
        # Some clients (Cursor) infer from `url`; others (ZCode, older Claude
        # Desktop builds) want the explicit type. Harmless where it's ignored.
        return {"mcpServers": {"cairn": {"type": "sse", "url": url}}}
    cmd = resolve_cg_command()
    if len(cmd) == 1:
        # cairn binary: args = ["serve"]
        return {"mcpServers": {"cairn": {"command": cmd[0], "args": ["serve"]}}}
    # module fallback (e.g. [python, "-m", "cairn.cli.main"]): append "serve" to args
    command, *prefix = cmd
    return {"mcpServers": {"cairn": {"command": command, "args": [*prefix, "serve"]}}}


def _python_for_hooks() -> str:
    """Absolute python path for `python -m cairn.hooks.claude_hooks` invocations."""
    return sys.executable


def _claude_hook_command(entrypoint: str) -> str:
    """Build a hook command string: `<python> -m cairn.hooks.claude_hooks <entry>`."""
    return f"{_python_for_hooks()} -m cairn.hooks.claude_hooks {entrypoint}"


def _hook_markers() -> list[str]:
    """Substrings that identify a cairn hook command, path-independently."""
    return [
        "cairn.hooks.claude_hooks post_edit",
        "cairn.hooks.claude_hooks session_end",
        # Legacy markers (kept so uninstall-agents can still strip hooks written
        # by older installs).
        "src.hooks.claude_hooks post_edit",
        "src.hooks.claude_hooks session_end",
    ]


_HOOK_ENTRYPOINTS = {"post_edit", "session_end"}


# --------------------------------------------------------------------------
# Template readers (with transformations)
# --------------------------------------------------------------------------

def _read_template(rel: str) -> str:
    return (_TEMPLATE_DIR / rel).read_text(encoding="utf-8")


def _claude_command_md(name: str) -> str:
    """Read a slash command body and prepend Claude Code frontmatter."""
    body = _read_template(f"commands/{name}.md")
    # Derive a one-line description from the first heading.
    first_line = next((ln for ln in body.splitlines() if ln.strip()), name)
    desc = first_line.lstrip("# ").strip()[:120]
    return f"---\ndescription: {desc}\n---\n\n{body}"


def _claude_agent_md(template_name: str = "cursor/cairn-explorer.json") -> str:
    """Translate a Cursor subagent JSON into a Claude Code agent .md file.

    Maps Cursor subagent fields to the richer Claude Code frontmatter
    (model, tools from readonly/extra_tools, is_background -> background).
    `skills: ["cairn"]` preloads the full cairn SKILL.md into the subagent, so
    the Cursor `prompt` text should stay specific to the subagent's role.
    """
    sub = json.loads(_read_template(template_name))

    model = sub.get("model", "inherit")

    # Build the tools line. MCP tools are always listed with the
    # mcp__cairn__ prefix so Claude Code exposes them.
    mcp_tools = ", ".join(
        f"mcp__cairn__{t}" for t in sub.get("tools", [])
    )
    if sub.get("readonly"):
        builtins = ["Read", "Grep", "Glob", "LS"]
    else:
        builtins = list(sub.get("extra_tools", []))

    parts = builtins + ([mcp_tools] if mcp_tools else [])
    tools_line = f"tools: {', '.join(parts)}" if parts else ""

    lines = [
        "---",
        f"name: {sub['name']}",
        f"description: {sub['description']}",
    ]
    if tools_line:
        lines.append(tools_line)
    lines.append(f"model: {model}")
    lines.append('mcpServers: ["cairn"]')
    lines.append('skills: ["cairn"]')
    lines.append("effort: low")
    if sub.get("is_background"):
        lines.append("background: true")
    lines.append("---")
    lines.append("")
    lines.append(sub["prompt"])
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Instruction file content
# --------------------------------------------------------------------------

_INSTRUCTIONS_BODY = """## Workflow: explore-first

### For almost any question -- "how does X work", a flow, surveying an area:
1. Call `explore(query)` FIRST. It returns matching symbols' verbatim source
   grouped by file, the call paths between them (including ambiguous dispatch
   hops), and a blast-radius summary -- one call, one answer.
2. Reach for the specific tools only to drill down when `explore` is thin:
   - `ask_compass(query)` -- cross-layer routing (graph + wiki + compass + memory)
   - `get_callers` / `get_callees` / `impact_analysis` -- deeper call-graph traversal
   - `search_knowledge` / `recall_memory` -- knowledge-layer questions `explore` doesn't cover

### Before editing a file, ALWAYS:
1. Call `ask_compass(file_path="<path>")` to load compass + memory context
2. Call `find_definition` for any symbol you need to understand
3. Call `get_callers` to understand who depends on what you are changing (within-repo)
4. Call `cross_repo_deps(repo_name)` for cross-repo blast radius
5. Call `impact_analysis(symbol_name)` if making breaking changes (within-repo recursive)

### Resolution-aware querying (precise vs fuzzy)
`get_callers`, `get_callees`, and `impact_analysis` default to **precise**:
they only follow edges the resolver could pin to exactly one definition.

- **Empty precise result ≠ "no callers".** It means "no *resolvable* callers."
  Before concluding a symbol is unused, retry with `fuzzy=True`.
- **Precise is ground truth for blast radius** — not inflated by name collisions.
- **Fuzzy is a candidate list, not truth** — verify each against actual code.
  A fuzzy result for `invoke` can span 200+ sites across repos/languages that
  merely share the name.
- **`resolution` label:** `exact` = trusted; `ambiguous` = multiple candidates,
  resolver declined to guess; `unresolved` = external/stdlib.

When precise is right: impact, refactoring, signature changes.
When fuzzy is right: auditing, dead-code hunting, exploring unfamiliar code.

### When you need architectural context:
- Call `get_compass(module_name)` for a 25-35 line navigation guide
- Call `search_knowledge(query, type_filter="Wiki")` for feature/architecture documentation

### When you need past decisions:
- Call `recall_memory(query)` -- symbol/title-keyed, NOT full-text. Query by
  symbol name or title tokens ("ApiFactory", "backoff"), not natural language.

### After completing a task, ALWAYS:
1. Run `cairn update` to refresh the graph with your changes
2. Call `record_memory` for any learnings:
   - type="decision" for architectural choices made
   - type="pattern" for reusable code patterns discovered
   - type="mistake" for errors others should avoid
   - type="workaround" for non-obvious solutions used
3. Set confidence (0.0-1.0) based on how sure you are

## Tool Quirks (empirically verified)

| Tool | Behavior | Workaround |
|------|----------|------------|
| `ask_compass` | Routes correctly but returns empty body skeletons when wiki/compass coverage is thin. | Drill down with the specific layer tool; don't treat empty response as "no info exists". |
| `recall_memory` | Multi-token lexical matching, with a semantic fallback when lexical search comes up empty. | Natural-language and multi-token queries ("backoff retry policy") work, not just single symbol tokens. |
| `impact_analysis` | Within-repo by default, but includes cross-repo consumer reach in its output. Precise mode only follows resolved edges, so common names can under-report. | Pair with `cross_repo_deps(repo)` for the full picture. Use `fuzzy=True` when precise impact looks suspiciously small for a widely-used symbol. |
| `search_symbols` | FTS5 + phrase splitting handles underscored tokens (`*core_ui_v4*` matches correctly). For camelCase or non-prefix substring patterns, unions in a LIKE-based substring pass (FTS5's `*` is prefix-only; unicode61 doesn't split camelCase). | Wildcards and substring queries both work, on underscored and camelCase names alike. |
| `get_callers`/`impact_analysis` on a Kotlin class invoked via `operator fun invoke` | A bare `someUseCase(params)` call (DI-injected property, the standard Android UseCase idiom) resolves the call edge to the *local property* in the calling file, not the class. The parser retargets these bare-call edges to the callee's declared type. | `this.someUseCase(params)` (explicit receiver) is a remaining gap; cross-check with `fuzzy=True` or a grep if that specific shape looks under-reported. |
| `semantic_search` | Defaults to RRF fusion (BM25 + vector, `CAIRN_FUSION=1` default): the returned `score` is a rank-fusion number (~0.01-0.02), not cosine similarity, regardless of the `threshold` argument. Real cosine scores (0.3-0.6+ for genuinely on-topic hits with `local`/`BAAI/bge-m3`) only show when fusion is off. | Rank order is meaningful either way. Set `CAIRN_FUSION=0` if you need the score to reflect actual match strength (e.g. deciding how confident a hit is), not just relative order. |
| `ann_backend_enabled` | On by default: `CAIRN_ANN_BACKEND` unset resolves to `sqlite-vec`. It degrades silently to the brute-force cosine scan if the extension fails to load. | Set `CAIRN_ANN_BACKEND=off` to force the brute-force scan. |

## LLM Task Queue (agent-decoupled synthesis)
Cairn never calls an LLM directly. To generate compass/wiki with LLM quality:
- `cairn task list --status pending` -- see queued work
- `cairn task show <id>` -> `cairn task claim <id>` -> `cairn task complete <id> --result-file <path>`
- The deterministic critic fact-checks every result; only graph-verified files/symbols allowed.

## CLI Fallback (if MCP tools are unavailable):
- `cairn def <symbol>` -- find definition
- `cairn callers <symbol>` -- who calls this
- `cairn impact <symbol>` -- what breaks if changed (within-repo)
- `cairn deps <repo>` -- cross-repo dependency map
- `cairn context <file>` -- load context for a file
- `cairn ask "<question>"` -- natural language query across all layers
- `cairn memory record <type> "<title>"` -- capture a learning

## Knowledge Files

The `.knowledge/` directory (in cairn/) contains OKF markdown files:
- `compass/` -- module navigation guides (25-35 lines each)
- `wiki/` -- architectural documentation
- `memory/tribal/` -- past decisions, patterns, mistakes
- `memory/raw/` -- ephemeral captures (do not read)
- `memory/drafts/` -- awaiting quality review (do not read)

You can read these files directly when MCP is unavailable.
"""


def _claude_instructions() -> str:
    return (
        "# Codebase Intelligence (Cairn)\n\n"
        "This workspace is connected to the cairn knowledge system. Use these tools\n"
        "to understand the codebase before making changes. Start with the router, drill\n"
        "down with layer-specific tools.\n\n"
    ) + _INSTRUCTIONS_BODY


def _agents_instructions() -> str:
    return (
        "# Codebase Intelligence System\n\n"
        "This workspace uses a local knowledge graph (cairn) for codebase intelligence.\n"
        "All AI coding agents working in this workspace should use these tools.\n\n"
        "## MCP Server\n"
        "- Name: `cairn` (auto-connected at session start)\n"
        "- Transport: stdio\n"
        "- 27 tools across 5 layers: graph (9), knowledge base + compass (5), memory (8), knowledge (5)\n"
        "  (`explore` is the recommended first call -- it aggregates the graph layer)\n"
        "\n"
    ) + _INSTRUCTIONS_BODY
