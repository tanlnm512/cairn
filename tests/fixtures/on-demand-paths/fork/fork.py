"""Fixture: two equal-length routes through distinct middle hops."""


def hub_top():
    return mid_left() + mid_right()


def mid_left():
    return hub_sink()


def mid_right():
    return hub_sink()


def hub_sink():
    return 0
