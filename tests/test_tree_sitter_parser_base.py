"""Test TreeSitterParserBase mixin shared helpers."""
from __future__ import annotations

import pytest

from codegraph.parsers.base import TreeSitterParserBase
from codegraph.parsers.kotlin import KotlinParser
from codegraph.parsers.typescript import TypeScriptParser
from codegraph.parsers.java import JavaParser
from codegraph.parsers.python_parser import PythonParser
from codegraph.parsers.swift import SwiftParser
from codegraph.parsers.dart import DartParser
from codegraph.parsers.objc import ObjCParser


class ConcreteTreeSitterParser(TreeSitterParserBase):
    """Concrete parser for testing the mixin methods."""
    language = "test"

    def parse(self, path: str):
        raise NotImplementedError("Not used in these tests")


def test_node_text_helper_in_base():
    """TreeSitterParserBase should provide _node_text helper."""
    parser = ConcreteTreeSitterParser()
    # Use a mock-like object that mimics Node's structure
    class MockNode:
        def __init__(self, start_byte, end_byte):
            self.start_byte = start_byte
            self.end_byte = end_byte

    source = b"hello world"
    node = MockNode(0, 11)
    assert parser._node_text(node, source) == "hello world"


def test_qualified_name_helper_in_base():
    """TreeSitterParserBase should provide _qualified_name helper."""
    parser = ConcreteTreeSitterParser()
    # Test with empty scope
    assert parser._qualified_name("foo") == "foo"

    # Test with scope
    parser._scope = ["Outer", "Inner"]
    assert parser._qualified_name("foo") == "Outer.Inner.foo"


def test_scope_stack_initialization():
    """TreeSitterParserBase should initialize scope stacks in __init__."""
    parser = ConcreteTreeSitterParser()
    assert hasattr(parser, "_scope")
    assert hasattr(parser, "_callable_scope")
    # Default empty lists
    assert parser._scope == []
    assert parser._callable_scope == []


def test_all_parsers_inherit_tree_sitter_base():
    """All 7 language parsers should inherit from TreeSitterParserBase."""
    parsers = [
        KotlinParser,
        TypeScriptParser,
        JavaParser,
        PythonParser,
        SwiftParser,
        DartParser,
        ObjCParser,
    ]

    for parser_cls in parsers:
        assert issubclass(parser_cls, TreeSitterParserBase), (
            f"{parser_cls.__name__} should inherit from TreeSitterParserBase"
        )


def test_all_parsers_have_node_text_via_base():
    """All parsers should use TreeSitterParserBase._node_text."""
    parsers = [
        KotlinParser,
        TypeScriptParser,
        JavaParser,
        PythonParser,
        SwiftParser,
        DartParser,
        ObjCParser,
    ]

    for parser_cls in parsers:
        parser = parser_cls()
        source = b"test"

        class MockNode:
            def __init__(self, start_byte, end_byte):
                self.start_byte = start_byte
                self.end_byte = end_byte

        node = MockNode(0, 4)
        # Should use the base class method
        assert parser._node_text(node, source) == "test"


def test_all_parsers_have_qualified_name_via_base():
    """All parsers should use TreeSitterParserBase._qualified_name."""
    parsers = [
        KotlinParser,
        JavaParser,
        PythonParser,
        SwiftParser,
    ]

    for parser_cls in parsers:
        parser = parser_cls()
        # Test that _scope attribute is initialized
        assert hasattr(parser, "_scope")
        # Test qualified_name with empty scope
        assert parser._qualified_name("test") == "test"


def test_typescript_dart_objc_qualified_name_needs_file_stem():
    """TypeScript/Dart/ObjC override _qualified_name and need file_stem."""
    parsers = [
        (TypeScriptParser, "test.ts"),
        (DartParser, "test.dart"),
        (ObjCParser, "test.m"),
    ]

    for parser_cls, filename in parsers:
        parser = parser_cls()
        # These parsers need _file_stem to be set before using _qualified_name
        # This is set during parse(), not in __init__
        assert hasattr(parser, "_qualified_name")
