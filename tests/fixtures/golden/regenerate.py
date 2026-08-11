"""Regenerate expected golden JSON files for parser regression testing.

Usage:
    .venv/bin/python -m tests.fixtures.golden.regenerate <lang|all>
"""
import sys
import json
from pathlib import Path

from cairn.parsers.kotlin import KotlinParser
from cairn.parsers.java import JavaParser
from cairn.parsers.swift import SwiftParser
from cairn.parsers.python_parser import PythonParser
from cairn.parsers.typescript import TypeScriptParser, JavaScriptParser
from cairn.parsers.dart import DartParser
from cairn.parsers.objc import ObjCParser
from cairn.parsers.go import GoParser
from cairn.parsers.php import PhpParser
from cairn.parsers.ruby import RubyParser
from cairn.parsers.csharp import CSharpParser
from cairn.parsers.c_family import CParser, CppParser

LANG_CONFIG = {
    "kotlin": (KotlinParser, "sample.kt"),
    "java": (JavaParser, "sample.java"),
    "swift": (SwiftParser, "sample.swift"),
    "python": (PythonParser, "sample.py"),
    "typescript": (TypeScriptParser, "sample.ts"),
    "javascript": (JavaScriptParser, "sample.js"),
    "dart": (DartParser, "sample.dart"),
    "objc": (ObjCParser, "sample.m"),
    "go": (GoParser, "sample.go"),
    "php": (PhpParser, "sample.php"),
    "ruby": (RubyParser, "sample.rb"),
    "csharp": (CSharpParser, "sample.cs"),
    "c": (CParser, "sample.c"),
    "cpp": (CppParser, "sample.cpp"),
}

GOLDEN_DIR = Path(__file__).parent


def normalise(parsed) -> dict:
    return {
        "language": parsed.language,
        "symbols": sorted(
            [
                {
                    "name": s.name,
                    "kind": s.kind,
                    "qualified_name": s.qualified_name,
                    "line_start": s.line_start,
                    "line_end": s.line_end,
                    "column_start": s.column_start,
                    "column_end": s.column_end,
                    "docstring": s.docstring,
                    "modifiers": sorted(s.modifiers),
                    "metadata": s.metadata,
                }
                for s in parsed.symbols
            ],
            key=lambda x: (x["kind"], x["name"], x["line_start"], x["column_start"]),
        ),
        "edges": sorted(
            [
                {
                    "source_name": e.source_name,
                    "kind": e.kind,
                    "target_name": e.target_name,
                    "line": e.line,
                    "column": e.column,
                    "receiver_type": e.receiver_type,
                }
                for e in parsed.edges
            ],
            key=lambda x: (x["source_name"], x["kind"], x["target_name"], x["line"], x["column"]),
        ),
        "imports": sorted(
            [
                {
                    "imported_path": i.imported_path,
                    "line": i.line,
                }
                for i in parsed.imports
            ],
            key=lambda x: (x["imported_path"], x["line"]),
        ),
    }


def regenerate_lang(lang: str):
    if lang not in LANG_CONFIG:
        raise ValueError(f"Unknown language {lang}. Supported: {list(LANG_CONFIG.keys())}")
    parser_cls, filename = LANG_CONFIG[lang]
    sample_path = GOLDEN_DIR / lang / filename
    expected_path = GOLDEN_DIR / lang / "expected.json"

    parser = parser_cls()
    parsed = parser.parse(str(sample_path))
    data = normalise(parsed)

    with open(expected_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    print(f"Generated {expected_path}")


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target == "all":
        for lang in LANG_CONFIG:
            regenerate_lang(lang)
    else:
        regenerate_lang(target)


if __name__ == "__main__":
    main()
