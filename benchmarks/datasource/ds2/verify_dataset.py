#!/usr/bin/env python3
"""DS-v2 ground-truth verifier: every expectation vs fresh builds of BOTH corpora.

The TC-006 sealing pass (task T010, FR-002). Reproduces the T008/T009
authoring method (AUTHORING.md) over the COMPLETE dataset:

1. Loads ``ground_truth/`` through ``cairn.eval.load_ground_truth`` (fails
   loudly on any shape violation -- the loader is the dataset's own gate).
2. Builds a FRESH graph per corpus: the vendored source tree is copied to a
   throwaway workspace and the COPY gets the empty ``.git`` scanner marker
   (same idiom as ``scripts/verify_ground_truth.py:build_fresh_graph`` and
   ``bench/corpus.py``; the committed trees never carry the marker). A
   degraded build (0 repos, parse errors, empty inventory) aborts -- a
   partial build must not mint verdicts.
3. Resolves EVERY expectation tier-1-exact against its corpus inventory:
   exact symbol-name equality PLUS exact repo-relative file-path equality
   after stripping the corpus prefix (``attrs-26.1.0/src/attr/_make.py#attrs``
   -> ``("attrs", "src/attr/_make.py")`` vs the attrs build; ``yarl/...``
   likewise). This is STRICTER than ``match_rank``'s tier-1 (file *suffix*),
   which is also run over the full pool for parity with the committed
   verifier: an exact match implies tier-1, tier-1 implies rank > 0.
4. Cross-checks the build facts against the ones recorded at authoring time
   (AUTHORING.md) and the counts against ``ground_truth/manifest.json`` --
   drift means the corpus or dataset content changed and the seal is stale.

Exit codes: 0 verified (100% tier-1-exact, no drift), 1 stale/aspirational
entries or count/tree-hash drift, 2 infrastructure failure (missing dirs,
degraded builds, malformed dataset).

Usage:
    uv run python benchmarks/datasource/ds2/verify_dataset.py
    uv run python benchmarks/datasource/ds2/verify_dataset.py --json
    uv run python benchmarks/datasource/ds2/verify_dataset.py --workroot /tmp/x
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cairn.bench.datasource import tree_hash  # noqa: E402
from cairn.eval import load_ground_truth, match_rank, parse_symbol_id  # noqa: E402

DS2_ROOT = Path(__file__).resolve().parent
GT_DIR = DS2_ROOT / "ground_truth"

# One entry per corpus: vendored source tree, symbol_id prefix, and the build
# facts recorded at authoring time (AUTHORING.md) -- the seal is verified
# AGAINST these; drift means the corpus content moved under the dataset.
CORPORA = {
    "attrs-26.1.0": {
        "source": DS2_ROOT / "second-corpus" / "attrs-26.1.0",
        "prefix": "attrs-26.1.0/",
        "recorded_facts": {
            "repos": 1, "files": 50, "symbols": 1672, "edges": 4174,
            "parse_errors": 0,
        },
    },
    "yarl": {
        "source": REPO_ROOT / "benchmarks" / "datasource" / "t2" / "yarl",
        "prefix": "yarl/",
        "recorded_facts": {
            "repos": 1, "files": 24, "symbols": 1066, "edges": 2432,
            "parse_errors": 0,
        },
    },
}

EXIT_OK = 0
EXIT_STALE = 1
EXIT_INFRA = 2


def build_fresh(source: Path, workroot: Path) -> tuple[dict, Path]:
    """Copy ``source`` into ``workroot``/workspace and build a graph there.

    The empty ``.git`` scanner marker goes on the COPY only (the committed
    tree must stay marker-free). Raises ValueError on a missing source, a
    build failure, or a degraded build (0 repos / parse errors).
    """
    from cairn.graph.builder import build_graph

    if not source.is_dir():
        raise ValueError(f"corpus source missing: {source}")
    workspace = workroot / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    repo_copy = workspace / source.name
    if repo_copy.exists():
        shutil.rmtree(repo_copy)
    shutil.copytree(source, repo_copy)
    (repo_copy / ".git").mkdir(exist_ok=True)  # scanner marker, COPY only
    db_path = workroot / "graph.db"
    try:
        summary = build_graph(
            workspace=str(workspace), db_path=str(db_path), verbose=False
        )
    except Exception as exc:  # noqa: BLE001 - any build failure is infra
        raise ValueError(f"graph build failed over {source}: {exc}") from exc
    facts = {k: summary.get(k) for k in
             ("repos", "files", "symbols", "edges", "parse_errors")}
    if facts["repos"] < 1:
        raise ValueError(f"no repos recognized under {workspace}")
    if facts["parse_errors"]:
        raise ValueError(
            f"degraded build over {source}: {facts['parse_errors']} parse error(s)"
        )
    return facts, db_path


def symbol_inventory(db_path: Path) -> list[dict]:
    """Every symbols row joined to its file path (the find_definition surface)."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT s.name, f.path AS file_path FROM symbols s "
            "JOIN files f ON s.file_id = f.id"
        ).fetchall()
    finally:
        conn.close()
    return [{"name": name, "file_path": path} for name, path in rows]


def verify_dataset(workroot: Path | None = None) -> tuple[dict, int]:
    """Run the full sealing pass; returns (report, exit_code)."""
    errors: list[str] = []
    owns = workroot is None
    if workroot is None:
        workroot = Path(tempfile.mkdtemp(prefix="cairn-ds2-verify-"))
    workroot = Path(workroot)

    report: dict = {"schema": "cairn-ds2-verification-run/1"}
    try:
        try:
            queries = load_ground_truth(GT_DIR)
        except ValueError as exc:
            return ({"schema": report["schema"], "errors": [str(exc)]}, EXIT_INFRA)

        builds: dict[str, dict] = {}
        exact_sets: dict[str, set] = {}
        pools: dict[str, list] = {}
        try:
            for name, cfg in CORPORA.items():
                wr = workroot / name
                wr.mkdir(parents=True, exist_ok=True)
                facts, db_path = build_fresh(cfg["source"], wr)
                inv = symbol_inventory(db_path)
                if not inv:
                    raise ValueError(f"empty symbol inventory for {name}")
                builds[name] = {
                    "facts": facts,
                    "recorded_facts": cfg["recorded_facts"],
                    "facts_match_authoring": facts == cfg["recorded_facts"],
                    "inventory": len(inv),
                    "tree_hash": tree_hash(cfg["source"]),
                }
                if facts != cfg["recorded_facts"]:
                    errors.append(
                        f"build facts drift for {name}: observed {facts} vs "
                        f"recorded {cfg['recorded_facts']}"
                    )
                exact_sets[name] = {(r["name"], r["file_path"]) for r in inv}
                pools[name] = inv
        except ValueError as exc:
            errors.append(str(exc))
            report["errors"] = errors
            return report, EXIT_INFRA

        # manifest cross-check (counts + corpus tree_hash pins)
        try:
            manifest = json.loads((GT_DIR / "manifest.json").read_text())
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"manifest.json unreadable: {exc}")
            manifest = None
        if manifest is not None:
            if manifest.get("dataset_version") != "DS-v2":
                errors.append(
                    f"manifest dataset_version {manifest.get('dataset_version')!r} "
                    "!= 'DS-v2'"
                )
            for name, cfg in CORPORA.items():
                pin = manifest.get("corpora", {}).get(name, {}).get("tree_hash")
                if pin != builds[name]["tree_hash"]:
                    errors.append(
                        f"manifest corpora.{name}.tree_hash {pin!r} != observed "
                        f"{builds[name]['tree_hash']!r} (corpus content drifted)"
                    )

        q_levels = Counter(q.level for q in queries)
        q_kinds = Counter((q.level, q.kind) for q in queries)
        q_corpus = Counter(
            q.expectations[0].symbol_id.split("/", 1)[0] for q in queries
        )
        exp_level_kind = Counter()
        exp_corpus = Counter()
        unresolved: list[dict] = []
        resolved = 0
        total = 0
        single_primary = True
        for q in queries:
            corpus = q.expectations[0].symbol_id.split("/", 1)[0]
            if any(e.symbol_id.split("/", 1)[0] != corpus for e in q.expectations):
                errors.append(f"{q.query_id}: expectations span corpora")
                continue
            if sum(1 for e in q.expectations if e.grade == 2) != 1:
                single_primary = False
            for exp in q.expectations:
                total += 1
                exp_level_kind[(q.level, q.kind)] += 1
                exp_corpus[corpus] += 1
                file_part, symbol_part = parse_symbol_id(exp.symbol_id)
                prefix = CORPORA[corpus]["prefix"]
                if not file_part.startswith(prefix):
                    unresolved.append({"symbol_id": exp.symbol_id,
                                       "issue": f"lacks prefix {prefix!r}"})
                    continue
                rel = file_part[len(prefix):]
                if (symbol_part, rel) in exact_sets[corpus]:
                    resolved += 1
                else:
                    unresolved.append({
                        "query_id": q.query_id, "level": q.level, "kind": q.kind,
                        "grade": exp.grade, "symbol_id": exp.symbol_id,
                        "issue": "not tier-1-exact in the fresh inventory",
                    })
                if match_rank(exp.symbol_id, pools[corpus],
                              k=len(pools[corpus])) == 0:
                    unresolved.append({
                        "query_id": q.query_id, "symbol_id": exp.symbol_id,
                        "issue": "match_rank rank 0 over the full pool",
                    })

        if manifest is not None:
            gt_block = manifest.get("ground_truth", {})
            expected_counts = {
                "l1_queries": gt_block.get("l1_queries"),
                "l5_queries": gt_block.get("l5_queries"),
                "expectations": gt_block.get("expectations"),
            }
            if expected_counts["l1_queries"] != q_levels.get("L1"):
                errors.append(f"manifest l1_queries {expected_counts['l1_queries']}"
                              f" != loader {q_levels.get('L1')}")
            if expected_counts["l5_queries"] != q_levels.get("L5"):
                errors.append(f"manifest l5_queries {expected_counts['l5_queries']}"
                              f" != loader {q_levels.get('L5')}")
            if expected_counts["expectations"] != total:
                errors.append(f"manifest expectations {expected_counts['expectations']}"
                              f" != observed {total}")

        report.update({
            "errors": errors,
            "dataset": str(GT_DIR.relative_to(REPO_ROOT)),
            "dataset_version": (manifest or {}).get("dataset_version"),
            "builds": builds,
            "loader": {
                "queries": len(queries),
                "levels": dict(sorted(q_levels.items())),
                "kinds": {f"{lv}:{kind}": n
                          for (lv, kind), n in sorted(q_kinds.items())},
                "queries_per_corpus": dict(sorted(q_corpus.items())),
            },
            "expectations": {
                "total": total,
                "tier1_exact": resolved,
                "unresolved": len(unresolved),
                "pass_rate": (resolved / total) if total else 0.0,
                "per_level_kind": {f"{lv}:{kind}": n for (lv, kind), n
                                   in sorted(exp_level_kind.items())},
                "per_corpus": dict(sorted(exp_corpus.items())),
                "every_query_exactly_one_grade2": single_primary,
            },
            "unresolved_rows": unresolved,
        })
        if errors:
            return report, EXIT_STALE
        if unresolved or total != 558 or resolved != total:
            return report, EXIT_STALE
        return report, EXIT_OK
    finally:
        if owns:
            shutil.rmtree(workroot, ignore_errors=True)


def _print_human(report: dict, code: int) -> None:
    if code == EXIT_INFRA:
        print("FAIL: infrastructure error -- nothing was verified:", file=sys.stderr)
        for err in report.get("errors", []):
            print(f"  {err}", file=sys.stderr)
        return
    builds = report["builds"]
    for name, b in sorted(builds.items()):
        print(
            f"corpus {name}: fresh build {b['facts']} "
            f"(facts match authoring: {b['facts_match_authoring']}, "
            f"tree_hash {b['tree_hash'][:12]}...)"
        )
    ld = report["loader"]
    ex = report["expectations"]
    print(
        f"loader: {ld['queries']} queries {ld['levels']} kinds {ld['kinds']} "
        f"per-corpus {ld['queries_per_corpus']}"
    )
    verdict = "OK  " if code == EXIT_OK else "FAIL"
    print(
        f"{verdict}: {ex['tier1_exact']}/{ex['total']} expectations tier-1-exact "
        f"(pass rate {ex['pass_rate']:.4f}, unresolved {ex['unresolved']}, "
        f"aspirational {ex['unresolved']}); exactly-one-grade-2-per-query "
        f"{ex['every_query_exactly_one_grade2']}"
    )
    for err in report.get("errors", []):
        print(f"  drift/error: {err}", file=sys.stderr)
    for row in report.get("unresolved_rows", [])[:20]:
        print(f"  unresolved: {row}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true",
                        help="print the machine-readable report")
    parser.add_argument("--workroot", type=Path, default=None,
                        help="scratch dir for workspace copies + graph DBs")
    args = parser.parse_args(argv)
    report, code = verify_dataset(workroot=args.workroot)
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        _print_human(report, code)
    return code


if __name__ == "__main__":
    sys.exit(main())
