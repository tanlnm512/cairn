"""Opaque-USR index shape; only occurrence positions are trustworthy."""


class Greeter:
    def hello(self, name):
        return "hi " + name


def run():
    g = Greeter()
    return g.hello("world")
