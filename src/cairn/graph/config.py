"""User configuration for cairn indexing."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Union


@dataclass
class CairnConfig:
    """Resolved workspace configuration for filtering, namespaces, ingestion, and analysis."""

    exclude: List[str] = field(default_factory=list)
    include: List[str] = field(default_factory=list)
    repo_namespaces: Dict[str, str] = field(default_factory=dict)
    ingest: Dict[str, object] = field(default_factory=dict)
    taint_sources: Dict[str, Set[str]] = field(default_factory=dict)
    taint_sinks: Dict[str, Set[str]] = field(default_factory=dict)
    include_nested_repos: bool = False
    source: Optional[Path] = None  # the file these came from, for diagnostics

    @property
    def is_default(self) -> bool:
        return (
            not self.exclude and not self.include
            and not self.repo_namespaces
            and not self.ingest
            and not self.taint_sources
            and not self.taint_sinks
            and not self.include_nested_repos
        )


# Config keys we recognize. Unknown keys are ignored (forward-compatible).
_EXCLUDE_KEY = "exclude"
_INCLUDE_KEY = "include"
_REPO_NAMESPACES_KEY = "repo_namespaces"
_INGEST_KEY = "ingest"
_TAINT_KEY = "taint"
_TAINT_SOURCES_KEY = "sources"
_TAINT_SINKS_KEY = "sinks"
_INCLUDE_NESTED_REPOS_KEY = "include_nested_repos"


def load_config(root: Union[str, Path]) -> CairnConfig:
    """Load ``cairn.json`` from ``root`` (workspace or repo dir).

    Returns a default config if no file exists or it is empty/malformed. On a
    malformed file, prints a warning to stderr and falls back to defaults
    rather than crashing the build -- a bad config must never break indexing.
    """
    root = Path(root)
    path = root / "cairn.json"
    if not path.exists():
        return CairnConfig()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        import sys
        print(f"warning: {path}: invalid JSON ({e}); ignoring config", file=sys.stderr)
        return CairnConfig()
    except OSError as e:
        import sys
        print(f"warning: {path}: could not read ({e}); ignoring config", file=sys.stderr)
        return CairnConfig()

    if not isinstance(raw, dict):
        import sys
        print(f"warning: {path}: top-level JSON must be an object; ignoring config",
              file=sys.stderr)
        return CairnConfig()

    exclude = _as_string_list(raw.get(_EXCLUDE_KEY), path, _EXCLUDE_KEY)
    include = _as_string_list(raw.get(_INCLUDE_KEY), path, _INCLUDE_KEY)
    repo_namespaces = _as_string_dict(raw.get(_REPO_NAMESPACES_KEY), path, _REPO_NAMESPACES_KEY)
    ingest = _as_dict(raw.get(_INGEST_KEY), path, _INGEST_KEY)
    taint = _as_dict(raw.get(_TAINT_KEY), path, _TAINT_KEY)
    taint_sources = _as_name_table(
        taint.get(_TAINT_SOURCES_KEY), path, f"{_TAINT_KEY}.{_TAINT_SOURCES_KEY}"
    )
    taint_sinks = _as_name_table(
        taint.get(_TAINT_SINKS_KEY), path, f"{_TAINT_KEY}.{_TAINT_SINKS_KEY}"
    )
    include_nested_repos = _as_bool(
        raw.get(_INCLUDE_NESTED_REPOS_KEY), path, _INCLUDE_NESTED_REPOS_KEY
    )
    return CairnConfig(
        exclude=exclude,
        include=include,
        repo_namespaces=repo_namespaces,
        ingest=ingest,
        taint_sources=taint_sources,
        taint_sinks=taint_sinks,
        include_nested_repos=include_nested_repos,
        source=path,
    )


def _as_bool(value, path: Path, key: str) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    import sys

    print(
        f"warning: {path}: '{key}' must be a boolean; ignoring config",
        file=sys.stderr,
    )
    return False


def _as_string_list(value, path: Path, key: str) -> List[str]:
    """Coerce a JSON value into a list[str] of non-empty patterns."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif not isinstance(item, str):
                import sys
                print(f"warning: {path}: '{key}' must contain only strings; "
                      f"skipping non-string entry", file=sys.stderr)
        return out
    import sys
    print(f"warning: {path}: '{key}' must be a string or list of strings; ignoring",
          file=sys.stderr)
    return []


def _as_string_dict(value, path: Path, key: str) -> Dict[str, str]:
    """Coerce a JSON value into a dict[str, str] of non-empty mappings.

    Accepts ``{"prefix": "repo"}`` (the documented shape) and drops malformed
    entries (non-string keys/values, empty strings) with a warning. A bad value
    never crashes the build: returns ``{}`` on type mismatch.
    """
    if value is None:
        return {}
    import sys

    if not isinstance(value, dict):
        print(f"warning: {path}: '{key}' must be a JSON object mapping "
              f"prefix -> repo id; ignoring", file=sys.stderr)
        return {}

    out: Dict[str, str] = {}
    for k, v in value.items():
        if not isinstance(k, str) or not isinstance(v, str):
            print(f"warning: {path}: '{key}' has a non-string entry "
                  f"({k!r}: {v!r}); skipping", file=sys.stderr)
            continue
        if k.strip() and v.strip():
            out[k.strip()] = v.strip()
    return out


def _as_name_table(value, path: Path, key: str) -> Dict[str, Set[str]]:
    """Coerce a JSON object into a taint category -> exact call-name table.

    Each category's value parses through ``_as_string_list``; a declared
    category is kept even when its name list is empty. Malformed tables
    return ``{}`` and never crash the build.
    """
    if value is None:
        return {}
    import sys

    if not isinstance(value, dict):
        print(f"warning: {path}: '{key}' must be a JSON object mapping "
              f"category -> call names; ignoring", file=sys.stderr)
        return {}

    out: Dict[str, Set[str]] = {}
    for category, names in value.items():
        if not isinstance(category, str) or not category.strip():
            print(f"warning: {path}: '{key}' has a non-string category "
                  f"({category!r}); skipping", file=sys.stderr)
            continue
        out[category.strip()] = set(_as_string_list(names, path, f"{key}.{category.strip()}"))
    return out


def _as_dict(value, path: Path, key: str) -> Dict[str, object]:
    """Return the raw JSON object for ``key``; ``{}`` on type mismatch.

    Values are kept as-is (nested objects/arrays included): the ingest
    package types and layers this section; here we only guarantee a dict
    and never crash the build.
    """
    if value is None:
        return {}
    import sys

    if not isinstance(value, dict):
        print(f"warning: {path}: '{key}' must be a JSON object; ignoring",
              file=sys.stderr)
        return {}
    return dict(value)
