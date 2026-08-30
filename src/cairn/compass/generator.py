"""Compass file generator: a graph-assisted 5-question framework.

For each module (package/directory), generates a 25-35 line compass file with:
  1. What Does This Module Do?
  2. Common Modification Patterns
  3. Build-Failure Patterns (derived from graph facts)
  4. Cross-Module Dependencies
  5. Tribal Knowledge

Uses the L1 graph for all facts (symbols, key files, cross-module deps). When an
LLM is available it synthesizes richer prose; otherwise a deterministic
graph-driven template is produced. Either way, the critic fact-checks against L1.

Two modes:
  - Deterministic (default): graph template, no LLM.
  - LLM-assisted (use_llm=True): generator->critic->revise loop, agent-decoupled.
    Facts always come from the graph; the agent only synthesizes prose. The
    deterministic critic runs after every revision and is the sole gatekeeper.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..graph.queries import trace_flow
from ..okf.bundle import OKFBundle
from ..okf.concept import OKFConcept

# Cap revise cycles to bound the generator->critic->revise loop.
from ..llm.tasks import MAX_REVISE_CYCLES

# Escape char for LIKE pattern matching. We escape the user-supplied
# module_path so its literal '%' and '_' characters are treated as literals,
# not wildcards (otherwise module_path="%" dumps the whole repo into one
# compass file). The parameterization ('?') already prevents SQL injection;
# this closes the separate wildcard-injection gap.
LIKE_ESCAPE_CHAR = "\\"


def _escape_like(value: str, escape: str = LIKE_ESCAPE_CHAR) -> str:
    """Escape LIKE wildcard metacharacters in `value` for safe use inside a
    `... LIKE ? ESCAPE '<escape>'` clause.

    Escapes the escape char itself first, then '%' and '_'. Returns the input
    unchanged when it is empty/None.
    """
    if not value:
        return ""
    return (
        value.replace(escape, escape * 2)
        .replace("%", escape + "%")
        .replace("_", escape + "_")
    )


def generate_compass(
    module_path: str,
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    repo: Optional[str] = None,
    llm_synthesize=None,
) -> OKFConcept:
    """Generate a compass OKF concept for a module path.

    Args:
        module_path: repo-relative or absolute path to the module directory.
        conn: graph DB connection.
        bundle: OKF bundle to write into (used for concept_id derivation).
        repo: optional repo name; if None, inferred from the path.
        llm_synthesize: optional callable(symbols, key_files, cross_deps) -> str body.
    """
    # 1. Find all symbols in the module path (path substring match).
    repo_filter = repo or _infer_repo(conn, module_path)
    symbols = _symbols_in_module(conn, module_path, repo_filter)

    # 2. Key files: rank by incoming edges (most-referenced = most important).
    key_files = _rank_key_files(conn, symbols, top=5)

    # 3. Cross-module dependencies.
    cross_deps = _cross_module_deps(conn, module_path, repo_filter)

    # 4. Quick commands (build commands inferred from gradle if available).
    quick_commands = _quick_commands(module_path, repo_filter)

    # 5. Synthesize body.
    if llm_synthesize:
        body = llm_synthesize(symbols, key_files, cross_deps, quick_commands)
    else:
        body = _template_body(symbols, key_files, cross_deps, quick_commands)

    title = _derive_title(module_path)
    concept_id = f"compass/{module_path.strip('/').replace('/', '-')}"
    tags = [repo_filter] if repo_filter else []
    tags += [t for t in module_path.split("/") if t]

    return OKFConcept(
        type="Compass",
        title=title,
        description=f"Navigation guide for {module_path}",
        resource=module_path,
        tags=tags[:6],
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        concept_id=concept_id,
        body=body,
    )


# --- helpers -------------------------------------------------------------

def _infer_repo(conn, module_path: str) -> Optional[str]:
    cur = conn.cursor()
    for r in cur.execute("SELECT id, path FROM repos"):
        # Match on repo id (stable basename) as a path segment of module_path.
        # repos.path is workspace-relative (e.g. "." or a repo name), so the id
        # is the durable identity.
        rid = r["id"]
        if module_path.startswith(rid + "/") or ("/" + rid + "/") in module_path \
                or module_path == rid:
            return rid
    # Fallback: single-repo workspace — there's only one repo.
    repos = cur.execute("SELECT id FROM repos").fetchall()
    if len(repos) == 1:
        return repos[0]["id"]
    return None


def _symbols_in_module(conn, module_path: str, repo: Optional[str]) -> List[dict]:
    cur = conn.cursor()
    q = """SELECT s.name, s.kind, s.qualified_name, s.line_start, f.path, f.repo_id
           FROM symbols s JOIN files f ON s.file_id = f.id
           WHERE f.path LIKE ? ESCAPE '\\'"""
    params: list = [f"%{_escape_like(module_path)}%"]
    if repo:
        q += " AND f.repo_id = ?"
        params.append(repo)
    rows = cur.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def _rank_key_files(conn, symbols: List[dict], top: int = 5) -> List[dict]:
    """Rank files by number of incoming edges (symbols defined there that are called)."""
    if not symbols:
        return []
    cur = conn.cursor()
    file_scores: dict[str, dict] = {}
    for s in symbols:
        # Count edges where this symbol is a target.
        n = cur.execute(
            "SELECT COUNT(*) AS c FROM edges WHERE target_id IN "
            "(SELECT id FROM symbols WHERE name = ?)",
            (s["name"],),
        ).fetchone()["c"]
        if s["path"] not in file_scores:
            file_scores[s["path"]] = {"path": s["path"], "repo": s["repo_id"], "score": 0, "symbols": []}
        file_scores[s["path"]]["score"] += n
        file_scores[s["path"]]["symbols"].append(s["name"])
    ranked = sorted(file_scores.values(), key=lambda x: -x["score"])
    return ranked[:top]


def _cross_module_deps(conn, module_path: str, repo: Optional[str]) -> List[str]:
    """Outgoing edges from this module's symbols to symbols in other modules."""
    cur = conn.cursor()
    mods = set()
    # files.path is repo-relative (portable), so target paths need no repo-root
    # stripping. We fetch repos.path only to strip it should an absolute path
    # appear (paths stored before the current contract).
    legacy_repo_root = ""
    if repo:
        row = cur.execute("SELECT path FROM repos WHERE id = ?", (repo,)).fetchone()
        if row:
            legacy_repo_root = row["path"]
    rows = cur.execute(
        """SELECT DISTINCT f2.path AS target_path, f2.repo_id AS target_repo
           FROM edges e
           JOIN symbols s1 ON e.source_id = s1.id
           JOIN files f1 ON s1.file_id = f1.id
           JOIN symbols s2 ON e.target_id = s2.id
           JOIN files f2 ON s2.file_id = f2.id
           WHERE f1.path LIKE ? ESCAPE '\\' AND f2.path NOT LIKE ? ESCAPE '\\'""",
        (f"%{_escape_like(module_path)}%", f"%{_escape_like(module_path)}%"),
    ).fetchall()
    for r in rows:
        rel = r["target_path"]
        # Strip an absolute repo root if present; repo-relative paths need none.
        if legacy_repo_root and rel.startswith(legacy_repo_root + "/"):
            rel = rel[len(legacy_repo_root) + 1:]
        # Skip build-output-like path noise; keep the top package dir.
        parts = [p for p in rel.split("/") if p and p not in ("src", "main", "java", "kotlin")]
        mod = f"{r['target_repo']}/" + "/".join(parts[:2]) if parts else r["target_repo"]
        mods.add(mod)
    return sorted(mods)[:8]


def _quick_commands(module_path: str, repo: Optional[str]) -> List[str]:
    cmds = []
    if repo:
        cmds.append(f"Build {repo}: `./gradlew :assembleDebug` (run in {repo}/)")
    cmds.append("Find callers: `cairn callers <symbol>`")
    return cmds


def _template_body(symbols, key_files, cross_deps, quick_commands, module_path=None) -> str:
    lines = ["# What Does This Module Do?"]
    if symbols:
        kinds: dict[str, int] = {}
        for s in symbols:
            kinds[s.get("kind", "?")] = kinds.get(s.get("kind", "?"), 0) + 1
        kind_summary = ", ".join(f"{k}({v})" for k, v in sorted(kinds.items(), key=lambda x: -x[1])[:5])
        lines.append(f"- {len(symbols)} symbols: {kind_summary}")
    else:
        lines.append("- (no symbols detected in this module)")
    lines.append("")
    lines.append("# Common Modification Patterns")
    if key_files:
        for kf in key_files:
            fname = kf["path"].split("/")[-1]
            sample_syms = kf.get("symbols", [])[:3]
            sample = ", ".join(sample_syms) if sample_syms else "various"
            lines.append(f"- `{fname}` - {kf.get('repo','?')} (refs: {kf.get('score',0)}; e.g. {sample})")
    else:
        lines.append("- (no files with incoming references detected)")
    lines.append("")
    lines.append("# Build-Failure Patterns")
    # Graph-derived heuristics.
    top_score = key_files[0].get("score", 0) if key_files else 0
    if top_score > 20:
        top = key_files[0]["path"].split("/")[-1]
        lines.append(f"- `{top}` is a high-traffic file ({top_score} incoming refs); "
                     "changes ripple widely — check `cairn impact` before modifying.")
    if cross_deps:
        lines.append(f"- This module reaches {len(cross_deps)} other modules; verify cross-module "
                     "impact before refactoring.")
    if len(symbols) > 50:
        lines.append(f"- Large module ({len(symbols)} symbols); consider splitting if adding features.")
    if not [l for l in lines if l.startswith("- ") and "high-traffic" not in l]:
        lines.append("- (run the critic pass with an LLM to surface deeper tribal knowledge)")
    lines.append("")
    lines.append("# Cross-Module Dependencies")
    for mod in cross_deps[:4]:
        lines.append(f"- {mod.split('/')[-1]} (`{mod}`)")
    if not cross_deps:
        lines.append("- (no cross-module references found)")
    lines.append("")
    lines.append("# Tribal Knowledge")
    lines.append("- (populated from code comments and TODOs when an LLM is available)")
    return "\n".join(lines) + "\n"


def _derive_title(module_path: str) -> str:
    parts = [p for p in module_path.strip("/").split("/") if p]
    return " / ".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else module_path)


# --- LLM-assisted generation with revise loop ---------------------------

def _gather_facts(conn: sqlite3.Connection, module_path: str, repo: Optional[str]) -> Dict[str, Any]:
    """Gather graph-grounded facts for a module. Single source of truth for synthesis."""
    repo = repo or _infer_repo(conn, module_path)
    symbols = _symbols_in_module(conn, module_path, repo)
    key_files = _rank_key_files(conn, symbols)
    cross_deps = _cross_module_deps(conn, module_path, repo)
    quick_commands = _quick_commands(module_path, repo)
    return {
        "resource": module_path,
        "module": module_path,
        "symbol_count": len(symbols),
        "key_files": [{"file": kf["path"].split("/")[-1], "refs": kf.get("score", 0)} for kf in key_files],
        "cross_deps": cross_deps,
        "quick_commands": quick_commands,
        "symbol_names": [s["name"] for s in symbols[:40]],
    }


def generate_compass_with_llm(
    module_path: str,
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    repo: Optional[str] = None,
    client: Optional[Any] = None,
) -> Dict[str, Any]:
    """Generate a compass via the generator->critic->revise loop.

    The LLM (via `client`) synthesizes prose from graph-grounded facts; the
    deterministic critic fact-checks after every pass and drives revision until
    facts are clean or MAX_REVISE_CYCLES is hit. Returns the final concept plus
    a trace of the loop.

    Args:
        client: an LLMClient (synthesize/revise). If None or unavailable, the
                function falls back to the deterministic generator.
    """
    # 1. Gather facts from the graph (single source of truth).
    facts = _gather_facts(conn, module_path, repo)

    # 2. If no client, fall back to deterministic generation.
    if client is None:
        concept = generate_compass(module_path, conn, bundle, repo=repo)
        return {"concept": concept, "mode": "deterministic", "cycles": 0, "fact_errors": []}

    # 3. Generate -> critic -> revise loop (bounded).
    from .critic import critic_concept

    trace = []
    draft = ""
    fact_errors: List[str] = []
    for cycle in range(MAX_REVISE_CYCLES + 1):
        if cycle == 0:
            draft = client.synthesize("compass-synthesize", facts)
        else:
            draft = client.revise("compass", draft, fact_errors, facts)
        if not draft:
            # Client unavailable/timed out -> deterministic fallback.
            concept = generate_compass(module_path, conn, bundle, repo=repo)
            return {"concept": concept, "mode": "deterministic-fallback",
                    "cycles": cycle, "fact_errors": ["llm unavailable"]}
        # Critic the draft.
        tmp = OKFConcept(type="Compass", resource=module_path, body=draft)
        result = critic_concept(tmp, conn)
        trace.append({"cycle": cycle, "errors": result.errors, "quality": result.quality_score})
        fact_errors = result.errors
        if not result.errors:
            break  # facts are clean; accept

    # 4. Build the final concept from the accepted draft.
    title = _derive_title(module_path)
    concept_id = f"compass/{module_path.strip('/').replace('/', '-')}"
    tags = ([repo] if repo else []) + [t for t in module_path.split("/") if t]
    concept = OKFConcept(
        type="Compass",
        title=title,
        description=f"Navigation guide for {module_path}",
        resource=module_path,
        tags=tags[:6],
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        concept_id=concept_id,
        body=draft,
    )
    return {
        "concept": concept,
        "mode": "llm",
        "cycles": len(trace),
        "fact_errors": fact_errors,
        "trace": trace,
    }


# ===========================================================================
# Flow compass: trace what happens when an entry point runs.
#
# A flow compass answers "what happens when X runs?" -- it traces the downward
# call chain from an entry point (HTTP handler, CLI command, Activity.onCreate,
# ...) across module boundaries and synthesizes a narrative of the business flow.
# ===========================================================================


def _repo_roots(conn: sqlite3.Connection) -> Dict[str, str]:
    """Map repo_id -> repo root path, for stripping to repo-relative paths."""
    cur = conn.cursor()
    return {row["id"]: row["path"] for row in cur.execute("SELECT id, path FROM repos")}


def _rel(path: str, repo: str, roots: Dict[str, str]) -> str:
    """Strip the repo root from an absolute file path -> repo-relative.

    Falls back to the original path if the repo root isn't known or doesn't
    match (e.g. the path is already relative).
    """
    root = roots.get(repo)
    if root and path.startswith(root + "/"):
        return path[len(root) + 1:]
    return path


def _gather_flow_facts(
    conn: sqlite3.Connection, entry: str, entry_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Gather graph-grounded facts for a flow trace. Single source of truth.

    Args:
        entry: the entry-point symbol name (human-readable label).
        entry_id: optional symbol database ID for collision-safe resolution
            (disambiguates multiple symbols sharing the same name).
    """
    flow = trace_flow(conn, entry, entry_id=entry_id)
    roots = _repo_roots(conn)

    # Build a readable indented chain string for the template / LLM prompt.
    chain_lines: List[str] = []
    for node in flow["chain"]:
        indent = "  " * node["depth"]
        fname = node["file"].split("/")[-1] if node["file"] else "?"
        chain_lines.append(
            f"{indent}- `{node['symbol']}` ({node['kind']}, {fname}:{node['repo']})"
        )

    return {
        "entry": entry,
        "mode": "flow",
        "total_steps": flow["total"],
        "truncated": flow["truncated"],
        "chain": chain_lines,
        # Store repo-relative file paths so the template emits clean paths
        # instead of machine-specific absolute paths.
        "chain_raw": [
            {**node, "file": _rel(node["file"], node["repo"], roots)}
            for node in flow["chain"]
        ],
        "branches": flow["branches"],
        "leaves": flow["leaves"],
        "modules": flow["modules"],
        "cycles": flow["cycles"],
    }


def _flow_template_body(facts: Dict[str, Any]) -> str:
    """Deterministic 5-section body for a flow compass."""
    entry = facts["entry"]
    chain = facts["chain"]
    branches = facts.get("branches", [])
    leaves = facts.get("leaves", [])
    modules = facts.get("modules", [])
    total = facts.get("total_steps", 0)

    lines = ["# What Does This Flow Do?"]
    lines.append(f"- Entry point: `{entry}`")
    lines.append(f"- Traces {total} call step(s) across {len(modules)} module(s).")
    if facts.get("truncated"):
        lines.append("- (trace truncated at the node limit; some branches omitted)")
    lines.append("")

    lines.append("# Call Sequence")
    if chain:
        lines.extend(chain)
    else:
        lines.append("- (no outgoing calls traced from the entry point)")
    lines.append("")

    lines.append("# Failure-Prone Steps")
    # Branch points = fan-out = more places to break under change.
    if branches:
        for b in branches[:5]:
            callees = ", ".join(f"`{c}`" for c in b["callees"][:4])
            lines.append(
                f"- `{b['symbol']}` branches to {callees} -- a change here "
                "ripples to multiple downstream paths."
            )
    # Leaves = terminal calls (DB writes, network, UI) -- side-effect surface.
    if leaves:
        sample = ", ".join(f"`{l}`" for l in leaves[:5])
        lines.append(f"- Terminal calls (side effects): {sample}.")
    if not branches and not leaves:
        lines.append("- (no branch points or terminal calls detected)")
    lines.append("")

    lines.append("# Modules Spanned")
    # List the distinct files touched, not synthetic "<repo>/<dir>" labels
    # (those look like file paths to the critic's file_exists check and fail).
    # Paths are repo-relative (stripped in _gather_flow_facts).
    seen_files: set[str] = set()
    for node in facts.get("chain_raw", []):
        f = node.get("file") or ""
        if f and f not in seen_files:
            seen_files.add(f)
    if seen_files:
        for f in sorted(seen_files)[:8]:
            fname = f.split("/")[-1]
            lines.append(f"- `{fname}` (`{f}`)")
    elif modules:
        # Fallback: no files resolved, show module labels without backticks
        # so the critic doesn't try to resolve them as paths.
        for mod in modules[:6]:
            lines.append(f"- {mod}")
    else:
        lines.append("- (single module)")
    lines.append("")

    lines.append("# Tribal Knowledge")
    if facts.get("cycles"):
        cyc = ", ".join(f"`{c['symbol']}`" for c in facts["cycles"][:3])
        lines.append(f"- Recursive/cyclic calls detected at: {cyc}.")
    lines.append("- (populated from code comments and TODOs when an LLM is available)")
    return "\n".join(lines) + "\n"


def generate_flow_compass(
    entry: str,
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    entry_id: Optional[str] = None,
    resource: Optional[str] = None,
    title: Optional[str] = None,
) -> OKFConcept:
    """Generate a flow compass OKF concept for an entry-point symbol.

    Traces the downward call chain from ``entry`` via :func:`trace_flow`,
    synthesizes a deterministic 5-section body from the traced facts, and
    returns the concept. The caller (CLI) runs the critic and persists.

    Args:
        entry: the entry-point symbol name (human-readable label).
        entry_id: optional symbol database ID for collision-safe resolution.
        resource: optional override for the concept's ``resource`` field.
            Defaults to ``entry``. For colliding names, pass a disambiguated
            key (e.g. ``handleCommand#ChatRoomViewModel.kt``) so coverage
            tracking in :func:`detect_flow_gaps` tracks each independently.
        title: optional override for the concept's ``title`` field. Defaults
            to ``Flow: {entry}``. For collisions, pass a qualified title.
    """
    facts = _gather_flow_facts(conn, entry, entry_id=entry_id)
    body = _flow_template_body(facts)

    # concept_id must be filesystem-safe and unique. For collisions, the
    # resource carries the disambiguator; sanitize it into the concept_id.
    safe_id = (resource or entry).replace("/", "-").replace(".", "-").replace("#", "-")
    concept_id = f"compass/flow-{safe_id}"
    return OKFConcept(
        type="Compass",
        title=title or f"Flow: {entry}",
        description=f"Execution flow traced from `{entry}`",
        resource=resource or entry,
        tags=["flow", entry.split(".")[-1]][:6],
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        concept_id=concept_id,
        body=body,
    )


def generate_flow_workflow(
    entry: str,
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    entry_id: Optional[str] = None,
    title: Optional[str] = None,
    max_steps: int = 20,
) -> str:
    """Generate a Knowledge-workflow from a flow trace.

    Turns the traced call chain (:func:`trace_flow`) into an ordered
    ``steps[]`` list via :func:`flow_to_workflow` and writes a
    ``Knowledge-workflow`` concept (``tier=asserted``, no critic gate). Returns
    the ``concept_id``.
    """
    from ..knowledge.workflow import add_workflow, flow_to_workflow

    facts = _gather_flow_facts(conn, entry, entry_id=entry_id)
    steps = flow_to_workflow(facts, max_steps=max_steps)
    display_title = title or f"Flow: {entry}"
    cid = add_workflow(
        bundle,
        title=display_title,
        steps=steps,
        resource=entry,
        tags=["flow", entry.split(".")[-1]],
    )
    return cid
