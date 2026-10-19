"""Alpha utilities for tokenizing and joining words."""

import re


def tokenize(text):
    """Split text into lowercase alphanumeric tokens.

    Longer description of tokenize behavior that spans
    multiple lines for the docstring vector.
    """
    return re.findall(r"[a-z0-9]+", text.lower())


def join_tokens(tokens, separator=" "):
    """Join tokens with a separator."""
    return separator.join(tokens)


class Greeter:
    """Builds greeting strings with punctuation."""

    def greet(self, name):
        """Return a greeting for name."""
        return f"Hello, {name}!"
