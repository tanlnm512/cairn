"""Wheel packaging coverage for agent-integration assets."""
from __future__ import annotations

import glob
import os
from pathlib import Path

from .packaging import package_data_globs


def _package_dir() -> Path:
    import cairn.agent_integration

    return Path(cairn.agent_integration.__file__).resolve().parent


def test_package_data_globs_cover_assets_and_exclude_bytecode():
    """Match every integration asset while never matching bytecode caches."""
    package = _package_dir()
    globs = package_data_globs("cairn.agent_integration")
    covered: set[str] = set()
    for pattern in globs:
        matches = glob.glob(
            os.path.join(glob.escape(str(package)), pattern), recursive=True
        )
        covered.update(
            Path(path).relative_to(package).as_posix()
            for path in matches
            if Path(path).is_file()
        )

    for path in sorted(package.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path.name == ".DS_Store":
            continue
        rel = path.relative_to(package).as_posix()
        assert rel in covered, f"{rel} matches none of the package-data globs {globs}"

    assert not any(
        "__pycache__" in Path(rel).parts or rel.endswith(".pyc")
        for rel in covered
    )
