"""Bounded auto-generation of missing SCIP indexes via known indexer binaries."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

# A real index run is a heavyweight step (full compile); bound it generously.
_INDEX_TIMEOUT_S = 30 * 60


def _no_env(out: str) -> Dict[str, str]:
    return {}


@dataclass(frozen=True)
class IndexerSpec:
    """How to invoke one known SCIP indexer: PATH binary, argv template, env template, install hint."""

    language: str
    tool: str
    build_command: Callable[[str, str], List[str]]
    env: Callable[[str], Dict[str, str]] = _no_env
    install_hint: str = ""


def _swift_cmd(repo: str, out: str) -> List[str]:
    # scip-swift: `scip-swift index <repo> --output <out>` (macOS/Xcode only).
    return ["scip-swift", "index", repo, "--output", out]


def _scip_java_cmd(repo: str, out: str) -> List[str]:
    # scip-java: `scip-java index --output <out>`, run from the project root.
    return ["scip-java", "index", "--output", out]


def _scip_typescript_cmd(repo: str, out: str) -> List[str]:
    # scip-typescript: `scip-typescript index --output <out>`.
    return ["scip-typescript", "index", "--output", out]


def _scip_python_cmd(repo: str, out: str) -> List[str]:
    # scip-python: `scip-python index <repo> --output=<out>`; npm, not pip.
    return ["scip-python", "index", repo, f"--output={out}"]


def _scip_go_cmd(repo: str, out: str) -> List[str]:
    # scip-go: `scip-go --output=<out>` (no `index` subcommand).
    return ["scip-go", f"--output={out}"]


def _rust_cmd(repo: str, out: str) -> List[str]:
    # rust-analyzer scip subcommand: the output path comes from RA_SCIPOUT, not a flag.
    return ["rust-analyzer", "scip", repo]


def _rust_env(out: str) -> Dict[str, str]:
    return {"RA_SCIPOUT": out}


_SCIP_JAVA_HINT = (
    "scip-java is the canonical SCIP indexer for Java AND Kotlin (the old "
    "scip-kotlin has been merged in). It requires a Gradle or Maven build. "
    "See https://github.com/sourcegraph/scip-java"
)

# Keyed by the scanner language name (the ``cairn.json`` ``scip.indexes`` key).
# Languages not in this map are never auto-generated; a committed index for
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
    "java": IndexerSpec(
        language="java",
        tool="scip-java",
        build_command=_scip_java_cmd,
        install_hint=_SCIP_JAVA_HINT,
    ),
    "kotlin": IndexerSpec(
        language="kotlin",
        # scip-kotlin is archived; scip-java indexes mixed Java+Kotlin sources.
        tool="scip-java",
        build_command=_scip_java_cmd,
        install_hint=_SCIP_JAVA_HINT,
    ),
    "typescript": IndexerSpec(
        language="typescript",
        tool="scip-typescript",
        build_command=_scip_typescript_cmd,
        install_hint=(
            "scip-typescript is the SCIP indexer for TypeScript/JavaScript. "
            "Install: `npm install -g @sourcegraph/scip-typescript`. "
            "See https://github.com/sourcegraph/scip-typescript"
        ),
    ),
    "python": IndexerSpec(
        language="python",
        tool="scip-python",
        build_command=_scip_python_cmd,
        install_hint=(
            "scip-python is the SCIP indexer for Python. It's an npm package "
            "(@sourcegraph/scip-python), not pip: "
            "`npm install -g @sourcegraph/scip-python`. "
            "See https://github.com/sourcegraph/scip-python"
        ),
    ),
    "go": IndexerSpec(
        language="go",
        tool="scip-go",
        build_command=_scip_go_cmd,
        install_hint=(
            "scip-go is the SCIP indexer for Go. Install: "
            "`go install github.com/scip-code/scip-go/cmd/scip-go@latest`. "
            "See https://github.com/scip-code/scip-go"
        ),
    ),
    "rust": IndexerSpec(
        language="rust",
        tool="rust-analyzer",
        build_command=_rust_cmd,
        env=_rust_env,
        install_hint=(
            "rust-analyzer's `scip` subcommand indexes Rust (output via the "
            "RA_SCIPOUT env var). Install rust-analyzer via rustup or your "
            "toolchain. See https://github.com/rust-lang/rust-analyzer"
        ),
    ),
}


def known_languages() -> List[str]:
    """Languages cairn can auto-generate a missing index for, sorted."""
    return sorted(_KNOWN_INDEXERS)


def spec_for(language: str) -> Optional[IndexerSpec]:
    """The registered indexer spec for ``language``, or None."""
    return _KNOWN_INDEXERS.get(language)


# skipped_files.reason values for generation failures; the column is CHECK-free
# TEXT and skipped_by_reason aggregates whatever values land there.
GEN_MISSING_BINARY = "scip_gen_missing_binary"
GEN_NONZERO_EXIT = "scip_gen_nonzero_exit"
GEN_TIMEOUT = "scip_gen_timeout"
GEN_OS_ERROR = "scip_gen_os_error"


@dataclass(frozen=True)
class GenerationResult:
    """One generation attempt: ok=True, or a scip_gen_* reason with a human detail."""

    ok: bool
    reason: Optional[str] = None
    detail: str = ""


def _failure(
    spec: IndexerSpec,
    reason: str,
    detail: str,
    log: Callable[..., None],
) -> GenerationResult:
    """Degrade one attempt: log the cause and the install hint, return the skip outcome."""
    prefix = f"  scip[{spec.language}]: "
    log(f"{prefix}{detail}; falling back to tree-sitter")
    log(f"{prefix}{spec.install_hint}")
    return GenerationResult(ok=False, reason=reason, detail=f"{detail}; {spec.install_hint}")


def generate_index_result(
    language: str,
    output_path: Path,
    repo_path: str,
    log: Callable[..., None] = lambda *a, **k: None,
) -> GenerationResult:
    """Attempt one bounded index generation, classifying failure as a scip_gen_* reason. Never raises."""
    spec = spec_for(language)
    if spec is None:
        return GenerationResult(ok=False, detail=f"no registered indexer for '{language}'")

    if output_path.exists():
        # Never rebuild an existing index -- the user (or CI) owns regeneration.
        return GenerationResult(ok=True)

    try:
        return _run_tool(spec, output_path, repo_path, log)
    except subprocess.TimeoutExpired:
        return _failure(spec, GEN_TIMEOUT,
                        f"{spec.tool} timed out after {_INDEX_TIMEOUT_S}s", log)
    except (subprocess.SubprocessError, OSError) as e:
        # FileNotFoundError (binary vanished between probe and exec) and other
        # invocation-level faults degrade identically.
        return _failure(spec, GEN_OS_ERROR, f"{spec.tool} invocation failed ({e})", log)
    except Exception as e:
        # One registry entry degrades independently, never fatally.
        return _failure(spec, GEN_OS_ERROR, f"{spec.tool} failed ({e})", log)


def _run_tool(
    spec: IndexerSpec,
    output_path: Path,
    repo_path: str,
    log: Callable[..., None],
) -> GenerationResult:
    """Run the spec's indexer once under the bounded timeout and classify the outcome."""
    if not shutil.which(spec.tool):
        return _failure(spec, GEN_MISSING_BINARY,
                        f"index missing and '{spec.tool}' not on PATH", log)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = spec.build_command(repo_path, str(output_path))
    log(f"  scip[{spec.language}]: generating index with {spec.tool} -> {output_path}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=_INDEX_TIMEOUT_S,
        env={**os.environ, **spec.env(str(output_path))},
    )

    if result.returncode != 0 or not output_path.exists():
        tail = (result.stderr or "").strip().splitlines()[-5:]
        if tail:
            log("    " + "\n    ".join(tail))
        return _failure(spec, GEN_NONZERO_EXIT,
                        f"{spec.tool} exited {result.returncode} "
                        f"without producing an index", log)

    return GenerationResult(ok=True)


def try_generate_index(
    language: str,
    output_path: Path,
    repo_path: str,
    log: Callable[..., None] = lambda *a, **k: None,
) -> bool:
    """Generate a missing index at ``output_path``; return True iff it exists afterwards. Never raises."""
    return generate_index_result(language, output_path, repo_path, log).ok
