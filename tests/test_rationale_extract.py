"""Rationale extraction: per-language comment-node map and marker matching."""

from __future__ import annotations

from typing import List

import pytest

from cairn.parsers import _registry
from cairn.parsers._rationale import COMMENT_NODE_TYPES, extract_rationale
from cairn.parsers.factory import get_parser as get_adapter


def _records(language: str, source: str) -> List:
    data = source.encode("utf-8")
    tree = _registry.get_parser(language).parse(data)
    node_types = COMMENT_NODE_TYPES[language]
    found = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in node_types:
            record = extract_rationale(node, data)
            if record is not None:
                found.append(record)
        stack.extend(node.children)
    return found


# (source, expected line, kind, text) -- line-comment form, one per mapped language.
_LINE_FIXTURES = {
    "c": ("// WHY: ABI lock\nint f(void) { return 0; }\n", 1, "why", "ABI lock"),
    "cpp": ("// NOTE: pimpl boundary\nclass A {};\n", 1, "note", "pimpl boundary"),
    "csharp": (
        "class A {\n    // WHY: framework contract\n    void F() {}\n}\n",
        2,
        "why",
        "framework contract",
    ),
    "dart": ("// WHY: platform channel\nvoid f() {}\n", 1, "why", "platform channel"),
    "go": (
        "package main\n\n// WHY: ordering is load-bearing\nvar X = 1\n",
        3,
        "why",
        "ordering is load-bearing",
    ),
    "java": ("class A {\n    // WHY: sync guard\n    void f() {}\n}\n", 2, "why", "sync guard"),
    "javascript": ("// WHY: event ordering\nexport const x = 1;\n", 1, "why", "event ordering"),
    "kotlin": (
        "class A {\n    // NOTE: pinned by contract\n    fun f() {}\n}\n",
        2,
        "note",
        "pinned by contract",
    ),
    "objc": ("// NOTE: retain cycle guard\nint f(void) { return 0; }\n", 1, "note", "retain cycle guard"),
    "php": ("<?php\n// HACK: session singleton\n$f = 1;\n", 2, "hack", "session singleton"),
    "python": ("# NOTE: keep sorted\nx = 1\n", 1, "note", "keep sorted"),
    "ruby": ("# NOTE: memoized on purpose\ndef f\n  1\nend\n", 1, "note", "memoized on purpose"),
    "rust": ("// HACK: shim for upstream bug\nfn f() {}\n", 1, "hack", "shim for upstream bug"),
    "swift": ("// HACK: bridge shim\nfunc f() {}\n", 1, "hack", "bridge shim"),
    "tsx": ("// NOTE: loader order\nexport const x = 1;\n", 1, "note", "loader order"),
    "typescript": ("// NOTE: loader order\nexport const x = 1;\n", 1, "note", "loader order"),
}


@pytest.mark.parametrize("language", sorted(_LINE_FIXTURES))
def test_line_comment_marker_extracts(language: str) -> None:
    source, line, kind, text = _LINE_FIXTURES[language]
    records = _records(language, source)
    assert [(r.line, r.kind, r.text) for r in records] == [(line, kind, text)]


# (source, expected line, kind, text) -- block-comment form, continuation folded.
# Ruby's block form is =begin/=end and never matches; pinned separately below.
_BLOCK_FIXTURES = {
    "c": ("/* WHY: kernel style\n   multi line */\nint f(void) { return 0; }\n", 1, "why", "kernel style multi line"),
    "cpp": ("/* NOTE: pimpl detail\n   folded */\nclass A {};\n", 1, "note", "pimpl detail folded"),
    "csharp": (
        "class A {\n    /*\n    WHY: xml free block\n    still folds\n    */\n    void F() {}\n}\n",
        2,
        "why",
        "xml free block still folds",
    ),
    "dart": ("/*\nNOTE: dartdoc block\nfolds too\n*/\nvoid f() {}\n", 1, "note", "dartdoc block folds too"),
    "go": (
        "package main\n\n/*\nWHY: block rationale\n   spread over lines\n*/\nvar X = 1\n",
        3,
        "why",
        "block rationale spread over lines",
    ),
    "java": (
        "class A {\n    /**\n     * NOTE: javadoc style\n     * continuation\n     */\n    void f() {}\n}\n",
        2,
        "note",
        "javadoc style continuation",
    ),
    "javascript": ("/*\nWHY: js block\nfolds\n*/\nexport const x = 1;\n", 1, "why", "js block folds"),
    "kotlin": (
        "class A {\n    /* NOTE: kdoc adjacent\n       folds here */\n    fun f() {}\n}\n",
        2,
        "note",
        "kdoc adjacent folds here",
    ),
    "objc": ("/* NOTE: header note\n   continues */\nint f(void) { return 0; }\n", 1, "note", "header note continues"),
    "php": ("<?php\n/* NOTE: php block\n   comment */\n$f = 1;\n", 2, "note", "php block comment"),
    "rust": ("/* HACK: block\n   continuation here */\nfn f() {}\n", 1, "hack", "block continuation here"),
    "swift": ("/* HACK: swift block\n   multiline node */\nfunc f() {}\n", 1, "hack", "swift block multiline node"),
    "tsx": ("/*\nHACK: tsx block\nfolds\n*/\nexport const x = 1;\n", 1, "hack", "tsx block folds"),
    "typescript": ("/*\nHACK: ts block\nfolds\n*/\nexport const x = 1;\n", 1, "hack", "ts block folds"),
}


@pytest.mark.parametrize("language", sorted(_BLOCK_FIXTURES))
def test_block_comment_marker_folds_into_one_record(language: str) -> None:
    source, line, kind, text = _BLOCK_FIXTURES[language]
    records = _records(language, source)
    assert [(r.line, r.kind, r.text) for r in records] == [(line, kind, text)]


def test_ruby_begin_block_yields_no_records() -> None:
    source = "=begin\nNOTE: legacy block form\n=end\nx = 1\n"
    assert _records("ruby", source) == []


def test_docstring_produces_no_records() -> None:
    source = (
        '"""NOTE: module contract."""\n'
        "\n"
        "def f():\n"
        '    """WHY: inner doc."""\n'
        "    return 1\n"
    )
    assert _records("python", source) == []


@pytest.mark.parametrize(
    "language,source",
    [
        ("python", "# TODO: later\nx = 1\n"),
        ("c", "// FIXME: soon\nint f(void) { return 0; }\n"),
        ("java", "/* FIXME: eventually */\nclass A {}\n"),
    ],
)
def test_todo_fixme_produce_no_records(language: str, source: str) -> None:
    assert _records(language, source) == []


@pytest.mark.parametrize(
    "language,source,kind,text",
    [
        ("python", "# note: lowercase marker\nx = 1\n", "note", "lowercase marker"),
        ("rust", "// Why: mixed case\nfn f() {}\n", "why", "mixed case"),
        ("java", "class A {}\n/* hack: upper in block */\n", "hack", "upper in block"),
    ],
)
def test_marker_case_insensitive_kind_lowercased(
    language: str, source: str, kind: str, text: str
) -> None:
    records = _records(language, source)
    assert [(r.kind, r.text) for r in records] == [(kind, text)]


def test_hash_nodes_stay_separate_and_no_space_matches() -> None:
    source = "#NOTE: no space after opener\n# plain prose continuation\nx = 1\n"
    records = _records("python", source)
    assert [(r.line, r.kind, r.text) for r in records] == [
        (1, "note", "no space after opener")
    ]


# (extension, source, expected records) -- one per distinct walk shape:
# dedicated base _walk (python), generic _visit_children (rust),
# dart's sibling-list traversal, and ruby's superclass-excluding walk
# (top level rides the shared base _walk, class bodies the excluding one).
_WALK_FIXTURES = {
    "python": (
        ".py",
        "# NOTE: top level\nx = 1\n\n\ndef f():\n    # WHY: inner guard\n    return x\n",
        [(1, "note", "top level"), (6, "why", "inner guard")],
    ),
    "rust": (
        ".rs",
        "// HACK: crate shim\nfn f() {}\n",
        [(1, "hack", "crate shim")],
    ),
    "dart": (
        ".dart",
        "// NOTE: lib note\nclass A {\n  // WHY: field guard\n  int x = 1;\n}\n",
        [(1, "note", "lib note"), (3, "why", "field guard")],
    ),
    "ruby": (
        ".rb",
        "# NOTE: top level\n\nclass A\n  # WHY: body note\n  def f\n    1\n  end\nend\n",
        [(1, "note", "top level"), (4, "why", "body note")],
    ),
}


@pytest.mark.parametrize("language", sorted(_WALK_FIXTURES))
def test_parser_walk_appends_rationale_records(tmp_path, language: str) -> None:
    extension, source, expected = _WALK_FIXTURES[language]
    path = tmp_path / f"probe{extension}"
    path.write_text(source)
    pf = get_adapter(language).parse(str(path))
    assert [(r.line, r.kind, r.text) for r in pf.rationale] == expected


def test_unmapped_language_yields_zero_records_no_error(tmp_path, monkeypatch) -> None:
    monkeypatch.delitem(COMMENT_NODE_TYPES, "python")
    path = tmp_path / "probe.py"
    path.write_text("# NOTE: no map entry\nx = 1\n")
    pf = get_adapter("python").parse(str(path))
    assert pf.rationale == []
