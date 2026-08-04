"""Tests for Phase 2.1 (LanguageAdapter helper dedup) + 2.2 (plugin hooks).

Guards:
1. The shared AST helpers (``_child_of_type``, ``_find_name``) live ONLY on
   ``TreeSitterParserBase`` -- no parser redefines them (the drift that
   previously let parsers disagree).
2. ``_extract_callee`` is intentionally NOT deduplicated -- it's genuinely
   language-specific (Python ``attribute``/``call`` vs Swift
   ``navigation_expression``), so each parser keeps its own.
3. The ``cairn.parsers.v1`` entry-point group lets an external package
   register a parser without forking cairn (the plugin-hooks gate).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PARSERS = REPO_ROOT / "src" / "cairn" / "parsers"


def _parser_files() -> list[Path]:
    return sorted(p for p in PARSERS.glob("*.py") if p.name not in ("__init__.py", "_registry.py", "base.py"))


class TestNoHelperDuplication:
    """Drift guard: the shared helpers must live on the base, not per-parser."""

    @pytest.mark.parametrize("helper", ["_child_of_type", "_find_name"])
    def test_helper_not_redefined_in_parsers(self, helper: str):
        redefined = []
        for p in _parser_files():
            src = p.read_text(encoding="utf-8")
            if f"def {helper}" in src:
                redefined.append(p.name)
        assert not redefined, (
            f"{helper} must be inherited from TreeSitterParserBase, not "
            f"redefined (Phase 2.1 dedup). Redefined in: {redefined}"
        )

    def test_extract_callee_stays_per_parser(self):
        """_extract_callee is language-specific and must NOT be lifted.

        Python/Swift/TypeScript have genuinely different callee-node shapes;
        deduping it would be wrong. This test pins that decision so a future
        over-zealous dedup pass doesn't regress it.
        """
        with_extract_callee = []
        for p in _parser_files():
            if "def _extract_callee" in p.read_text(encoding="utf-8"):
                with_extract_callee.append(p.name)
        # python_parser, swift, typescript each define their own.
        assert "python_parser.py" in with_extract_callee
        assert "swift.py" in with_extract_callee
        assert "typescript.py" in with_extract_callee

    def test_base_class_provides_shared_helpers(self):
        from cairn.parsers.base import TreeSitterParserBase

        for helper in ("_child_of_type", "_find_name", "_qualified_name", "_node_text"):
            assert hasattr(TreeSitterParserBase, helper), (
                f"TreeSitterParserBase must provide {helper}"
            )

    def test_parsers_inherit_find_name(self):
        """Each tree-sitter parser instance must resolve _find_name via the base."""
        from cairn.parsers.base import TreeSitterParserBase
        from cairn.parsers.dart import DartParser
        from cairn.parsers.objc import ObjCParser
        from cairn.parsers.typescript import TypeScriptParser

        for cls in (DartParser, ObjCParser, TypeScriptParser):
            inst = cls()
            # The method on the instance resolves to the BASE definition, not a
            # per-class override (Phase 2.1 removed those).
            assert type(inst).__dict__.get("_find_name") is None, (
                f"{cls.__name__} still overrides _find_name"
            )
            assert inst._find_name.__func__ is TreeSitterParserBase._find_name


class TestPluginEntryPoints:
    """Phase 2.2: external packages register parsers via entry_points."""

    def test_entry_point_group_constant(self):
        from cairn.parsers._registry import _PLUGIN_ENTRY_POINT_GROUP

        assert _PLUGIN_ENTRY_POINT_GROUP == "cairn.parsers.v1"

    def test_plugin_capsule_lookup_returns_none_for_unknown(self):
        from cairn.parsers._registry import _load_plugin_capsule

        # No plugin registered -> None, not an error.
        assert _load_plugin_capsule("definitely-not-a-language-xyz") is None

    def test_fake_entry_point_is_discovered(self):
        """A fake entry point registered via patch is picked up by the registry."""
        import importlib.metadata

        from cairn.parsers._registry import (
            _PLUGIN_ENTRY_POINT_GROUP,
            _load_plugin_capsule,
        )

        capsule_marker = object()  # stand-in for a tree-sitter capsule

        class FakeEP:
            name = "fakelang"

            def load(self):
                def factory():
                    return capsule_marker
                return factory

        # Patch the actual importlib.metadata.entry_points (what _registry
        # imports lazily). The registry module does a local `import
        # importlib.metadata`, so patch the canonical location it resolves to.
        def fake_entry_points(*args, group=None, **kwargs):
            if group == _PLUGIN_ENTRY_POINT_GROUP:
                return [FakeEP()]
            return []

        with patch.object(importlib.metadata, "entry_points", fake_entry_points):
            result = _load_plugin_capsule("fakelang")
        assert result is capsule_marker, "fake entry point should have been discovered"

    def test_broken_plugin_is_skipped_not_fatal(self):
        """A plugin whose load() raises must not break the registry."""
        import importlib.metadata

        from cairn.parsers._registry import (
            _PLUGIN_ENTRY_POINT_GROUP,
            _load_plugin_capsule,
        )

        class BrokenEP:
            name = "brokenlang"

            def load(self):
                raise ImportError("simulated broken plugin")

        def fake_entry_points(*args, group=None, **kwargs):
            if group == _PLUGIN_ENTRY_POINT_GROUP:
                return [BrokenEP()]
            return []

        with patch.object(importlib.metadata, "entry_points", fake_entry_points):
            # Broken plugin is skipped -> None (no language provided).
            assert _load_plugin_capsule("brokenlang") is None
