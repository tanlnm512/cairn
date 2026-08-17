"""T023 (FR-006, D-016 DS-v2 leg): zero-shot validation of the ladder
candidates over the DS-v2 ground truth (D-011, BEIR-style).

The ladder's DS-v1 leg is run_ladder_candidates.py (the k-fold pooled
aggregate -- the tune-side evidence and the ship gate). This runner is the
VALIDATION leg: tune on DS-v1, validate zero-shot on DS-v2's two corpora --
no lever decision is made here. Candidates evaluated: the D-016 set --
(a) enrich+rerank-off, (b) enrich_idf+rerank-off, (c) the best new-lever
combo by the T020/T021 rows -- plus the incumbent (all-levers-off) for
within-family context. NEVER a diff against any DS-v1 row (D-008/D-011:
DS-v2 rows are their own family).

Protocol (D-009, inherited verbatim): one process, torch threads pinned
to 1, local bge-m3 (CAIRN_EMBED_BACKEND unset), brute-force cosine
(CAIRN_ANN_BACKEND=off), rerank marker CAIRN_RERANK=1 with flat pairs and
gate margin 0.45 (the incumbent row is rerank-active under the marker,
exactly today's retrieval; candidates that carry rerank=False win over
the marker). Models warmed with one untimed semantic_search per config
(flag-shaped warm-up, mirroring run_mv_sweep) before the timed seam.
Evaluation runs through the unchanged evaluate_full_set seam -- the
reporting path for final numbers AFTER lever decisions, exactly its
documented contract -- over ALL 198 DS-v2 queries.

Rows (D-011): per-corpus L1 rows (corpus derived from the expectations'
corpus-prefixed file paths) plus the macro-average across corpora, never
an aggregate alone, never a cross-corpus row diff. The 44 L5 queries are
evaluated and reported as their own leg, honestly: L5 retrieval is OKF
bundle search (no retrieval tunables; evaluate_graded_query routes by
level), and the benchmark corpora carry no .knowledge/ bundle, so the L5
leg measures the bundle-less harness floor (0.0 by construction) -- a
structural zero that is the same for every candidate, recorded, never
blended into the L1 rows.

Subcommands:
  build  -- copy both corpora into <workroot>/workspace (each COPY gets
           the empty .git scanner marker; committed trees stay
           marker-free), build the graph, verify the combined DS-v2
           build facts (sums of T010's per-corpus VERIFICATION facts).
  embed  -- the ONE real-embedding pass (base rows + mv rows + term_df:
           candidate (c) is the multivector combo, so the pass drives
           embed_all(multivector=True) -- the same single pass the CLI's
           --multivector flag runs).
  run    -- the zero-shot evaluation; emits the measurement doc plus a
            machine-mergeable rows payload (cairn-ds2-rows/1).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DS2_GROUND_TRUTH = REPO_ROOT / "benchmarks" / "datasource" / "ds2" / "ground_truth"
CORPUS_SOURCES = {
    "yarl": REPO_ROOT / "benchmarks" / "datasource" / "t2" / "yarl",
    "attrs-26.1.0": (
        REPO_ROOT / "benchmarks" / "datasource" / "ds2" / "second-corpus"
        / "attrs-26.1.0"
    ),
}
HERE = Path(__file__).resolve().parent

#: Combined DS-v2 build facts -- the sums of T010's sealed per-corpus
#: facts (VERIFICATION.md: attrs-26.1.0 1 repo / 50 files / 1672 symbols;
#: yarl 1 repo / 24 files / 1066 symbols; both 0 parse errors). A drifted
#: build aborts the measurement, never feeds it.
EXPECTED_FACTS = {"repos": 2, "files": 74, "symbols": 2738, "parse_errors": 0}

#: DS-v2 dataset facts (T009/T010 loader-verified; the run re-derives
#: them through the same loader before any retrieval runs).
EXPECTED_COUNTS = {
    "queries": 198,
    "l1": 154,
    "l5": 44,
    "l1_by_corpus": {"attrs-26.1.0": 106, "yarl": 48},
    "l5_by_corpus": {"attrs-26.1.0": 30, "yarl": 14},
}

#: The D-016 candidate set. (a)/(b) fixed; (c) is the best new-lever
#: combo by the T020/T021 rows -- resolved when those land, before this
#: runner executes (the resolution is part of T023's commit, visible in
#: the emitted document's candidates block).
CANDIDATES: dict[str, tuple[str, dict]] = {
    "enrich-rerankoff": (
        "enrich+rerank-off",
        {"enrich": True, "rerank": False},
    ),
    "enrichidf-rerankoff": (
        "enrich_idf+rerank-off",
        {"enrich": True, "enrich_idf": True, "rerank": False},
    ),
    # (c) resolved 2026-08-17 from the landed T020/T021 rows: multivector
    # is the best new-lever combo by the DS-v1 k-fold pooled bootstrap
    # (delta +0.1414, p=0.0035; PRF is negative at both grid points,
    # delta -0.0447/-0.0550) -- and the only configuration reaching both
    # SC-1 targets (0.5588/0.3395).
    "best-new-lever": ("multivector", {"multivector": True}),
}

INCUMBENT = "all-levers-off"


def cmd_build(args: argparse.Namespace) -> int:
    from cairn.graph.builder import build_graph

    workroot = Path(args.workroot)
    workspace = workroot / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    for name, src in CORPUS_SOURCES.items():
        repo_copy = workspace / name
        if repo_copy.exists():
            shutil.rmtree(repo_copy)
        shutil.copytree(src, repo_copy)
        (repo_copy / ".git").mkdir(exist_ok=True)  # scanner marker, COPY only
    db_path = workroot / "graph.db"
    if db_path.exists():
        db_path.unlink()
    summary = build_graph(
        workspace=str(workspace), db_path=str(db_path), verbose=False
    )
    facts = {k: summary.get(k) for k in EXPECTED_FACTS}
    print(json.dumps({"db": str(db_path), "facts": facts}, sort_keys=True))
    if facts != EXPECTED_FACTS:
        print(
            f"BUILD FACTS DRIFT: {facts} != recorded {EXPECTED_FACTS}",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_embed(args: argparse.Namespace) -> int:
    import cairn.paths  # noqa: F401  (injects ~/.cairn/lib: torch importable)

    import torch

    torch.set_num_threads(1)  # D-009 protocol: torch threads pinned to 1

    from cairn.graph import embeddings as emb
    from cairn.graph.schema import get_db

    db_path = Path(args.workroot) / "graph.db"
    conn = get_db(str(db_path))  # the embed CLI's own opener (Row factory)
    try:
        # multivector=True: candidate (c) is the mv combo, so the DS-v2 DB
        # needs embeddings_mv rows (the same single pass the CLI's
        # --multivector flag drives; base rows + term_df either way).
        summary = emb.embed_all(conn, multivector=True)
    finally:
        conn.close()
    conn = sqlite3.connect(str(db_path))
    try:
        n_vec = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        n_mv = conn.execute("SELECT COUNT(*) FROM embeddings_mv").fetchone()[0]
        n_df = conn.execute("SELECT COUNT(*) FROM term_df").fetchone()[0]
        db_mb = round(db_path.stat().st_size / (1024.0 * 1024.0), 4)
    finally:
        conn.close()
    print(
        json.dumps(
            {
                "db": str(db_path),
                "embed_summary": summary,
                "embeddings_rows": n_vec,
                "embeddings_mv_rows": n_mv,
                "term_df_rows": n_df,
                "db_mb": db_mb,
                "torch_num_threads": torch.get_num_threads(),
            },
            sort_keys=True,
            default=str,
        )
    )
    if n_vec != EXPECTED_FACTS["symbols"]:
        print(
            f"EMBED COUNT DRIFT: {n_vec} != {EXPECTED_FACTS['symbols']}",
            file=sys.stderr,
        )
        return 1
    if n_mv < EXPECTED_FACTS["symbols"]:
        # The name kind alone contributes one row per symbol.
        print(
            f"MV ROWS MISSING: embeddings_mv={n_mv} < {EXPECTED_FACTS['symbols']} "
            "-- candidate (c) would silently measure flag-off shapes",
            file=sys.stderr,
        )
        return 1
    if n_df < 1:
        print("term_df EMPTY: the enrich_idf df_lookup would be dataless",
              file=sys.stderr)
        return 1
    return 0


def _corpus_of(query) -> str:
    """The query's corpus, derived from its expectations' file paths.

    DS-v2 expectations are corpus-prefixed (``attrs-26.1.0/src/...#sym`` /
    ``yarl/...#sym`` -- the manifest's symbol_id_prefix convention). Every
    expectation of one query must share the prefix; a mixed query is a
    dataset-shape violation and aborts loudly.
    """
    prefixes = set()
    for exp in query.expectations:
        file_part = exp.symbol_id.split("#", 1)[0]
        prefixes.add(file_part.split("/", 1)[0])
    if len(prefixes) != 1:
        raise ValueError(
            f"{query.query_id}: expectations span corpora {sorted(prefixes)}"
        )
    return prefixes.pop()


def _figures(per_query: dict, durations: dict, ids: list[str], p95) -> dict:
    recalls = sorted(per_query[q]["recall_at_10"] for q in ids)
    mrrs = [per_query[q]["mrr"] for q in ids]
    times = sorted(durations[q] for q in ids)
    return {
        "n_queries": len(ids),
        "recall_at_10": round(sum(recalls) / len(ids), 4),
        "mrr": round(sum(mrrs) / len(ids), 4),
        "p95_ms": round(p95(times, 95.0), 4),
    }


def cmd_run(args: argparse.Namespace) -> int:
    # D-009 protocol pins, set before any cairn model code reads them.
    import os

    os.environ["CAIRN_RERANK"] = "1"  # incumbent's session protocol marker
    os.environ["CAIRN_ANN_BACKEND"] = "off"  # brute-force cosine, no vec0
    # CAIRN_EMBED_BACKEND intentionally untouched: unset = local bge-m3.

    import cairn.paths  # noqa: F401  (injects ~/.cairn/lib: torch importable)

    import torch

    torch.set_num_threads(1)

    from cairn.eval import (
        _percentile,
        evaluate_full_set,
        load_ground_truth,
        paired_bootstrap,
    )
    from cairn.graph.queries import semantic_search
    from cairn.graph.schema import get_db
    from cairn.graph.semantic import RetrievalParams

    if not CANDIDATES.get("best-new-lever"):
        print(
            "candidate (c) best-new-lever is unresolved: fill it from the "
            "T020/T021 rows before running (D-016)",
            file=sys.stderr,
        )
        return 2

    gt = load_ground_truth(DS2_GROUND_TRUTH)
    l1 = [q for q in gt if q.level == "L1"]
    l5 = [q for q in gt if q.level == "L5"]
    counts = {
        "queries": len(gt),
        "l1": len(l1),
        "l5": len(l5),
        "l1_by_corpus": {
            c: sum(1 for q in l1 if _corpus_of(q) == c) for c in CORPUS_SOURCES
        },
        "l5_by_corpus": {
            c: sum(1 for q in l5 if _corpus_of(q) == c) for c in CORPUS_SOURCES
        },
    }
    if counts != EXPECTED_COUNTS:
        print(
            f"DATASET COUNT DRIFT: {counts} != recorded {EXPECTED_COUNTS}",
            file=sys.stderr,
        )
        return 1

    conn = get_db(args.db)
    try:
        n_vec = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        n_sym = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        n_mv = conn.execute("SELECT COUNT(*) FROM embeddings_mv").fetchone()[0]
        n_df = conn.execute("SELECT COUNT(*) FROM term_df").fetchone()[0]
        if (n_vec, n_sym) != (EXPECTED_FACTS["symbols"],) * 2 or n_df < 1:
            print(
                f"SCRATCH DB DRIFT: embeddings={n_vec} symbols={n_sym} "
                f"term_df={n_df} != recorded {EXPECTED_FACTS}",
                file=sys.stderr,
            )
            return 1
        if n_mv < EXPECTED_FACTS["symbols"]:
            print(
                f"MV ROWS MISSING: embeddings_mv={n_mv} < "
                f"{EXPECTED_FACTS['symbols']} -- candidate (c) would "
                "silently measure flag-off shapes",
                file=sys.stderr,
            )
            return 1
        db_mb = round(
            Path(args.db).stat().st_size / (1024.0 * 1024.0), 4
        )

        configs: list[tuple[str, dict | None]] = [
            (INCUMBENT, None),
            *(
                (label, dict(fields))
                for _key, (label, fields) in sorted(CANDIDATES.items())
            ),
        ]
        reports: dict[str, dict] = {}
        for label, fields in configs:
            params = None if fields is None else RetrievalParams(**fields)
            # Flag-shaped untimed warm-up (models + this config's code
            # paths) before the timed seam.
            semantic_search(conn, "warm up the models", limit=1, params=params)
            started = time.perf_counter()
            reports[label] = evaluate_full_set(conn, gt, params=params)
            print(
                f"evaluated {label}: n={reports[label]['n_queries']} "
                f"elapsed_s={time.perf_counter() - started:.1f}"
            )
    finally:
        conn.close()

    # --- Per-corpus L1 rows + macro-average (D-011), plus the L5 leg. ---
    corpus_of = {q.query_id: _corpus_of(q) for q in gt}
    l1_ids = sorted(q.query_id for q in l1)
    l5_ids = sorted(q.query_id for q in l5)
    corpora = sorted(CORPUS_SOURCES)

    rows: list[dict] = []
    per_corpus: dict[str, dict] = {}
    for label, fields in configs:
        # The mv marker is DERIVED from the combo's own lever spec (the
        # CANDIDATES fields above), never hardcoded: only a combo whose
        # fields carry multivector=True is measured against the
        # embeddings_mv store (the incumbent's None = all levers off).
        mv = bool(fields and fields.get("multivector"))
        report = reports[label]
        pq, dur = report["per_query"], report["durations_ms"]
        blocks = {
            corpus: _figures(pq, dur, [q for q in l1_ids if corpus_of[q] == corpus], _percentile)
            for corpus in corpora
        }
        macro = {
            metric: round(
                sum(blocks[c][metric] for c in corpora) / len(corpora), 4
            )
            for metric in ("recall_at_10", "mrr", "p95_ms")
        }
        macro["n_queries"] = sum(blocks[c]["n_queries"] for c in corpora)
        l5_block = _figures(pq, dur, l5_ids, _percentile)
        # Within-family validation evidence (never a DS-v1 diff): the
        # paired bootstrap per corpus, candidate vs incumbent, over the
        # same DS-v2 L1 per-query arrays.
        boot = {}
        if label != INCUMBENT:
            inc = reports[INCUMBENT]["per_query"]
            for corpus in corpora:
                ids = [q for q in l1_ids if corpus_of[q] == corpus]
                boot[corpus] = paired_bootstrap(
                    [pq[q]["recall_at_10"] for q in ids],
                    [inc[q]["recall_at_10"] for q in ids],
                )
        per_corpus[label] = {
            "l1_per_corpus": blocks,
            "l1_macro_average": macro,
            "l5_leg": {
                **l5_block,
                "note": "structural zero: L5 retrieval is OKF bundle "
                "search (no retrieval tunables; evaluate_graded_query "
                "routes by level) and the benchmark corpora carry no "
                ".knowledge/ bundle -- identical for every config, "
                "recorded, never blended into the L1 rows",
            },
            "bootstrap_vs_incumbent_by_corpus": boot,
            "per_query": pq,
            "durations_ms": dur,
        }
        for corpus in corpora:
            rows.append(
                {
                    "family": "ds-v2",
                    "dataset": "DS-v2",
                    "corpus": corpus,
                    "combo": label,
                    "mv": mv,
                    "db_mb": db_mb,
                    "level": "L1",
                    **blocks[corpus],
                }
            )
        rows.append(
            {
                "family": "ds-v2",
                "dataset": "DS-v2",
                "corpus": "macro-average",
                "combo": label,
                "mv": mv,
                "db_mb": db_mb,
                "level": "L1",
                **{
                    k: macro[k]
                    for k in ("n_queries", "recall_at_10", "mrr", "p95_ms")
                },
            }
        )
        rows.append(
            {
                "family": "ds-v2",
                "dataset": "DS-v2",
                "corpus": "all",
                "combo": label,
                "mv": mv,
                "db_mb": db_mb,
                "level": "L5",
                **{
                    k: l5_block[k]
                    for k in ("n_queries", "recall_at_10", "mrr", "p95_ms")
                },
            }
        )

    doc = {
        "schema": "cairn-ds2-zeroshot/1",
        "task": "T023 (FR-006): D-016 ladder candidates, DS-v2 zero-shot "
        "validation leg (D-011)",
        "protocol": "D-009 pins (threads 1, local bge-m3, brute cosine, "
        "CAIRN_RERANK=1 marker with flat pairs + gate 0.45); evaluate_full_set "
        "over all 198 DS-v2 queries after untimed per-config warm-up; serial "
        "run on an otherwise-quiet reference machine (MEASURE.md)",
        "candidates": {
            key: label for key, (label, _f) in CANDIDATES.items()
        },
        "counts": counts,
        "db_mb": db_mb,
        "configs": {
            label: {
                k: v
                for k, v in block.items()
                if k not in ("per_query", "durations_ms")
            }
            for label, block in per_corpus.items()
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")

    rows_payload = {
        "schema": "cairn-ds2-rows/1",
        "task": "T023 (FR-006): DS-v2 rows to merge into "
        "benchmarks/quality/ablation.json",
        "counts": counts,
        "db_mb": db_mb,
        "rows": rows,
        "l5_legs": {
            label: block["l5_leg"] for label, block in per_corpus.items()
        },
    }
    rows_path = HERE / "rows-ds2.json"
    rows_path.write_text(
        json.dumps(rows_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(f"wrote {out}")
    print(f"wrote {rows_path} ({len(rows)} rows)")
    for label, _fields in configs:
        block = per_corpus[label]
        print(
            f"{label}: "
            + " ".join(
                f"{c}={block['l1_per_corpus'][c]['recall_at_10']}/"
                f"{block['l1_per_corpus'][c]['mrr']}"
                for c in corpora
            )
            + f" macro={block['l1_macro_average']['recall_at_10']}/"
            f"{block['l1_macro_average']['mrr']}"
            f" l5={block['l5_leg']['recall_at_10']}/{block['l5_leg']['mrr']}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="copy both corpora + build the graph")
    b.add_argument("--workroot", default="/tmp/ds2-zeroshot")
    b.set_defaults(func=cmd_build)
    e = sub.add_parser("embed", help="the one bge-m3 embed pass (+ term_df)")
    e.add_argument("--workroot", default="/tmp/ds2-zeroshot")
    e.set_defaults(func=cmd_embed)
    r = sub.add_parser("run", help="the zero-shot evaluation")
    r.add_argument("--db", default="/tmp/ds2-zeroshot/graph.db")
    r.add_argument("--out", default=str(HERE / "sweep-ds2-zeroshot.json"))
    r.set_defaults(func=cmd_run)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
