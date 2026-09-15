"""Construction of built-in BaseParser adapters by language."""

from __future__ import annotations

from importlib import import_module
from threading import Lock
from typing import Mapping, Optional, Tuple, cast

from .base import BaseParser


_PARSER_CONSTRUCTORS: Mapping[str, Tuple[str, str]] = {
    "kotlin": ("kotlin", "KotlinParser"),
    "java": ("java", "JavaParser"),
    "swift": ("swift", "SwiftParser"),
    "python": ("python_parser", "PythonParser"),
    "typescript": ("typescript", "TypeScriptParser"),
    "javascript": ("typescript", "JavaScriptParser"),
    "dart": ("dart", "DartParser"),
    "objc": ("objc", "ObjCParser"),
    "go": ("go", "GoParser"),
    "php": ("php", "PhpParser"),
    "ruby": ("ruby", "RubyParser"),
    "rust": ("rust", "RustParser"),
    "csharp": ("csharp", "CSharpParser"),
    "c": ("c_family", "CParser"),
    "cpp": ("c_family", "CppParser"),
}


class ParserFactory:
    """Resolve built-in language adapters, caching one instance per language."""

    def __init__(self) -> None:
        self._instances: dict[str, BaseParser] = {}
        self._lock = Lock()

    def get_parser(self, language: str) -> Optional[BaseParser]:
        """Return the cached adapter for ``language``, or ``None``."""
        with self._lock:
            if language not in self._instances:
                parser = self.create(language)
                if parser is not None:
                    self._instances[language] = parser
            return self._instances.get(language)

    def create(self, language: str) -> Optional[BaseParser]:
        """Return a new adapter for ``language``, or ``None`` if unsupported."""
        constructor = _PARSER_CONSTRUCTORS.get(language)
        if constructor is None:
            return None

        module_name, class_name = constructor
        module = import_module(f".{module_name}", __package__)
        parser_type = getattr(module, class_name)
        return cast(BaseParser, parser_type())


_parser_factory = ParserFactory()


def get_parser(language: str) -> Optional[BaseParser]:
    """Resolve a parser through the shared factory cache."""
    return _parser_factory.get_parser(language)
