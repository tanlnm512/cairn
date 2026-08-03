"""Tests for import-aware resolution: package-qualified matching + type-scoped fallback.

Covers two related features of ``_import_aware_candidates`` (formerly split
across test_resolver_package_qualified.py and this file; merged 2026-07-31 to
deduplicate -- the two files tested the same function with 5 near-identical
cases):

1. M4 package-qualified matching: ``import com.example.RepoA`` resolves a call
   ``RepoA.create()`` to ``com.example.RepoA.create`` via contiguous-subsequence
   matching (not just ``qsegs[0]`` equality).
2. f22 type-scoped fallback: when the qname is type-scoped (``RepoA.create``)
   but the import is package-qualified, contiguous matching fails; a fallback
   matches just the last segment of the import tail. Lower confidence than a
   full contiguous match (see test_full_contiguous_match_scores_higher...).
"""
from __future__ import annotations

from codegraph.graph.resolver import _import_aware_candidates


def test_package_qualified_resolves_via_contiguous_subsequence():
    """M4: ``import com.example.RepoA`` resolves ``RepoA.create()`` to
    ``com.example.RepoA.create`` -- 'RepoA' (last import segment) aligns with
    qsegs[0] of the candidate qname."""
    my_imports = ["com.example.RepoA"]
    cands = [("sid1", "repoA", "file1", "com.example.RepoA.create")]
    result = _import_aware_candidates("create", my_imports, cands)
    assert len(result) == 1
    assert result[0][0] == "sid1"


def test_nested_package_qualified_resolves():
    """M4: deeply nested package-qualified imports resolve."""
    my_imports = ["com.example.lib.ApiFactory"]
    cands = [("sid1", "repoA", "file1", "com.example.lib.ApiFactory.create")]
    result = _import_aware_candidates("create", my_imports, cands)
    assert len(result) == 1
    assert result[0][0] == "sid1"


def test_type_scoped_qname_with_package_qualified_import():
    """Type-scoped qname with package-qualified import resolves via last-segment fallback.

    Given:
    - import com.example.RepoA (full import path with package segments)
    - Candidate: RepoA.create (type-scoped qualified_name, no package segments)
    - target_name: create (extracted from call site)

    Expected: candidate should match via last-segment fallback where 'RepoA'
    (last segment of import tail) matches qsegs[0] of 'RepoA.create'.

    This currently fails (scores 0) because the contiguous-subsequence match
    looks for ['com', 'example', 'RepoA'] in ['RepoA', 'create'], which never aligns.
    """
    # Simulating: import com.example.RepoA
    my_imports = ["com.example.RepoA"]

    # Candidate: (symbol_id, repo, file_id, qualified_name)
    # Note: qname is type-scoped (RepoA.create) without package segments
    cands = [
        ("sid1", "repoA", "file1", "RepoA.create"),
    ]

    target_name = "create"

    result = _import_aware_candidates(target_name, my_imports, cands)

    # Should match via last-segment fallback
    # Before fix: len(result) == 0 (no match)
    # After fix: len(result) == 1 (matches via fallback)
    assert len(result) == 1
    assert result[0][0] == "sid1"


def test_full_contiguous_match_scores_higher_than_last_segment_fallback():
    """Full contiguous matches score higher than last-segment fallback.

    Given:
    - import com.example.RepoA
    - Candidate1: com.example.RepoA.create (package-qualified, full contiguous match)
    - Candidate2: RepoA.create (type-scoped, last-segment fallback)

    Expected: Candidate1 should win (higher confidence).
    """
    my_imports = ["com.example.RepoA"]
    cands = [
        ("sid1", "repoA", "file1", "com.example.RepoA.create"),
        ("sid2", "repoA", "file1", "RepoA.create"),
    ]
    target_name = "create"

    result = _import_aware_candidates(target_name, my_imports, cands)

    # Should have exactly one winner (no ambiguity)
    assert len(result) == 1
    # The package-qualified candidate should win (full contiguous match)
    assert result[0][0] == "sid1"


def test_single_segment_import_still_works():
    """Single-segment imports (no package) still work at original confidence.

    Given:
    - import RepoA (simple import, no package segments)
    - Candidate: RepoA.create

    Expected: should match at original confidence (not via fallback).
    """
    my_imports = ["RepoA"]
    cands = [
        ("sid1", "repoA", "file1", "RepoA.create"),
    ]
    target_name = "create"

    result = _import_aware_candidates(target_name, my_imports, cands)

    assert len(result) == 1
    assert result[0][0] == "sid1"


def test_deeply_nested_package_qualified_import_with_type_scoped_qname():
    """Deeply nested package-qualified import with type-scoped qname.

    Given:
    - import com.example.lib.utils.ApiFactory (deeply nested)
    - Candidate: ApiFactory.create (type-scoped)

    Expected: should match via last-segment fallback ('ApiFactory' matches qsegs[0]).
    """
    my_imports = ["com.example.lib.utils.ApiFactory"]
    cands = [
        ("sid1", "repoA", "file1", "ApiFactory.create"),
    ]
    target_name = "create"

    result = _import_aware_candidates(target_name, my_imports, cands)

    assert len(result) == 1
    assert result[0][0] == "sid1"


def test_last_segment_fallback_does_not_match_unrelated_import():
    """Last-segment fallback should not match unrelated imports.

    Given:
    - import com.example.OtherRepo
    - Candidate: RepoA.create

    Expected: no match because 'OtherRepo' != 'RepoA'.
    """
    my_imports = ["com.example.OtherRepo"]
    cands = [
        ("sid1", "repoA", "file1", "RepoA.create"),
    ]
    target_name = "create"

    result = _import_aware_candidates(target_name, my_imports, cands)

    assert len(result) == 0


def test_direct_import_pattern_unaffected():
    """DIRECT import pattern (symbol imported as-is) should be unaffected.

    Given:
    - import com.example.RepoA
    - Candidate: com.example.RepoA (the type itself)
    - target_name: RepoA

    Expected: should match via suffix pattern (entire qname matches import),
    not via the new fallback.
    """
    my_imports = ["com.example.RepoA"]
    cands = [
        ("sid1", "repoA", "file1", "com.example.RepoA"),
    ]
    target_name = "RepoA"

    result = _import_aware_candidates(target_name, my_imports, cands)

    assert len(result) == 1
    assert result[0][0] == "sid1"
