"""Bearer API-key resolution and constant-time credential checks for the MCP server."""
from __future__ import annotations

import os
from hmac import compare_digest


def resolve_api_key(explicit: str | None) -> str | None:
    """Return the explicit key when non-empty, else CAIRN_MCP_API_KEY, else None."""
    if explicit:
        return explicit
    return os.environ.get("CAIRN_MCP_API_KEY") or None


def bearer_key_ok(provided: str | None, expected: str) -> bool:
    """Return True when provided carries the expected Bearer credential (raw header value or bare token); anything missing or malformed is False."""
    if not provided or not expected:
        return False
    parts = provided.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        credential = parts[1]
    elif len(parts) == 1:
        credential = parts[0]
    else:
        return False
    return compare_digest(credential.encode("utf-8"), expected.encode("utf-8"))
