"""Test-symbol detection for impact analysis.

There is no ``test`` symbol kind in the graph: the ``symbols.kind`` domain is
only ``method | property | class | function | interface | enum | variable``.
Test functions are indexed as ordinary ``method``/``function`` symbols, so
test detection is heuristic, combining two signals:

  1. PATH — the symbol's file lives under a conventional test source root
     (``src/test/``, ``*Test.kt``, ``*_test.go``, ``test_*.py``, ``__tests__/``,
     ``*.spec.ts``, ``*Tests.java`` ...). This is the primary signal.
  2. NAME — the symbol's own name or qualified_name ends in ``Test``/``Spec``
     (e.g. ``LoginRepositoryTest``, ``PaymentServiceSpec``). A secondary signal
     for repos that colocate tests outside conventional roots.

A symbol is a test if EITHER signal fires. The ``detection_method`` field
records which, so a missed test is diagnosable rather than silent.
"""
from __future__ import annotations

import re
from typing import Dict, List

# Path patterns (matched as substrings, case-sensitive where language-conventional).
# Kept conservative: each must be a real test convention, not a word that
# appears in production paths. Order doesn't matter; a file is a test-file if
# ANY matches.
_TEST_PATH_PATTERNS = (
    "/src/test/",            # JVM/Gradle (Kotlin, Java)
    "/src/androidTest/",     # Android instrumentation tests
    "/test/",                # Python, Go, generic
    "/tests/",               # Node, Python, generic
    "/__tests__/",           # Jest convention
    "/spec/",                # RSpec/Jasmine convention
    "Test.kt",               # Kotlin free-standing test file
    "Test.java",             # JUnit file-naming
    "Tests.java",
    "_test.go",              # Go
    "_test.dart",            # Dart/Flutter
    "_test.py",              # Python pytest/unittest
    "test_",                 # Python pytest (file starts with test_)
    ".spec.ts",              # TypeScript/Jest
    ".spec.js",
    ".test.ts",              # TypeScript/Jest alternate
    ".test.js",
    "Test.swift",            # XCTest
    "Spec.swift",
)

# Name patterns: symbol name or qualified_name ENDS in Test/Spec/Tests/Specs.
# Anchored at the end of the final segment so ``LatestUpdate`` doesn't match.
_TEST_NAME_RE = re.compile(r"(Test|Spec|Tests|Specs)$")


def is_test_symbol(file_path: str, symbol_name: str = "", qualified_name: str = "") -> Dict:
    """Classify whether a symbol is a test.

    Returns ``{"is_test": bool, "detection_method": str}`` where
    ``detection_method`` is one of ``"path"``, ``"name"``, ``"path+name"``,
    or ``""`` (not a test). Designed to run as a cheap filter over an impact
    result set; the path check is a substring scan over ~20 patterns.
    """
    path_hit = bool(file_path) and any(p in file_path for p in _TEST_PATH_PATTERNS)
    # Avoid a false positive where a file is named `Test.kt` / `*Tests.java` but
    # lives under a production source root (`src/main/`, `src/release/`). A bare
    # `Test` filename alone is a weak signal; the path must corroborate it by
    # being outside mainline source roots. (Caught in audit 2026-07-30: 7 real
    # `src/main/.../Test.kt` files in customer-android would otherwise be
    # mislabeled as tests.)
    if path_hit and "/src/main/" in file_path:
        # Only the strong directory signals (src/test, src/androidTest, tests/,
        # __tests__, spec/) survive in a production root -- NOT filename-only
        # patterns like "Test.kt".
        strong = (
            "/src/test/", "/src/androidTest/", "/test/", "/tests/",
            "/__tests__/", "/spec/", "_test.go", "_test.dart", "_test.py",
            ".spec.ts", ".spec.js", ".test.ts", ".test.js",
        )
        if not any(p in file_path for p in strong):
            path_hit = False
    name_hit = False
    if symbol_name or qualified_name:
        # Check the final segment of the qualified_name first (most specific),
        # then the bare symbol name.
        final_seg = ""
        if qualified_name:
            final_seg = qualified_name.replace("/", ".").split(".")[-1]
        name_hit = bool(_TEST_NAME_RE.search(final_seg or symbol_name or ""))
    # The name signal (suffix Test/Spec) is weak on its own: A/B-test classes,
    # test-data builders, and utility classes named `Test` live in `src/main/`.
    # Demote it in production roots unless a strong directory signal also fires.
    # (Audit 2026-07-30: `PadBookingABTest` in src/main was a name-only false
    # positive after the path guard alone.)
    if name_hit and file_path and "/src/main/" in file_path and not path_hit:
        name_hit = False
    if path_hit and name_hit:
        method = "path+name"
    elif path_hit:
        method = "path"
    elif name_hit:
        method = "name"
    else:
        method = ""
    return {"is_test": bool(method), "detection_method": method}


def filter_tests(impacted: List[Dict]) -> List[Dict]:
    """Given an impact result list (entries with ``symbol``, ``file``, ``repo``,
    ``depth``), return the subset that are tests, each annotated with
    ``detection_method``. Non-test entries are excluded.
    """
    out: List[Dict] = []
    for r in impacted:
        cls = is_test_symbol(r.get("file", ""), r.get("symbol", ""), r.get("qualified_name", ""))
        if cls["is_test"]:
            entry = dict(r)
            entry["detection_method"] = cls["detection_method"]
            out.append(entry)
    return out
