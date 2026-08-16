#!/usr/bin/env python3
"""Local T3 corpus fetch-by-pin (FR-006/AC7, TC-030..TC-033).

Fetches one ``t3`` entry from ``benchmarks/datasource/manifest.json`` by
EXPLICIT pinned-commit checkout and optionally runs the bench against it:

    uv run python scripts/fetch_t3_corpus.py --list
    uv run python scripts/fetch_t3_corpus.py "home-assistant/core"
    uv run python scripts/fetch_t3_corpus.py "torvalds/linux" --run-bench

The pin-enforcement contract (the whole point of this command):

* The checkout is ALWAYS ``git clone --no-checkout`` followed by
  ``git checkout --detach <pinned-sha>`` -- the default-branch HEAD is
  never materialized, so a T3 result can never silently measure a moving
  branch (codegraph's contamination lesson: pin enforcement lives in the
  command, not in the caller's discipline).
* After the checkout, ``git rev-parse HEAD`` is compared to the manifest
  pin EXACTLY. A pin that is unreachable (moved, force-pushed away, never
  existed in the clone) fails loudly -- exit 3, naming the entry, the
  expected pin, and what was found instead -- and the script never
  proceeds on any other commit.

This is a LOCAL, maintainer-run command (decision D-009): the multi-GB
clones happen into a cache OUTSIDE this repository (default
``~/.cairn/bench-t3/<name>``), the script lives under ``scripts/`` -- never
``src/cairn``, so ``grep -rn "git clone" src/cairn --include="*.py"`` stays
at 0 matches -- and NOTHING here is wired into CI (TC-033 standing guard:
no T3 fetch step appears in ``ci.yml``; the suite stays offline).

How a ``--run-bench`` result ties back to the manifest entry: the bench
runs as ``cairn bench --workspace <checkout> --json --save <dest>/<name>.json``,
so the T013 artifact stamp already carries the dataset identity. The stamp
hook ``build_artifact_stamp(t3_entry=...)`` (src/cairn/bench/datasource.py,
built in T013 for exactly this wiring) records a T3 pin in the
``dataset.t3_entry`` block -- but the ``cairn bench`` CLI surface takes no
t3 flag, and widening it is outside this task's scope. The script therefore
stamps the SAME shape into the artifact ``--save`` produced (repo + commit
+ scale, verbatim from the manifest entry, only after a verified checkout),
making the saved JSON self-describing about which pinned corpus measured it.

Usage:
    uv run python scripts/fetch_t3_corpus.py --list
    uv run python scripts/fetch_t3_corpus.py <entry-name> [--manifest path]
                                                [--cache dir] [--dest dir]
                                                [--run-bench]

Exit codes (the contract tests and the docs rely on):
    0  fetched (and benched, if requested) with the pin verified exactly
    1  usage -- unknown entry name, missing git, --list + entry together,
       or a --cache inside this repository (D-009)
    2  unusable manifest -- unreadable/not-JSON, no t3 section, or the
       requested entry is malformed (bad sha shape, missing pin keys)
    3  PIN FAILURE -- clone/fetch/checkout failed or the verified HEAD does
       not equal the manifest pin; the message names the entry, the
       expected pin, and what was found (TC-031)
    4  bench failure -- the ``cairn bench`` subprocess itself exited non-zero
       (its exit code is reported; a fresh stamp is only written on rc 0).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Allow running from a source checkout without installing (same pattern as
# scripts/verify_datasource.py). Guarded insert so repeated imports -- e.g.
# the test suite loading this file as a module -- never grow sys.path.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from cairn.bench.datasource import load_manifest  # noqa: E402

# Default pin location (same artifact verify_datasource.py reads); overridable
# via --manifest so scratch experiments and tests never touch the committed file.
DEFAULT_MANIFEST = REPO_ROOT / "benchmarks" / "datasource" / "manifest.json"

# Clone cache OUTSIDE the repo (D-009): the T3 corpora are multi-GB and never
# vendored (D-010). Entry names may contain "/" (e.g. "home-assistant/core"),
# so each entry gets a sanitized directory under this root. --dest defaults
# here too, so a default run leaves NOTHING inside the repository.
DEFAULT_CACHE_ROOT = Path.home() / ".cairn" / "bench-t3"

# Exit-code contract (see module docstring). Named because tests and the docs
# assert against the meaning, not the number (same doctrine as
# scripts/verify_datasource.py).
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_MANIFEST = 2
EXIT_PIN = 3
EXIT_BENCH = 4

# Same pin shape validate_manifest enforces (40-char sha-1, 64-char sha-256
# repos accepted). Re-declared locally because the script must also vet
# scratch manifests that never pass the full schema validator.
_HEX_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")

# The manifest keys a t3 entry must carry for a fetch to be meaningful.
REQUIRED_ENTRY_KEYS = ("name", "url", "commit", "scale_hint")


class ManifestError(Exception):
    """Unusable manifest for this command (exit 2): unreadable, no t3
    section, or a malformed requested entry. Nothing was cloned."""


class UsageError(Exception):
    """Invocation problem (exit 1): an entry name the manifest does not
    carry. Nothing was cloned and no pin was attempted."""


class PinError(Exception):
    """Pin-enforcement failure (exit 3): the pinned commit could not be
    checked out exactly. The message names the entry, the expected pin,
    and what was found instead."""


def sanitize_name(name: str) -> str:
    """Filesystem-safe form of an entry name for cache/result paths.

    ``home-assistant/core`` -> ``home-assistant__core``. Anything outside
    ``[A-Za-z0-9._-]`` collapses to ``__``; leading/trailing dots are
    stripped so no hidden dirs or ``..`` traversal can survive. Raises
    ManifestError if nothing sane remains.
    """
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "__", name).strip(".")
    if not sanitized or sanitized in {".", ".."}:
        raise ManifestError(f"t3 entry name {name!r} has no filesystem-safe form")
    return sanitized


def load_t3_entries(manifest_path: Path | str) -> list[dict]:
    """Read the manifest and return its t3 entry list.

    Raises ManifestError for every shape this command cannot work with:
    unreadable/not-JSON (from load_manifest), a missing or non-list t3
    section, or an empty entry list. Per-entry key checks happen at fetch
    time (only the requested entry needs to be sound -- D-010 keeps t3
    optional and independently extendable).
    """
    try:
        manifest = load_manifest(manifest_path)
    except (OSError, ValueError) as exc:
        raise ManifestError(f"manifest unusable: {exc}") from exc
    t3 = manifest.get("t3") if isinstance(manifest, dict) else None
    if not isinstance(t3, dict) or "entries" not in t3:
        raise ManifestError(f"manifest {manifest_path} has no t3 section (it is optional -- D-010)")
    entries = t3["entries"]
    if not isinstance(entries, list) or not entries:
        raise ManifestError(f"manifest {manifest_path}: t3.entries must be a non-empty list")
    return entries


def find_entry(entries: list[dict], name: str) -> dict:
    """The entry whose ``name`` matches exactly, else UsageError listing
    what IS available (so a typo fails in milliseconds with the fix in hand)."""
    for entry in entries:
        if isinstance(entry, dict) and entry.get("name") == name:
            return entry
    available = ", ".join(
        repr(e.get("name")) for e in entries if isinstance(e, dict) and e.get("name")
    )
    raise UsageError(
        f"no t3 entry named {name!r} in the manifest (available: {available or 'none'})"
    )


def check_entry_shape(entry: dict) -> None:
    """Vet the requested entry's pin keys before any git work (exit 2 class):
    the four T019 keys present, the commit a plausible git sha. A pin that
    cannot be attempted at all is a manifest problem, not a pin failure."""
    for key in REQUIRED_ENTRY_KEYS:
        value = entry.get(key)
        if not isinstance(value, str) or not value:
            raise ManifestError(
                f"t3 entry {entry.get('name')!r}: key {key!r} must be a non-empty string"
            )
    pin = entry["commit"]
    if not _HEX_SHA.fullmatch(pin):
        raise ManifestError(
            f"t3 entry {entry.get('name')!r}: commit {pin!r} is not a 40/64-char hex git sha"
        )


def _git(args: list[str]) -> subprocess.CompletedProcess:
    """Run git, captured; never raises (callers compose their own failures)."""
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _git_stderr_tail(proc: subprocess.CompletedProcess, limit: int = 400) -> str:
    """The informative tail of a failed git invocation, for the loud message."""
    text = (proc.stderr or proc.stdout or "").strip()
    return text if len(text) <= limit else text[-limit:]


def cache_repo(url: str, cache_dir: Path, entry_name: str) -> None:
    """Materialize ``url`` at ``cache_dir``: fresh ``git clone --no-checkout``
    the first time, ``git fetch origin`` after that (a re-run must not
    re-download a multi-GB clone just to move between pins).

    ``--no-checkout`` is deliberate: the default-branch worktree is never
    created, so the only checkout this cache ever serves is the detached pin.
    Raises PinError (the remote named) if git cannot produce a usable cache.
    """
    if (cache_dir / ".git").exists():
        print(f"updating cached clone {cache_dir}")
        proc = _git(["-C", str(cache_dir), "fetch", "--quiet", "origin"])
        if proc.returncode != 0:
            raise PinError(
                f"t3 entry {entry_name!r}: could not update cached clone {cache_dir} "
                f"from {url}: {_git_stderr_tail(proc)}"
            )
        return
    if cache_dir.exists():
        raise PinError(
            f"t3 entry {entry_name!r}: cache path {cache_dir} exists and is not a git "
            "repository; remove it or pass --cache elsewhere"
        )
    print(f"cloning {url} -> {cache_dir}")
    proc = _git(["clone", "--no-checkout", url, str(cache_dir)])
    if proc.returncode != 0:
        raise PinError(
            f"t3 entry {entry_name!r}: git clone of {url} failed: {_git_stderr_tail(proc)}"
        )


def checkout_pin(entry: dict, cache_dir: Path) -> str:
    """Detach-checkout the manifest pin and VERIFY it, returning the verified sha.

    The verification is the load-bearing step (TC-031): ``rev-parse HEAD``
    after the checkout must equal the manifest pin EXACTLY. An unreachable
    pin (the commit is not in the clone -- moved, force-pushed away, typo'd)
    makes ``git checkout`` itself fail; a checkout that somehow landed
    elsewhere trips the equality check. Both raise PinError naming the
    entry, the expected pin, and what was found instead -- the script never
    silently proceeds on a different commit.
    """
    name, pin = entry["name"], entry["commit"]
    proc = _git(["-C", str(cache_dir), "checkout", "--detach", pin])
    if proc.returncode != 0:
        found = _repo_head(cache_dir) or "no commit checked out"
        raise PinError(
            f"t3 entry {name!r}: pinned commit {pin} is UNREACHABLE -- "
            f"git checkout failed: {_git_stderr_tail(proc)}\n"
            f"  expected pin : {pin}\n"
            f"  found        : {found}\n"
            "  The manifest pin does not exist in the cloned repository (moved? "
            "force-pushed away?). Refusing to run against any other commit; "
            "re-pin via the manifest writer (T019) or investigate the remote."
        )
    head = _repo_head(cache_dir)
    if head != pin:
        raise PinError(
            f"t3 entry {name!r}: pin verification FAILED -- checked-out HEAD does "
            "not equal the manifest pin.\n"
            f"  expected pin : {pin}\n"
            f"  found        : {head or 'no commit checked out'}\n"
            "  Never proceeding on a different commit; the manifest pin and the "
            "cache disagree -- investigate before re-running."
        )
    return pin


def _repo_head(cache_dir: Path) -> str | None:
    """The cache's current HEAD sha, or None if git cannot answer (used only
    to build the 'found' half of a loud failure message)."""
    proc = _git(["-C", str(cache_dir), "rev-parse", "HEAD"])
    return proc.stdout.strip() if proc.returncode == 0 else None


def bench_command() -> list[str]:
    """Argv prefix that invokes the cairn CLI in a subprocess.

    Prefers an installed ``cairn`` console script; falls back to a
    ``python -c`` bootstrap that inserts this checkout's ``src/`` (the same
    path bootstrap this script uses), so the command works from a bare
    checkout without an install.
    """
    exe = shutil.which("cairn")
    if exe:
        return [exe]
    snippet = (
        "import sys; sys.path.insert(0, r'%s'); "
        "from cairn.cli import main; sys.exit(main(sys.argv[1:]))" % (REPO_ROOT / "src")
    )
    return [sys.executable, "-c", snippet]


def run_bench_subprocess(argv: list[str]) -> int:
    """Execute the bench argv (separated so tests can stub the subprocess).

    Streams the bench's own output through -- a real T3 run is long, and
    its progress/JSON belong on the invoking terminal, not in a buffer.
    """
    return subprocess.run(argv).returncode


def stamp_t3_entry(save_path: Path, entry: dict) -> None:
    """Record the manifest entry in the saved bench artifact's dataset block.

    Writes ``dataset.t3_entry`` with the verbatim manifest pin (name, url,
    commit, scale_hint) -- byte-for-byte the block ``build_artifact_stamp(
    t3_entry=...)`` would have produced in-process (T013 built the hook;
    the CLI exposes no t3 flag, so this script applies it post-save). Only
    called after a verified checkout, so the stamped pin is one git itself
    confirmed. A missing/corrupt ``dataset`` block is created rather than
    trusted: the stamp must not silently no-op.
    """
    try:
        payload = json.loads(save_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"bench saved no readable artifact at {save_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ManifestError(f"bench artifact {save_path} is not a JSON object")
    dataset = payload.get("dataset")
    if not isinstance(dataset, dict):
        dataset = {}
        payload["dataset"] = dataset
    dataset["t3_entry"] = {key: entry[key] for key in REQUIRED_ENTRY_KEYS}
    save_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def list_entries(entries: list[dict], manifest_path: Path | str) -> None:
    """--list: print every t3 pin without touching the network or the cache.

    One block per entry -- name, url, pinned commit, scale hint -- plus a
    count line. Read-only by construction: no cache dir is created, no git
    runs, so it is safe anywhere (including a scratch manifest inspection).
    """
    print(f"t3 entries pinned in {manifest_path} ({len(entries)}):")
    for entry in entries:
        if not isinstance(entry, dict):
            print("  <malformed entry>")
            continue
        print(f"  {entry.get('name', '<unnamed>')}")
        print(f"    url:    {entry.get('url', '?')}")
        print(f"    commit: {entry.get('commit', '?')}  (pinned; never the default-branch HEAD)")
        print(f"    scale:  {entry.get('scale_hint', '?')}")


def fetch(
    entry: dict,
    *,
    cache_root: Path,
    dest: Path,
    run_bench: bool,
) -> int:
    """Clone (or update) the entry's cache, verify the pin, optionally bench.

    Returns the process exit code; raises ManifestError/PinError upward for
    main() to map and print loudly.
    """
    name = entry["name"]
    pin = entry["commit"]
    cache_dir = cache_root / sanitize_name(name)
    print(f"entry    : {name}")
    print(f"pin      : {pin}")
    print(f"cache    : {cache_dir}")

    cache_root.mkdir(parents=True, exist_ok=True)
    cache_repo(entry["url"], cache_dir, name)
    verified = checkout_pin(entry, cache_dir)
    print(f"verified : HEAD == {verified} (exact pin match)")
    print(f"checkout : {cache_dir}  [detached at the pinned commit]")

    if not run_bench:
        return EXIT_OK

    dest.mkdir(parents=True, exist_ok=True)
    save_path = dest / f"{sanitize_name(name)}.json"
    argv = bench_command() + [
        "bench",
        "--workspace", str(cache_dir),
        "--json",
        "--save", str(save_path),
    ]
    print(f"bench    : cairn {' '.join(argv[argv.index('bench') + 1:])}")
    rc = run_bench_subprocess(argv)
    if rc != 0:
        print(
            f"FAIL: bench for t3 entry {name!r} exited non-zero (code {rc}); "
            "no t3_entry stamp was written.",
            file=sys.stderr,
        )
        return EXIT_BENCH
    stamp_t3_entry(save_path, entry)
    print(f"stamped  : {save_path} dataset.t3_entry = {name} @ {pin}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "entry",
        nargs="?",
        default=None,
        help="manifest t3 entry name to fetch by pinned commit (see --list)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print the manifest's t3 pins and exit (no network, no cache writes)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"manifest to read t3 pins from (default: {DEFAULT_MANIFEST})",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=None,
        help=f"clone cache root, OUTSIDE this repository "
        f"(default: {DEFAULT_CACHE_ROOT})",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="directory for the --run-bench result JSON "
        "(default: the cache root, so nothing lands inside this repo)",
    )
    parser.add_argument(
        "--run-bench",
        action="store_true",
        help="after a VERIFIED checkout, run "
        "`cairn bench --workspace <checkout> --json --save <dest>/<name>.json` "
        "and stamp the manifest entry into the saved artifact",
    )
    args = parser.parse_args(argv)

    if args.list and args.entry is not None:
        print("FAIL: pass either --list or an entry name, not both.", file=sys.stderr)
        return EXIT_USAGE
    if not args.list and args.entry is None:
        parser.print_usage(sys.stderr)
        print("FAIL: an entry name (or --list) is required.", file=sys.stderr)
        return EXIT_USAGE

    try:
        entries = load_t3_entries(args.manifest)
    except ManifestError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return EXIT_MANIFEST

    if args.list:
        list_entries(entries, args.manifest)
        return EXIT_OK

    if shutil.which("git") is None:
        print("FAIL: git is required for a T3 fetch but was not found on PATH.", file=sys.stderr)
        return EXIT_USAGE

    cache_root = args.cache if args.cache is not None else DEFAULT_CACHE_ROOT
    # D-009 guard: the multi-GB clone must never land inside this repository.
    if cache_root.resolve().is_relative_to(REPO_ROOT.resolve()):
        print(
            f"FAIL: --cache {cache_root} is inside this repository ({REPO_ROOT}); "
            "T3 clones live outside it (D-009).",
            file=sys.stderr,
        )
        return EXIT_USAGE
    dest = args.dest if args.dest is not None else cache_root

    try:
        entry = find_entry(entries, args.entry)
        check_entry_shape(entry)
        return fetch(entry, cache_root=cache_root, dest=dest, run_bench=args.run_bench)
    except UsageError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except ManifestError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return EXIT_MANIFEST
    except PinError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return EXIT_PIN


if __name__ == "__main__":
    sys.exit(main())
