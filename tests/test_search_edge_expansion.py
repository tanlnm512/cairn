"""Stored-edge related-expansion in search_knowledge (D1).

search_knowledge's cross-doc expansion reads the relationship index
(``knowledge_edges``, rebuilt after ingest) instead of recomputing
tag/module overlap at query time. Covers the validation-contract
behaviors: VAL-INGEST-012 (a stored extracted/derived edge boosts a
neighbor that shares no tags/modules, while an unconnected control doc
is not boosted) and VAL-INGEST-016 (inferred edges never boost -- the
low-trust rule).
"""
from __future__ import annotations

import sqlite3
import tempfile

import pytest

from cairn.graph.schema import _apply_schema
from cairn.knowledge.index import rebuild_knowledge_index
from cairn.knowledge.search import search_knowledge
from cairn.knowledge.store import add_document, update_status
from cairn.okf.bundle import OKFBundle

# Matches ONLY the parent doc's body tokens (three tokens -> the multi-token
# lexical path, the one path that expands related docs).
PARENT_QUERY = "chronoflux manifold tuning"


@pytest.fixture
def bundle():
    with tempfile.TemporaryDirectory() as tmp:
        yield OKFBundle(tmp)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    _apply_schema(c)
    yield c
    c.close()


def _add(bundle, title, body, *, tags=None, modules=None, relationships=None):
    return add_document(
        bundle,
        title=title,
        body=body,
        doc_type="spec",
        tags=tags or [],
        affects_modules=modules or [],
        relationships=relationships,
    )


def _rebuild(conn, bundle):
    rebuild_knowledge_index(conn, bundle)
    conn.commit()


def _results_by_id(conn, bundle, query, **kwargs):
    results = search_knowledge(conn, bundle, query, **kwargs)
    return {r["concept_id"]: r for r in results}


def _concept_ids(results):
    return [r["concept_id"] for r in results]


def _disjoint_trio(bundle):
    """Three docs with zero tag/module overlap: parent (matches
    PARENT_QUERY), an unconnected control, and a plain third doc."""
    _add(bundle, "Sediment Report", "quarterly sediment readings.", tags=["reports"])
    parent = _add(
        bundle, "Kessel Run Calibration", "chronoflux manifold tuning notes.",
        tags=["calibration"], modules=["nav"],
    )
    return parent


# --- VAL-INGEST-012: stored extracted edge boosts a disjoint neighbor ---


class TestExtractedEdgeExpansion:
    def test_stored_edge_boosts_neighbor_without_any_overlap(self, bundle, conn):
        neighbor = _add(
            bundle, "Hyperdrive Maintenance", "hyperdrive overhaul steps.",
            tags=["maintenance"],
        )
        parent = _add(
            bundle, "Kessel Run Calibration", "chronoflux manifold tuning notes.",
            tags=["calibration"], modules=["nav"],
            relationships=[{
                "concept_id": neighbor,
                "relation": "relates-to",
                "kind": "extracted",
            }],
        )
        control = _add(
            bundle, "Sediment Report", "quarterly sediment readings.",
            tags=["reports"],
        )
        _rebuild(conn, bundle)

        results = _results_by_id(conn, bundle, PARENT_QUERY)

        assert results[parent]["provenance"] == "lexical_knowledge"
        boosted = results[neighbor]
        assert boosted["provenance"] == "lexical_knowledge_expanded"
        # The expansion boost: >=50% of the parent's score (behavior-
        # compatible with the previous query-time overlap boost).
        assert boosted["score"] == pytest.approx(results[parent]["score"] * 0.5)
        # A control doc with no edges and no overlap is never boosted.
        assert control not in results
        # No neighbor surfaces twice.
        ids = _concept_ids(search_knowledge(conn, bundle, PARENT_QUERY))
        assert len(ids) == len(set(ids))

    def test_both_edge_directions_collapse_to_one_expansion(self, bundle, conn):
        """The index stores both directions of a derived pair; each neighbor
        still surfaces exactly once (the caller's already-expanded set only
        covers expansions of earlier parents, not within one parent)."""
        control = _add(
            bundle, "Sediment Report", "quarterly sediment readings.",
            tags=["fleet"],
        )
        neighbor = _add(
            bundle, "Hyperdrive Maintenance", "hyperdrive overhaul steps.",
            tags=["maintenance"],
        )
        parent = _add(
            bundle, "Kessel Run Calibration", "chronoflux manifold tuning notes.",
            tags=["fleet", "calibration"],
            relationships=[{
                "concept_id": neighbor,
                "relation": "relates-to",
                "kind": "extracted",
            }],
        )
        _rebuild(conn, bundle)

        # Sanity: the parent has edges in both directions (derived control
        # pair plus the declared extracted neighbor).
        parent_edges = conn.execute(
            "SELECT doc_id, related_id FROM knowledge_edges "
            "WHERE doc_id = ? OR related_id = ?",
            (parent, parent),
        ).fetchall()
        assert len(parent_edges) == 3

        results = search_knowledge(conn, bundle, PARENT_QUERY)
        ids = _concept_ids(results)
        assert ids.count(parent) == 1
        assert ids.count(neighbor) == 1
        assert ids.count(control) == 1
        expanded = {
            r["concept_id"] for r in results
            if r["provenance"] == "lexical_knowledge_expanded"
        }
        assert expanded == {neighbor, control}

    def test_low_score_parent_still_lifts_neighbor_to_the_floor(self, bundle, conn):
        """A minimal-scoring parent still promotes its neighbor at the 0.5
        floor (the same floor the query-time overlap formula carried)."""
        neighbor = _add(
            bundle, "Hyperdrive Maintenance", "hyperdrive overhaul steps.",
            tags=["maintenance"],
        )
        parent = _add(
            bundle, "Kessel Run Calibration", "chronoflux",
            tags=["calibration"],
            relationships=[{
                "concept_id": neighbor,
                "relation": "relates-to",
                "kind": "extracted",
            }],
        )
        _rebuild(conn, bundle)

        # Two tokens keeps the query on the multi-token path; only
        # "chronoflux" hits the parent, scoring it 1 (one body token).
        results = _results_by_id(conn, bundle, "chronoflux zzzq")
        assert results[parent]["score"] == 1
        assert results[neighbor]["score"] == 0.5
        assert results[neighbor]["provenance"] == "lexical_knowledge_expanded"

    def test_incoming_edge_direction_boosts_too(self, bundle, conn):
        parent = _add(
            bundle, "Kessel Run Calibration", "chronoflux manifold tuning notes.",
            tags=["calibration"],
        )
        neighbor = _add(
            bundle, "Hyperdrive Maintenance", "hyperdrive overhaul steps.",
            tags=["maintenance"],
            relationships=[{
                "concept_id": parent,
                "relation": "relates-to",
                "kind": "extracted",
            }],
        )
        _rebuild(conn, bundle)

        results = _results_by_id(conn, bundle, PARENT_QUERY)
        assert results[neighbor]["provenance"] == "lexical_knowledge_expanded"

    def test_archived_neighbor_still_respects_visibility(self, bundle, conn):
        neighbor = _add(
            bundle, "Hyperdrive Maintenance", "hyperdrive overhaul steps.",
            tags=["maintenance"],
        )
        parent = _add(
            bundle, "Kessel Run Calibration", "chronoflux manifold tuning notes.",
            tags=["calibration"],
            relationships=[{
                "concept_id": neighbor,
                "relation": "relates-to",
                "kind": "extracted",
            }],
        )
        update_status(bundle, neighbor, "archived")
        _rebuild(conn, bundle)

        results = _results_by_id(conn, bundle, PARENT_QUERY)
        assert neighbor not in results
        assert list(results) == [parent]

        expanded = _results_by_id(
            conn, bundle, PARENT_QUERY, include_archived=True
        )
        assert expanded[neighbor]["provenance"] == "lexical_knowledge_expanded"

    def test_self_matching_neighbor_not_duplicated_as_expansion(self, bundle, conn):
        neighbor = _add(
            bundle, "Hyperdrive Maintenance", "hyperdrive overhaul steps.",
            tags=["maintenance"],
        )
        _add(
            bundle, "Kessel Run Calibration", "chronoflux manifold tuning notes.",
            tags=["calibration"],
            relationships=[{
                "concept_id": neighbor,
                "relation": "relates-to",
                "kind": "extracted",
            }],
        )
        _rebuild(conn, bundle)

        results = search_knowledge(conn, bundle, "chronoflux manifold hyperdrive")
        neighbor_hits = [
            r for r in results if r["concept_id"] == neighbor
        ]
        assert len(neighbor_hits) == 1
        assert neighbor_hits[0]["provenance"] == "lexical_knowledge"


# --- VAL-INGEST-012: stored derived edge (tag overlap) boosts too ---


class TestDerivedEdgeExpansion:
    def test_derived_edge_boosts_tag_overlap_neighbor(self, bundle, conn):
        # parent + neighbor share the "fleet" tag but nothing else; the
        # overlap materializes as kind=derived edges at rebuild time.
        neighbor = _add(
            bundle, "Hyperdrive Maintenance", "hyperdrive overhaul steps.",
            tags=["fleet"],
        )
        parent = _add(
            bundle, "Kessel Run Calibration", "chronoflux manifold tuning notes.",
            tags=["fleet", "calibration"],
        )
        control = _add(
            bundle, "Sediment Report", "quarterly sediment readings.",
            tags=["reports"],
        )
        _rebuild(conn, bundle)

        stored_kinds = {
            r["kind"] for r in conn.execute(
                "SELECT DISTINCT kind FROM knowledge_edges"
            ).fetchall()
        }
        assert "derived" in stored_kinds

        results = _results_by_id(conn, bundle, PARENT_QUERY)
        assert results[neighbor]["provenance"] == "lexical_knowledge_expanded"
        assert results[neighbor]["score"] == pytest.approx(
            results[parent]["score"] * 0.5
        )
        assert control not in results
        # The derived pair is stored in both directions; the neighbor must
        # still surface exactly once.
        ids = _concept_ids(search_knowledge(conn, bundle, PARENT_QUERY))
        assert ids.count(neighbor) == 1

    def test_expansion_reads_stored_edges_not_query_time_overlap(self, bundle, conn):
        """Without a rebuilt index there is no expansion, even for docs
        sharing tags: expansion is driven by stored knowledge_edges."""
        _add(
            bundle, "Hyperdrive Maintenance", "hyperdrive overhaul steps.",
            tags=["fleet"],
        )
        parent = _add(
            bundle, "Kessel Run Calibration", "chronoflux manifold tuning notes.",
            tags=["fleet", "calibration"],
        )

        results = _results_by_id(conn, bundle, PARENT_QUERY)
        assert list(results) == [parent]


# --- VAL-INGEST-016: inferred edges never boost (low-trust rule) ---


class TestInferredEdgeExclusion:
    def test_inferred_only_neighbor_gets_no_expansion_boost(self, bundle, conn):
        neighbor = _add(
            bundle, "Hyperdrive Maintenance", "hyperdrive overhaul steps.",
            tags=["maintenance"],
        )
        parent = _add(
            bundle, "Kessel Run Calibration", "chronoflux manifold tuning notes.",
            tags=["calibration"],
            relationships=[{
                "concept_id": neighbor,
                "relation": "relates-to",
                "kind": "inferred",
            }],
        )
        control = _add(
            bundle, "Sediment Report", "quarterly sediment readings.",
            tags=["reports"],
        )
        _rebuild(conn, bundle)

        # Sanity: the inferred edge IS stored in the index.
        stored_kinds = {
            r["kind"] for r in conn.execute(
                "SELECT DISTINCT kind FROM knowledge_edges"
            ).fetchall()
        }
        assert stored_kinds == {"inferred"}

        results = _results_by_id(conn, bundle, PARENT_QUERY)
        # Searching terms matching only the parent does not elevate the
        # inferred-only neighbor: it stays absent alongside the control.
        assert list(results) == [parent]
        assert neighbor not in results
        assert control not in results
