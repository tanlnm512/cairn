"""Stateful Kotlin receiver type inference post-pass.

Runs over query-extracted Kotlin symbols and call edges to infer local variable
and receiver types for member resolution.
"""
from __future__ import annotations

from typing import Dict
from cairn.parsers.base import ParsedFile


def kotlin_receiver_types(parsed: ParsedFile) -> ParsedFile:
    """Post-pass to resolve Kotlin call edge receiver types."""
    # Map local/field identifiers to inferred type names
    type_env: Dict[str, str] = {}

    for sym in parsed.symbols:
        if sym.kind in ("class", "interface", "enum"):
            type_env[sym.name] = sym.name

    for edge in parsed.edges:
        if edge.kind == "calls":
            # If target has form receiver.method, check type_env for receiver
            parts = edge.target_name.split(".")
            if len(parts) > 1:
                receiver = parts[0]
                if receiver in type_env:
                    edge.receiver_type = type_env[receiver]
                elif receiver == "this" and parsed.symbols:
                    edge.receiver_type = parsed.symbols[0].name

    return parsed
