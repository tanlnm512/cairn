"""Utility 04; the duplicated shared() makes its callsites ambiguous."""


def shared(a, b):
    return a - b
