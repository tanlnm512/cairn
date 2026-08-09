"""L2/L3 compass + router MCP tools: get_compass, search_knowledge,
ask_compass.

The bundle/OKF-read path that powers module navigation guides, knowledge-base
search, and the natural-language cross-layer router.
"""
from __future__ import annotations

from mcp.types import ToolAnnotations

from ._server_core import _bundle, _conn, _rw_conn, mcp
from .metric_buffering import instrument


def _critic_verdict_block(result) -> str:
    """A machine-readable critic verdict appended to a tool's prose response.

    Lets an agent parse the structured verdict (passed / errors / warnings /
    quality) without regex-ing the human-readable lines above. Additive: the
    prose response is unchanged; this block is always last and fenced.
    """
    import json
    verdict = {
        "passed": bool(result.passed),
        "quality_score": round(float(result.quality_score), 3),
        "errors": list(result.errors),
        "warnings": list(result.warnings),
    }
    return "```cairn-critic\n" + json.dumps(verdict, indent=2) + "\n```"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def get_compass(module: str) -> str:
    """Get the compass navigation guide for a module. Returns the OKF compass body."""
    bundle = _bundle()
    # Try to find a compass concept matching the module.
    for cid in bundle.list_concepts(prefix="compass/"):
        try:
            c = bundle.read_concept(cid)
            if module in (c.resource or "") or module in cid:
                return f"# {c.title}\n\n{c.body}"
        except Exception:
            continue
    return f"No compass file found for '{module}'. Generate with: cairn compass generate {module}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def search_knowledge(query: str, type_filter: str = "", limit: int = 10, full_body: bool = False) -> str:
    """Search the knowledge base (wiki, compass, patterns, memory). Results from
    bundle.search(), optionally filtered by concept type prefix.

    type_filter: '' (all), 'Wiki' (wiki articles), 'Pattern' (non-obvious patterns),
                 'Compass' (module guides), 'Memory' (past decisions).
    full_body: True returns the full concept body; False returns title + description only.
    """
    bundle = _bundle()
    results = bundle.search(query, limit=limit)
    if type_filter:
        results = [c for c in results if c.type.startswith(type_filter)]
    if not results:
        label = f" {type_filter}" if type_filter else ""
        return f"No{label} results matching '{query}'."
    if full_body:
        out = []
        for c in results:
            out.append(f"# {c.title}\n{c.body}")
        return "\n\n---\n\n".join(out)
    out = [f"{len(results)} results matching '{query}':"]
    for c in results:
        out.append(f"  {c.title} ({c.concept_id})")
        if c.description:
            out.append(f"    {c.description}")
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def ask_compass(query: str, file_path: str = "") -> str:
    """Natural language across all layers. Routes to graph/wiki/compass/memory.

    query: natural language question (e.g. 'where do we handle retries').
    file_path: optional source file path — when set, auto-loads compass +
               wiki + memory for that file (file-path-aware mode).

    For structural questions prefer explore() (pure L1, always concrete); use
    ask_compass when you want cross-layer context (wiki explanations, tribal
    knowledge, past decisions). The response names the layers it queried so you
    know what coverage it actually checked, and flags when every layer came up
    empty (thin coverage — drill down with a specific layer tool, don't assume
    'no info exists').

    Example:
        ask_compass("where do we handle retries")
        ->  Intent: call_graph (routed to ALL; queried: graph, wiki, memory)

            [graph]
              ...
            [wiki]
              Retry & backoff policy: ...
    """
    from cairn.compass.router import route_query

    bundle = _bundle()

    # File-path-aware mode: load compass + wiki + memory for a specific file.
    if file_path and not query:
        parts = [p for p in file_path.split("/") if p]
        module_guess = "/".join(parts[:4]) if len(parts) >= 4 else file_path
        out = [f"Context for {file_path}:", f"  inferred module: {module_guess}"]
        conn = _conn()
        loaded_compass_concept = None
        try:
            # Compass whose resource overlaps the path.
            for cid in bundle.list_concepts(prefix="compass/"):
                c = bundle.read_concept(cid)
                if c.resource and (c.resource in file_path or file_path in c.resource):
                    out.append(f"\n# Compass: {c.title}\n{c.body}")
                    loaded_compass_concept = c
                    break
        finally:
            # Keep conn open for the critic verdict below if a concept loaded.
            if loaded_compass_concept is None:
                conn.close()
        # If a compass concept was loaded, surface its critic verdict so the
        # caller knows whether the context they just got is graph-verified
        # (promise #2). The verdict is additive (appended after the concept body).
        if loaded_compass_concept is not None:
            try:
                from cairn.compass.critic import critic_concept
                out.append(_critic_verdict_block(critic_concept(loaded_compass_concept, conn)))
            except Exception:
                pass  # verdict is advisory; never block a context load
            finally:
                conn.close()
        # Wiki mentioning path segments.
        seg = parts[-1].replace(".kt", "").replace(".java", "") if parts else ""
        if seg:
            for c in bundle.search(seg, limit=3):
                if c.type.startswith("Wiki"):
                    out.append(f"\n# Wiki: {c.title}\n{c.body[:500]}...")
                    break
        # Memory for the path.
        if seg:
            for c in bundle.search(seg, limit=3):
                if c.concept_id.startswith("memory/"):
                    out.append(f"\n# Memory: {c.title}")
                    if c.description:
                        out.append(f"  {c.description}")
                    break
        return "\n".join(out)

    # Normal NL routing mode (with optional file-path context boost).
    conn = _conn()
    try:
        result = route_query(query, conn, bundle)
    finally:
        conn.close()
    layers_queried = result.get("layers_queried") or []
    out = [
        f"Intent: {result['intent']} (routed to {result['layer']}"
        + (f"; queried: {', '.join(layers_queried)}" if layers_queried else "")
        + ")"
    ]
    if result.get("degraded"):
        out.append("(Showing fallback results — targeted layer had no coverage.)")
    # Empty across ALL queried layers: say so explicitly, so an agent drills
    # down with a specific layer tool rather than concluding nothing exists.
    if result.get("empty"):
        out.append(
            "(No results from any layer — coverage for this query is thin. "
            "Don't conclude 'no info exists'; drill down with the specific "
            "layer tool: explore() for structure, search_knowledge() for docs, "
            "or recall_memory() for past decisions.)"
        )
    for layer, data in result["results"].items():
        out.append(f"\n[{layer}]")
        if isinstance(data, dict):
            for k, v in data.items():
                out.append(f"  {k}: {v}")
        elif isinstance(data, list):
            for item in data:
                out.append(f"  {item}")
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def trace_flow(entry: str, max_depth: int = 8) -> str:
    """Trace the downward call chain from an entry-point symbol.

    Returns the ordered call sequence (what happens when `entry` runs),
    branch points (fan-out), and terminal calls (side effects). Read-only.

    entry: the entry-point symbol name (HTTP handler, CLI command, ViewModel
           handleCommand, repository method, ...).
    max_depth: deepest call hop to follow (default 8).
    """
    from cairn.graph.traversal import trace_flow as _trace_flow

    conn = _conn()
    try:
        flow = _trace_flow(conn, entry, max_depth=max_depth)
    finally:
        conn.close()

    if flow["total"] <= 1:
        return f"No outgoing calls traced from '{entry}'."

    out = [f"Traced {flow['total']} step(s) from '{entry}' "
           f"({len(flow['branches'])} branches, {len(flow['leaves'])} terminals):"]
    for node in flow["chain"]:
        indent = "  " * node["depth"]
        out.append(f"{indent}- `{node['symbol']}` ({node['kind']})")
    if flow["branches"]:
        out.append("\nBranch points:")
        for b in flow["branches"][:8]:
            callees = ", ".join(f"`{c}`" for c in b["callees"][:4])
            out.append(f"  `{b['symbol']}` -> {callees}")
    if flow["leaves"]:
        out.append(f"\nTerminal calls: {', '.join(f'`{l}`' for l in flow['leaves'][:8])}")
    if flow["cycles"]:
        cyc = ", ".join(f"`{c['symbol']}`" for c in flow["cycles"][:3])
        out.append(f"\nCyclic calls: {cyc}")
    if flow["truncated"]:
        out.append("\n(trace truncated at node limit)")
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
@instrument
def generate_flow(entry: str, as_workflow: bool = False, max_steps: int = 20) -> str:
    """Generate a flow compass (and optionally a workflow) from a call-graph trace.

    Traces the downward call chain from `entry`, synthesizes a deterministic
    5-section compass body, runs the critic gate, and writes the concept.
    With as_workflow=True, also generates a Knowledge-workflow doc with the
    traced steps as ordered, editable procedural knowledge.

    entry: the entry-point symbol name.
    as_workflow: also generate a Knowledge-workflow doc (default False).
    max_steps: with as_workflow, cap workflow steps (default 20).
    """
    from cairn.compass.generator import (
        _gather_flow_facts, generate_flow_compass, generate_flow_workflow,
    )
    from cairn.compass.critic import critic_concept

    bundle = _bundle()
    conn = _rw_conn()
    try:
        facts = _gather_flow_facts(conn, entry)
        if facts["total_steps"] <= 1:
            return f"No outgoing calls traced from '{entry}' — nothing to document."

        results = []

        # Workflow (procedural knowledge) — no critic gate (tier=asserted).
        if as_workflow:
            cid = generate_flow_workflow(entry, conn, bundle, max_steps=max_steps)
            results.append(f"Workflow: {cid} ({max_steps} steps max)")

        # Compass (declarative) — critic-gated.
        concept = generate_flow_compass(entry, conn, bundle)
        result = critic_concept(concept, conn)
        if not result.passed:
            # Surface WHY the critic rejected it (broken file/symbol refs),
            # not just the count — an agent can act on the specific references.
            results.append(f"Compass REJECTED by critic (quality={result.quality_score:.2f}, "
                           f"{len(result.errors)} errors, {len(result.warnings)} warnings). "
                           "Workflow (if any) was still written.")
            for e in result.errors[:8]:
                results.append(f"  ERROR: {e}")
            for w in result.warnings[:4]:
                results.append(f"  warn: {w}")
            if result.errors:
                results.append("The body cited backtick references not found in the graph — "
                               "rebuild (cairn build) or fix the references before promoting.")
            results.append(_critic_verdict_block(result))
            return "\n".join(results)

        bundle.write_concept(concept)
        results.append(f"Compass: {concept.concept_id} (quality={result.quality_score:.2f})")
        results.append(_critic_verdict_block(result))
        return "\n".join(results)
    finally:
        conn.close()
