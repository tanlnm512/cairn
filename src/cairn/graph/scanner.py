"""Repo scanner: discover repos and enumerate source files by language."""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path, PosixPath, WindowsPath
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
    # (parsers/typescript.py) but is tagged "typescript" here so it routes
    # to the same parser/builder dispatch as .ts.
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
    # PHP/Ruby web languages.
    ".php": "php",
    ".phtml": "php",
    ".php3": "php",
    ".php4": "php",
    ".php5": "php",
    ".rb": "ruby",
    ".rbw": "ruby",
    ".rs": "rust",
    ".cs": "csharp",
    ".csx": "csharp",
    ".m": "objc",
    ".mm": "objc",
    # `.h` is ambiguous across C/C++/Objective-C, so it routes to the sentinel
    # "header" and is resolved to the sniffed language (objc/cpp/c) by
    # resolve_file_language() at FileInfo construction time.
    ".h": "header",
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


def resolve_file_language(suffix: str, abs_path: str) -> str:
    """Map a suffix to its parser language, sniffing ambiguous headers."""
    lang = EXTENSION_MAP.get(suffix)
    if lang == "header":
        return detect_header_language(abs_path)
    return lang or ""

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
    # cairn's own store (never index the index)
    ".cairn",
}

# Layer D: skip files above this size. Generated blobs (minified JS, large
# generated R.java/R.swift) dominate queries and add no value.
MAX_FILE_SIZE = 1_000_000

# Skip-reason constants (stored in skipped_files.reason).
REASON_DEFAULT_SKIP = "default_skip"
REASON_GITIGNORED = "gitignored"
REASON_CONFIG_EXCLUDE = "config_exclude"
REASON_SIZE_CAP = "size_cap"
REASON_MINIFIED = "minified_asset"
REASON_PARSER_UNAVAILABLE = "parser_unavailable"

# Workspace root resolved from the current context (see src/paths.py):
#   CAIRN_WORKSPACE env > registered ancestor > cwd. Resolved at import time;
#   pass an explicit workspace to override.
from cairn.paths import resolve_workspace as _resolve_workspace

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


@dataclass(frozen=True)
class RepositoryRecord:
    repo_id: str
    path: Path


# Use the concrete base where Path itself remains abstract.
_RepositoryPathBase: type = (
    Path if hasattr(Path, "parser")
    else (WindowsPath if os.name == "nt" else PosixPath)
)


class RepositoryPath(_RepositoryPathBase):
    """Path carrying the stable repository id used by graph storage."""

    __slots__ = ("repo_id",)
    repo_id: str


def _repository_path(record: RepositoryRecord) -> RepositoryPath:
    path = RepositoryPath(str(record.path))
    path.repo_id = record.repo_id
    return path


def repository_id(repo_path: Path) -> str:
    """Return the stable repository id carried by a discovered repo path."""
    return getattr(repo_path, "repo_id", repo_path.name)


def _descendant_directories(root: Path) -> Iterator[Path]:
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.is_symlink():
            continue
        if child.name in DEFAULT_SKIP_DIRS or child.name.lower() in DEFAULT_SKIP_DIRS:
            continue
        yield child
        yield from _descendant_directories(child)


def _top_level_records(root: Path) -> list[RepositoryRecord]:
    records = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name in DEFAULT_SKIP_DIRS or child.name.lower() in DEFAULT_SKIP_DIRS:
            continue
        if (child / ".git").exists():
            records.append(RepositoryRecord(child.name, child))
    if records:
        return records
    if (root / ".git").exists():
        return [RepositoryRecord(root.name, root)]
    return []


def discover_repo_records(workspace: str = DEFAULT_WORKSPACE) -> list[RepositoryRecord]:
    """Return stable ids and roots for every repository selected by config."""
    root = Path(workspace)
    if not root.is_dir():
        return []

    top_level = _top_level_records(root)
    records: list[RepositoryRecord] = []
    workspace_config = None
    for record in top_level:
        records.append(record)
        if workspace_config is None:
            from .config import load_config

            workspace_config = load_config(root)
        include_nested = workspace_config.include_nested_repos or (
            load_config(record.path).include_nested_repos
        )
        if not include_nested:
            continue

        for candidate in _descendant_directories(record.path):
            has_git = (candidate / ".git").exists()
            if not has_git:
                continue
            relative = candidate.relative_to(record.path).as_posix()
            records.append(
                RepositoryRecord(f"{record.repo_id}/{relative}", candidate)
            )
    return sorted(records, key=lambda item: str(item.path))


def discover_repos(workspace: str = DEFAULT_WORKSPACE) -> List[Path]:
    """Return configured repository roots, falling back to the workspace root."""
    return [
        _repository_path(record)
        for record in discover_repo_records(workspace)
    ]


def is_single_repo_workspace(workspace: str = DEFAULT_WORKSPACE) -> bool:
    """Return whether the workspace root itself is the repository."""
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
    """Map a repository name to its workspace path."""
    for record in discover_repo_records(workspace):
        if record.repo_id == repo_name:
            return _repository_path(record)
    ws = Path(workspace)
    if is_single_repo_workspace(workspace):
        return ws
    return ws / repo_name


def resolve_file_path(workspace: str, repo_id: str, stored_path: str) -> str:
    """Resolve a stored graph path to an absolute disk path."""
    if Path(stored_path).is_absolute():
        return stored_path  # absolute path stored as-is
    return str(resolve_repo_path(workspace, repo_id) / stored_path)


def infer_repo_for_path(abs_path: str, workspace: str) -> Optional[str]:
    """Return the longest owning repository name for a path."""
    root = Path(workspace).resolve()
    try:
        rel = Path(abs_path).resolve().relative_to(root)
    except ValueError:
        return None
    if not rel.parts:
        return None
    resolved_path = Path(abs_path).resolve()
    matching = [
        record
        for record in discover_repo_records(workspace)
        if resolved_path != record.path
        and str(resolved_path).startswith(str(record.path.resolve()) + os.sep)
    ]
    if matching:
        return max(matching, key=lambda record: len(record.path.parts)).repo_id
    return None


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# gitignore loading (Layer B)
# ---------------------------------------------------------------------------

# Watchers must invalidate this cache when a gitignore changes.
_gitignore_cache: dict[str, list[tuple[str, pathspec.PathSpec]]] = {}


def _load_gitignores(repo_root: Path) -> list[tuple[str, pathspec.PathSpec]]:
    """Return compiled nested gitignore specifications for a repository."""
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
    """Return whether any nested gitignore excludes a path."""
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
    """Return compiled repository-root include and exclude specifications."""
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


def _is_minified(path: Path) -> bool:
    """Return whether a filename marks a minified generated bundle."""
    return ".min." in path.name.lower()


def classify_file(
    abs_path: Path,
    repo_root: Path,
    gitignore_specs: list[tuple[str, pathspec.PathSpec]],
    exclude_spec,
    include_spec,
) -> Tuple[bool, str]:
    """Return indexing eligibility and skip reason for one file."""
    try:
        rel_parts = abs_path.relative_to(repo_root).parts
    except ValueError:
        return False, REASON_DEFAULT_SKIP

    # `include` override: checked first. A path explicitly included skips the
    # A/B/C checks (but still subject to D: minified skip + size cap).
    if include_spec is not None and _match_root_relative(include_spec, abs_path, repo_root):
        # Still enforce the Layer D generated-asset skips even for
        # included files.
        if _is_minified(abs_path):
            return False, REASON_MINIFIED
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

    # Layer C: cairn.json exclude.
    if exclude_spec is not None and _match_root_relative(exclude_spec, abs_path, repo_root):
        return False, REASON_CONFIG_EXCLUDE

    # Layer D: generated assets — minified bundles, then the size cap.
    if _is_minified(abs_path):
        return False, REASON_MINIFIED
    try:
        size = abs_path.stat().st_size
    except OSError:
        return False, REASON_DEFAULT_SKIP
    if size > MAX_FILE_SIZE:
        return False, REASON_SIZE_CAP

    return True, ""


def is_source_file(path: Path) -> bool:
    """Return whether a path has a known source extension."""
    return path.suffix in EXTENSION_MAP


def _is_skipped(path: Path, repo_root: Path) -> bool:
    """Return whether the full scan filter skips a path."""
    if not is_source_file(path):
        return True
    specs = _load_gitignores(repo_root)
    exclude_spec, include_spec = _build_config_spec(repo_root)
    should_index, _ = classify_file(path, repo_root, specs, exclude_spec, include_spec)
    return not should_index


def _iter_repo_files(repo_path: Path) -> Iterator[Path]:
    boundaries = {
        str(path.resolve())
        for path in _descendant_directories(repo_path)
        if (path / ".git").exists()
    }
    for directory, names, files in os.walk(repo_path):
        kept = []
        for name in sorted(names):
            child = Path(directory) / name
            if child.is_symlink():
                continue
            if name in DEFAULT_SKIP_DIRS or name.lower() in DEFAULT_SKIP_DIRS:
                continue
            if str(child.resolve()) in boundaries:
                continue
            kept.append(name)
        names[:] = kept
        for name in sorted(files):
            child = Path(directory) / name
            # Symlinked files are skipped like symlinked dirs: their content
            # lives outside the repo root and must not enter the graph.
            if child.is_symlink():
                continue
            yield child


def _grammar_available(language: str) -> bool:
    """True if the parser for `language` can load in this process."""
    from ..parsers._registry import is_language_available

    return is_language_available(language)


def iter_source_files(repo_path: Path) -> Iterator[Path]:
    """Yield repository source files that pass all scan filters."""
    repo_path = Path(repo_path)
    specs = _load_gitignores(repo_path)
    exclude_spec, include_spec = _build_config_spec(repo_path)
    grammar_available: dict[str, bool] = {}
    for path in _iter_repo_files(repo_path):
        if not path.is_file():
            continue
        if path.suffix not in EXTENSION_MAP:
            continue
        should_index, _ = classify_file(
            path, repo_path, specs, exclude_spec, include_spec
        )
        if not should_index:
            continue
        language = EXTENSION_MAP[path.suffix]
        available = grammar_available.get(language)
        if available is None:
            available = grammar_available[language] = _grammar_available(language)
        if not available:
            # Parser-unavailable files must never be yielded: a caller that
            # cannot index them (drift scan) would flag them forever.
            continue
        yield path


def iter_files_and_skips(repo_path: Path) -> Tuple[List[FileInfo], List[SkipInfo]]:
    """Return repository files to index and records for skipped files."""
    repo_id = repository_id(repo_path)
    repo_path = Path(repo_path)
    specs = _load_gitignores(repo_path)
    exclude_spec, include_spec = _build_config_spec(repo_path)

    files: List[FileInfo] = []
    skips: List[SkipInfo] = []
    grammar_available: dict[str, bool] = {}
    for path in _iter_repo_files(repo_path):
        if not path.is_file():
            continue
        if path.suffix not in EXTENSION_MAP:
            continue  # not a source file at all; neither indexed nor "skipped"
        should_index, reason = classify_file(
            path, repo_path, specs, exclude_spec, include_spec
        )
        rel = str(path.relative_to(repo_path))
        if should_index:
            language = resolve_file_language(path.suffix, str(path))
            available = grammar_available.get(language)
            if available is None:
                available = grammar_available[language] = _grammar_available(language)
            if not available:
                skips.append(
                    SkipInfo(
                        repo=repo_id,
                        path=str(path),
                        rel_path=rel,
                        reason=REASON_PARSER_UNAVAILABLE,
                    )
                )
                continue
            files.append(
                FileInfo(
                    repo=repo_id,
                    repo_path=str(repo_path),
                    path=str(path),
                    rel_path=rel,
                    language=language,
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
                    repo=repo_id,
                    path=str(path),
                    rel_path=rel,
                    reason=reason,
                    size_bytes=size,
                )
            )
    return files, skips


def scan_repo(repo_path: Path) -> List[FileInfo]:
    """Return all source files selected in one repository."""
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
    return max(counts, key=lambda name: counts[name])
