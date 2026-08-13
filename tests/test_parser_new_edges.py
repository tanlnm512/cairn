"""Regression tests for dropped call edges:

- Java field-initializer calls — ``field_declaration`` now descends via ``_walk``
  (previously returned, dropping e.g. ``Repo r = createRepo();``).
- Java constructor calls ``new Foo()`` — ``object_creation_expression`` handler
  (previously had no handler, so every ``new`` produced no edge).
- PHP constructor calls ``new Foo()`` — ``object_creation_expression`` added to
  ``_CALL_NODES`` (previously omitted).

Same edge-drop family as the TS var-declarator / ``public_field_definition``
fixes (see ``test_jsx_references.py``).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cairn.parsers.java import JavaParser
from cairn.parsers.php import PhpParser


def _parse(parser_cls, source: bytes, suffix: str):
    """Parse ``source`` with ``parser_cls`` via a temp file. Returns ParsedFile."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="wb") as f:
        f.write(source)
        path = f.name
    try:
        return parser_cls().parse(path)
    finally:
        Path(path).unlink(missing_ok=True)


def _call_targets(pf):
    """All `calls` edge target_names from a ParsedFile."""
    return [e.target_name for e in pf.edges if e.kind == "calls"]


class TestJavaNewAndFieldEdges:
    def test_field_initializer_call_emitted(self):
        src = b"class S { Repo r = createRepo(); }\n"
        pf = _parse(JavaParser, src, ".java")
        assert "createRepo" in _call_targets(pf)

    def test_new_expression_emits_constructor_call(self):
        src = b"class S { void f() { Foo x = new Foo(); } }\n"
        pf = _parse(JavaParser, src, ".java")
        assert "Foo" in _call_targets(pf)

    def test_qualified_new_uses_simple_name(self):
        src = b"class S { void f() { Object o = new com.example.Bar(); } }\n"
        pf = _parse(JavaParser, src, ".java")
        assert "Bar" in _call_targets(pf)


class TestPhpNewEdge:
    def test_new_expression_emits_constructor_call(self):
        src = b"<?php class S { function f() { $x = new Foo(); } }\n"
        pf = _parse(PhpParser, src, ".php")
        assert "Foo" in _call_targets(pf)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
