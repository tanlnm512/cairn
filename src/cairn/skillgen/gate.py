"""Pre-write critic gate for generated skills.

Routes the exact SKILL.md bytes destined for disk through the compass
critic's ``validate_paths`` (consume-only, concept-adapted): any
backtick-quoted file/symbol reference the graph cannot resolve rejects
the draft (FR-004).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable, List, Tuple

from ..compass.critic import validate_paths
from ..okf.concept import OKFConcept
from ..refs import (
    extract_file_refs,
    extract_symbol_refs,
    symbol_exists,
    unresolved_file_refs,
)

__all__ = ["GateResult", "verify_draft"]


@dataclass(frozen=True)
class GateResult:
    """Gate outcome: ``ok`` (truthy) or ``rejected`` with failing refs."""

    ok: bool
    failing_refs: Tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.ok

    @classmethod
    def accepted(cls) -> "GateResult":
        return cls(ok=True)

    @classmethod
    def rejected(cls, failing_refs: Iterable[str]) -> "GateResult":
        return cls(ok=False, failing_refs=tuple(failing_refs))


class _DraftBundle:
    """Read-only bundle view over one in-memory draft concept."""

    def __init__(self, concept: OKFConcept) -> None:
        self._concept = concept

    def list_concepts(self) -> List[str]:
        return [self._concept.concept_id]

    def read_concept(self, concept_id: str) -> OKFConcept:
        return self._concept


def verify_draft(conn: sqlite3.Connection, rendered: str) -> GateResult:
    """Verify the exact ``render_skill`` bytes against the graph.

    ``rendered`` is the full document (frontmatter + body). Returns
    ``GateResult.ok`` when every backtick-quoted file/symbol reference
    resolves; otherwise ``rejected`` lists the unresolvable refs.
    """
    concept = OKFConcept(
        type="skill-draft", concept_id="skillgen:draft", body=rendered
    )
    if not validate_paths(conn, _DraftBundle(concept)):
        return GateResult.accepted()
    return GateResult.rejected(_failing_refs(conn, rendered))


def _failing_refs(conn: sqlite3.Connection, rendered: str) -> Tuple[str, ...]:
    files = unresolved_file_refs(conn, extract_file_refs(rendered))
    symbols = [
        ref for ref in extract_symbol_refs(rendered) if not symbol_exists(conn, ref)
    ]
    return tuple(dict.fromkeys(files + symbols))
