"""Repo scanner: discover repos and enumerate source files by language.

4-layer filtering. A file is indexed only if it passes ALL of:
  A. not under a DEFAULT_SKIP_DIRS directory (build output, vcs, deps, ...)
  B. not matched by a .gitignore (root + nested, gitwildmatch semantics)
  C. not matched by codegraph.json `exclude` (repo-root-relative globs)
  D. not larger than MAX_FILE_SIZE (default 1 MB)

`include` (codegraph.json) overrides A/B/C: a path matched by `include` is
indexed even if a default skip dir or gitignore would have excluded it. This is
the explicit opt-in for a checked-in vendored dependency.

Skips are recorded in the `skipped_files` table (reason-tagged) so they are
auditable via `cg stats` rather than silent. `scan_repo` is a pure read of the
filesystem and does not touch the DB; the builder records skips when it indexes
(see :func:`record_skip`).
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import pathspec

logger = logging.getLogger(__name__)

# Extension -> language mapping.
EXTENSION_MAP = {
    ".kt": "kotlin",
    ".java": "java",
    ".swift": "swift",
    ".py": "python",
    # TypeScript/JavaScript. .tsx picks the TSX grammar internally
    # (src/parsers/typescript.py) but is still tagged "typescript" here so it
    # routes to the same parser/builder dispatch as .ts.
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".dart": "dart",
    ".go": "go",
    ".m": "objc",
    ".mm": "objc",
    ".h": "header",  # Disambiguated at scan time via detect_header_language
    ".hpp": "cpp",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
}


def detect_header_language(path_str: str) -> str:
    """Disambiguate .h headers between objc, cpp, and c."""
    try:
        with open(path_str, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read(4096)
        if "@interface" in content or "@protocol" in content or "#import" in content:
            return "objc"
        if "class " in content or "namespace " in content or "template " in content or "std::" in content:
            return "cpp"
        return "c"
    except Exception:
        return "c"

# Layer A: directories to never descend into. Applied even without a .gitignore.
# Covers build output, VCS, deps, caches, and IDE state across stacks so the
# graph is hand-written source, not third-party noise. Keep names lowercase --
# matched case-insensitively against the final path component below.
DEFAULT_SKIP_DIRS = {
    # build output
    "build", "out", "dist", "target", "bin", "obj",
    # vcs
    ".git", ".hg", ".svn",
    # package managers / deps
    "node_modules", ".venv", "venv", "env", "Pods", "vendor",
    "Carthage", ".swiftpm", ".build",
    # gradle/maven
    ".gradle", ".m2",
    # caches / generated
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".next", ".nuxt", ".turbo", ".parcel-cache", "coverage", ".nyc_output",
    # IDE / editor state
    ".idea", ".vscode",
    # codegraph's own store (never index the index)
    ".codegraph",
}

# Layer D: skip files above this size. Generated blobs (minified JS, large
# generated R.java/R.swift) dominate queries they touch and add no value.
# 1 MB matches the upstream default.
MAX_FILE_SIZE = 1_000_000

# Skip-reason constants (stored in skipped_files.reason).
REASON_DEFAULT_SKIP = "default_skip"
REASON_GITIGNORED = "gitignored"
REASON_CONFIG_EXCLUDE = "config_exclude"
REASON_SIZE_CAP = "size_cap"

# Workspace root resolved from the current context (see src/paths.py):
#   CODEGRAPH_WORKSPACE env > registered ancestor > cwd.
# Resolved at import time; a fresh `cg` invocation resolves against the cwd it
# was launched from. Pass an explicit workspace to override.
from codegraph.paths import resolve_workspace as _resolve_workspace

DEFAULT_WORKSPACE = str(_resolve_workspace())


@dataclass
class FileInfo:
    repo: str
    repo_path: str
    path: str  # absolute
    rel_path: str  # relative to repo root
    language: str
    hash: str


@dataclass
class SkipInfo:
    """A file the scanner chose not to index, with the reason."""

    repo: str
    path: str  # absolute
    rel_path: str  # relative to repo root
    reason: str
    size_bytes: Optional[int] = None


def discover_repos(workspace: str = DEFAULT_WORKSPACE) -> List[Path]:
    """Return all immediate subdirectories of `workspace` that contain a `.git`.

    Falls back to treating the workspace root itself as a repo if no child
    directories contain a `.git` but the workspace root does.  This supports
    the common single-repo use-case where ``cg init`` is run inside the
    repository rather than in a parent directory containing multiple repos.
    """
    root = Path(workspace)
    if not root.is_dir():
        return []
    repos = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name in DEFAULT_SKIP_DIRS or child.name.lower() in DEFAULT_SKIP_DIRS:
            continue
        if (child / ".git").exists():
            repos.append(child)
    # Single-repo fallback: workspace root is itself a git repo.
    if not repos and (root / ".git").exists():
        repos.append(root)
    return repos


def is_single_repo_workspace(workspace: str = DEFAULT_WORKSPACE) -> bool:
    """Return True if the workspace root itself is a git repo (no child repos).

    In a multi-repo workspace, repos are subdirectories of the workspace.
    In a single-repo workspace, the workspace root IS the repo.
    """
    root = Path(workspace)
    if not root.is_dir():
        return False
    if not (root / ".git").exists():
        return False
    # If any child directory has .git, this is multi-repo.
    for child in root.iterdir():
        if child.is_dir() and (child / ".git").exists():
            return False
    return True


def resolve_repo_path(workspace: str, repo_name: str) -> Path:
    """Map a repo name to its filesystem path under the workspace.

    Multi-repo: ``workspace/repo_name``
    Single-repo: the workspace root itself (repo_name matches root dir name).
    """
    ws = Path(workspace)
    if is_single_repo_workspace(workspace):
        return ws
    return ws / repo_name


def infer_repo_for_path(abs_path: str, workspace: str) -> Optional[str]:
    """Infer the repo name for an absolute file path under the workspace.

    Multi-repo: first path component under workspace is the repo name.
    Single-repo: workspace root name is the repo name (no sub-repo directory).
    """
    root = Path(workspace).resolve()
    try:
        rel = Path(abs_path).resolve().relative_to(root)
    except ValueError:
        return None
    if not rel.parts:
        return None
    if is_single_repo_workspace(str(root)):
        return root.name
    return rel.parts[0]


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# gitignore loading (Layer B)
# ---------------------------------------------------------------------------

# Cache: repo_root -> list of (dir_of_gitignore, compiled PathSpec).
# A file's gitignore match is the union of all .gitignore files from its own
# directory up to the repo root. We compile each .gitignore once and walk.
# NOTE(phase-2): the file watcher must invalidate this cache (clear the repo
# entry) when a .gitignore changes, so edits to ignore rules take effect.
_gitignore_cache: dict[str, list[tuple[str, pathspec.PathSpec]]] = {}


def _load_gitignores(repo_root: Path) -> list[tuple[str, pathspec.PathSpec]]:
    """Find and compile every .gitignore from repo_root down (one level deep
    into subdirs is NOT done eagerly -- we collect lazily by walking during
    scan). Returns a list of (gitignore_dir_str, spec).

    For correctness with nested .gitignore files, we collect ALL of them under
    the repo via a single walk; each spec is checked against the path RELATIVE
    TO THAT SPEC'S DIRECTORY. This mirrors git's semantics: a pattern in
    ``src/.gitignore`` applies to paths under ``src/``.
    """
    key = str(repo_root)
    cached = _gitignore_cache.get(key)
    if cached is not None:
        return cached

    specs: list[tuple[str, pathspec.PathSpec]] = []
    for gi in repo_root.rglob(".gitignore"):
        try:
            lines = gi.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        # Skip empty-only gitignores (rglob still returns them).
        if not any(ln.strip() and not ln.strip().startswith("#") for ln in lines):
            continue
        try:
            spec = pathspec.PathSpec.from_lines("gitignore", lines)
        except Exception:
            logger.debug("skipping malformed gitignore", exc_info=True)
            continue
        specs.append((str(gi.parent), spec))
    _gitignore_cache[key] = specs
    return specs


def _is_gitignored(abs_path: Path, repo_root: Path,
                   specs: list[tuple[str, pathspec.PathSpec]]) -> bool:
    """True if `abs_path` is ignored by any .gitignore under the repo.

    Each spec is matched against the path relative to that spec's own directory,
    so nested gitignores behave like git.
    """
    abs_str = str(abs_path)
    for gi_dir, spec in specs:
        if abs_str == gi_dir or not abs_str.startswith(gi_dir + os.sep):
            continue
        rel = abs_path.relative_to(gi_dir).as_posix()
        if spec.match_file(rel):
            return True
    return False


# ---------------------------------------------------------------------------
# Config specs (Layer C + include override)
# ---------------------------------------------------------------------------

def _build_config_spec(repo_root: Path):
    """Return (exclude_spec_or_None, include_spec_or_None) from codegraph.json.

    Uses src.graph.config.load_config; compiled into pathspec PathSpecs here so
    the scanner can match in one pass. Patterns are repo-root-relative.
    """
    from .config import load_config

    cfg = load_config(repo_root)
    exclude_spec = (
        pathspec.PathSpec.from_lines("gitignore", cfg.exclude)
        if cfg.exclude else None
    )
    include_spec = (
        pathspec.PathSpec.from_lines("gitignore", cfg.include)
        if cfg.include else None
    )
    return exclude_spec, include_spec


def _match_root_relative(spec, abs_path: Path, repo_root: Path) -> bool:
    """Match a root-relative pathspec (Layer C / include)."""
    try:
        rel = abs_path.relative_to(repo_root).as_posix()
    except ValueError:
        return False
    return spec.match_file(rel)


# ---------------------------------------------------------------------------
# The 4-layer filter
# ---------------------------------------------------------------------------


def _is_under_skip_dir(rel_parts: tuple) -> bool:
    """Layer A: True if any path component is a default skip dir."""
    for part in rel_parts:
        if part in DEFAULT_SKIP_DIRS or part.lower() in DEFAULT_SKIP_DIRS:
            return True
    return False


def classify_file(
    abs_path: Path,
    repo_root: Path,
    gitignore_specs: list[tuple[str, pathspec.PathSpec]],
    exclude_spec,
    include_spec,
) -> Tuple[bool, str]:
    """Return (should_index, reason_if_skipped) for one source file.

    Runs the layers in order. `include` overrides A/B/C (Layer D size cap is
    NOT overridable -- a 50 MB vendored blob helps no one).
    """
    try:
        rel_parts = abs_path.relative_to(repo_root).parts
    except ValueError:
        return False, REASON_DEFAULT_SKIP

    # `include` override: checked first. A path explicitly included skips the
    # A/B/C checks (but still subject to D: size cap).
    if include_spec is not None and _match_root_relative(include_spec, abs_path, repo_root):
        # Still enforce size cap even for included files.
        try:
            size = abs_path.stat().st_size
        except OSError:
            return False, REASON_DEFAULT_SKIP
        if size > MAX_FILE_SIZE:
            return False, REASON_SIZE_CAP
        return True, ""

    # Layer A: default skip dirs.
    if _is_under_skip_dir(rel_parts):
        return False, REASON_DEFAULT_SKIP

    # Layer B: gitignore.
    if _is_gitignored(abs_path, repo_root, gitignore_specs):
        return False, REASON_GITIGNORED

    # Layer C: codegraph.json exclude.
    if exclude_spec is not None and _match_root_relative(exclude_spec, abs_path, repo_root):
        return False, REASON_CONFIG_EXCLUDE

    # Layer D: size cap.
    try:
        size = abs_path.stat().st_size
    except OSError:
        return False, REASON_DEFAULT_SKIP
    if size > MAX_FILE_SIZE:
        return False, REASON_SIZE_CAP

    return True, ""


def is_source_file(path: Path) -> bool:
    """True if `path` has a known source extension. Pure extension check only.

    Reused by the file watcher to decide whether a changed file is worth
    re-indexing. Does NOT apply the full 4-layer filter -- use
    :func:`classify_file` for that.
    """
    return path.suffix in EXTENSION_MAP


def _is_skipped(path: Path, repo_root: Path) -> bool:
    """Convenience: would the 4-layer filter skip this file?

    For the file watcher's event gate. Loads gitignores + config fresh each
    call (acceptable for a watcher hot path that fires per-change; the heavy
    rglob in _load_gitignores is cached per-repo).
    """
    if not is_source_file(path):
        return True
    specs = _load_gitignores(repo_root)
    exclude_spec, include_spec = _build_config_spec(repo_root)
    should_index, _ = classify_file(path, repo_root, specs, exclude_spec, include_spec)
    return not should_index


def iter_source_files(repo_path: Path) -> Iterator[Path]:
    """Yield source files under a repo that pass the 4-layer filter.

    Backwards-compatible signature: callers that don't need skip reporting still
    get just the files to index. For skip reporting, use :func:`scan_repo_with_skips`.
    """
    repo_path = Path(repo_path)
    specs = _load_gitignores(repo_path)
    exclude_spec, include_spec = _build_config_spec(repo_path)
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in EXTENSION_MAP:
            continue
        should_index, _ = classify_file(
            path, repo_path, specs, exclude_spec, include_spec
        )
        if should_index:
            yield path


def iter_files_and_skips(repo_path: Path) -> Tuple[List[FileInfo], List[SkipInfo]]:
    """Scan a repo, returning both files-to-index and files-skipped.

    Entry point used by the builder: returns the indexed FileInfos AND the
    SkipInfos so the builder can record both (symbols/edges for the former,
    skipped_files rows for the latter).
    """
    repo_path = Path(repo_path)
    specs = _load_gitignores(repo_path)
    exclude_spec, include_spec = _build_config_spec(repo_path)

    files: List[FileInfo] = []
    skips: List[SkipInfo] = []
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in EXTENSION_MAP:
            continue  # not a source file at all; neither indexed nor "skipped"
        should_index, reason = classify_file(
            path, repo_path, specs, exclude_spec, include_spec
        )
        rel = str(path.relative_to(repo_path))
        if should_index:
            files.append(
                FileInfo(
                    repo=repo_path.name,
                    repo_path=str(repo_path),
                    path=str(path),
                    rel_path=rel,
                    language=EXTENSION_MAP[path.suffix],
                    hash=file_sha256(path),
                )
            )
        else:
            size = None
            try:
                size = path.stat().st_size
            except OSError:
                pass
            skips.append(
                SkipInfo(
                    repo=repo_path.name,
                    path=str(path),
                    rel_path=rel,
                    reason=reason,
                    size_bytes=size,
                )
            )
    return files, skips


def scan_repo(repo_path: Path) -> List[FileInfo]:
    """Enumerate all source files in a single repo (backwards-compatible).

    Returns only the files to index; skips are not reported here. New callers
    should prefer :func:`iter_files_and_skips`.
    """
    files, _ = iter_files_and_skips(repo_path)
    return files


def scan_workspace(
    workspace: str = DEFAULT_WORKSPACE, repo_filter: Optional[str] = None
) -> List[FileInfo]:
    """Scan all repos under workspace. If repo_filter given, scan only that repo."""
    if repo_filter:
        repo_path = resolve_repo_path(workspace, repo_filter)
        if not (repo_path / ".git").exists():
            return []
        return scan_repo(repo_path)

    files = []
    for repo in discover_repos(workspace):
        files.extend(scan_repo(repo))
    return files


def infer_repo_language(files: List[FileInfo]) -> Optional[str]:
    """Dominant language for a repo (by file count)."""
    counts: dict[str, int] = {}
    for f in files:
        counts[f.language] = counts.get(f.language, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)
