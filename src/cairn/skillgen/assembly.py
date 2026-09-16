"""Assemble a skill draft from compass, ranked symbols, and memory (FR-001).

Symbol selection is centrality-ranked top-K (FR-002): ``rank_candidates``
orders the resolved candidates score desc then qualified name asc and drops
any whose definition does not resolve in the graph; ``top_k`` caps the
list. ``ranking_tier`` is the tier that scored the symbols
(``"closure"``/``"degree"``); ``"unranked"`` only when the resolution has
no candidates to rank.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..memory.promotion import search_memory
from ..okf.bundle import OKFBundle
from ..paths import default_knowledge_path
from .ranking import rank_candidates
from .selector import SelectorResolution

DEFAULT_TOP_K = 20
DEFAULT_TOP_N = 5

__all__ = ["DEFAULT_TOP_K", "DEFAULT_TOP_N", "SkillDraft", "assemble_draft"]


@dataclass
class SkillDraft:
    """Assembled skill draft consumed by the emit step.

    ``symbols`` are qualified names in ranked order (centrality score desc,
    qualified name asc); ``ranking_tier`` names the tier that produced that
    order; ``memories`` entries are each a memory title followed by its
    body; ``compass_body`` carries the compass concept's own headings.
    """

    module: str
    compass_body: str
    symbols: list[str]
    memories: list[str]
    ranking_tier: str


def assemble_draft(
    conn: sqlite3.Connection,
    resolution: SelectorResolution,
    top_k: int = DEFAULT_TOP_K,
    top_n: int = DEFAULT_TOP_N,
) -> SkillDraft:
    """Assemble compass body, ranked top-K symbols, and top-N memories for a resolution.

    Symbols come from ``rank_candidates`` capped by ``top_k``. Knowledge
    sections are optional content, not preconditions: a module with no
    compass concept or no memories assembles empty sections. Never raises
    for an empty or unmatched resolution — such resolutions yield an empty
    module, empty sections, and the ``unranked`` tier.
    """
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if top_n <= 0:
        raise ValueError("top_n must be positive")

    candidates = resolution.candidates
    if candidates:
        ranked = rank_candidates(conn, candidates)
        symbols = ranked.symbols[:top_k]
        tier = ranked.tier
    else:
        symbols = []
        tier = "unranked"
    module = _module_of(conn, candidates)
    bundle = OKFBundle(str(_knowledge_path(conn)))
    return SkillDraft(
        module=module,
        compass_body=_compass_body(bundle, module),
        symbols=symbols,
        memories=_memories(conn, bundle, module, symbols, top_n),
        ranking_tier=tier,
    )


def _module_of(
    conn: sqlite3.Connection, candidates: list[str]
) -> str:
    """Longest common repo-relative directory of the candidates' files.

    Empty when the candidates span repositories or share no directory
    (root-level files count as no directory).
    """
    repo_id = None
    prefix = None
    for qualified in candidates:
        rows = conn.execute(
            "SELECT DISTINCT f.repo_id, f.path FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE s.qualified_name = ? OR s.name = ? "
            "ORDER BY f.repo_id, f.path",
            (qualified, qualified),
        ).fetchall()
        for row in rows:
            if repo_id is None:
                repo_id = row["repo_id"]
            elif repo_id != row["repo_id"]:
                return ""
            dirs = row["path"].split("/")[:-1]
            prefix = dirs if prefix is None else _common_prefix(prefix, dirs)
    return "/".join(prefix or [])


def _common_prefix(a: list[str], b: list[str]) -> list[str]:
    out: list[str] = []
    for x, y in zip(a, b):
        if x != y:
            break
        out.append(x)
    return out


def _knowledge_path(conn: sqlite3.Connection) -> Path:
    """Knowledge dir for a graph connection: ``<db dir>/.knowledge``."""
    row = conn.execute("PRAGMA database_list").fetchone()
    db_file = row[2] if row is not None else ""
    if db_file:
        return Path(db_file).parent / ".knowledge"
    return default_knowledge_path()


def _compass_body(bundle: OKFBundle, module: str) -> str:
    """Body of the module's compass concept; '' when the module has none.

    Matching mirrors the reader behind ``get_compass``: the module matches a
    compass concept's resource or concept id.
    """
    if not module:
        return ""
    for cid in bundle.list_concepts(prefix="compass/"):
        try:
            concept = bundle.read_concept(cid)
        except Exception:
            continue
        if module in (concept.resource or "") or module in cid:
            return concept.body
    return ""


def _memories(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    module: str,
    symbols: list[str],
    top_n: int,
) -> list[str]:
    """Top-N memory entries (title + body) relevant to the module and symbols.

    Default tiers only — superseded memories stay hidden (``search_memory``
    defaults); called without a session so recall refs are not inflated.
    """
    query = " ".join([module, *(s.rsplit(".", 1)[-1] for s in symbols)]).strip()
    if not query:
        return []
    hits = search_memory(conn, bundle, query)
    entries = []
    for concept in hits[:top_n]:
        title = concept.title or ""
        entries.append(f"{title}\n\n{concept.body}".strip())
    return entries
