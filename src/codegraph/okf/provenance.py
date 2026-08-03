"""Provenance tier classification for OKF concepts.

Every concept is categorized by how it was produced — this axis determines
trust, caching, and decay policy.

  DERIVED      — Parsed from code. Always true, no LLM, no critic. Staleness
                 fixed by re-parsing.
  SYNTHESIZED — LLM-generated from the graph (compass, wiki). Critic-checked;
                 stales on code change.
  ASSERTED     — Human/agent claims (memory, knowledge). Scored and decayed.
"""
from __future__ import annotations

from enum import Enum


class Tier(str, Enum):
    DERIVED = "derived"
    SYNTHESIZED = "synthesized"
    ASSERTED = "asserted"
