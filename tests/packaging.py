"""Shared wheel package-data test helpers."""
from __future__ import annotations

import re
from pathlib import Path


def package_data_globs(package_name: str) -> list[str]:
    """Return package-data globs for package_name from pyproject.toml."""
    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    section = pyproject.split("[tool.setuptools.package-data]", 1)[1].split(
        "\n[", 1
    )[0]
    match = re.search(rf'"{re.escape(package_name)}"\s*=\s*\[([^\]]*)\]', section)
    assert match, f"the {package_name} package-data table is missing"
    globs = re.findall(r'"([^"]+)"', match.group(1))
    assert globs, f"the {package_name} package-data table is empty"
    return globs
