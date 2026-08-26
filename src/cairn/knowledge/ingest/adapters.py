"""Source adapters for staged document ingestion.

Every adapter implements SourceAdapter and yields one tuple per document:
``(repo, relpath, text, origin)``. Adapters are read-only iterators over
their source; parsing, classification, staging, and store writes happen
downstream.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator, List, Protocol, Tuple, Union

logger = logging.getLogger(__name__)

#: Origin label for directly-fed documents.
FED_ORIGIN = "fed"

#: Fed documents have no originating repo; the workspace is their scope.
FED_REPO = "workspace"

#: The adapter yield contract: (repo, relpath, text, origin).
SourcedDoc = Tuple[str, str, str, str]


class SourceAdapter(Protocol):
    """A documentation source, iterated one document at a time.

    iter_docs() yields SourcedDoc tuples:

    repo -- originating repository; FED_REPO for fed documents
    relpath -- path relative to the feed root; a fed file uses its path
        as given
    text -- decoded document content
    origin -- provenance label, e.g. FED_ORIGIN
    """

    def iter_docs(self) -> Iterator[SourcedDoc]: ...


class FedMarkdownAdapter:
    """Feeds explicitly-given markdown files and/or directories.

    Directories are walked recursively for ``*.md`` in sorted order; a fed
    file yields only when its suffix is ``.md``. Every yield carries
    repo=FED_REPO and origin=FED_ORIGIN.
    """

    def __init__(self, paths: Iterable[Union[str, Path]]) -> None:
        self._paths: List[Path] = [Path(p) for p in paths]

    def iter_docs(self) -> Iterator[SourcedDoc]:
        for path in self._paths:
            if not path.exists():
                raise FileNotFoundError(f"Fed path does not exist: {path}")
            if path.is_dir():
                yield from self._iter_directory(path)
            elif path.suffix == ".md":
                yield from self._yield_document(path, path.as_posix())
            else:
                logger.warning("Skipping non-markdown fed file %s", path)

    def _iter_directory(self, root: Path) -> Iterator[SourcedDoc]:
        for md_file in sorted(root.rglob("*.md")):
            relpath = md_file.relative_to(root).as_posix()
            yield from self._yield_document(md_file, relpath)

    def _yield_document(self, path: Path, relpath: str) -> Iterator[SourcedDoc]:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Skipping unreadable fed document %s: %s", path, e)
            return
        yield (FED_REPO, relpath, text, FED_ORIGIN)

#: Allowlist of repository doc directories walked by RepoScanAdapter (FR-001).
DEFAULT_DOC_DIRS: Tuple[str, ...] = ("docs", "decisions", "adr", "adrs")


@dataclass(frozen=True)
class SkipRule:
    """One skip-list matcher: a glob pattern plus its reason.

    kind "dir" matches any directory part of the repo-relative path;
    kind "file" matches the file name. Patterns compare lowercased.
    ``category`` names the built-in group a workspace config can
    disable (FR-010); workspace-added rules use the "workspace" group.
    """

    kind: str
    pattern: str
    reason: str
    category: str = ""


#: FR-001 skip-list for knowledge ingestion. Distinct from the graph
#: scanner's DEFAULT_SKIP_DIRS (src/cairn/graph/scanner.py:102), which is
#: code-indexing only and logs no reason; every rule here carries one.
SKIP_LIST: Tuple[SkipRule, ...] = (
    SkipRule("dir", "drafts", "skip-list: drafts directory", "drafts"),
    SkipRule("dir", "meetings", "skip-list: meetings directory", "meetings"),
    SkipRule("dir", "*_generated", "skip-list: generated mirror directory", "generated"),
    SkipRule("dir", "changelogs", "skip-list: changelogs directory", "changelogs"),
    SkipRule("dir", "templates", "skip-list: templates directory", "templates"),
    SkipRule("file", "changelog*", "skip-list: changelog file", "changelogs"),
    SkipRule("file", "template*", "skip-list: template file", "templates"),
    SkipRule("file", "*-template.md", "skip-list: template file", "templates"),
    SkipRule("file", "meeting-notes*", "skip-list: meeting-notes file", "meetings"),
)


def _effective_rules(
    skip_add: Tuple[str, ...], skip_disable: Tuple[str, ...]
) -> Tuple[SkipRule, ...]:
    """Built-in rules minus disabled categories, plus workspace patterns."""
    disabled = {category.lower() for category in skip_disable}
    rules = [rule for rule in SKIP_LIST if rule.category.lower() not in disabled]
    for pattern in skip_add:
        kind = "dir" if pattern.endswith("/") else "file"
        rules.append(
            SkipRule(
                kind, pattern.rstrip("/").lower(),
                f"skip-list: {pattern} (workspace)", "workspace",
            )
        )
    return tuple(rules)


def _skip_reason(
    rel: PurePosixPath, rules: Tuple[SkipRule, ...] = SKIP_LIST
) -> str | None:
    """First matching rule's reason for a repo-relative doc path, if any."""
    parts = rel.parts
    for rule in rules:
        if rule.kind == "dir":
            if any(fnmatch(part.lower(), rule.pattern) for part in parts[:-1]):
                return rule.reason
        elif fnmatch(parts[-1].lower(), rule.pattern):
            return rule.reason
    return None


class RepoScanAdapter:
    """Walks a repository's allowlisted doc directories (FR-001).

    Each configured doc dir is walked recursively for ``*.md`` in sorted
    order. Skip-listed documents are not yielded; they are recorded in
    :attr:`skipped` as ``(relpath, reason)`` pairs and logged with their
    reason.
    """

    def __init__(
        self,
        repo_root: Union[str, Path],
        doc_dirs: Tuple[str, ...] = DEFAULT_DOC_DIRS,
        skip_add: Tuple[str, ...] = (),
        skip_disable: Tuple[str, ...] = (),
    ) -> None:
        self._repo_root = Path(repo_root)
        self._doc_dirs = tuple(doc_dirs)
        self._rules = _effective_rules(tuple(skip_add), tuple(skip_disable))
        #: Every skip as a ``(relpath, reason)`` pair, in walk order.
        self.skipped: List[Tuple[str, str]] = []

    def iter_docs(self) -> Iterator[SourcedDoc]:
        if not self._repo_root.is_dir():
            raise FileNotFoundError(f"Repo root does not exist: {self._repo_root}")
        self.skipped = []
        repo = self._repo_root.name
        for doc_dir in self._doc_dirs:
            root = self._repo_root / doc_dir
            if not root.is_dir():
                continue
            for md_file in sorted(root.rglob("*.md")):
                rel = md_file.relative_to(self._repo_root)
                reason = _skip_reason(rel, self._rules)
                if reason is not None:
                    self.skipped.append((rel.as_posix(), reason))
                    logger.info("Skipping %s: %s", rel.as_posix(), reason)
                    continue
                try:
                    text = md_file.read_text(encoding="utf-8")
                except Exception as e:
                    self.skipped.append((rel.as_posix(), f"unreadable: {e}"))
                    logger.warning("Skipping unreadable document %s: %s", rel.as_posix(), e)
                    continue
                yield (repo, rel.as_posix(), text, repo)


#: Origin label for documents converted from binary formats (FR-003).
CONVERTED_ORIGIN = "converted"


class FedBinaryAdapter:
    """Feeds pdf/docx files through the ``cairn[ingest]`` converter.

    Converted markdown enters the pipeline with origin "converted";
    a missing extra or a garbage extraction skips with a reason instead
    of crashing the run.
    """

    def __init__(self, paths: Iterable[Union[str, Path]]) -> None:
        self._paths: List[Path] = [Path(p) for p in paths]
        #: Every skip as a ``(relpath, reason)`` pair.
        self.skipped: List[Tuple[str, str]] = []

    def iter_docs(self) -> Iterator[SourcedDoc]:
        from cairn.knowledge.ingest.convert import CONVERT_SUFFIXES, convert_document

        self.skipped = []
        for path in self._paths:
            if not path.exists():
                raise FileNotFoundError(f"Fed path does not exist: {path}")
            if path.suffix.lower() not in CONVERT_SUFFIXES:
                continue
            relpath = path.as_posix()
            markdown, reason = convert_document(path)
            if reason is not None:
                self.skipped.append((relpath, reason))
                logger.info("Skipping %s: %s", relpath, reason)
                continue
            yield (FED_REPO, relpath, markdown, CONVERTED_ORIGIN)
