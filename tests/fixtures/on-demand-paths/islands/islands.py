"""Fixture: two disconnected components for no-path checks."""


def isl_a1():
    return isl_a2()


def isl_a2():
    return None


def isl_b1():
    return isl_b2()


def isl_b2():
    return None
