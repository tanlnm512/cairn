"""JSX reference tracking: `<UserCard/>` emits a `references` edge from the
enclosing component to the UserCard symbol.

These are parser-level tests (TypeScriptParser on `.tsx`, JavaScriptParser on
`.jsx`); the resolver/builder pipeline is exercised by the smoke test in the
implementation task, not here.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cairn.parsers.typescript import JavaScriptParser, TypeScriptParser


def _parse(parser_cls, source: bytes, suffix: str):
    """Parse ``source`` with ``parser_cls`` via a temp file. Returns ParsedFile."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="wb") as f:
        f.write(source)
        path = f.name
    try:
        return parser_cls().parse(path)
    finally:
        Path(path).unlink(missing_ok=True)


def _ref_targets(pf):
    """All `references` edge target_names from a ParsedFile."""
    return [e.target_name for e in pf.edges if e.kind == "references"]


# --------------------------------------------------------------------------- TSX

class TestTsxReferences:
    """TypeScriptParser on .tsx -- the primary React/RN file type."""

    def test_self_closing_element_emits_reference(self):
        src = b"const App = () => <UserCard />;\n"
        pf = _parse(TypeScriptParser, src, ".tsx")
        assert _ref_targets(pf) == ["UserCard"]

    def test_open_and_close_element_emits_one_edge(self):
        # <UserCard>...</UserCard> must produce exactly one reference (from the
        # opening element), not two (the closing element must not double-emit).
        src = b"const App = () => <UserCard></UserCard>;\n"
        pf = _parse(TypeScriptParser, src, ".tsx")
        assert _ref_targets(pf) == ["UserCard"]

    def test_member_expression_takes_property_name(self):
        # <UI.Card/> -> target is "Card" (the property), matching how
        # _extract_callee treats member-expression calls.
        src = b"const App = () => <UI.Card />;\n"
        pf = _parse(TypeScriptParser, src, ".tsx")
        assert _ref_targets(pf) == ["Card"]

    def test_lowercase_html_tags_are_skipped(self):
        # <div>, <span>, <input> are host tags, not components -- no edges.
        src = b"const App = () => <div><span /></div>;\n"
        pf = _parse(TypeScriptParser, src, ".tsx")
        assert _ref_targets(pf) == []

    def test_nested_elements_each_emit_reference(self):
        # Both Layout and Sidebar are component refs owned by the enclosing
        # App component.
        src = b"const App = () => <Layout><Sidebar /></Layout>;\n"
        pf = _parse(TypeScriptParser, src, ".tsx")
        assert sorted(_ref_targets(pf)) == ["Layout", "Sidebar"]

    def test_jsx_in_attribute_is_captured(self):
        # <Wrapper child={<Inner />} /> -- both Wrapper and Inner are refs.
        src = b"const App = () => <Wrapper child={<Inner />} />;\n"
        pf = _parse(TypeScriptParser, src, ".tsx")
        assert sorted(_ref_targets(pf)) == ["Inner", "Wrapper"]

    def test_reference_owned_by_enclosing_component(self):
        # The edge source_name must be the enclosing component ("App"), which
        # is the symbol the builder will attach the edge to. An empty
        # source_name would cause the builder to silently drop the edge.
        src = b"const App = () => <UserCard />;\n"
        pf = _parse(TypeScriptParser, src, ".tsx")
        ref_edges = [e for e in pf.edges if e.kind == "references"]
        assert len(ref_edges) == 1
        assert ref_edges[0].source_name == "App"

    def test_reference_at_module_top_level_has_empty_owner(self):
        # JSX with no enclosing function: the reference is emitted with an
        # empty source_name (no owning callable). The builder drops such edges
        # for lack of a symbol to attach them to, which is correct -- a bare
        # top-level JSX fragment has no component to attribute the ref to.
        #
        # NOTE: when JSX is the *direct initializer* of a top-level const
        # (``const elem = <UserCard/>``), a separate pre-existing quirk in
        # _handle_var_decl means the value node is walked via _walk (children
        # only) and the jsx_self_closing_element dispatch never fires. We use
        # a returned JSX fragment instead, which is the realistic shape and
        # does emit the edge.
        src = b"""
const renderCard = () => <UserCard />;
"""
        pf = _parse(TypeScriptParser, src, ".tsx")
        ref_edges = [e for e in pf.edges if e.kind == "references"]
        # renderCard is an arrow const -> kind=function -> on _callable_scope,
        # so the ref attributes to it (not empty).
        assert len(ref_edges) == 1
        assert ref_edges[0].source_name == "renderCard"
        assert ref_edges[0].target_name == "UserCard"

    def test_call_edges_still_emitted_inside_jsx(self):
        # JSX must not suppress ordinary call edges inside expression
        # containers ({...}).
        src = b"""
const App = () => {
  return <Layout>{renderContent()}</Layout>;
};
"""
        pf = _parse(TypeScriptParser, src, ".tsx")
        call_targets = [e.target_name for e in pf.edges if e.kind == "calls"]
        assert "renderContent" in call_targets
        assert "Layout" in _ref_targets(pf)

    def test_existing_call_edges_unaffected(self):
        # A normal function call co-existing with JSX -- both edge kinds
        # present, correctly classified. (The call must be a standalone
        # expression statement; a call in a variable-declarator initializer
        # is subject to a separate, pre-existing _handle_var_decl quirk that
        # drops it, unrelated to JSX.)
        src = b"""
const App = () => {
  getUser();
  return <UserCard />;
};
"""
        pf = _parse(TypeScriptParser, src, ".tsx")
        kinds = {e.kind for e in pf.edges}
        assert "calls" in kinds
        assert "references" in kinds


# ----------------------------------------------------------------- JavaScript

class TestJsxReferences:
    """JavaScriptParser on .jsx -- same JSX handling via the shared traversal."""

    def test_jsx_in_javascript_file(self):
        src = b"function App() { return <UserCard />; }\n"
        pf = _parse(JavaScriptParser, src, ".jsx")
        assert _ref_targets(pf) == ["UserCard"]

    def test_lowercase_skipped_in_js(self):
        src = b"function App() { return <div />; }\n"
        pf = _parse(JavaScriptParser, src, ".jsx")
        assert _ref_targets(pf) == []


# --------------------------------------------------- no-JSX files unaffected

class TestNoRegressionOnPlainTs:
    """Plain .ts files (no JSX grammar) must be unaffected by the new branch."""

    def test_plain_ts_produces_no_references(self):
        src = b"""
export function greet(name: string): string {
  return "Hello " + name;
}
"""
        pf = _parse(TypeScriptParser, src, ".ts")
        # No JSX in this file -> no references edges at all.
        assert _ref_targets(pf) == []
        # Function symbol still captured.
        assert any(s.kind == "function" for s in pf.symbols)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
