"""T014 (FR-003): build the scratch measurement DB over the DS-v1 t2 corpus.

Two subcommands, one workroot:

* ``build`` -- copy ``benchmarks/datasource/t2/yarl`` into
  ``<workroot>/workspace/yarl`` (the COPY gets the empty ``.git`` scanner
  marker; the committed tree stays marker-free), build the graph there and
  verify the recorded DS-v1 build facts (1 repo / 24 files / 1066 symbols /
  0 parse errors -- T008's authoring facts, reproduced by T010's seal).
* ``embed`` -- the ONE real-embedding pass over the built graph: local
  bge-m3 (CAIRN_EMBED_BACKEND unset), torch threads pinned to 1 (the
  D-009 protocol discipline inherited from benchmarks/quality/ablation.md).
  ``embed_all`` ends with ``rebuild_term_df`` (T011), so the pass also
  mints the ``term_df`` table the FR-003 ``enrich_idf`` lookup reads. No
  vec0 index is built: quality runs use brute-force cosine
  (CAIRN_ANN_BACKEND=off at measurement time).

The scratch DB lives under the workroot (default /tmp/fr003-calibration) --
never inside the repo; only the sweep documents this directory's companion
driver emits land in the repository.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
T2_YARL = REPO_ROOT / "benchmarks" / "datasource" / "t2" / "yarl"

#: DS-v1's recorded build facts (T008 authoring, T010 seal): a degraded or
#: drifted scratch build must abort the calibration, never feed it.
EXPECTED_FACTS = {"repos": 1, "files": 24, "symbols": 1066, "parse_errors": 0}


def cmd_build(args: argparse.Namespace) -> int:
    from cairn.graph.builder import build_graph

    workroot = Path(args.workroot)
    workspace = workroot / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    repo_copy = workspace / T2_YARL.name
    if repo_copy.exists():
        shutil.rmtree(repo_copy)
    shutil.copytree(T2_YARL, repo_copy)
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
    # get_db (the embed CLI's own opener): Row factory + pragmas --
    # embed_all reads rows via r.keys(), which plain tuples lack.
    conn = get_db(str(db_path))
    try:
        summary = emb.embed_all(conn)
    finally:
        conn.close()
    conn = sqlite3.connect(str(db_path))
    try:
        n_vec = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
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
                "term_df_rows": n_df,
                "db_mb": db_mb,
                "torch_num_threads": torch.get_num_threads(),
            },
            sort_keys=True,
            default=str,
        )
    )
    if n_vec != EXPECTED_FACTS["symbols"]:
        print(f"EMBED COUNT DRIFT: {n_vec} != {EXPECTED_FACTS['symbols']}", file=sys.stderr)
        return 1
    if n_df < 1:
        print("term_df EMPTY: the FR-003 df_lookup would be dataless", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workroot", default="/tmp/fr003-calibration")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="copy t2/yarl + build the graph")
    sub.add_parser("embed", help="the one bge-m3 embed pass (+ term_df)")
    args = parser.parse_args(argv)
    return {"build": cmd_build, "embed": cmd_embed}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
