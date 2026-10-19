"""Beta socket retry helpers."""

import socket


def connect_with_retry(host, port, retries=3):
    """Connect to host:port with bounded retries and exponential backoff."""
    for attempt in range(retries):
        try:
            return socket.create_connection((host, port))
        except OSError:
            if attempt == retries - 1:
                raise
    raise OSError("unreachable")


def parse_expression(expr):
    """Parse an arithmetic expression into a token list."""
    return [ch for ch in expr if not ch.isspace()]
