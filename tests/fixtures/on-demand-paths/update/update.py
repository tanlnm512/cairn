"""Fixture: mutable two-symbol chain for update-cycle tests."""


def upd_a():
    return upd_b()


def upd_b():
    return None
