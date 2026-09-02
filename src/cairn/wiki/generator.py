"""Architecture reports (the deterministic, non-LLM path).

Produces a graph-derived architectural summary per repo -- OKF concepts of
type Architecture-Report at ``reports/architecture/{repo}``, built from
graph statistics (symbol-kind distribution, most-referenced classes,
cross-repo deps). Deterministic, no LLM call, no external process.

These are diagnostics, NOT wiki pages: they never pass through the
critic-gated promotion that ``Wiki-Article`` pages require and are
therefore excluded from the wiki layer's search surface (router and
ask_compass match the gated types only). They stay reachable via
unfiltered ``search_knowledge``.

Wiki bodies are critic-checked: the deterministic critic verifies
backtick-quoted file/symbol references against the graph. A body with broken
references carries ``errors``; by default the write still proceeds, but the
critic verdict is returned for transparency.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import List, Tuple

from ..compass.critic import CriticResult, critic_concept
from ..graph.queries import cross_repo_deps, get_stats
from ..okf.bundle import OKFBundle
from ..okf.concept import OKFConcept


def generate_wiki(repo: str, conn: sqlite3.Connection, bundle: OKFBundle) -> List[OKFConcept]:
    """Generate wiki concepts for a repo (graph-derived architecture summary)."""
    concepts, _ = generate_wiki_with_critic(repo, conn, bundle)
    return concepts


def generate_wiki_with_critic(
    repo: str, conn: sqlite3.Connection, bundle: OKFBundle
) -> Tuple[List[OKFConcept], List[CriticResult]]:
    """Generate wiki concepts and run the critic on each.

    Returns ``(concepts, critic_results)`` aligned by index. The critic is
    informational here; its verdict is returned so callers can surface
    warnings or block on hard errors.
    """
    concepts = _graph_derived_wiki(repo, conn, bundle)
    results = [critic_concept(c, conn) for c in concepts]
    return concepts, results


def _graph_derived_wiki(repo: str, conn: sqlite3.Connection, bundle: OKFBundle) -> List[OKFConcept]:
    """Produce a graph-derived architecture wiki for a repo."""
    cur = conn.cursor()
    stats = get_stats(conn)
    deps = cross_repo_deps(conn, repo)

    # Dominant patterns: count classes vs interfaces vs enums.
    by_kind = cur.execute(
        "SELECT s.kind, COUNT(*) AS c FROM symbols s JOIN files f ON s.file_id=f.id "
        "WHERE f.repo_id = ? GROUP BY s.kind ORDER BY c DESC",
        (repo,),
    ).fetchall()

    # Top classes by incoming edges (likely core abstractions).
    top_classes = cur.execute(
        """SELECT s.name, COUNT(e.id) AS incoming
           FROM symbols s
           LEFT JOIN edges e ON e.target_id = s.id
           JOIN files f ON s.file_id = f.id
           WHERE f.repo_id = ? AND s.kind IN ('class','interface')
           GROUP BY s.id ORDER BY incoming DESC LIMIT 10""",
        (repo,),
    ).fetchall()

    body_parts = [f"# {repo} Architecture\n"]
    body_parts.append("## Overview\n")
    body_parts.append(
        f"{repo} contains {stats['by_repo'].get(repo, 0)} symbols across "
        f"{cur.execute('SELECT COUNT(*) FROM files WHERE repo_id=?', (repo,)).fetchone()[0]} files.\n"
    )
    body_parts.append("\n## Symbol Distribution\n")
    for r in by_kind:
        body_parts.append(f"- {r['kind']}: {r['c']}")
    body_parts.append("\n## Core Abstractions (most-referenced classes)\n")
    for r in top_classes:
        body_parts.append(f"- `{r['name']}` ({r['incoming']} incoming references)")
    body_parts.append("\n## Cross-Repo Dependencies\n")
    if deps["dependencies"]:
        body_parts.append("Depends on:")
        for d in deps["dependencies"]:
            body_parts.append(f"- {d['repo']} ({d['evidence']}, x{d['count']})")
    else:
        body_parts.append("(no cross-repo dependencies detected)")
    body_parts.append("\n## Dependents\n")
    if deps["dependents"]:
        for d in deps["dependents"]:
            body_parts.append(f"- {d['repo']} (x{d['count']})")
    else:
        body_parts.append("(none)")

    body = "\n".join(body_parts) + "\n"
    concept = OKFConcept(
        type="Architecture-Report",
        title=f"{repo} Architecture",
        description=f"Graph-derived architectural overview of {repo}",
        resource=repo,
        tags=[repo, "architecture"],
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        concept_id=f"reports/architecture/{repo}",
        body=body,
    )
    return [concept]
