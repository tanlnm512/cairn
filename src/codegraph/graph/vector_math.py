"""Pure vector math helpers with no graph dependencies.

Both cosine scan paths -- graph symbols and knowledge docs -- share the same
implementation here.
"""
from __future__ import annotations


def l2norm(vec) -> float:
    """L2 (Euclidean) norm of a vector."""
    s = 0.0
    for v in vec:
        s += v * v
    return s ** 0.5


def dot(a, b) -> float:
    """Dot product of two equal-length vectors."""
    return sum(x * y for x, y in zip(a, b))


def cosine(a, b) -> float:
    """Cosine similarity, zero-safe (returns 0.0 for a zero vector)."""
    na = l2norm(a)
    nb = l2norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot(a, b) / (na * nb)
