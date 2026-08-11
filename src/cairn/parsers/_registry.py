"""Parser registry: builds tree-sitter Parsers from per-language wheels.

Centralizes tree-sitter runtime setup so each language parser only deals with
AST traversal. Uses tree-sitter 0.26 + per-language wheels (tree-sitter-kotlin,
tree-sitter-java, tree-sitter-python, tree-sitter-swift, tree-sitter-typescript,
tree-sitter-javascript, tree-sitter-dart, tree-sitter-objc, tree-sitter-php,
tree-sitter-ruby).

Most language modules expose a bare language() function returning a PyCapsule.
tree-sitter-typescript is the exception: one wheel ships two grammars via
language_typescript() (.ts/.mts/.cts) and language_tsx() (.tsx) -- there is no
plain language(). Both are registered here under the keys "typescript" and
"tsx" respectively; src/parsers/typescript.py picks between them per file
suffix. "javascript" uses the dedicated tree-sitter-javascript wheel (its
grammar already covers JSX in .jsx files), not the TypeScript grammar.

External packages can register additional languages via the
``cairn.parsers.v1`` entry-point group. An entry point in that group must
resolve to a zero-arg callable returning a tree-sitter language capsule
(PyCapsule). The ``v1`` suffix encodes the API version so a future breaking
change can ship under ``cairn.parsers.v2`` without breaking v1 plugins.
"""
from __future__ import annotations

import functools
import warnings

from tree_sitter import Language, Parser


@functools.lru_cache(maxsize=16)
def get_parser(language: str) -> Parser:
    """Return a cached tree-sitter Parser for the given language.

    Raises ValueError if the language is not supported.
    """
    capsule = _load_language_capsule(language)
    return Parser(Language(capsule))


# Languages whose wheel doesn't expose a plain language() function. Each entry
# maps to (module_name, function_name).
_SPECIAL_LOADERS = {
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "javascript": ("tree_sitter_javascript", "language"),
    # tree-sitter-php ships two grammars with non-standard entry points:
    # language_php() (PHP+HTML, "program" nodes) and language_php_only() (pure
    # PHP AST, no HTML wrapper). We use php_only so the parser sees clean
    # declaration nodes instead of an embedded-html tree.
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
        # Not a built-in. Fall through to the plugin entry-point scan:
        # external packages may register this language via the
        # ``cairn.parsers.v1`` entry-point group. Built-ins stay preferred
        # (faster, no metadata walk) -- the scan only runs on a miss.
        capsule = _load_plugin_capsule(language)
        if capsule is not None:
            return capsule
        raise  # re-raise the original ValueError (unsupported language)


def _load_plugin_capsule(language: str):
    """Look up ``language`` in the ``cairn.parsers.v1`` entry-point group.

    Returns the language capsule (PyCapsule) from the first matching entry
    point, or None if no plugin registered this language. An entry point in the
    group resolves to a zero-arg callable returning the capsule. Failures in a
    plugin (bad entry point, import error) are caught and skipped so one broken
    plugin can't break the whole registry -- it just won't provide its language.
    """
    import importlib.metadata

    try:
        eps = importlib.metadata.entry_points(group=_PLUGIN_ENTRY_POINT_GROUP)
    except TypeError:
        # Python <3.10: entry_points() returns a dict keyed by group.
        all_eps = importlib.metadata.entry_points()
        eps = all_eps.get(_PLUGIN_ENTRY_POINT_GROUP, [])
    for ep in eps:
        if ep.name == language:
            try:
                factory = ep.load()
                return factory()
            except Exception as exc:  # noqa: BLE001 - a broken plugin is skipped
                # A broken plugin is skipped, not fatal -- the language simply
                # stays unsupported unless another entry point provides it. But
                # we surface a warning so a misbehaving plugin doesn't silently
                # disappear without any diagnostic signal.
                warnings.warn(
                    f"cairn parser plugin {ep.name!r} for language "
                    f"{language!r} failed to load and was skipped: "
                    f"{type(exc).__name__}: {exc}",
                    stacklevel=2,
                )
                continue
    return None


# Entry-point group for external parser plugins (see module docstring).
_PLUGIN_ENTRY_POINT_GROUP = "cairn.parsers.v1"


def _load_language_module(language: str):
    mapping = {
        "kotlin": "tree_sitter_kotlin",
        "java": "tree_sitter_java",
        "python": "tree_sitter_python",
        "swift": "tree_sitter_swift",
        "dart": "tree_sitter_dart",
        "objc": "tree_sitter_objc",
        "go": "tree_sitter_go",
        "ruby": "tree_sitter_ruby",
    }
    mod_name = mapping.get(language)
    if mod_name is None:
        raise ValueError(f"Unsupported language: {language}")
    import importlib

    return importlib.import_module(mod_name)
