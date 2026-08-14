"""Semantic + lexical search over knowledge documents.

  1. Lexical (multi-token + cross-doc expansion) -- works without semantic extra
  2. Semantic fallback (cosine scan) -- when lexical empty
  3. Graph bridge -- cross_repo_deps for affects_repos matches
"""
from __future__ import annotations

import logging
from typing import Dict, List, Any

from cairn.graph import BASE_STOP_WORDS, simple_tokenize
from cairn.okf.bundle import OKFBundle

logger = logging.getLogger(__name__)

# Common English words filtered from query tokens before matching. Shared
# baseline defined in src/graph/tokenize.py.
_STOP_WORDS = BASE_STOP_WORDS

# Field weights for multi-token scoring.
_WEIGHT_TITLE = 10
_WEIGHT_DESCRIPTION = 5
_WEIGHT_TAGS = 5
_WEIGHT_BODY = 1


def _visible(concept, include_archived: bool) -> bool:
    """True unless the doc is archived and the caller didn't opt in.

    "superseded" docs still surface by default; only "archived" is excluded.
    """
    if include_archived:
        return True
    return concept.extensions.get("doc_status") != "archived"


def search_knowledge(
    conn,
    bundle: OKFBundle,
    query: str,
    limit: int = 20,
    threshold: float = 0.3,
    include_archived: bool = False,
) -> List[Dict[str, Any]]:
    """Search knowledge docs. Returns list of result dicts.

    Each result: {concept_id, title, doc_type, score, provenance,
                  affects_modules, affects_repos, chunk}

    Archived documents are excluded by default (see `_visible`); pass
    include_archived=True to see them anyway (e.g. an explicit audit).
    """
    # 1. Lexical search (substring across title/description/body/tags)
    results = _lexical_search(bundle, query, limit, include_archived)

    # 2. Semantic fallback when lexical finds nothing
    if not results:
        results = _semantic_search(conn, bundle, query, limit, threshold, include_archived)

    # 3. Graph bridge -- add cross_repo_deps for affects_repos
    for r in results:
        repos = r.get("affects_repos", [])
        if repos:
            r["graph_deps"] = _graph_bridge(conn, repos)

    return results


def _lexical_search(bundle, query, limit, include_archived=False):
    """Multi-token lexical search scoped to knowledge/ concepts.

    Tokenizes the query, scores each knowledge document per-token with field
    weighting (title > description/tags > body), then expands results via
    cross-doc linkage (shared affects_modules or tags).
    """
    # Tokenize: split on non-alphanumeric, filter stop words, keep >=3 chars.
    tokens = simple_tokenize(query, stop_words=_STOP_WORDS)

    # Fast path: if no tokens after filtering, fall back to original substring.
    if not tokens:
        return _lexical_search_substring(bundle, query, limit, include_archived)

    # Single token: use original substring search (cheaper, still effective).
    if len(tokens) == 1:
        return _lexical_search_substring(bundle, tokens[0], limit, include_archived)

    # Scan knowledge concepts only (bundle root contains memory/, compass/,
    # wiki/ etc — we only want knowledge/ prefixed concepts).
    knowledge_cids = bundle.list_concepts(prefix="knowledge/")
    scored = {}  # rel_id -> (score, concept)
    for cid in knowledge_cids:
        try:
            concept = bundle.read_concept(cid)
        except Exception:
            continue
        if not _visible(concept, include_archived):
            continue
        score = 0
        title_lower = (concept.title or "").lower()
        desc_lower = (concept.description or "").lower()
        body_lower = (concept.body or "").lower()
        tags_joined = " ".join(concept.tags).lower()
        for tok in tokens:
            if tok in title_lower:
                score += _WEIGHT_TITLE
            if tok in desc_lower:
                score += _WEIGHT_DESCRIPTION
            if tok in tags_joined:
                score += _WEIGHT_TAGS
            if tok in body_lower:
                score += _WEIGHT_BODY
        if score > 0:
            scored[cid] = (score, concept)

    # Rank by score descending.
    ranked = sorted(scored.items(), key=lambda x: -x[1][0])

    out = []
    expansion_budget = limit // 2
    expanded_ids = set()

    for cid, (score, concept) in ranked[:limit]:
        doc_type = cid.split("/")[1] if "/" in cid else "unknown"
        out.append({
            "concept_id": cid,
            "title": concept.title,
            "doc_type": doc_type,
            "score": score,
            "provenance": "lexical_knowledge",
            "affects_modules": concept.extensions.get("affects_modules", []),
            "affects_repos": concept.extensions.get("affects_repos", []),
            "chunk": (concept.description or "") + "\n" + (concept.body or "")[:200],
        })

        # Cross-doc expansion: find related docs sharing affects_modules or tags.
        if expansion_budget > 0:
            related = _find_related(
                bundle, knowledge_cids, concept, score, scored, expanded_ids, include_archived
            )
            for rel_cid, rel_score, rel_concept in related[:expansion_budget]:
                rel_doc_type = rel_cid.split("/")[1] if "/" in rel_cid else "unknown"
                out.append({
                    "concept_id": rel_cid,
                    "title": rel_concept.title,
                    "doc_type": rel_doc_type,
                    "score": rel_score,
                    "provenance": "lexical_knowledge_expanded",
                    "affects_modules": rel_concept.extensions.get("affects_modules", []),
                    "affects_repos": rel_concept.extensions.get("affects_repos", []),
                    "chunk": (rel_concept.description or "") + "\n" + (rel_concept.body or "")[:200],
                })
                expanded_ids.add(rel_cid)
                expansion_budget -= 1

    return out[:limit]


def _lexical_search_substring(bundle, query, limit, include_archived=False):
    """Original substring search (single-token or fallback path)."""
    knowledge_cids = set(bundle.list_concepts(prefix="knowledge/"))
    hits = bundle.search(query, limit=limit * 2)
    out = []
    for c in hits:
        rel_id = c.concept_id
        matched = False
        for kcid in knowledge_cids:
            if rel_id.endswith(kcid) or rel_id == kcid:
                rel_id = kcid
                matched = True
                break
        if not matched:
            continue
        if not _visible(c, include_archived):
            continue
        doc_type = rel_id.split("/")[1] if "/" in rel_id else "unknown"
        out.append({
            "concept_id": rel_id,
            "title": c.title,
            "doc_type": doc_type,
            "score": 1.0,
            "provenance": "lexical_knowledge",
            "affects_modules": c.extensions.get("affects_modules", []),
            "affects_repos": c.extensions.get("affects_repos", []),
            "chunk": (c.description or "") + "\n" + c.body[:200],
        })
    return out[:limit]


def _find_related(
    bundle, knowledge_cids, parent_concept, parent_score, scored, already_in_results,
    include_archived=False,
):
    """Find related knowledge docs sharing affects_modules or tags.

    Returns [(cid, score, concept), ...] sorted by relevance. Score is 50% of
    the parent's score, boosted if multiple fields overlap.
    """
    parent_modules = set(parent_concept.extensions.get("affects_modules", []))
    parent_tags = set(parent_concept.tags or [])
    if not parent_modules and not parent_tags:
        return []

    related = []
    for cid in knowledge_cids:
        if cid in already_in_results or cid in scored:
            continue
        try:
            concept = bundle.read_concept(cid)
        except Exception:
            continue
        if not _visible(concept, include_archived):
            continue
        c_modules = set(concept.extensions.get("affects_modules", []))
        c_tags = set(concept.tags or [])
        overlap = len(parent_modules & c_modules) + len(parent_tags & c_tags)
        if overlap > 0:
            score = max(parent_score * 0.5 + overlap, 0.5)
            related.append((cid, score, concept))

    related.sort(key=lambda x: -x[1])
    return related


def _semantic_search(conn, bundle, query, limit, threshold, include_archived=False):
    """Semantic cosine scan over knowledge_embeddings table.

    NO symbols/files JOIN (unlike queries.semantic_search).
    Reads concept metadata from the bundle, not from DB.

    Every degrade branch (backend unavailable, no knowledge embeddings, an
    unexpected error) records one ``semantic_unavailable`` signal on the
    'knowledge' surface (F4) -- previously this returned ``[]`` with no trace
    at all, so a silently-empty semantic path was indistinguishable from "no
    matches".
    """
    try:
        from cairn.graph import embeddings as emb
        if not emb.embeddings_available():
            _note_semantic_off("unavailable")
            return []
        if emb.embed_knowledge_count(conn) == 0:
            _note_semantic_off("no_embeddings")
            return []

        # Under the dep-free hash fallback the cosine signal is token-overlap
        # only; flag it once and annotate provenance so the caller can tell the
        # semantic results are degraded.
        emb.warn_hash_fallback_once(logger, context="search_knowledge")
        prov = "semantic_knowledge (hash backend)" if emb.is_hash_fallback() else "semantic_knowledge"

        model = emb.current_model(corpus="knowledge")
        q_blob, q_dim = emb.embed_query(query)

        # NO JOIN — just the embeddings table
        rows = conn.execute(
            "SELECT doc_id, vec, chunk, dim FROM knowledge_embeddings WHERE model = ?",
            (model,),
        ).fetchall()

        # Cosine scan — dual path (numpy or pure-Python). Deliberately brute-force:
        # the knowledge corpus is small and curated, so a full-table scan is
        # sub-millisecond and not worth a vec0 index (see graph/ann_index.py for
        # the ANN path, which covers only the code-corpus embeddings table).
        scored = _cosine_scan(rows, q_blob, q_dim, threshold)
        scored.sort(key=lambda x: -x[0])

        out = []
        # Iterate the full ranked list (not scored[:limit]) and stop once we
        # have `limit` visible results.
        for score, doc_id, chunk in scored:
            if len(out) >= limit:
                break
            try:
                concept = bundle.read_concept(doc_id)
            except Exception:
                continue
            if not _visible(concept, include_archived):
                continue
            doc_type = doc_id.split("/")[1] if "/" in doc_id else "unknown"
            out.append({
                "concept_id": doc_id,
                "title": concept.title,
                "doc_type": doc_type,
                "score": round(score, 4),
                "provenance": prov,
                "affects_modules": concept.extensions.get("affects_modules", []),
                "affects_repos": concept.extensions.get("affects_repos", []),
                "chunk": chunk,
            })
        return out
    except Exception:
        _note_semantic_off("error")
        return []  # never crash — mirrors promotion.py:138 pattern


def _note_semantic_off(reason: str) -> None:
    """Best-effort semantic_unavailable signal for the knowledge surface.

    Never raises (the degrade path must stay crash-proof); no-op when the
    telemetry package itself is unavailable.
    """
    try:
        from cairn.telemetry import note_semantic_unavailable

        note_semantic_unavailable("knowledge", reason)
    except Exception:
        logger.debug("knowledge semantic_unavailable emit failed", exc_info=True)


def _cosine_scan(rows, q_blob, q_dim, threshold):
    """Cosine similarity scan. Returns [(score, doc_id, chunk), ...].

    Thin adapter over the shared ``cairn.retrieval.cosine_scan``.
    """
    from cairn.retrieval import cosine_scan

    triples = [(r["vec"], r["dim"], (r["doc_id"], r["chunk"])) for r in rows]
    return [
        (score, doc_id, chunk)
        for score, (doc_id, chunk) in cosine_scan(q_blob, q_dim, triples, threshold)
    ]


def _graph_bridge(conn, repos):
    """Run cross_repo_deps for each repo in affects_repos. Returns dict."""
    from cairn.graph import cross_repo_deps
    out = {}
    for repo in repos:
        try:
            deps = cross_repo_deps(conn, repo)
            out[repo] = deps
        except Exception:
            out[repo] = {"error": "repo not found in graph"}
    return out
