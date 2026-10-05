"""Fixture: entry-to-target diamond for depth-bound checks."""


def dep_entry():
    return dep_left() + dep_right()


def dep_left():
    return dep_target()


def dep_right():
    return dep_target()


def dep_target():
    return 0
