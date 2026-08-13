"""JSX reference tracking: `<UserCard/>` emits a `references` edge from the
enclosing component to the UserCard symbol.

Also covers the variable-declarator initializer fix (call_expression / JSX as
the direct value of ``const x = ...``), which previously dropped the edge.

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


def _call_targets(pf):
    """All `calls` edge target_names from a ParsedFile."""
    return [e.target_name for e in pf.edges if e.kind == "calls"]


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

    def test_reference_in_arrow_const_at_top_level(self):
        # ``const X = () => <UserCard/>`` -- the arrow const becomes a
        # kind=function symbol on _callable_scope, so the ref attributes to X.
        src = b"const renderCard = () => <UserCard />;\n"
        pf = _parse(TypeScriptParser, src, ".tsx")
        ref_edges = [e for e in pf.edges if e.kind == "references"]
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
        assert "renderContent" in _call_targets(pf)
        assert "Layout" in _ref_targets(pf)

    def test_calls_and_references_coexist(self):
        # A normal function call co-existing with JSX -- both edge kinds
        # present, correctly classified.
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

    # --- variable-declarator initializer (previously dropped) ---------------

    def test_jsx_as_var_declarator_initializer(self):
        # ``const x = <UserCard/>`` -- previously the JSX ref was dropped
        # because _handle_var_decl walked the value's children rather than
        # visiting the value node itself. Now fixed.
        src = b"const App = () => { const x = <UserCard />; };\n"
        pf = _parse(TypeScriptParser, src, ".tsx")
        assert _ref_targets(pf) == ["UserCard"]

    def test_call_as_var_declarator_initializer(self):
        # ``const x = getUser()`` -- the var-declarator fix also recovers
        # dropped call edges (the bug predated JSX work and affected calls).
        src = b"function App() { const x = getUser(); }\n"
        pf = _parse(TypeScriptParser, src, ".tsx")
        assert "getUser" in _call_targets(pf)

    def test_new_expression_as_var_declarator_initializer(self):
        # ``let r = new Foo()`` -- recovered after the fix.
        src = b"function App() { let r = new Foo(); }\n"
        pf = _parse(TypeScriptParser, src, ".tsx")
        assert "Foo" in _call_targets(pf)

    def test_var_declarator_no_double_emission(self):
        # Nested call as initializer must emit exactly one edge per call.
        src = b"function App() { const x = foo(bar()); }\n"
        pf = _parse(TypeScriptParser, src, ".tsx")
        assert _call_targets(pf).count("foo") == 1
        assert _call_targets(pf).count("bar") == 1

    # --- real-world React patterns ------------------------------------------

    def test_conditional_rendering_both_branches(self):
        # {cond ? <A/> : <B/>} -- both branches captured.
        src = b"const App = ({cond}) => cond ? <A /> : <B />;\n"
        pf = _parse(TypeScriptParser, src, ".tsx")
        assert sorted(_ref_targets(pf)) == ["A", "B"]

    def test_list_rendering_callback_ownership(self):
        # {items.map(i => <Row key={i.id} />)} -- Row captured, owned by the
        # enclosing component (not the anonymous arrow).
        src = b"""
const App = ({items}) => (
  <ul>{items.map(i => <Row key={i.id} />)}</ul>
);
"""
        pf = _parse(TypeScriptParser, src, ".tsx")
        ref_edges = [e for e in pf.edges if e.kind == "references"]
        row_edges = [e for e in ref_edges if e.target_name == "Row"]
        assert len(row_edges) == 1
        # Attributed to App, not the anonymous map callback.
        assert row_edges[0].source_name == "App"

    def test_spread_attributes(self):
        src = b"const App = () => <UserCard {...props} />;\n"
        pf = _parse(TypeScriptParser, src, ".tsx")
        assert _ref_targets(pf) == ["UserCard"]

    def test_react_forwardref_emits_inner_reference(self):
        # The HOC pattern: React.forwardRef((props, ref) => <Widget/>).
        # The forwardRef call edge is emitted with empty source (dropped by
        # builder, correct -- no owning symbol), but the inner <Widget/> ref
        # survives and attributes to the anonymous arrow (source "").
        src = b"const X = React.forwardRef((props, ref) => <Widget />);\n"
        pf = _parse(TypeScriptParser, src, ".tsx")
        # The forwardRef call edge is present at parse time.
        assert "forwardRef" in _call_targets(pf)
        # The inner Widget reference is captured.
        assert "Widget" in _ref_targets(pf)


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


# --------------------------------------------- class-field initializer call edges

class TestFieldInitializerCallEdges:
    """Call edges inside class field initializers (e.g. ``repo = createRepo()``).

    Regression: ``public_field_definition`` returned without descending into
    the initializer, so every call there was dropped -- the same family as the
    var-declarator edge-drop fix covered above.
    """

    def test_call_in_field_initializer_is_emitted(self):
        src = b"class Service {\n  defaultRepo = createRepo();\n}\n"
        pf = _parse(TypeScriptParser, src, ".ts")
        assert "createRepo" in _call_targets(pf)

    def test_single_call_emits_exactly_one_edge(self):
        src = b"class S { a = build(); }\n"
        pf = _parse(TypeScriptParser, src, ".ts")
        assert _call_targets(pf).count("build") == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
