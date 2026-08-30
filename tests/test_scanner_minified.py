"""Scanner Layer D: minified bundles skip as generated assets.

A vendored min.js committed under src/ (e.g. a JS library bundle) parses
into hundreds of junk symbols whose internal calls dominate every
degree-ranked view. The dir layers only catch vendor/ trees, so the
filename marker is the skip that works; like the size cap it is NOT
overridable by a config include.
"""
from __future__ import annotations

from pathlib import Path

import pathspec

from cairn.graph.scanner import (
    MAX_FILE_SIZE,
    REASON_MINIFIED,
    REASON_SIZE_CAP,
    classify_file,
)


def _specs(include=None, exclude=None):
    include_spec = (
        pathspec.PathSpec.from_lines("gitignore", include) if include else None
    )
    exclude_spec = (
        pathspec.PathSpec.from_lines("gitignore", exclude) if exclude else None
    )
    return [], exclude_spec, include_spec


def _write(tmp_path: Path, rel: str, size: int = 10) -> Path:
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x" * size)
    return f


def test_min_js_skips_with_minified_reason(tmp_path):
    f = _write(tmp_path, "src/web/static/vis-network.min.js")
    keep, reason = classify_file(f, tmp_path, *_specs())
    assert keep is False
    assert reason == REASON_MINIFIED


def test_min_mjs_and_mixed_case_marker_also_skip(tmp_path):
    for rel in ("lib/app.min.mjs", "lib/Vendor.Min.JS"):
        f = _write(tmp_path, rel)
        keep, reason = classify_file(f, tmp_path, *_specs())
        assert keep is False, rel
        assert reason == REASON_MINIFIED, rel


def test_dot_min_in_a_directory_name_does_not_skip(tmp_path):
    # The marker is a filename check: x.min.y as a DIR component with a
    # clean filename stays indexable.
    f = _write(tmp_path, "src/x.min.y/normal.py")
    keep, reason = classify_file(f, tmp_path, *_specs())
    assert keep is True
    assert reason == ""


def test_plain_source_still_indexes(tmp_path):
    f = _write(tmp_path, "src/web/app.js")
    keep, reason = classify_file(f, tmp_path, *_specs())
    assert keep is True
    assert reason == ""


def test_include_spec_cannot_override_minified(tmp_path):
    f = _write(tmp_path, "src/web/static/app.min.js")
    keep, reason = classify_file(
        f, tmp_path, *_specs(include=["*.min.js"])
    )
    assert keep is False
    assert reason == REASON_MINIFIED


def test_minified_reason_wins_over_size_cap(tmp_path):
    f = _write(
        tmp_path, "src/web/static/app.min.js", size=MAX_FILE_SIZE + 1
    )
    keep, reason = classify_file(f, tmp_path, *_specs())
    assert keep is False
    assert reason == REASON_MINIFIED


def test_oversize_plain_file_keeps_size_cap_reason(tmp_path):
    f = _write(tmp_path, "src/web/generated_big.js", size=MAX_FILE_SIZE + 1)
    keep, reason = classify_file(f, tmp_path, *_specs())
    assert keep is False
    assert reason == REASON_SIZE_CAP
