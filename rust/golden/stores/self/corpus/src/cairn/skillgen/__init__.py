"""Skill generation: resolve a selector, rank candidates, assemble and emit a SKILL.md."""
from .selector import SelectorResolution, resolve_selector

__all__ = ["SelectorResolution", "resolve_selector"]
