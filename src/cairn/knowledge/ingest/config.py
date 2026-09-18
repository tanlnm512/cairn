"""Typed layering of the ``ingest`` config section over built-in defaults."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping


@dataclass(frozen=True)
class IngestConfig:
    """Workspace overrides layered over the ingest built-in defaults."""

    classification: Dict[str, str] = field(default_factory=dict)
    skip_add: List[str] = field(default_factory=list)
    skip_disable: List[str] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: Mapping[str, object] | None) -> "IngestConfig":
        """Type the raw config dict; malformed entries are dropped."""
        if not isinstance(raw, Mapping):
            return cls()

        classification: Dict[str, str] = {}
        raw_rules = raw.get("classification")
        if isinstance(raw_rules, Mapping):
            for keyword, doc_type in raw_rules.items():
                if isinstance(keyword, str) and isinstance(doc_type, str):
                    if keyword.strip() and doc_type.strip():
                        classification[keyword.strip().lower()] = doc_type.strip()

        skip_add: List[str] = []
        skip_disable: List[str] = []
        raw_skip = raw.get("skip")
        if isinstance(raw_skip, Mapping):
            add = raw_skip.get("add")
            if isinstance(add, (list, tuple)):
                skip_add = [p for p in add if isinstance(p, str) and p.strip()]
            disable = raw_skip.get("disable")
            if isinstance(disable, (list, tuple)):
                skip_disable = [
                    c for c in disable if isinstance(c, str) and c.strip()
                ]

        return cls(
            classification=classification,
            skip_add=list(skip_add),
            skip_disable=[c.lower() for c in skip_disable],
        )


def load_ingest_config(workspace_root=None) -> IngestConfig:
    """Read the ingest section of the workspace's cairn.json (if any)."""
    from cairn.graph.config import load_config
    from cairn.paths import resolve_workspace

    root = workspace_root if workspace_root is not None else resolve_workspace()
    return IngestConfig.from_raw(load_config(root).ingest)
