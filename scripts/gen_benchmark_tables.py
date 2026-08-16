#!/usr/bin/env python3
"""Generate the reference tables in docs/benchmarks.md from committed baselines.

T017 (FR-005, AC6; TC-024..TC-026/TC-028). The doc's three placeholder
families -- retrieval quality, perf, scaling -- are wrapped in sentinel
markers; this script regenerates ONLY the bytes between each family's
sentinels, reading committed baseline artifacts
(``benchmarks/baselines/<DS-version>/``) and nothing else. Every byte
outside a start/end sentinel pair is preserved verbatim.

Sentinels (one pair per family, exact lines, invisible in rendered markdown):

    <!-- cairn-bench-tables:quality start --> ... <!-- cairn-bench-tables:quality end -->
    <!-- cairn-bench-tables:perf start -->    ... <!-- cairn-bench-tables:perf end -->
    <!-- cairn-bench-tables:scaling start --> ... <!-- cairn-bench-tables:scaling end -->

The sentinels wrap the WHOLE table (header + separator + data rows, plus the
quality family's notes/provenance block), separated from surrounding prose by
blank lines, so each table still renders as one markdown table -- a sentinel
placed between table rows would split it.

Pinned formatting (the byte-idempotency contract -- TC-025). Regeneration is
a pure function of the artifacts: rows are SORTED (perf ops by name
(codepoint order), scaling points by n_files, quality fixed L1 then L5) and
every number is rendered with a fixed spec. TC-026 traceability -- each cell
maps 1:1 to an artifact key with only this documented rounding/units:

  quality  samples      <- L{n}.n_queries                 int
           recall@10    <- L{n}.recall_at_10              {:.4f}
           mrr          <- L{n}.mrr                       {:.4f}
  perf     median (ms)  <- ops[].median_ms                {:.2f}
           p95 (ms)     <- ops[].p95_ms                   {:.2f}
           ops/sec      <- ops[].ops_per_sec              {:.2f}
  scaling  files        <- points[].n_files               int
           symbols      <- points[].symbols               int
           build (s)    <- points[].build_s               {:.3f}
           embed (s)    <- points[].embed_s               {:.3f}
           DB MB        <- points[].db_mb                 {:.2f}
           resolve      <- points[].resolve_rate          {:.3f}  (fraction, not %)
           peak MB      <- points[].peak_mem_mb           {:.2f}

Columns are space-padded to the widest cell (header or data) in that column,
one space each side of the cell; the separator row is dashes of the same
width. No thousands separators anywhere.

The quality family's generated block also carries, under the table: the
per-corpus query/expectation counts, the L5 surface-absent annotation (from
the artifact's ``l5_surface``), and a provenance line (dataset version,
runner class + machine flavor, mint date from the timestamp, cairn version,
embed backend/model) so readers know what they are comparing against.

Failure mode (TC-024): a missing artifact file, a missing/malformed sentinel
pair for a family, or an artifact record missing a required key aborts with
exit code 1 and a message naming the family/artifact -- the script never
re-emits placeholders and never guesses.

Usage:
    uv run python scripts/gen_benchmark_tables.py
    uv run python scripts/gen_benchmark_tables.py \
        --baselines benchmarks/baselines/DS-v1 --docs docs/benchmarks.md

Exit codes: 0 = doc regenerated (or already current); 1 = error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Allow running from a source checkout without installing (same pattern as
# scripts/mint_baselines.py). Guarded insert so repeated imports never grow
# sys.path.
REPO_ROOT = Path(__file__).resolve().parents[1]

FAMILIES: Tuple[str, ...] = ("quality", "perf", "scaling")
ARTIFACT_NAMES: Dict[str, str] = {
    "quality": "quality.json",
    "perf": "perf.json",
    "scaling": "scaling.json",
}

# Exact-line sentinel match. The family set is closed so an unknown family in
# the doc cannot silently pass (it is never looked up, and the three known
# ones are all required).
SENTINEL_LINE_RE = re.compile(
    r"^<!--\s*cairn-bench-tables:(?P<family>quality|perf|scaling)\s+"
    r"(?P<side>start|end)\s*-->$"
)


class GenerationError(Exception):
    """A missing/malformed input; the message names the offending family/artifact."""


# ---------------------------------------------------------------------------
# Baseline loading
# ---------------------------------------------------------------------------


def load_baseline(baselines_dir: Path, family: str) -> Dict[str, Any]:
    """Read one family's committed artifact, failing loudly if absent/broken."""
    path = baselines_dir / ARTIFACT_NAMES[family]
    if not path.is_file():
        raise GenerationError(
            f"missing baseline artifact for family '{family}': {path} does not exist"
        )
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except json.JSONDecodeError as exc:
        raise GenerationError(f"baseline artifact {path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise GenerationError(f"baseline artifact {path} must be a JSON object")
    return payload


def _require(record: Dict[str, Any], key: str, what: str) -> Any:
    if key not in record:
        raise GenerationError(f"{what} is missing required key '{key}'")
    return record[key]


def _num(value: Any, what: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise GenerationError(f"{what} must be a number, got {value!r}")
    return float(value)


# ---------------------------------------------------------------------------
# Table rendering (pinned formats -- see module docstring)
# ---------------------------------------------------------------------------


def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> List[str]:
    """Render a padded GFM table. Deterministic: widths derive from the cells."""
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))
    ]
    lines = [
        "| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(headers)) + " |",
        "|-" + "-|-".join("-" * w for w in widths) + "-|",
    ]
    for row in rows:
        lines.append(
            "| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) + " |"
        )
    return lines


def render_quality(artifact: Dict[str, Any]) -> List[str]:
    """Retrieval-quality table + notes + provenance, from quality.json."""
    rows = []
    for corpus in ("L1", "L5"):
        block = _require(artifact, corpus, f"quality artifact '{corpus}' block")
        recall = _num(
            _require(block, "recall_at_10", f"quality artifact {corpus}"),
            f"quality artifact {corpus}.recall_at_10",
        )
        mrr = _num(
            _require(block, "mrr", f"quality artifact {corpus}"),
            f"quality artifact {corpus}.mrr",
        )
        n_queries = _num(
            _require(block, "n_queries", f"quality artifact {corpus}"),
            f"quality artifact {corpus}.n_queries",
        )
        # L5 carries the surface-absent dagger; the note under the table
        # explains it (0.0 by construction, not a retrieval failure).
        label = f"{corpus} †" if corpus == "L5" and "l5_surface" in artifact else corpus
        rows.append([label, f"{n_queries:.0f}", f"{recall:.4f}", f"{mrr:.4f}"])

    lines = render_table(["corpus", "samples", "recall@10", "mrr"], rows)
    lines.append("")

    notes: List[str] = []
    for corpus in ("L1", "L5"):
        block = artifact[corpus]
        q = int(_num(block.get("n_queries", 0), f"{corpus}.n_queries"))
        e = int(_num(block.get("n_expectations", 0), f"{corpus}.n_expectations"))
        notes.append(f"{q} {corpus} queries / {e} expectations")
    gt_path = artifact.get("ground_truth", {}).get("path")
    gt_clause = f" — graded pair ({gt_path}), identity-first matcher" if gt_path else ""
    lines.append("> " + "; ".join(notes) + gt_clause + ".")
    l5_surface = artifact.get("l5_surface")
    if l5_surface:
        version = artifact.get("dataset", {}).get("version", "?")
        lines.append(
            f"> † L5 surface absent for {version}: {l5_surface} — "
            "scores are 0.0 by construction, not retrieval failures."
        )
    lines.append("> " + _provenance_line(artifact, "quality.json"))
    return lines


def render_perf(artifact: Dict[str, Any]) -> List[str]:
    """Per-op latency table from perf.json; one row per OpTiming, sorted by name."""
    ops = _require(artifact, "ops", "perf artifact")
    if not isinstance(ops, list) or not ops:
        raise GenerationError("perf artifact 'ops' must be a non-empty list")
    rows = []
    for op in ops:
        name = _require(op, "name", "perf artifact op")
        median = _num(
            _require(op, "median_ms", f"perf op '{name}'"),
            f"perf op '{name}'.median_ms",
        )
        p95 = _num(
            _require(op, "p95_ms", f"perf op '{name}'"), f"perf op '{name}'.p95_ms"
        )
        rate = _num(
            _require(op, "ops_per_sec", f"perf op '{name}'"),
            f"perf op '{name}'.ops_per_sec",
        )
        rows.append([str(name), f"{median:.2f}", f"{p95:.2f}", f"{rate:.2f}"])
    rows.sort(key=lambda row: row[0])
    return render_table(["operation", "median (ms)", "p95 (ms)", "ops/sec"], rows)


def render_scaling(artifact: Dict[str, Any]) -> List[str]:
    """Per-size scaling table from scaling.json; rows sorted by n_files."""
    points = _require(artifact, "points", "scaling artifact")
    if not isinstance(points, list) or not points:
        raise GenerationError("scaling artifact 'points' must be a non-empty list")
    rows = []
    for point in points:
        what = "scaling artifact point"
        cells = {}
        for key, spec in (
            ("n_files", ".0f"),
            ("symbols", ".0f"),
            ("build_s", ".3f"),
            ("embed_s", ".3f"),
            ("db_mb", ".2f"),
            ("resolve_rate", ".3f"),
            ("peak_mem_mb", ".2f"),
        ):
            value = _num(_require(point, key, what), f"{what}.{key}")
            cells[key] = f"{value:{spec}}"
        rows.append(list(cells.values()))
    rows.sort(key=lambda row: int(row[0]))
    return render_table(
        ["files", "symbols", "build (s)", "embed (s)", "DB MB", "resolve", "peak MB"],
        rows,
    )


RENDERERS = {
    "quality": render_quality,
    "perf": render_perf,
    "scaling": render_scaling,
}


def _provenance_line(artifact: Dict[str, Any], artifact_name: str) -> str:
    """One line under the quality table: dataset version, machine, mint date."""
    dataset = artifact.get("dataset", {})
    version = dataset.get("version", "?")
    profile = artifact.get("machine_profile", {})
    runner = _require(profile, "runner_class", "quality artifact machine_profile")
    machine_bits = []
    for key in ("os", "arch"):
        if profile.get(key) is not None:
            machine_bits.append(str(profile[key]))
    if profile.get("cpu_count") is not None:
        machine_bits.append(f"{profile['cpu_count']} CPUs")
    machine = f" ({', '.join(machine_bits)})" if machine_bits else ""
    timestamp = _require(artifact, "timestamp", "quality artifact")
    mint_date = str(timestamp)[:10]
    extras = []
    if artifact.get("cairn_version"):
        extras.append(f"cairn {artifact['cairn_version']}")
    embed = artifact.get("embed", {})
    if embed.get("backend"):
        extras.append(f"embed {embed['backend']}" + (f" / {embed['model']}" if embed.get("model") else ""))
    extra_clause = f", {', '.join(extras)}" if extras else ""
    return (
        f"Source: {version} baseline (benchmarks/baselines/{version}/{artifact_name}) — "
        f"runner class {runner}{machine}, minted {mint_date}{extra_clause}."
    )


# ---------------------------------------------------------------------------
# Doc rewriting (sentinel-bounded, byte-exact outside)
# ---------------------------------------------------------------------------


def _sentinel_indices(lines: Sequence[str], family: str) -> Tuple[int, int]:
    """Line indices of a family's start/end sentinels; validates the pairing."""
    starts, ends = [], []
    for idx, line in enumerate(lines):
        match = SENTINEL_LINE_RE.match(line)
        if match and match.group("family") == family:
            (starts if match.group("side") == "start" else ends).append(idx)
    if not starts and not ends:
        raise GenerationError(
            f"docs/benchmarks.md has no sentinel markers for family '{family}' "
            "(expected '<!-- cairn-bench-tables:{family} start -->' and a matching 'end')"
        )
    if len(starts) != 1 or len(ends) != 1:
        raise GenerationError(
            f"family '{family}' needs exactly one start and one end sentinel, "
            f"found {len(starts)} start / {len(ends)} end"
        )
    if ends[0] <= starts[0]:
        raise GenerationError(f"family '{family}': end sentinel precedes its start sentinel")
    return starts[0], ends[0]


def apply_family(text: str, family: str, generated: Sequence[str]) -> str:
    """Replace the bytes between a family's sentinels; leave everything else."""
    lines = text.split("\n")
    start, end = _sentinel_indices(lines, family)
    # Keep the sentinel lines themselves; splice the generated block between.
    return "\n".join(lines[: start + 1] + list(generated) + lines[end:])


def generate(baselines_dir: Path, docs_path: Path) -> bool:
    """Regenerate all three families in the doc. Returns True if bytes changed."""
    blocks = {}
    for family in FAMILIES:
        artifact = load_baseline(baselines_dir, family)
        block = RENDERERS[family](artifact)
        if not block:
            raise GenerationError(f"family '{family}' rendered an empty block")
        blocks[family] = block

    original = docs_path.read_text(encoding="utf-8")
    updated = original
    for family in FAMILIES:
        updated = apply_family(updated, family, blocks[family])

    if updated != original:
        docs_path.write_text(updated, encoding="utf-8")
        return True
    return False


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate docs/benchmarks.md reference tables from committed baselines."
    )
    parser.add_argument(
        "--baselines",
        default=None,
        help="baselines directory (default: <repo>/benchmarks/baselines/DS-v1)",
    )
    parser.add_argument(
        "--docs", default=None, help="benchmark doc path (default: <repo>/docs/benchmarks.md)"
    )
    args = parser.parse_args(argv)

    baselines_dir = Path(args.baselines) if args.baselines else REPO_ROOT / "benchmarks/baselines/DS-v1"
    docs_path = Path(args.docs) if args.docs else REPO_ROOT / "docs/benchmarks.md"

    try:
        if not docs_path.is_file():
            raise GenerationError(f"docs file not found: {docs_path}")
        changed = generate(baselines_dir, docs_path)
    except GenerationError as exc:
        print(f"gen_benchmark_tables.py: error: {exc}", file=sys.stderr)
        return 1

    if changed:
        print(f"regenerated tables in {docs_path} (families: {', '.join(FAMILIES)})")
    else:
        print(f"{docs_path} already current (families: {', '.join(FAMILIES)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
