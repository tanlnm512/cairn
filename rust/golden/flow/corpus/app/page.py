"""Page assembly with a split renderer."""


def render_text(x):
    return str(x)


def render(x):
    return str(x)


def render(x):
    return repr(x)


def page(x):
    return render(x)
