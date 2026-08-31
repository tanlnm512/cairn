"""Deterministic wiki catalog planner.

Turns the code graph into an ordered page plan: an overview page first,
then one page per module. A module is a path-prefix bucket of
``files.path`` (same bucketing as ``graph.stats.group_by_top_level``);
modules are ranked by cross-module incoming edge degree (degree DESC,
module name ASC) so a large self-referential module cannot win. Each page
record carries ``page_id``, ``title``, ``description``, ``module``,
``seeds`` (graph-grounded ``files``/``symbols``), and ``input_hash``
(sha256 over the canonical JSON of the record without the hash) so
regeneration can skip unchanged pages. Pure reads over the graph DB --
stdlib only, no LLM call, no writes.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any, Dict, List

_SEED_LIMIT = 10


class WikiPlannerError(Exception):
    """Raised when the graph cannot yield a page plan (nothing indexed)."""


def _module_of(path: str, repo_root: str) -> str:
    """Bucket a file path into its module (first 2-3 path segments).

    Mirrors ``graph.stats.group_by_top_level`` exactly, including the
    strip of legacy absolute paths (rows written before paths became
    repo-relative).
    """
    if repo_root and path.startswith(repo_root + "/"):
        path = path[len(repo_root) + 1:]
    parts = path.split("/")
    return "/".join(parts[:3]) if len(parts) >= 3 else parts[0]


def _like_under_prefix(prefix: str) -> str:
    """LIKE pattern matching paths strictly under ``prefix`` (wildcards escaped)."""
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "/%"


def _under_prefix(alias: str) -> str:
    """SQL predicate: ``alias``.path is the prefix itself or lies under it."""
    return f"({alias}.path = ? OR {alias}.path LIKE ? ESCAPE '\\')"


def _module_incoming_degree(cur: sqlite3.Cursor, repo: str, prefix: str) -> int:
    """Count edges entering ``prefix`` from outside it (cross-module only)."""
    like = _like_under_prefix(prefix)
    row = cur.execute(
        f"""SELECT COUNT(e.id) AS incoming
           FROM edges e
           JOIN symbols ts ON e.target_id = ts.id
           JOIN files tf ON ts.file_id = tf.id
           JOIN symbols ss ON e.source_id = ss.id
           JOIN files sf ON ss.file_id = sf.id
           WHERE tf.repo_id = ?
             AND {_under_prefix('tf')}
             AND NOT {_under_prefix('sf')}""",
        (repo, prefix, like, prefix, like),
    ).fetchone()
    return row["incoming"]


def _module_files(cur: sqlite3.Cursor, repo: str, prefix: str) -> List[str]:
    """All indexed file paths under ``prefix``, in path order."""
    like = _like_under_prefix(prefix)
    rows = cur.execute(
        f"SELECT f.path FROM files f WHERE f.repo_id = ? AND {_under_prefix('f')} "
        "ORDER BY f.path",
        (repo, prefix, like),
    ).fetchall()
    return [r["path"] for r in rows]


def _top_symbols(cur: sqlite3.Cursor, repo: str, prefix: str = "") -> List[str]:
    """Top symbol names by incoming edge count (generator's JOIN).

    Scoped to ``prefix`` when given, repo-wide otherwise; LEFT JOIN keeps
    zero-incoming symbols in the seeds.
    """
    sql = """SELECT s.name, COUNT(e.id) AS incoming
           FROM symbols s
           LEFT JOIN edges e ON e.target_id = s.id
           JOIN files f ON s.file_id = f.id
           WHERE f.repo_id = ?"""
    params: List[Any] = [repo]
    if prefix:
        sql += f" AND {_under_prefix('f')}"
        params += [prefix, _like_under_prefix(prefix)]
    sql += " GROUP BY s.id ORDER BY incoming DESC, s.name ASC LIMIT ?"
    params.append(_SEED_LIMIT)
    return [r["name"] for r in cur.execute(sql, params).fetchall()]


def _top_files(cur: sqlite3.Cursor, repo: str) -> List[str]:
    """Repo-wide seed files: most symbols first, path order breaking ties."""
    rows = cur.execute(
        """SELECT f.path, COUNT(s.id) AS symbols
           FROM files f LEFT JOIN symbols s ON s.file_id = f.id
           WHERE f.repo_id = ? GROUP BY f.id
           ORDER BY symbols DESC, f.path ASC LIMIT ?""",
        (repo, _SEED_LIMIT),
    ).fetchall()
    return [r["path"] for r in rows]


def _slug(module: str) -> str:
    """Filesystem-safe page id: lowercase, non-alphanumeric runs to '-', stripped."""
    return re.sub(r"[^a-z0-9]+", "-", module.lower()).strip("-")


def _page(
    page_id: str, title: str, description: str, module: str, seeds: Dict[str, List[str]]
) -> Dict[str, Any]:
    """Build one page record; ``input_hash`` covers the entry without itself."""
    entry: Dict[str, Any] = {
        "page_id": page_id,
        "title": title,
        "description": description,
        "module": module,
        "seeds": seeds,
    }
    canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    entry["input_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return entry


def build_page_plan(
    conn: sqlite3.Connection, repo: str, pages_cap: int = 10
) -> List[Dict[str, Any]]:
    """Plan the wiki pages for ``repo`` from the graph.

    Returns ordered page records: the overview page first, then modules
    ranked by cross-module incoming edge degree DESC (module name ASC on
    ties), capped at ``pages_cap`` records including the overview. Each
    module page's ``seeds`` name exactly that module's file paths and its
    top symbols by incoming degree. Raises ``WikiPlannerError`` when the
    repo has no indexed files.
    """
    cur = conn.cursor()
    repo_row = cur.execute("SELECT path FROM repos WHERE id = ?", (repo,)).fetchone()
    repo_root = repo_row["path"] if repo_row else ""

    paths = [
        r["path"]
        for r in cur.execute(
            "SELECT path FROM files WHERE repo_id = ?", (repo,)
        ).fetchall()
    ]
    if not paths:
        raise WikiPlannerError(
            f"no indexed files for repo '{repo}'; run 'cairn build' first"
        )

    module_files: Dict[str, List[str]] = {}
    for p in paths:
        module_files.setdefault(_module_of(p, repo_root), []).append(p)
    for members in module_files.values():
        members.sort()

    degrees = {
        module: _module_incoming_degree(cur, repo, module) for module in module_files
    }
    ranked = sorted(module_files, key=lambda m: (-degrees[m], m))

    plan = [
        _page(
            page_id="overview",
            title=f"{repo} architecture overview",
            description=f"Architecture overview of {repo} across {len(ranked)} modules.",
            module="",
            seeds={"files": _top_files(cur, repo), "symbols": _top_symbols(cur, repo)},
        )
    ]
    for module in ranked[: max(pages_cap - 1, 0)]:
        plan.append(
            _page(
                page_id=_slug(module),
                title=module,
                description=(
                    f"Module {module}: {len(module_files[module])} indexed files."
                ),
                module=module,
                seeds={
                    "files": module_files[module],
                    "symbols": _top_symbols(cur, repo, prefix=module),
                },
            )
        )
    return plan
