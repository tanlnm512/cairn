"""Parser registry: builds tree-sitter Parsers from per-language wheels."""
from __future__ import annotations

import functools
import warnings

from tree_sitter import Language, Parser


@functools.lru_cache(maxsize=16)
def get_parser(language: str) -> Parser:
    """Return a cached tree-sitter Parser, raising ValueError when unsupported."""
    capsule = _load_language_capsule(language)
    return Parser(Language(capsule))


def is_language_available(language: str) -> bool:
    """Return whether the language capsule can be loaded."""
    try:
        _load_language_capsule(language)
    except Exception:
        return False
    return True


# Languages whose wheel doesn't expose a plain language() function. Each entry
# maps to (module_name, function_name).
_SPECIAL_LOADERS = {
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "javascript": ("tree_sitter_javascript", "language"),
    # php_only yields a pure PHP AST instead of an embedded-HTML tree.
    "php": ("tree_sitter_php", "language_php_only"),
}


def _load_language_capsule(language: str):
    import importlib

    special = _SPECIAL_LOADERS.get(language)
    if special is not None:
        mod_name, func_name = special
        mod = importlib.import_module(mod_name)
        return getattr(mod, func_name)()

    try:
        lang_mod = _load_language_module(language)
        return lang_mod.language()
    except ValueError:
        # Built-in misses fall through to the parser plugin entry-point group.
        capsule = _load_plugin_capsule(language)
        if capsule is not None:
            return capsule
        raise  # re-raise the original ValueError (unsupported language)


def _load_plugin_capsule(language: str):
    """Return the first working plugin capsule for language, else None."""
    import importlib.metadata

    eps = importlib.metadata.entry_points(group=_PLUGIN_ENTRY_POINT_GROUP)
    for ep in eps:
        if ep.name == language:
            try:
                factory = ep.load()
                return factory()
            except Exception as exc:  # noqa: BLE001 - a broken plugin is skipped
                # Broken plugins warn and are skipped so registration remains usable.
                warnings.warn(
                    f"cairn parser plugin {ep.name!r} for language "
                    f"{language!r} failed to load and was skipped: "
                    f"{type(exc).__name__}: {exc}",
                    stacklevel=2,
                )
                continue
    return None


# Entry-point group for external parser plugins.
_PLUGIN_ENTRY_POINT_GROUP = "cairn.parsers.v1"


def _load_language_module(language: str):
    mapping = {
        "kotlin": "cairn._tree_sitter_kotlin",
        "java": "tree_sitter_java",
        "python": "tree_sitter_python",
        "swift": "tree_sitter_swift",
        "dart": "tree_sitter_dart",
        "objc": "tree_sitter_objc",
        "go": "tree_sitter_go",
        "ruby": "tree_sitter_ruby",
        "rust": "tree_sitter_rust",
        "csharp": "tree_sitter_c_sharp",
        "c": "tree_sitter_c",
        "cpp": "tree_sitter_cpp",
    }
    mod_name = mapping.get(language)
    if mod_name is None:
        raise ValueError(f"Unsupported language: {language}")
    import importlib

    return importlib.import_module(mod_name)
