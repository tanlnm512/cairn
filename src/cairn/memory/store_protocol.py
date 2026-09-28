"""Lifecycle review decisions for the memory layer."""
from __future__ import annotations

from enum import Enum


class Decision(str, Enum):
    """Lifecycle review decisions for memory concept promotion and archival."""

    NEW = "new"              # first capture / newly created
    PROMOTE = "promote"      # raised to a higher tier (drafts -> tribal)
    KEEP_DRAFT = "keep_draft"  # stays a draft candidate (needs more evidence)
    ARCHIVE = "archive"      # demoted out (low score / decayed / suppressed)
    DUPLICATE = "duplicate"  # consolidated into another memory
    AMBIGUOUS = "ambiguous"  # critic couldn't decide; left in place
