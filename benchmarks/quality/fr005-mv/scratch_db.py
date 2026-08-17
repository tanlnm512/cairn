"""T021 (FR-005): scratch measurement DB over the DS-v1 t2 corpus.

``build`` -- copy ``benchmarks/datasource/t2/yarl`` into
``<workroot>/workspace/yarl`` (the COPY gets the empty ``.git`` scanner
marker; the committed tree stays marker-free), build the graph there and
verify the recorded DS-v1 build facts (1 repo / 24 files / 1066 symbols /
0 parse errors -- T008's authoring facts, reproduced by T010's seal).
Mirrors fr003-calibration/scratch_db.py's ``build`` verbatim (same
workroot layout, same facts gate) so the two measurement DBs are
structurally comparable.

The embedding pass is NOT driven from here: T021's brief runs it through
the landed CLI flag — ``cairn embed --db <workroot>/graph.db
--multivector`` — one command, polled. Per the D-009 quality-DB doctrine
(brute cosine at measurement time, T014's scratch DB carried no vec0
either) the pass runs with ``CAIRN_ANN_BACKEND=off``: no vec_/vecmv_
shadow tables, so db_mb reflects table storage and stays comparable with
the committed session baseline (db_mb 6.8945, no vec0). Torch threads are
pinned to 1 via ``OMP_NUM_THREADS=1``/``MKL_NUM_THREADS=1`` in the env
of that CLI invocation (the D-009 pin; the CLI itself does not pin).

The scratch DB lives under the workroot (default /tmp/fr005-mv) — never
inside the repo; only the documents this directory's companion driver
emits land in the repository.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
T2_YARL = REPO_ROOT / "benchmarks" / "datasource" / "t2" / "yarl"

#: DS-v1's recorded build facts (T008 authoring, T010 seal): a degraded or
#: drifted scratch build must abort the measurement, never feed it.
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
    summary = build_graph(workspace=str(workspace), db_path=str(db_path), verbose=False)
    facts = {k: summary.get(k) for k in EXPECTED_FACTS}
    print(json.dumps({"db": str(db_path), "facts": facts}, sort_keys=True))
    if facts != EXPECTED_FACTS:
        print(
            f"BUILD FACTS DRIFT: {facts} != recorded {EXPECTED_FACTS}",
            file=__import__("sys").stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workroot", default="/tmp/fr005-mv")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="copy t2/yarl + build the graph")
    args = parser.parse_args(argv)
    return {"build": cmd_build}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
