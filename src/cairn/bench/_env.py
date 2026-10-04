"""Shared env snapshot/restore discipline for the bench suites."""
from __future__ import annotations

import os
from typing import Iterable


def snapshot_env(names: Iterable[str]) -> dict[str, str | None]:
    """Snapshot ``names`` to {name: value or None}; pair with restore_env."""
    return {name: os.environ.get(name) for name in names}


def restore_env(saved: dict[str, str | None]) -> None:
    """Restore env vars snapshotted by snapshot_env and reset the embed backend cache."""
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    from cairn.graph import embeddings as emb

    emb.reset_backend_cache()
