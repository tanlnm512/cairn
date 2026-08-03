"""Post-pass receiver-type inference for query-extracted parser data.

Currently Kotlin-only: resolves call-edge receiver types by matching
receiver identifiers against a type environment built from type symbols.
"""
from codegraph.parsers.inference.kotlin import kotlin_receiver_types

__all__ = ["kotlin_receiver_types"]
