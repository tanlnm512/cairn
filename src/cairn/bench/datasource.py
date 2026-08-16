"""Datasource manifest helpers: path-order-independent tree hash + JSON I/O,
plus the bench-artifact stamp (dataset/cairn-version/machine-profile, FR-004).

Why this module exists (FR-001): the T1 benchmark corpus is *regenerated*
from a seed, not committed, so CI on any runner must be able to prove its
regeneration is byte-for-byte the corpus the manifest was minted against.
A whole-tree hash over raw bytes is not usable for that: ``os.listdir``
order varies by filesystem and OS, and archive-based digests (tar/cpio)
capture mtimes and uid/gid noise. The fix is a Git-tree-shaped
sorted-manifest digest (decision D-003): hash each file's content, then
hash the *sorted* ``"<mode> <relpath>\\0<content-sha>"`` entries. The
digest's input is then a pure function of {file set, contents, modes} --
enumeration order can never leak in, which is what makes the assert
byte-identical on ubuntu and macOS runners.

Digest byte format (FROZEN -- changing any byte changes every recorded
hash; a format change ships as a new dataset version, never an edit):

    entry      := mode SP relpath NUL sha256hex
    mode       := "644" | "755"            (git-style, see below)
    relpath    := path relative to root, "/"-separated (as_posix)
    sha256hex  := 64 lowercase hex chars of sha256(file bytes)
    digest     := sha256(concatenation of entries, sorted by relpath,
                         code-point order, no separators between entries)

The entry stream needs no separators to be unambiguous: the mode field is
fixed-width (3), a relpath can never contain NUL (POSIX forbids it in
filenames), and the content digest is fixed-width (64 hex). This mirrors
how git concatenates tree entries.

Mode normalization (deliberate): git records only 100644/100755 for
regular files -- it does not track group/other permission differences
because they carry the *creating process's umask*, not content. Raw
``st_mode & 0o777`` would leak that noise: a file written under umask 022
is 0644, under umask 077 it is 0600, and the "same" corpus would hash
differently across runners -- exactly the cross-machine equality this
digest exists to provide. So mode is normalized git-style: ``755`` iff
the owner-execute bit (``stat.S_IXUSR``) is set, else ``644``.

The ``.git`` scanner-marker rule (CONSTANT -- never change one side of it):
``generate_corpus`` creates an empty ``.git`` *directory* as the repo
marker for the scanner (corpus.py:50-52). Directories contribute no digest
entries -- only files do -- so the empty marker is invisible to the digest
by construction, under either value of ``include_git_dir_marker``. The
flag therefore governs something else: whether files *inside* real
``.git`` directories (a git checkout's metadata) are hashed. The default
``False`` excludes them, because git metadata (e.g. the index) embeds
mtimes and machine-specific state -- precisely the noise a content pin
must not see.

Manifest JSON schema (minted by T002, validated here):

    {
      "schema": "cairn-bench-datasource-manifest",
      "version": 1,
      "t1": {
        "generator_git_sha": "<40- or 64-char hex commit>",
        "seed": 49374,
        "sizes": [60, 200],
        "complexity": "medium",
        "entries": {
          "60": {"tree_hash": "<64 hex>", "counts": {"files": 61,
                                                     "lines": 900,
                                                     "bytes": 21000}}
        }
      }
    }

``counts`` is the ``corpus_stats`` shape (corpus.py:99). Unknown
top-level sections (the ``t3`` pin list lands in T019) are ignored by
``validate_manifest`` so later sections can extend the schema without
this validator rejecting them.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
from pathlib import Path

from .. import __version__

# Schema tag + version for self-describing manifest artifacts (D-001's
# doctrine: artifacts carry their own provenance; the tag lets a reader
# distinguish this format from any future one before interpreting keys).
MANIFEST_SCHEMA = "cairn-bench-datasource-manifest"
MANIFEST_VERSION = 1

# Required-key contracts enforced by validate_manifest. Exposed because the
# minter (T002) and the tests both build manifests against the same list --
# one definition, no drift.
REQUIRED_MANIFEST_KEYS = ("schema", "version", "t1")
REQUIRED_T1_KEYS = ("generator_git_sha", "seed", "sizes", "complexity", "entries")
REQUIRED_ENTRY_KEYS = ("tree_hash", "counts")
REQUIRED_COUNT_KEYS = ("files", "lines", "bytes")
# The three complexity profiles generate_corpus understands (corpus.py:43-48).
VALID_COMPLEXITIES = ("low", "medium", "high")

_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX_SHA = re.compile(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?")


def _is_int(value: object) -> bool:
    """True for real ints -- bool is an int subclass and never counts."""
    return isinstance(value, int) and not isinstance(value, bool)


def _normalized_mode(path: Path) -> int:
    """Git-style mode class for one file: 0o755 if owner-executable else 0o644.

    See the module docstring for why raw permission bits are deliberately
    NOT used (umask noise would break cross-machine digest equality).
    """
    return 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644


def _iter_files(root: Path, *, include_git_dir_marker: bool):
    """Yield (relpath, path) for every file under root, unsorted.

    Only entries for which ``os.path.isfile`` holds are yielded (symlinks
    to files are followed and hashed by target content); directories and
    other non-files (fifos, sockets) contribute nothing. Directories named
    ``.git`` are pruned unless ``include_git_dir_marker`` -- see the module
    docstring for the constant marker rule.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        if not include_git_dir_marker:
            dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_file():
                yield path.relative_to(root).as_posix(), path


def tree_hash(root: Path | str, *, include_git_dir_marker: bool = False) -> str:
    """Path-order-independent content digest of a directory tree.

    Computes the sorted-manifest digest described in the module docstring:
    ``sha256`` over ``"<mode> <relpath>\\0<sha256(content)>"`` entries for
    every file, sorted by "/"-separated relative path. Two trees with the
    same files, contents, and exec bits hash identically regardless of the
    order files were created in or the order the filesystem enumerates
    them -- the property the CI regenerate-and-assert check (AC2) relies on.

    Args:
        root: directory to hash. Must exist and be a directory.
        include_git_dir_marker: when False (default), files inside any
            ``.git`` directory are excluded from the hashed set (git
            metadata is machine noise, not corpus content). When True they
            are hashed like any other file. The empty ``.git`` *marker
            directory* written by ``generate_corpus`` contains no files, so
            this flag never changes a generated corpus's digest.

    Returns:
        The 64-hex sha256 digest; an empty tree yields the sha256 of zero
        bytes (``e3b0c442...b7852b855``), not an error -- an empty corpus
        is a legitimate (if useless) pinned state.

    Raises:
        FileNotFoundError: if ``root`` is missing or not a directory.
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"tree_hash root is not a directory: {root}")
    entries = sorted(_iter_files(root, include_git_dir_marker=include_git_dir_marker))
    digest = hashlib.sha256()
    for relpath, path in entries:
        content_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        mode = _normalized_mode(path)
        digest.update(f"{mode:o} {relpath}\0{content_sha}".encode("utf-8"))
    return digest.hexdigest()


def load_manifest(path: Path | str) -> dict:
    """Read a manifest JSON file and return it as a dict.

    Deliberately does NOT validate: callers combine it with
    ``validate_manifest`` so the load step can distinguish "unreadable"
    (this raises) from "readable but wrong" (validator reports). Unknown
    sections are preserved as-is for forward compatibility.

    Raises:
        FileNotFoundError: if ``path`` does not exist (from read_text).
        ValueError: if the file is not valid JSON.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest {path} is not valid JSON: {exc}") from exc
    return data


def save_manifest(path: Path | str, manifest: dict) -> None:
    """Write a manifest as byte-stable JSON (sorted keys, indent 2, one \\n).

    Byte stability is the point: the manifest is a committed artifact that
    CI and docs generators regenerate and diff (the same doctrine as the
    byte-idempotent docs tables), so the serialization must be a pure
    function of the dict. ``sort_keys`` guarantees key order never depends
    on insertion order.
    """
    text = json.dumps(manifest, sort_keys=True, indent=2) + "\n"
    Path(path).write_text(text, encoding="utf-8")


def validate_manifest(manifest: object) -> list[str]:
    """Check a manifest against the required schema; [] means valid.

    Returns a list of human-readable error strings, each prefixed with the
    dotted path of the offending field (``t1.entries.60.tree_hash: ...``),
    so a minter can fix every problem in one pass instead of whack-a-mole.
    Missing required keys are the hard contract (FR-001); type checks and
    the sizes/entries cross-check guard the "tree-hash at every declared
    size" invariant the CI assert depends on.

    Unknown top-level sections (e.g. the ``t3`` pins from T019) are ignored
    -- extending the schema must not invalidate existing manifests.
    """
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return [f"manifest: expected a JSON object, got {type(manifest).__name__}"]
    for key in REQUIRED_MANIFEST_KEYS:
        if key not in manifest:
            errors.append(f"manifest: missing required key '{key}'")
    if "schema" in manifest and manifest["schema"] != MANIFEST_SCHEMA:
        errors.append(
            f"schema: expected {MANIFEST_SCHEMA!r}, got {manifest['schema']!r}"
        )
    if "version" in manifest and manifest["version"] != MANIFEST_VERSION:
        errors.append(f"version: expected {MANIFEST_VERSION}, got {manifest['version']!r}")

    t1 = manifest.get("t1")
    if not isinstance(t1, dict):
        if "t1" in manifest:
            errors.append("t1: expected a JSON object")
        return errors  # nothing further is checkable without a t1 object
    for key in REQUIRED_T1_KEYS:
        if key not in t1:
            errors.append(f"t1: missing required key '{key}'")

    sha = t1.get("generator_git_sha")
    if "generator_git_sha" in t1 and (
        not isinstance(sha, str) or not _HEX_SHA.fullmatch(sha)
    ):
        errors.append(
            "t1.generator_git_sha: expected a 40-char hex git sha "
            "(64-char sha-256 repos accepted)"
        )
    seed = t1.get("seed")
    if "seed" in t1 and not _is_int(seed):
        errors.append(f"t1.seed: expected an integer, got {seed!r}")
    sizes = t1.get("sizes")
    if "sizes" in t1 and (
        not isinstance(sizes, list)
        or not sizes
        or not all(_is_int(s) and s > 0 for s in sizes)
    ):
        errors.append("t1.sizes: expected a non-empty list of positive integers")
    complexity = t1.get("complexity")
    if "complexity" in t1 and complexity not in VALID_COMPLEXITIES:
        errors.append(
            f"t1.complexity: expected one of {VALID_COMPLEXITIES}, got {complexity!r}"
        )

    entries = t1.get("entries")
    if "entries" in t1:
        if not isinstance(entries, dict):
            errors.append("t1.entries: expected a JSON object keyed by size")
        else:
            if isinstance(sizes, list) and sizes:
                declared = {str(s) for s in sizes}
                for size in sorted(declared - set(entries)):
                    errors.append(f"t1.entries: declared size {size} has no entry")
                for extra in sorted(set(entries) - declared):
                    errors.append(f"t1.entries: entry {extra!r} matches no declared size")
            for key in sorted(entries):
                prefix = f"t1.entries.{key}"
                entry = entries[key]
                if not isinstance(entry, dict):
                    errors.append(f"{prefix}: expected a JSON object")
                    continue
                for req in REQUIRED_ENTRY_KEYS:
                    if req not in entry:
                        errors.append(f"{prefix}: missing required key '{req}'")
                tree = entry.get("tree_hash")
                if "tree_hash" in entry and (
                    not isinstance(tree, str) or not _HEX64.fullmatch(tree)
                ):
                    errors.append(f"{prefix}.tree_hash: expected 64 lowercase hex chars")
                counts = entry.get("counts")
                if "counts" in entry:
                    if not isinstance(counts, dict):
                        errors.append(f"{prefix}.counts: expected a JSON object")
                        continue
                    for req in REQUIRED_COUNT_KEYS:
                        if req not in counts:
                            errors.append(f"{prefix}.counts: missing required key '{req}'")
                    for req in REQUIRED_COUNT_KEYS:
                        value = counts.get(req)
                        if req in counts and not (_is_int(value) and value >= 0):
                            errors.append(
                                f"{prefix}.counts.{req}: expected a non-negative "
                                f"integer, got {value!r}"
                            )
    return errors


# --- bench-artifact stamp (FR-004, decisions D-005/D-006) -------------------
#
# Every `cairn bench` payload (perf / scaling / agent) is stamped at the CLI
# layer -- beside the existing `payload["timestamp"]` assignment, never inside
# the reports' ``to_dict`` (D-006: keeps the payload-shape tests and the 14
# programmatic ``to_dict`` consumers untouched; additive keys are safe for
# ``.github/scripts/bench_compare.py`` which reads via ``.get``).

# The datasource this repo's benchmarks run against. The name is a stable
# identifier for the whole benchmarks/datasource/ tree, independent of which
# T-level section (T1 corpus / T2 snapshot / T3 pins) a run consumed.
DATASET_NAME = "benchmark-datasource"

# Which manifest entry's tree-hash represents *the dataset identity* in the
# stamp. D-005 leaves the choice open; the default perf-suite corpus size
# (300, cli/bench.py --n-files default) is used because it is a pure function
# of the manifest -- independent of whatever --n-files/--sizes a particular
# run used, and meaningful even for --workspace runs that bypass the synthetic
# corpus entirely. A run's actual size is (and stays) the payload's own data;
# the stamp answers "which pinned dataset does this artifact belong to".
STAMP_IDENTITY_SIZE = 300


def default_manifest_path() -> Path | None:
    """Locate ``benchmarks/datasource/manifest.json``; None when not found.

    Two candidates, in order: the working directory (how CI and maintainers
    invoke ``cairn bench`` -- from the repo root) and the source tree the
    package lives in (covers CliRunner-style isolated cwds and editable
    installs). A wheel/sdist install has no ``benchmarks/`` directory and
    correctly degrades to a stamped-but-unpinned dataset block.
    """
    candidates = [
        Path.cwd() / "benchmarks" / "datasource" / "manifest.json",
        Path(__file__).resolve().parents[3] / "benchmarks" / "datasource" / "manifest.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def runner_class(env: dict[str, str] | None = None) -> str:
    """Classify where the bench ran: ``reference-local`` or ``ci-<runner>``.

    D-005: maintainer-generated baselines run outside CI and stamp
    ``reference-local``; anything under GitHub Actions stamps
    ``ci-<RUNNER_NAME>`` (slugified: the runner name may contain spaces and
    digits, e.g. ``GitHub Actions 12`` -> ``ci-github-actions-12``). The
    class is a *label to warn on*, never normalized away.
    """
    source = os.environ if env is None else env
    if source.get("GITHUB_ACTIONS"):
        name = source.get("RUNNER_NAME") or "github-actions"
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        return f"ci-{slug or 'github-actions'}"
    return "reference-local"


def machine_profile(env: dict[str, str] | None = None) -> dict:
    """The D-005 machine-profile fields: cheap, honest, warn-don't-normalize.

    ``cpu`` falls back to ``platform.machine()`` because
    ``platform.processor()`` returns ``""`` on several Linux configurations --
    an empty stamp field would be indistinguishable from "not collected".
    """
    arch = platform.machine()
    return {
        "arch": arch,
        "cpu": platform.processor() or arch,
        "cpu_count": os.cpu_count(),
        "os": platform.platform(),
        "runner_class": runner_class(env),
    }


def _dataset_block(manifest_path: Path | str | None, t3_entry) -> dict:
    """The ``dataset`` stamp block; degrades with a reason, never raises.

    Degradation contract (T013): a bench run must not crash because the
    manifest is absent or short a field -- the block carries ``reason`` and
    nulls instead, so an unstamped-dataset artifact is self-describing about
    *why*. ``version`` reads the manifest's ``dataset_version`` field via
    ``.get`` (additive): today's manifest records only the schema ``version``
    (1) and the T1 pins, so version stamps null-with-reason until a later
    manifest mints a dataset version (T019 is the next writer).
    """
    block: dict = {"name": DATASET_NAME}
    if t3_entry is not None:
        block["t3_entry"] = t3_entry

    path = default_manifest_path() if manifest_path is None else Path(manifest_path)
    if path is None or not path.is_file():
        block["version"] = None
        block["reason"] = "manifest missing"
        return block
    try:
        manifest = load_manifest(path)
    except (OSError, ValueError) as exc:
        block["version"] = None
        block["reason"] = f"manifest unreadable: {exc}"
        return block

    block["version"] = manifest.get("dataset_version")
    t1 = manifest.get("t1")
    entries = t1.get("entries", {}) if isinstance(t1, dict) else {}
    entry = entries.get(str(STAMP_IDENTITY_SIZE))
    block["tree_hash"] = entry.get("tree_hash") if isinstance(entry, dict) else None
    block["identity_size"] = STAMP_IDENTITY_SIZE
    missing = []
    if block["version"] is None:
        missing.append("dataset_version")
    if block["tree_hash"] is None:
        missing.append(f"tree_hash at identity size {STAMP_IDENTITY_SIZE}")
    if missing:
        block["reason"] = "manifest records no " + " and no ".join(missing)
    return block


def build_artifact_stamp(
    *,
    manifest_path: Path | str | None = None,
    env: dict[str, str] | None = None,
    t3_entry=None,
) -> dict:
    """Build the FR-004 stamp for a bench payload: dataset + cairn + machine.

    Called ONCE per CLI invocation (``cairn bench`` computes it up front and
    applies it beside the timestamp at every payload site -- D-006). All
    sub-builds degrade instead of raising: a missing manifest yields
    ``dataset: {name, version: null, reason: "manifest missing"}`` so the
    bench always completes and the artifact explains its own gaps.

    Args:
        manifest_path: manifest to read the dataset identity from; None
            (default) auto-locates it via :func:`default_manifest_path`.
        env: environment mapping for :func:`runner_class` (tests inject a
            synthetic one); None reads ``os.environ``.
        t3_entry: optional T3 manifest pin (name/url/commit dict or its
            name) recorded in the dataset block when a T3-scale run produced
            the artifact (T020 wires this); None omits the key.

    Returns:
        ``{"dataset": {...}, "cairn_version": <cairn.__version__>,
        "machine_profile": {arch, cpu, cpu_count, os, runner_class}}``.
    """
    return {
        "dataset": _dataset_block(manifest_path, t3_entry),
        "cairn_version": __version__,
        "machine_profile": machine_profile(env),
    }
