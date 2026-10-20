"""Cycle and recursion demos."""
from app.cycle_b import b


def a():
    return b()


def recurse(n):
    if n <= 0:
        return 0
    return recurse(n - 1)
