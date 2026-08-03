"""Drift-prevention test: assert knowledge-layer dependency directions.

Companion to ``test_agent_surface.py``'s structural drift tests. This turns
the Phase 0.2 layering rules into CI failures so the fixes don't regress:

1. ``memory`` (L4) must NOT import from ``compass`` (L2). The shared
   reference-extraction logic lives in the neutral ``codegraph.refs`` module;
   both layers import from it, not from each other.
2. ``knowledge`` (L5) and ``memory`` (L4) may use the ``graph`` (L1) public
   API (``codegraph.graph`` / ``..graph``) but must NOT reach into internal
   graph submodules (``graph.tokenize``, ``graph.vector_math``,
   ``graph.embeddings``, ``graph.fusion``, ``graph.reranker``,
   ``graph.ann_index``). Those internals are exposed via ``graph.__all__``
   for higher layers to consume as the public surface.

The test walks AST ``ImportFrom`` / ``Import`` nodes per source file so it
catches both top-level and lazy (in-function) imports. Relative imports are
resolved against the file's package.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "codegraph"

# Layers that must not reach into compass internals from memory.
# Layers that consume the graph public API only -- these graph submodules are
# implementation details, not public surface. Anything in __all__ of
# graph/__init__.py is fair game; submodule-direct imports are not.
GRAPH_INTERNAL_SUBMODULES = {
    "tokenize",
    "vector_math",
    "embeddings",
    "fusion",
    "reranker",
    "ann_index",
    "semantic",
    "lexical",
    "traversal",
    "cross_repo",
    "stats",
    "explore",
    "queries",
    "scanner",
    "schema",
    "builder",
    "resolver",
    "incremental",
    "dataflow",
    "metric_buffering",
}


def _iter_layer_py(layer: str):
    """Yield .py files under src/codegraph/<layer>/."""
    layer_dir = SRC / layer
    if not layer_dir.is_dir():
        return
    for p in sorted(layer_dir.rglob("*.py")):
        yield p


def _imported_modules(tree: ast.AST):
    """Yield (module_path, level) tuples for every ImportFrom in the AST.

    ``module_path`` is the dotted name (None for relative star-imports);
    ``level`` is the dot-count (0 = absolute).
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            yield (node.module, node.level)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield (alias.name, 0)


def _resolve_relative(file: Path, level: int, module: str | None) -> str:
    """Resolve a relative import to an absolute dotted path.

    ``level`` dots climb out of the file's package. E.g. from
    ``memory/scoring.py`` (package ``codegraph.memory``), ``from ..compass``
    (level 2, module "compass.critic") resolves to ``codegraph.compass.critic``.
    """
    pkg_parts = file.relative_to(SRC.parent).with_suffix("").parts
    # Drop the final module filename to get the containing package.
    if file.name == "__init__.py":
        containing_pkg = list(pkg_parts)  # the file's own dir IS the package
    else:
        containing_pkg = list(pkg_parts[:-1])
    # Climb `level-1` packages (level 1 = current package).
    base = containing_pkg[: len(containing_pkg) - (level - 1)] if level > 0 else []
    if module:
        return ".".join(base + [module])
    return ".".join(base)


class TestLayerDirection:
    def test_memory_does_not_import_compass(self):
        """L4 (memory) must not import from L2 (compass).

        The shared ref-extraction helpers were extracted to ``codegraph.refs``
        so memory and compass both depend on the neutral module, breaking the
        former ``memory.scoring -> compass.critic`` edge.
        """
        violations = []
        for py in _iter_layer_py("memory"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for module, level in _imported_modules(tree):
                if level and level > 0:
                    resolved = _resolve_relative(py, level, module)
                    if resolved.startswith("codegraph.compass") or (
                        module and "compass" in resolved
                    ):
                        violations.append(f"{py.name}: from {level*'.'}{module}")
                elif module and (module.startswith("codegraph.compass") or
                                 module.startswith("src.compass")):
                    violations.append(f"{py.name}: from {module}")
        assert not violations, (
            "memory (L4) reaches into compass (L2). Shared ref logic should "
            "live in codegraph.refs (neutral), not be imported across the "
            "layer boundary. Violations:\n  " + "\n  ".join(violations)
        )

    def test_memory_knowledge_use_graph_public_api_only(self):
        """L4 (memory) and L5 (knowledge) must not reach into graph internals.

        ``graph.tokenize`` / ``graph.vector_math`` / ``graph.embeddings`` etc.
        are implementation modules. Higher layers consume them via the public
        ``codegraph.graph`` surface (re-exported in graph/__init__.py __all__).
        """
        violations = []
        for layer in ("memory", "knowledge"):
            for py in _iter_layer_py(layer):
                tree = ast.parse(py.read_text(encoding="utf-8"))
                for module, level in _imported_modules(tree):
                    if level and level > 0:
                        resolved = _resolve_relative(py, level, module)
                        if not resolved.startswith("codegraph.graph"):
                            continue
                    elif module and (
                        module.startswith("codegraph.graph") or
                        module.startswith("src.graph")
                    ):
                        resolved = module
                    else:
                        continue
                    # resolved is a codegraph.graph.X[.Y] import. Check if the
                    # FIRST segment after "codegraph.graph" is an internal
                    # submodule.
                    tail = resolved[len("codegraph.graph"):]
                    if tail and tail[0] == ".":
                        first_seg = tail[1:].split(".")[0]
                        if first_seg in GRAPH_INTERNAL_SUBMODULES:
                            violations.append(
                                f"{layer}/{py.name}: reaches into graph internal "
                                f"'{resolved}' -- import via codegraph.graph public API"
                            )
        assert not violations, (
            "Higher layers (memory/knowledge) must use the graph public API, "
            "not internal submodules. Violations:\n  " + "\n  ".join(violations)
        )
