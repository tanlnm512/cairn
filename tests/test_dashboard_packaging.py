"""Wheel packaging coverage for the dashboard's data files.

The package-data globs in pyproject.toml are the only thing that ships
templates/ and static/ in a built wheel. Source-tree tests never
exercise them (they run from the repo), so a data file the globs miss
renders fine in every test and vanishes from every wheel install — a
flat ``static/*`` glob silently drops whole nested subtrees, whose
files then 404 at runtime on wheel installs. This suite replays
setuptools' own matching (recursive glob over the package dir, so
``**`` spans directories) so any new data file at any depth fails
here instead of on an install.
"""
from __future__ import annotations

import glob
import os
import re
from pathlib import Path

# Every packaged data subtree of the dashboard package.
_DATA_DIRS = ("templates", "static")


def _package_dir() -> Path:
    import cairn.dashboard

    return Path(cairn.dashboard.__file__).resolve().parent


def _dashboard_package_data_globs() -> list:
    """The cairn.dashboard entries of [tool.setuptools.package-data],
    read straight out of pyproject.toml. (Parsed with a regex, not
    tomllib — that is 3.11+ stdlib and the CI matrix runs 3.10.)"""
    pyproject = (_package_dir().parents[2] / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    section = pyproject.split("[tool.setuptools.package-data]", 1)[1].split(
        "\n[", 1
    )[0]
    match = re.search(r"\"cairn\.dashboard\"\s*=\s*\[([^\]]*)\]", section)
    assert match, "the cairn.dashboard package-data table is missing"
    globs = re.findall(r"\"([^\"]+)\"", match.group(1))
    assert globs, "the cairn.dashboard package-data table is empty"
    return globs


def test_package_data_globs_cover_every_dashboard_data_file():
    """Every file under the dashboard's data subtrees matches at least
    one package-data glob under setuptools' wheel-build matching — a
    file no glob reaches fails here, never as a runtime 404 on a wheel
    install."""
    package = _package_dir()
    globs = _dashboard_package_data_globs()
    covered: set = set()
    for pattern in globs:
        # setuptools matches each converted pattern against the package
        # dir with recursive globbing (build_py.find_data_files); the
        # package root is escaped so the repo path never reads as a
        # pattern.
        matches = glob.glob(
            os.path.join(glob.escape(str(package)), pattern), recursive=True
        )
        covered.update(
            Path(p).relative_to(package).as_posix()
            for p in matches
            if Path(p).is_file()
        )
    for subdir in _DATA_DIRS:
        for path in sorted((package / subdir).rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(package).as_posix()
            assert rel in covered, (
                f"{rel} matches none of the package-data globs {globs}"
            )
