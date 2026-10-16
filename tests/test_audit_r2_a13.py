"""Regression tests for audit round 2 batch A13 (parser fix surfaces)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cairn.parsers.csharp import CSharpParser
from cairn.parsers.python_parser import PythonParser
from cairn.parsers.typescript import TypeScriptParser


def _parse(parser_cls, source: bytes, suffix: str):
    """Parse ``source`` with ``parser_cls`` via a temp file. Returns ParsedFile."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="wb") as f:
        f.write(source)
        path = f.name
    try:
        return parser_cls().parse(path)
    finally:
        Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# G030 — decorated Python methods persist kind="method"
# ---------------------------------------------------------------------------

class TestPythonDecoratedMethodKind:
    def test_decorated_methods_are_methods(self):
        """@staticmethod/@property/@classmethod defs in a class body keep
        kind="method"; the decorated_definition wrapper must not hide it."""
        pf = _parse(
            PythonParser,
            b"class Engine:\n"
            b"    def plain(self):\n"
            b"        return 1\n"
            b"\n"
            b"    @staticmethod\n"
            b"    def stat():\n"
            b"        return 2\n"
            b"\n"
            b"    @property\n"
            b"    def prop(self):\n"
            b"        return 3\n"
            b"\n"
            b"    @classmethod\n"
            b"    def build(cls):\n"
            b"        return cls()\n"
            b"\n"
            b"def free():\n"
            b"    pass\n",
            ".py",
        )
        kinds = {s.name: s.kind for s in pf.symbols}
        assert kinds == {
            "Engine": "class",
            "plain": "method",
            "stat": "method",
            "prop": "method",
            "build": "method",
            "free": "function",
        }


# ---------------------------------------------------------------------------
# G031 — a failed TypeScript parse leaves no scope residue on the reused parser
# ---------------------------------------------------------------------------

class TestTypeScriptScopeReset:
    def test_failed_parse_does_not_poison_reused_parser(self, monkeypatch):
        """A mid-walk crash must not leak _scope into the next parse of the
        cached instance (factory caches one parser per language)."""
        parser = TypeScriptParser()

        def _write(source: bytes) -> str:
            with tempfile.NamedTemporaryFile(suffix=".ts", delete=False, mode="wb") as f:
                f.write(source)
                return f.name

        bad = _write(b"class Poisoned {\n    m() { boom(); }\n}\n")
        good = _write(b"class Clean {\n    m() { ok(); }\n}\n")
        try:
            original = TypeScriptParser._visit

            def crash(self, node, source, pf):
                if node.type == "method_definition":
                    raise RuntimeError("boom")
                return original(self, node, source, pf)

            monkeypatch.setattr(TypeScriptParser, "_visit", crash)
            with pytest.raises(RuntimeError):
                parser.parse(bad)
            monkeypatch.undo()
            pf = parser.parse(good)
        finally:
            Path(bad).unlink(missing_ok=True)
            Path(good).unlink(missing_ok=True)
        # The scope is exactly [file_stem]; no "Poisoned" segment leaks in.
        stem = Path(good).stem
        names = {s.name: s.qualified_name for s in pf.symbols}
        assert names == {
            "Clean": f"{stem}.Clean",
            "m": f"{stem}.Clean.m",
        }


# ---------------------------------------------------------------------------
# G145 — C# enum members carry the enum's scope segment
# ---------------------------------------------------------------------------

class TestCSharpEnumMemberQualification:
    def test_enum_members_carry_enum_scope(self):
        """Members of two enums sharing a member name stay distinct; the enum
        symbol itself is not double-qualified."""
        pf = _parse(
            CSharpParser,
            b"namespace App {\n"
            b"    enum Color { Red, Green }\n"
            b"    enum Mood { Red }\n"
            b"}\n",
            ".cs",
        )
        cases = sorted(
            s.qualified_name for s in pf.symbols if s.kind == "enum_case"
        )
        assert cases == ["App.Color.Green", "App.Color.Red", "App.Mood.Red"]
        enums = {s.name: s.qualified_name for s in pf.symbols if s.kind == "enum"}
        assert enums == {"Color": "App.Color", "Mood": "App.Mood"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
