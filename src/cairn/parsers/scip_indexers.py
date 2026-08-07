"""Generic SCIP indexer orchestrator.

cairn is a *consumer* of SCIP indexes, never a producer -- indexes are generated
out-of-band (CI, a make target) by compiler-grade indexers and pointed at via
``cairn.json``. This module is the one bounded exception: during ``cairn build``,
if a language declares a SCIP index in ``cairn.json`` but the index file is
*missing* and a known indexer binary is on PATH, the orchestrator runs the
indexer once to produce the file. It then hands off to the existing importer.

Design rules (matching cairn's "never break the build over an external tool"
discipline -- see ``utils/git.py`` and the SCIP import hook in ``builder.py``):

- **Auto but bounded**: generation triggers only when the index is configured
  *and absent*. An existing index is never rebuilt -- the user (or CI) owns the
  regeneration cadence. Re-running a full compiler build on every ``cairn
  build`` would be unreasonable.
- **Never raises**: a missing binary, a nonzero exit, an OS error, or a timeout
  is logged (visible under ``-v``) and falls back to tree-sitter. The caller's
  ``if idx_path.exists()`` gate then naturally skips the language.
- **No language-specific code**: each known indexer is an :class:`IndexerSpec`
  row in a registry keyed by language. Swift is one entry alongside Kotlin and
  TypeScript; adding another (Java, Python, Go, Rust) is one line.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

# A build is the heavyweight step (full Swift/Xcode compile, ts-server crawl).
# Give it generous headroom without blocking a build indefinitely.
_INDEX_TIMEOUT_S = 30 * 60


@dataclass(frozen=True)
class IndexerSpec:
    """How to invoke one known SCIP indexer for a language.

    Attributes:
        language: scanner language name (must match a ``cairn.json`` scip key),
            e.g. ``"swift"``.
        tool: the binary name looked up on PATH, e.g. ``"scip-swift"``.
        build_command: ``(repo_path, output_path) -> argv list``. Each indexer
            has a slightly different argument order, so the command is built
            per-spec rather than templated.
        install_hint: surfaced in verbose logs when the tool is missing, so the
            user knows where to get it.
    """

    language: str
    tool: str
    build_command: Callable[[str, str], List[str]]
    install_hint: str


def _swift_cmd(repo: str, out: str) -> List[str]:
    # scip-swift: `scip-swift index <repo> --output <out>` (defaultSubcommand).
    return ["scip-swift", "index", repo, "--output", out]


def _kotlin_cmd(repo: str, out: str) -> List[str]:
    # scip-kotlin: `scip-kotlin index --output <out> <repo>`.
    return ["scip-kotlin", "index", "--output", out, repo]


def _typescript_cmd(repo: str, out: str) -> List[str]:
    # scip-typescript: `scip-typescript index --output <out> <repo>`.
    return ["scip-typescript", "index", "--output", out, repo]


# Registry of indexers cairn knows how to drive. Keyed by the scanner language
# name (the same string used as a ``cairn.json`` ``scip`` key), so a config like
# ``{"scip": {"swift": "build/scip/swift.scip"}}`` looks up the swift spec.
# Languages not in this map simply aren't auto-generated; a committed index for
# them is still consumed unchanged.
_KNOWN_INDEXERS: Dict[str, IndexerSpec] = {
    "swift": IndexerSpec(
        language="swift",
        tool="scip-swift",
        build_command=_swift_cmd,
        install_hint=(
            "scip-swift is the SCIP indexer for Swift. Build it from source on a "
            "Mac (macOS/Xcode only): https://github.com/phuongddx/scip-swift"
        ),
    ),
    "kotlin": IndexerSpec(
        language="kotlin",
        tool="scip-kotlin",
        build_command=_kotlin_cmd,
        install_hint=(
            "scip-kotlin is the SCIP indexer for Kotlin. "
            "See https://github.com/sourcegraph/scip-kotlin"
        ),
    ),
    "typescript": IndexerSpec(
        language="typescript",
        tool="scip-typescript",
        build_command=_typescript_cmd,
        install_hint=(
            "scip-typescript is the SCIP indexer for TypeScript/JavaScript. "
            "See https://github.com/sourcegraph/scip-typescript"
        ),
    ),
}


def known_languages() -> List[str]:
    """Languages for which cairn can auto-generate a missing index."""
    return sorted(_KNOWN_INDEXERS)


def spec_for(language: str) -> Optional[IndexerSpec]:
    """Return the registered indexer spec for a language, or ``None``."""
    return _KNOWN_INDEXERS.get(language)


def try_generate_index(
    language: str,
    output_path: Path,
    repo_path: str,
    log: Callable[..., None] = lambda *a, **k: None,
) -> bool:
    """Generate a missing SCIP index if a known indexer is installed.

    Returns ``True`` iff the index file now exists at ``output_path``. **Never
    raises** -- a missing/failing/timeout indexer logs (visible under ``-v``)
    and returns ``False``, so the caller's existence gate falls back to
    tree-sitter for that language. Mirrors the discipline of
    :func:`utils.git._run_git` and the SCIP import hook.

    ``output_path``'s parent directory is created if needed; the indexer is
    responsible for writing the file there.
    """
    spec = spec_for(language)
    if spec is None:
        # Not a known language -- nothing to auto-generate. A committed index
        # for it is still consumed; this path just isn't one cairn can produce.
        return False

    if output_path.exists():
        # Already present (caller normally checks this first, but be idempotent
        # in case of a race or a direct call): never rebuild an existing index.
        return True

    if not shutil.which(spec.tool):
        log(f"  scip[{language}]: index missing and '{spec.tool}' not on PATH; "
            f"{spec.install_hint}")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = spec.build_command(repo_path, str(output_path))
    log(f"  scip[{language}]: generating index with {spec.tool} -> {output_path}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_INDEX_TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError) as e:
        # FileNotFoundError (subclass of OSError) covers a binary that vanished
        # between the shutil.which probe and exec; TimeoutExpired is a
        # SubprocessError subclass. All degrade identically: log + fallback.
        log(f"  scip[{language}]: {spec.tool} invocation failed ({e}); "
            f"falling back to tree-sitter")
        return False

    if result.returncode != 0 or not output_path.exists():
        tail = (result.stderr or "").strip().splitlines()[-5:]
        log(f"  scip[{language}]: {spec.tool} exited {result.returncode} "
            f"without producing an index; falling back to tree-sitter")
        if tail:
            log("    " + "\n    ".join(tail))
        return False

    return True
