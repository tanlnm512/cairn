"""Direct regression tests for the scope-5 parser audit fixes (P2/P3).

Each test parses the exact failing idiom by hand (the golden snapshots alone
can't be trusted for these: they are REGENERATED from parser output, so a
systematic drop self-validates -- cf. the kotlin golden baking in the missing
class-body property symbols).

Fixes covered:
- F1 Ruby chained calls: ``repo.find(1).update(x)``, ``xs.map{}.compact()``,
  ``a.b.map() { }`` and ``self.helper(x)`` were silently dropped by the
  proc-shorthand guard, which counted identifiers instead of using their
  position relative to the ``.``. ``p.(1)`` (real proc shorthand) stays skipped.
- F2 PHP namespaced calls: ``App\\Utils\\sanitize(...)`` stored the full
  namespace path as target_name, which can never match the callee's bare
  symbol name (the resolver keys on symbols.name; its import tier splits
  ``/`` and ``.`` but never ``\\``). Same class of fix for qualified ``new``
  (edge was dropped entirely) and qualified scoped-call receivers.
- F3 Kotlin class-body properties: tree-sitter-kotlin 1.1.0 emits
  ``identifier`` (not ``simple_identifier``) for the name inside
  ``variable_declaration``, so every class-body ``val``/``var`` produced no
  Symbol.
- F4 SCIP merge: the name-only fallback folded a definition into an arbitrary
  same-named tree-sitter row (overloads), re-pointing exact edges. Now the
  nearest-line row wins and ties are left un-merged.
- F5 SCIP language fallback: ``.h`` mapped to ``c`` unconditionally; now the
  file content is sniffed (objc/cpp/c) like the scanner does when the
  workspace root is known.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from cairn.parsers.kotlin import KotlinParser
from cairn.parsers.php import PhpParser
from cairn.parsers.ruby import RubyParser
from cairn.parsers.scip_importer import scip_available


def _parse(parser_cls, source: bytes, suffix: str):
    """Parse ``source`` with ``parser_cls`` via a temp file. Returns ParsedFile."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="wb") as f:
        f.write(source)
        path = f.name
    try:
        return parser_cls().parse(path)
    finally:
        Path(path).unlink(missing_ok=True)


def _call_edges(pf):
    return [e for e in pf.edges if e.kind == "calls"]


def _targets(pf):
    return [e.target_name for e in _call_edges(pf)]


# ---------------------------------------------------------------------------
# F1 — Ruby: chained calls must not be dropped
# ---------------------------------------------------------------------------

class TestRubyChainedCalls:
    def test_chained_call_with_argument_list_not_dropped(self):
        """``repo.find(1).update(name: 'x')`` records BOTH links of the chain.

        Regression: the outer call (children ``[call, '.', identifier,
        argument_list]``) tripped the proc-shorthand guard -- one identifier,
        no constants, an argument_list -- so ``update`` was dropped.
        """
        pf = _parse(
            RubyParser,
            b"class Repo\n  def go\n    repo.find(1).update(name: 'x')\n  end\nend\n",
            ".rb",
        )
        targets = _targets(pf)
        assert "find" in targets
        assert "update" in targets

    def test_chained_call_after_block_not_dropped(self):
        """``user.orders.map { }.compact()`` records ``compact`` too.

        The ``compact()`` link's receiver is itself a call (``map { ... }``),
        which is never counted as a receiver by the guard -- only the
        trailing-identifier position matters.
        """
        pf = _parse(
            RubyParser,
            b"class Orders\n  def go\n    user.orders.map { |o| o }.compact()\n  end\nend\n",
            ".rb",
        )
        targets = _targets(pf)
        assert "orders" in targets
        assert "map" in targets
        assert "compact" in targets

    def test_parens_and_block_call_not_dropped(self):
        """``foo.bar.map() { |x| x }`` records ``map`` when its receiver is a call.

        Paren+block form: the argument_list makes the old guard see "one
        identifier + args" and skip, even though ``map`` follows the dot.
        """
        pf = _parse(
            RubyParser,
            b"class C\n  def go\n    foo.bar.map() { |x| x }\n  end\nend\n",
            ".rb",
        )
        targets = _targets(pf)
        assert "bar" in targets
        assert "map" in targets

    def test_self_dot_call_with_args_not_dropped(self):
        """``self.helper(x)`` records ``helper``: ``self`` is a ``self`` node,
        not an identifier, so the old guard saw a lone identifier + args."""
        pf = _parse(
            RubyParser,
            b"class C\n  def go\n    self.helper(x)\n  end\nend\n",
            ".rb",
        )
        assert "helper" in _targets(pf)

    def test_proc_call_shorthand_still_skipped(self):
        """``p.(1)`` has no method identifier (the identifier sits BEFORE the
        dot) and must keep producing no call edge."""
        pf = _parse(
            RubyParser,
            b"class C\n  def go\n    p.(1)\n  end\nend\n",
            ".rb",
        )
        assert _call_edges(pf) == []

    def test_plain_and_constant_calls_unchanged(self):
        """Preserved behavior: ``obj.method(x)`` and ``Logger.new`` keep their
        edges; a constant receiver still yields receiver_type for Tier 0."""
        pf = _parse(
            RubyParser,
            b"class C\n  def go\n    obj.method(x)\n    Logger.new\n  end\nend\n",
            ".rb",
        )
        edges = {(e.target_name, e.receiver_type) for e in _call_edges(pf)}
        assert ("method", None) in edges  # lowercase receiver -> no type hint
        assert ("new", "Logger") in edges  # constant receiver -> type hint


# ---------------------------------------------------------------------------
# F2 — PHP: namespaced (qualified) call targets must use the last segment
# ---------------------------------------------------------------------------

class TestPhpQualifiedNames:
    def test_namespaced_function_call_target_is_last_segment(self):
        """``App\\Utils\\sanitize($x)`` targets ``sanitize`` (the callee's bare
        symbol name), not the full namespace path that could never resolve."""
        pf = _parse(
            PhpParser,
            b"<?php\nfunction caller($x) { App\\Utils\\sanitize($x); }\n",
            ".php",
        )
        assert "sanitize" in _targets(pf)
        assert "App\\Utils\\sanitize" not in _targets(pf)

    def test_fully_qualified_global_call_still_bare(self):
        """``\\array_map(...)`` keeps resolving to the bare ``array_map``."""
        pf = _parse(
            PhpParser,
            b"<?php\nfunction caller() { \\array_map('f', []); }\n",
            ".php",
        )
        assert "array_map" in _targets(pf)

    def test_qualified_new_uses_last_segment(self):
        """``new App\\Models\\User()`` targets ``User`` (previously the edge
        was dropped entirely: only ``name`` children were considered, never
        ``qualified_name``)."""
        pf = _parse(
            PhpParser,
            b"<?php\nfunction f() { $u = new App\\Models\\User(); }\n",
            ".php",
        )
        assert "User" in _targets(pf)

    def test_qualified_scoped_call_receiver_is_last_segment(self):
        """``App\\Utils\\Formatter::format(...)`` targets ``format`` with
        receiver_type ``Formatter`` (a full-path receiver could never match a
        type symbol in Tier 0)."""
        pf = _parse(
            PhpParser,
            b"<?php\nfunction f() { App\\Utils\\Formatter::format($s); }\n",
            ".php",
        )
        edge = next(e for e in _call_edges(pf) if e.target_name == "format")
        assert edge.receiver_type == "Formatter"

    def test_namespaced_call_resolves_end_to_end(self, tmp_path):
        """The whole point of the last-segment fix: a qualified call RESOLVES.

        Builds a tiny in-memory graph (callee defined under a namespace, caller
        invoking it qualified) and asserts the edge is ``exact``.
        """
        from cairn.graph.builder import build_graph

        ws = tmp_path / "ws"
        repo = ws / "demo"
        (repo / ".git").mkdir(parents=True)
        (repo / "utils.php").write_text(
            "<?php\nnamespace App\\Utils;\nfunction sanitize($rows) { return $rows; }\n"
        )
        (repo / "caller.php").write_text(
            "<?php\nfunction run($rows) { return App\\Utils\\sanitize($rows); }\n"
        )
        db_path = str(tmp_path / "graph.db")
        build_graph(workspace=str(ws), db_path=db_path, verbose=False)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT e.resolution, t.qualified_name AS target_qname
                   FROM edges e JOIN symbols t ON e.target_id = t.id
                   WHERE e.kind = 'calls' AND t.name = 'sanitize'"""
            ).fetchall()
            assert rows, "expected the qualified call edge to resolve to sanitize"
            assert all(r["resolution"] == "exact" for r in rows)
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# F3 — Kotlin: class-body val/var must emit property Symbols
# ---------------------------------------------------------------------------

class TestKotlinClassBodyProperties:
    def test_class_body_properties_emit_symbols(self):
        """tree-sitter-kotlin 1.1.0 emits ``identifier`` (not
        ``simple_identifier``) for the property name; every class-body
        ``val``/``var`` shape must produce a property Symbol."""
        pf = _parse(
            KotlinParser,
            b"interface I { val id: String }\n"
            b"class User {\n"
            b"    var plain: String = \"\"\n"
            b"    @Inject lateinit var api: Api\n"
            b"    private val getUser: GetUserUseCase = GetUserUseCase()\n"
            b"}\n",
            ".kt",
        )
        props = {s.name: s for s in pf.symbols if s.kind == "property"}
        assert "id" in props, "interface-body val dropped"
        assert "plain" in props, "class-body var dropped"
        assert "api" in props, "annotated lateinit var dropped"
        assert "getUser" in props, "private val with initializer dropped"
        # Modifiers survive on the class-body properties.
        assert "private" in props["getUser"].modifiers
        assert "lateinit" in props["api"].modifiers

    def test_constructor_val_params_still_properties(self):
        """Regression guard: primary-constructor ``val`` params (the shape that
        always worked) keep producing property Symbols."""
        pf = _parse(
            KotlinParser,
            b"class User(val id: String, var name: String)\n",
            ".kt",
        )
        props = {s.name for s in pf.symbols if s.kind == "property"}
        assert {"id", "name"} <= props


# ---------------------------------------------------------------------------
# F4 — SCIP merge: nearest-line match, skip on ambiguous overloads
# ---------------------------------------------------------------------------

pytestmark_scip = pytest.mark.skipif(
    not scip_available(), reason="[scip] extra not installed"
)


def _scip_conn() -> sqlite3.Connection:
    from cairn.graph.schema import _apply_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    _apply_schema(c)
    return c


def _insert_ts_symbol(conn, sym_id, file_id, name, line_start):
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, "
        "line_start, line_end, column_start, column_end, source) "
        "VALUES (?, ?, ?, ?, 'method', ?, ?, 0, 10, 'tree_sitter')",
        (sym_id, file_id, name, name, line_start, line_start),
    )


@pytestmark_scip
class TestScipMergeOverloads:
    def _index_with_def_at(self, path, name, line_1based):
        """A one-document, one-definition SCIP protobuf index."""
        from cairn.parsers import _scip_pb2

        idx = _scip_pb2.Index()
        doc = idx.documents.add()
        doc.relative_path = path
        occ = doc.occurrences.add()
        occ.symbol = f"scip-kotlin com example {name}#"
        occ.symbol_roles = 1  # Definition
        occ.syntax_kind = 16  # IdentifierFunctionDefinition
        occ.single_line_range.line = line_1based - 1
        occ.single_line_range.start_character = 4
        occ.single_line_range.end_character = 4 + len(name)
        return idx.SerializeToString()

    def test_merge_prefers_nearest_line_among_overloads(self):
        """Same-named tree-sitter symbols at lines 10 and 30; the SCIP def
        anchors at line 12 -- it must fold into the line-10 row, not an
        arbitrary one."""
        from cairn.parsers.scip_importer import import_scip_bytes

        conn = _scip_conn()
        conn.execute(
            "INSERT INTO files (id, path, repo_id, hash, line_count, language) "
            "VALUES ('f1', 'A.kt', 'demo', 'h', 40, 'kotlin')"
        )
        _insert_ts_symbol(conn, "ts-near", "f1", "foo", 10)
        _insert_ts_symbol(conn, "ts-far", "f1", "foo", 30)
        conn.commit()

        stats = import_scip_bytes(
            conn, self._index_with_def_at("A.kt", "foo", 12), repo_id="demo"
        )
        assert stats["symbols_merged"] == 1
        sources = {
            r["id"]: r["source"]
            for r in conn.execute(
                "SELECT id, source FROM symbols WHERE name = 'foo'"
            )
        }
        assert sources["ts-near"] == "merged"
        assert sources["ts-far"] == "tree_sitter"

    def test_merge_skips_tied_overloads(self):
        """Two same-named tree-sitter rows equidistant from the SCIP def: no
        defensible merge -- leave the standalone SCIP row (its exact edges
        keep pointing at it) rather than re-pointing them at a wrong overload."""
        from cairn.parsers.scip_importer import import_scip_bytes

        conn = _scip_conn()
        conn.execute(
            "INSERT INTO files (id, path, repo_id, hash, line_count, language) "
            "VALUES ('f1', 'A.kt', 'demo', 'h', 40, 'kotlin')"
        )
        _insert_ts_symbol(conn, "ts-a", "f1", "foo", 10)
        _insert_ts_symbol(conn, "ts-b", "f1", "foo", 20)
        conn.commit()

        stats = import_scip_bytes(
            conn, self._index_with_def_at("A.kt", "foo", 15), repo_id="demo"
        )
        assert stats["symbols_merged"] == 0
        sources = {
            r["id"]: r["source"]
            for r in conn.execute(
                "SELECT id, source FROM symbols WHERE name = 'foo'"
            )
        }
        assert sources["ts-a"] == "tree_sitter"
        assert sources["ts-b"] == "tree_sitter"
        # The standalone SCIP row survives with its exact edges intact.
        assert "scip" in sources.values()

    def test_merge_line_mismatch_single_candidate_still_merges(self):
        """One same-named row whose line disagrees: nearest-line is trivially
        unambiguous, so the merge still happens (old name-only behavior)."""
        from cairn.parsers.scip_importer import import_scip_bytes

        conn = _scip_conn()
        conn.execute(
            "INSERT INTO files (id, path, repo_id, hash, line_count, language) "
            "VALUES ('f1', 'A.kt', 'demo', 'h', 40, 'kotlin')"
        )
        _insert_ts_symbol(conn, "ts-1", "f1", "foo", 10)
        conn.commit()

        stats = import_scip_bytes(
            conn, self._index_with_def_at("A.kt", "foo", 14), repo_id="demo"
        )
        assert stats["symbols_merged"] == 1
        row = conn.execute(
            "SELECT source FROM symbols WHERE name = 'foo'"
        ).fetchone()
        assert row["source"] == "merged"


# ---------------------------------------------------------------------------
# F5 — SCIP language fallback: .h sniffed, not hardcoded to c
# ---------------------------------------------------------------------------

@pytestmark_scip
class TestScipHeaderLanguage:
    def _header_index(self, rel_path):
        from cairn.parsers import _scip_pb2

        idx = _scip_pb2.Index()
        doc = idx.documents.add()
        doc.relative_path = rel_path
        doc.language = ""  # indexers often leave this blank
        occ = doc.occurrences.add()
        occ.symbol = "scip-c demo Bridge#"
        occ.symbol_roles = 1
        occ.single_line_range.line = 0
        occ.single_line_range.start_character = 0
        occ.single_line_range.end_character = 6
        return idx.SerializeToString()

    def test_header_language_sniffed_with_ws_root(self, tmp_path):
        """With the workspace root known, an ObjC-flavored ``.h`` gets
        files.language='objc' -- matching what the scanner records for the
        same file (the hybrid skip logic keys off this)."""
        from cairn.parsers.scip_importer import import_scip_bytes

        ws = tmp_path / "ws"
        repo = ws / "demo"
        (repo / ".git").mkdir(parents=True)
        (repo / "Bridge.h").write_text(
            "#import <Foundation/Foundation.h>\n@interface Bridge\n@end\n"
        )
        conn = _scip_conn()
        import_scip_bytes(
            conn, self._header_index("demo/Bridge.h"), repo_id="demo", ws_root=ws
        )
        row = conn.execute(
            "SELECT language FROM files WHERE path LIKE '%Bridge.h'"
        ).fetchone()
        assert row["language"] == "objc"

    def test_header_language_falls_back_to_c_without_ws_root(self):
        """Standalone import (no ws_root): the file can't be read, the
        extension fallback 'c' is preserved."""
        from cairn.parsers.scip_importer import import_scip_bytes

        conn = _scip_conn()
        import_scip_bytes(
            conn, self._header_index("Bridge.h"), repo_id="demo"
        )
        row = conn.execute(
            "SELECT language FROM files WHERE path = 'Bridge.h'"
        ).fetchone()
        assert row["language"] == "c"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
