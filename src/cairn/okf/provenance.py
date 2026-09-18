"""Provenance tier classification for OKF concepts."""
from __future__ import annotations

from enum import Enum


class Tier(str, Enum):
    DERIVED = "derived"
    SYNTHESIZED = "synthesized"
    ASSERTED = "asserted"
