"""User configuration for cairn indexing.

Reads a ``cairn.json`` at the workspace (or repo) root. Supports
gitignore-style ``exclude`` / ``include`` globs, matched against repo-root-
relative paths. ``include`` overrides ``exclude`` and the default skip set, so a
checked-in vendored directory can be pulled back into the graph.

This module is intentionally minimal: it parses the config file and returns a
dataclass. The actual matching (gitignore semantics, negations, ``**``) is done
with :mod:`pathspec` in :mod:`src.graph.scanner`, which builds the combined
``PathSpec`` from the default skip set + gitignore + this config.

Example ``cairn.json``::

    {
      "exclude": ["static/", "**/vendor/**"],
      "include": ["vendor/lib/"]
    }

If no file is present, :func:`load_config` returns a default (empty) config and
the scanner applies only its built-in skip rules + ``.gitignore``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union


@dataclass
class CairnConfig:
    """Resolved exclude/include globs and cross-repo namespaces for a workspace.

    Both lists hold gitignore-style patterns (e.g. ``"static/"``,
    ``"**/vendor/**"``). They are combined with pathspec so negations and
    ``**`` work exactly as in ``.gitignore``.

    ``repo_namespaces`` maps import-path prefixes to owning repo ids (e.g.
    ``{"com.example.sdk": "sdk"}``) and feeds ``cross_repo_deps``. When empty,
    cross-repo analysis falls back to the built-in default map.

    ``scip`` maps language -> index file path (relative to workspace root) for
    pre-built SCIP indexes. At build time cairn resolves each path and keeps
    only languages whose file actually exists; the rest fall back to tree-sitter.
    """

    exclude: List[str] = field(default_factory=list)
    include: List[str] = field(default_factory=list)
    repo_namespaces: Dict[str, str] = field(default_factory=dict)
    scip: Dict[str, str] = field(default_factory=dict)
    source: Optional[Path] = None  # the file these came from, for diagnostics

    @property
    def is_default(self) -> bool:
        return (
            not self.exclude and not self.include
            and not self.repo_namespaces and not self.scip
        )


# Config keys we recognize. Unknown keys are ignored (forward-compatible).
_EXCLUDE_KEY = "exclude"
_INCLUDE_KEY = "include"
_REPO_NAMESPACES_KEY = "repo_namespaces"
_SCIP_KEY = "scip"


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
    scip = _as_string_dict(raw.get(_SCIP_KEY), path, _SCIP_KEY)
    return CairnConfig(
        exclude=exclude,
        include=include,
        repo_namespaces=repo_namespaces,
        scip=scip,
        source=path,
    )


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
