"""Fixture: linear call chain for shortest-path ordering."""


def chain_a():
    return chain_b()


def chain_b():
    return chain_c()


def chain_c():
    return chain_d()


def chain_d():
    return None
