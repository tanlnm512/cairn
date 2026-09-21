"""Covered by the committed index; calls a sibling and the standard library."""


def provider(n):
    return n * 2


def consumer(n):
    doubled = provider(n)
    return len(str(doubled))
