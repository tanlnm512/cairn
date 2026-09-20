"""Covered by no index document; keeps its tree-sitter edges."""


def standalone():
    return hash((1, 2))


def caller():
    return standalone()
