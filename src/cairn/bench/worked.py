"""Worked-bundle writer: raw result JSON + inputs manifest + README companion."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

import click

MANIFEST_SCHEMA = "cairn.worked.manifest.v1"

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]

# Stamp keys the manifest copies verbatim from the payload (suites stamp
# these beside the timestamp; swe-bench installs its own dataset block).
_STAMP_KEYS = ("dataset", "cairn_version", "machine_profile")


def version_segment(payload: dict) -> str:
    """Run-dir version segment: dataset version, else revision sha[:12], else unversioned."""
    dataset = payload.get("dataset") or {}
    version = dataset.get("version")
    if isinstance(version, str) and version:
        return version
    revision = dataset.get("revision_sha")
    if isinstance(revision, str) and revision:
        return revision[:12]
    return "unversioned"


def _default_benchmarks_root() -> Path:
    from cairn.bench.datasource import default_baselines_root

    root = default_baselines_root()
    return root.parent if root is not None else Path.cwd() / "benchmarks"


def resolve_run_dir(
    worked_dir: str | Path, payload: dict, suite: str, benchmarks_root: Path | None = None
) -> Path:
    """Canonical worked/<suite>-<version>/ under the benchmarks root, else DIR exactly."""
    worked = Path(worked_dir)
    root = Path(benchmarks_root) if benchmarks_root is not None else _default_benchmarks_root()
    if worked.resolve() == root.resolve():
        return worked / "worked" / f"{suite}-{version_segment(payload)}"
    return worked


def scrub_path(token: str, repo_root: Path | None) -> str:
    """Render one path token repo-relative, or $HOME/$TMPDIR-templated; else verbatim."""
    path = Path(token)
    if not path.is_absolute():
        return token
    resolved = path.resolve()
    anchors: list[tuple[Path, str]] = []
    if repo_root is not None:
        anchors.append((Path(repo_root).resolve(), ""))
    anchors.append((Path.home().resolve(), "$HOME/"))
    anchors.append((Path(tempfile.gettempdir()).resolve(), "$TMPDIR/"))
    for anchor, prefix in anchors:
        if resolved != anchor and resolved.is_relative_to(anchor):
            rest = resolved.relative_to(anchor).as_posix()
            return f"{prefix}{rest}"
    return token


def _scrub_strings(value: object, repo_root: Path | None) -> object:
    if isinstance(value, str):
        return scrub_path(value, repo_root)
    if isinstance(value, list):
        return [_scrub_strings(item, repo_root) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_strings(item, repo_root) for key, item in value.items()}
    return value


def recorded_command() -> list[str]:
    """The effective bench command line rebuilt from the click Context; [] outside one."""
    ctx = click.get_current_context(silent=True)
    if ctx is None:
        return []
    tokens = ["cairn", ctx.info_name or ""]
    for param in ctx.command.params:
        value = ctx.params.get(param.name)
        if getattr(param, "is_flag", False):
            if value:
                tokens.append(param.opts[0])
        elif value is not None:
            tokens.extend([param.opts[0], str(value)])
    return tokens


def build_manifest(
    payload: dict,
    suite: str,
    *,
    seed: int | None = None,
    repeats: int | None = None,
    runs: int | None = None,
    embed_backend: str | None = None,
    command: list[str] | None = None,
    repo_root: Path | None = None,
    artifacts: list[str] | tuple = (),
) -> dict:
    """cairn.worked.manifest.v1: inputs + stamp block + recorded command + artifacts."""
    dataset = payload.get("dataset") or {}
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "inputs": {
            "suite": suite,
            "dataset_version": dataset.get("version"),
            "seed": seed,
            "repeats": repeats,
            "runs": runs,
            "embed_backend": embed_backend,
        },
        "stamp": {key: payload[key] for key in _STAMP_KEYS if key in payload},
        "command": command if command is not None else recorded_command(),
        "artifacts": sorted(artifacts),
    }
    return _scrub_strings(manifest, repo_root)  # type: ignore[return-value]


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _bundle_readme(run_name: str, raw_name: str, manifest: dict) -> str:
    command = " ".join(manifest["command"])
    return (
        f"# Worked bundle: {run_name}\n"
        "\n"
        "Reproduce this run with the command recorded in `manifest.json`\n"
        "(`$HOME`/`$TMPDIR`-templated paths expand before running):\n"
        "\n"
        "```sh\n"
        f"{command}\n"
        "```\n"
        "\n"
        f"- `{raw_name}` -- raw result JSON, the identical payload `--save` writes.\n"
        "- `manifest.json` -- inputs manifest: suite, dataset version, seed/repeats/runs,\n"
        "  embed backend, machine stamp, full command line.\n"
        "- `README.md` -- this companion.\n"
    )


def _default_repo_root() -> Path | None:
    for candidate in (Path.cwd(), _PACKAGE_ROOT):
        if (candidate / "benchmarks").is_dir():
            return candidate
    return None


def write_worked_bundle(
    payload: dict,
    worked_dir: str | Path,
    *,
    suite: str,
    seed: int | None = None,
    repeats: int | None = None,
    runs: int | None = None,
    embed_backend: str | None = None,
    command: list[str] | None = None,
    repo_root: Path | None = None,
    benchmarks_root: Path | None = None,
) -> list[Path]:
    """Write <suite>.json + manifest.json + README.md atomically; returns the written paths.

    The passed payload is never mutated. Raises OSError when the target is
    unwritable; the caller owns the fail-clean ordering.
    """
    run_dir = resolve_run_dir(worked_dir, payload, suite, benchmarks_root)
    run_dir.mkdir(parents=True, exist_ok=True)
    raw_name = f"{suite}.json"
    raw_path = run_dir / raw_name
    _atomic_write_text(raw_path, json.dumps(payload, indent=2))
    manifest = build_manifest(
        payload,
        suite,
        seed=seed,
        repeats=repeats,
        runs=runs,
        embed_backend=embed_backend,
        command=command,
        repo_root=repo_root,
        artifacts=(raw_name, "manifest.json", "README.md"),
    )
    manifest_path = run_dir / "manifest.json"
    _atomic_write_text(manifest_path, json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    readme_path = run_dir / "README.md"
    _atomic_write_text(readme_path, _bundle_readme(run_dir.name, raw_name, manifest))
    written = [raw_path, manifest_path, readme_path]
    _append_inventory(written, run_dir, repo_root)
    return written


def _append_inventory(written: list[Path], run_dir: Path, repo_root: Path | None) -> None:
    if repo_root is None:
        repo_root = _default_repo_root()
        if repo_root is None:
            return
    anchor = Path(repo_root).resolve()
    if not run_dir.resolve().is_relative_to(anchor):
        return
    append_inventory_rows(run_dir, written, anchor)


def append_inventory_rows(run_dir: Path, written: list[Path], repo_root: Path) -> list[str]:
    """Append one row per new JSON artifact to benchmarks/README.md; returns the rows.

    Rows key repo-relative with the run's README.md as companion; a key already
    present is never appended again. Missing inventory README appends nothing.
    """
    readme = Path(repo_root) / "benchmarks" / "README.md"
    if not readme.is_file():
        return []
    text = readme.read_text(encoding="utf-8")
    anchor = Path(repo_root).resolve()
    present = set(re.findall(r"^\| `([^`]+)`", text, re.MULTILINE))
    run_rel = run_dir.resolve().relative_to(anchor).as_posix()
    rows = []
    for path in written:
        if path.suffix != ".json":
            continue
        key = path.resolve().relative_to(anchor).as_posix()
        if key not in present:
            rows.append((key, f"| `{key}` | `{run_rel}/README.md` |\n"))
    if not rows:
        return []
    rows.sort()
    lines = text.splitlines(keepends=True)
    insert_at = len(lines)
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].startswith("|"):
            insert_at = index + 1
            break
    lines[insert_at:insert_at] = [row for _, row in rows]
    _atomic_write_text(readme, "".join(lines))
    return [row for _, row in rows]
