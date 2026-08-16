#!/usr/bin/env python3
"""Warm-time harness: first semantic query wall-time in a fresh process (T022, FR-007).

Measures what the boot-time model warm-up (``cairn.graph.model_warmup``,
wired in ``mcp_server/server.py``) buys the user on their first
``semantic_search``: two FRESH subprocesses over one pre-built tiny embedded
DB --

* **cold arm**: no warm-up; the first query pays the full lazy load of the
  sentence-transformers embedder (inside ``embed_query``) and the
  CrossEncoder reranker (inside ``rerank()``), exactly as a server without
  warm-up would.
* **warm arm**: ``warm_models_in_background()`` is called and its boot
  thread JOINED before the query. A real server never joins (serving starts
  while weights load); the harness joins because it is the deterministic
  post-warm measurement point. ``CAIRN_WARM_MODELS`` is honored by the
  warm-up function itself -- the harness never bypasses the kill switch.

The fixture is deliberately tiny and synthetic: the metric is MODEL LOAD
time, corpus-independent by design (a bigger corpus only grows the encode/
scan share of the query). The artifact therefore stamps the checkout's
dataset identity (same T013 ``build_artifact_stamp`` as every bench
artifact) for machine/version context and records the actual fixture under
``fixture``.

The committed artifact (``benchmarks/quality/warm_time.json``) carries a
``notes`` field stating that the phase-doc figure -- first semantic query
9,428 -> 322 ms, ``docs/phases/performance-gap/task.md:40`` (P0-1) -- is
ADVISORY context, not a gate: no committed baseline ever carried a
warm-time number, so there is no BEFORE to regress against. This harness
is the re-measurement path the phase figure never had.

Usage:
    uv run python scripts/measure_warm_time.py              # full mint -> benchmarks/quality/warm_time.json
    uv run python scripts/measure_warm_time.py --force      # overwrite a pre-commit mint (D-010 spirit)
    uv run python scripts/measure_warm_time.py --mode build --workroot /tmp/wt   # build fixture only
    uv run python scripts/measure_warm_time.py --mode cold --db /tmp/wt/graph.db # one arm, JSON on stdout
    uv run python scripts/measure_warm_time.py --mode cold --backend hash --db ...  # dep-free smoke arm

``--backend hash`` is the smoke path only (tests use it): no model ever
loads, so warm/cold collapse to the same query cost and the full mint
REFUSES to run under it. Hash mode also forces ``CAIRN_RERANK=0`` so the
smoke stays dep-free even on a machine whose persistent rerank marker
(``~/.cairn/rerank_enabled``) would otherwise load the cross-encoder.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow running from a source checkout without installing (same pattern as
# scripts/mint_baselines.py). Guarded insert so repeated imports never grow
# sys.path.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

WARM_TIME_SCHEMA = "cairn-warm-time/1"
DEFAULT_OUT = REPO_ROOT / "benchmarks" / "quality" / "warm_time.json"

# Natural-language query with no verbatim symbol name in it: the rerank
# confidence gate requires an exact-name hit to skip, so this query forces
# the cold arm to pay the cross-encoder load inside the measured region.
DEFAULT_QUERY = "split a url string into its components and parse its query parameters"

# Tiny synthetic workspace: enough symbols for a healthy candidate pool
# (semantic_search reranks max(limit*5, 50) candidates), nothing more.
FIXTURE_SOURCES = {
    "tinyurl/models.py": '''"""Tiny URL model fixtures for the warm-time harness."""


class URL:
    """A parsed URL split into scheme, host, path, and query components."""

    def __init__(self, scheme, host, path, query):
        self.scheme = scheme
        self.host = host
        self.path = path
        self.query = query

    def components(self):
        """Return the URL broken into its component parts as a dict."""
        return {
            "scheme": self.scheme,
            "host": self.host,
            "path": self.path,
            "query": self.query,
        }


def parse_url(raw):
    """Split a raw URL string into scheme, host, path, and query components."""
    scheme, _, rest = raw.partition("://")
    host, _, path_and_query = rest.partition("/")
    path, _, query = path_and_query.partition("?")
    return URL(scheme, host, "/" + path, query)


def unquote_component(value):
    """Decode percent-encoding in one URL component."""
    out = []
    i = 0
    while i < len(value):
        if value[i] == "%" and i + 2 < len(value):
            out.append(chr(int(value[i + 1 : i + 3], 16)))
            i += 3
        else:
            out.append(value[i])
            i += 1
    return "".join(out)
''',
    "tinyurl/queries.py": '''"""Query-string helpers for the warm-time harness fixture."""

from tinyurl.models import parse_url, unquote_component


def split_query(query_string):
    """Split a URL query string into its key/value parameter pairs."""
    if not query_string:
        return []
    pairs = []
    for part in query_string.split("&"):
        key, _, value = part.partition("=")
        pairs.append((unquote_component(key), unquote_component(value)))
    return pairs


def encode_query(params):
    """Encode a dict of parameters into a URL query string."""
    return "&".join(f"{k}={v}" for k, v in params.items())


def decode_query(query_string):
    """Parse a query string into a dict, last value winning per key."""
    return dict(split_query(query_string))


def normalize_url(raw):
    """Parse a raw URL and rebuild its query string in canonical form."""
    parsed = parse_url(raw)
    return "?".join([parsed.scheme + "://" + parsed.host + parsed.path,
                     encode_query(decode_query(parsed.query))])
''',
}


def _emit(payload: dict) -> None:
    """Child contract: exactly one JSON line on stdout (parents parse it)."""
    print(json.dumps(payload), flush=True)


def _note(msg: str) -> None:
    """Progress goes to stderr: stdout is the child JSON channel."""
    print(f"[warm-time] {msg}", file=sys.stderr, flush=True)


def _apply_backend_policy(backend: str) -> None:
    """Pin the child's backend env so arms and build agree.

    ``local`` (the mint path) deliberately UNSETS ``CAIRN_EMBED_BACKEND``:
    the artifact measures the default configuration a real server boots
    with, not an env-pinned one. ``hash`` sets it explicitly and also forces
    ``CAIRN_RERANK=0`` (see module docstring: hash is the dep-free smoke
    path; the cross-encoder is not part of it).
    """
    if backend == "hash":
        os.environ["CAIRN_EMBED_BACKEND"] = "hash"
        os.environ["CAIRN_RERANK"] = "0"
    else:
        os.environ.pop("CAIRN_EMBED_BACKEND", None)


def run_build(workroot: Path, backend: str) -> dict:
    """Build the tiny fixture workspace + graph and embed it.

    Runs in its own process (the orchestrator spawns ``--mode build``), so
    the model load that ``embed_all`` pays here can never warm the arms --
    subprocess isolation is what keeps the cold arm honest.
    """
    _apply_backend_policy(backend)
    from cairn.graph import embeddings as emb
    from cairn.graph.builder import build_graph
    from cairn.graph.schema import get_db

    workspace = workroot / "workspace"
    repo = workspace / "tinyurl"
    for rel, source in FIXTURE_SOURCES.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    # Empty .git dir = the scanner's repo marker (same idiom as
    # scripts/mint_baselines.py mint_quality; only ever on the throwaway
    # copy inside workroot).
    (repo / ".git").mkdir(exist_ok=True)

    db_path = workroot / "graph.db"
    build = build_graph(workspace=str(workspace), db_path=str(db_path), verbose=False)
    conn = get_db(str(db_path))
    try:
        embed_summary = emb.embed_all(conn)
    finally:
        conn.close()
    payload = {
        "db": str(db_path),
        "build": {
            key: build.get(key)
            for key in ("repos", "files", "symbols", "edges", "parse_errors")
        },
        "embed": {
            "backend_effective": emb._effective_backend(),
            "model": emb.current_model(),
            "embedded": embed_summary.get("embedded"),
            "skipped": embed_summary.get("skipped"),
            "total": embed_summary.get("total"),
            "reaped": embed_summary.get("reaped"),
        },
    }
    _emit(payload)
    return payload


def _gate_snapshot() -> dict:
    """Everything that decides whether the measured query loads models."""
    from cairn.graph import embeddings as emb, reranker as rrk

    return {
        "embed_backend_effective": emb._effective_backend(),
        "embed_model": emb.current_model(),
        "embed_model_cached": emb.model_is_cached(),
        "rerank_enabled": rrk.rerank_enabled(),
        "reranker_available": rrk.reranker_available(),
        "reranker_model": rrk.current_rerank_model(),
        "reranker_model_cached": rrk.reranker_model_is_cached(),
        "env": {
            "CAIRN_EMBED_BACKEND": os.environ.get("CAIRN_EMBED_BACKEND"),
            "CAIRN_EMBED_LOCAL_MODEL": os.environ.get("CAIRN_EMBED_LOCAL_MODEL"),
            "CAIRN_RERANK": os.environ.get("CAIRN_RERANK"),
            "CAIRN_RERANK_MODEL": os.environ.get("CAIRN_RERANK_MODEL"),
            "CAIRN_FUSION": os.environ.get("CAIRN_FUSION"),
            "CAIRN_WARM_MODELS": os.environ.get("CAIRN_WARM_MODELS"),
        },
    }


def run_arm(mode: str, db_path: str, backend: str, query: str) -> dict:
    """Measure the first ``semantic_search`` in this FRESH process.

    warm: ``warm_models_in_background()`` + join (the deterministic
    post-warm point; a real server overlaps warm-up with serving), then
    time the query. cold: time the query with no warm-up at all -- the
    lazy model loads land inside the measured region, which is the cost
    the server boot warm-up exists to remove.
    """
    assert mode in ("warm", "cold")
    _apply_backend_policy(backend)
    from cairn.graph import reranker as rrk
    from cairn.graph import embeddings as emb
    from cairn.graph.model_warmup import warm_models_in_background
    from cairn.graph.schema import get_db
    from cairn.graph.semantic import semantic_search

    gates = _gate_snapshot()
    conn = get_db(db_path)  # opened before timing: the metric is QUERY time
    try:
        warmup: dict = {"called": False, "started": None, "join_ms": None}
        if mode == "warm":
            warmup["called"] = True
            t0 = time.perf_counter()
            thread = warm_models_in_background()
            if thread is None:
                # Kill switch (CAIRN_WARM_MODELS=0/false/no) or the pytest
                # no-start guard. Never bypassed here: record it honestly.
                warmup["started"] = False
            else:
                warmup["started"] = True
                thread.join()
                warmup["join_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        t1 = time.perf_counter()
        results = semantic_search(conn, query)
        first_query_ms = round((time.perf_counter() - t1) * 1000, 1)
    finally:
        conn.close()

    # Cache state proves what the query actually paid. With warm-up active
    # both process-level caches must be populated and the query itself must
    # be encode/predict only.
    payload = {
        "mode": mode,
        "first_query_ms": first_query_ms,
        "results": len(results),
        "top_result": results[0].get("name") if results else None,
        "reranked": bool(results[0].get("reranked")) if results else False,
        "warmup": warmup,
        "warm_caches": {
            "embedder": bool(emb._MODEL_CACHE),
            "reranker": bool(rrk._RERANKER_CACHE),
        },
        "gates": gates,
    }
    _emit(payload)
    return payload


def _refuse(reason: str) -> "SystemExit":
    return SystemExit(f"refusing to mint: {reason}")


def preflight(backend: str) -> dict:
    """Gate the full mint so the artifact can only be honest.

    The mint must measure the phase-figure path (both models load inside
    the cold query; warm-up populates both): that needs a real local
    embedder with cached weights, a reranker that is enabled + installed +
    cached, warm-up not kill-switched off, and no pytest/GHA context (the
    warm-up thread refuses to start under PYTEST_CURRENT_TEST, and a
    reference-machine artifact is never minted from CI).
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise _refuse(
            "PYTEST_CURRENT_TEST is set -- warm_models_in_background will not "
            "start a warm thread inside a test; run the mint from a plain shell"
        )
    if os.environ.get("GITHUB_ACTIONS"):
        raise _refuse(
            "under GitHub Actions -- warm-time is a reference-machine artifact "
            "(machine_profile records the class; D-005)"
        )
    if backend != "local":
        raise _refuse(
            f"--backend {backend!r} cannot mint: with no local embedder no "
            "model ever loads and warm/cold collapse to the same query cost "
            "(hash is the smoke path for tests, not a mint path)"
        )
    _apply_backend_policy(backend)
    from cairn.graph import embeddings as emb, reranker as rrk

    emb.reset_backend_cache()
    if emb._effective_backend() != "local":
        raise _refuse(
            "effective embed backend is not 'local' (install the [semantic] "
            "extra) -- the artifact measures model LOAD time; hash vectors "
            "never load a model"
        )
    if not emb.model_is_cached():
        raise _refuse(
            f"embedder weights {emb.current_model()!r} not in the local HF "
            "cache -- the harness never downloads (warm-up is a warm path, "
            "not an install path)"
        )
    rrank_gates = {
        "enabled": rrk.rerank_enabled(),
        "available": rrk.reranker_available(),
        "cached": rrk.reranker_model_is_cached(),
        "model": rrk.current_rerank_model(),
    }
    if not all((rrank_gates["enabled"], rrank_gates["available"], rrank_gates["cached"])):
        raise _refuse(
            f"reranker gates not all pass {rrank_gates} -- the cold arm would "
            "skip the cross-encoder load and the artifact would not measure "
            "the phase-figure path; enable reranking (CAIRN_RERANK=1 or the "
            "download marker) with cached weights and re-run"
        )
    if (os.environ.get("CAIRN_WARM_MODELS") or "").strip().lower() in ("0", "false", "no"):
        raise _refuse(
            "CAIRN_WARM_MODELS is explicitly off -- the 'warm' arm would "
            "measure a cold query; unset the kill switch to mint"
        )
    return {
        "embed_backend_effective": emb._effective_backend(),
        "embed_model": emb.current_model(),
        "reranker": rrank_gates,
    }


def _spawn(args: list[str]) -> dict:
    """Run one child mode; return its parsed stdout JSON line."""
    cmd = [sys.executable, str(Path(__file__).resolve()), *args]
    _note(" ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"child {' '.join(args)} exited {proc.returncode}")
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        sys.stderr.write(proc.stdout)
        raise SystemExit(f"child {' '.join(args)} emitted no parseable JSON line")


def _validate_arm(arm: dict, mode: str) -> None:
    """Fail the mint loudly when an arm did not measure what it claims."""
    where = f"{mode} arm"
    if arm.get("results", 0) < 1:
        raise SystemExit(f"{where}: semantic_search returned no results -- fixture too weak to measure")
    # Both gates for a rerank pass were checked up front, so the confidence
    # gate is the only way rerank could be absent; its skip needs an
    # exact-name hit, which the natural-language fixture query cannot make.
    if not arm.get("reranked"):
        raise SystemExit(
            f"{where}: query did not rerank (gate skip or degradation) -- the "
            "cold arm would under-report by skipping the cross-encoder load"
        )
    if mode == "warm":
        if not arm["warmup"]["started"]:
            raise SystemExit(
                f"{where}: warm_models_in_background returned no thread "
                "(kill switch or pytest guard) -- not a warm measurement"
            )
        if not (arm["warm_caches"]["embedder"] and arm["warm_caches"]["reranker"]):
            raise SystemExit(
                f"{where}: post-join caches not populated {arm['warm_caches']} -- "
                "the query was not actually warm"
            )


def mint(out_path: Path, backend: str, query: str, force: bool) -> dict:
    """Full mint: preflight -> build -> warm arm -> cold arm -> artifact."""
    gates = preflight(backend)
    if out_path.exists() and not force:
        raise SystemExit(
            f"{out_path} already exists; committed artifacts are re-measured "
            "with --force before they land, never silently overwritten "
            "(D-010 spirit)"
        )

    workroot = Path(tempfile.mkdtemp(prefix="cairn-warm-time-"))
    try:
        _note(f"workroot {workroot}")
        built = _spawn(["--mode", "build", "--backend", backend, "--workroot", str(workroot)])
        if built["build"]["parse_errors"] or built["build"]["symbols"] < 8:
            raise SystemExit(f"degraded fixture build: {built['build']}")
        if built["embed"]["backend_effective"] != "local" or built["embed"]["embedded"] != built["embed"]["total"]:
            raise SystemExit(f"fixture embedding incomplete: {built['embed']}")

        db = built["db"]
        warm = _spawn(["--mode", "warm", "--backend", backend, "--db", db, "--query", query])
        cold = _spawn(["--mode", "cold", "--backend", backend, "--db", db, "--query", query])
        _validate_arm(warm, "warm")
        _validate_arm(cold, "cold")
    finally:
        import shutil

        shutil.rmtree(workroot, ignore_errors=True)

    from cairn.bench.datasource import build_artifact_stamp

    stamp = build_artifact_stamp()
    warm_ms = warm["first_query_ms"]
    cold_ms = cold["first_query_ms"]
    payload = {
        "schema": WARM_TIME_SCHEMA,
        "suite": "warm-time",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        # T013 stamp: checkout dataset identity + cairn version + machine
        # profile. The measured fixture is the synthetic workspace below --
        # warm-time is model-load time, corpus-independent by design.
        **stamp,
        "harness": "scripts/measure_warm_time.py",
        "reproduce": "uv run python scripts/measure_warm_time.py --force",
        "embed": {"backend": gates["embed_backend_effective"], "model": gates["embed_model"]},
        "reranker": gates["reranker"],
        "fixture": {
            "name": "warm-time-tinyurl (synthetic)",
            "files": built["build"]["files"],
            "symbols": built["build"]["symbols"],
            "edges": built["build"]["edges"],
            "embedded": built["embed"]["embedded"],
            "query": query,
            "note": "tiny by design: the metric is model LOAD time, not retrieval quality or scan scale",
        },
        "env": {
            "CAIRN_WARM_MODELS": warm["gates"]["env"]["CAIRN_WARM_MODELS"],
            "CAIRN_EMBED_BACKEND": warm["gates"]["env"]["CAIRN_EMBED_BACKEND"],
            "CAIRN_EMBED_LOCAL_MODEL": warm["gates"]["env"]["CAIRN_EMBED_LOCAL_MODEL"],
            "CAIRN_RERANK": warm["gates"]["env"]["CAIRN_RERANK"],
            "CAIRN_RERANK_MODEL": warm["gates"]["env"]["CAIRN_RERANK_MODEL"],
            "CAIRN_FUSION": warm["gates"]["env"]["CAIRN_FUSION"],
        },
        "measurement": {
            "cold": cold,
            "warm": warm,
            "speedup_cold_over_warm": round(cold_ms / warm_ms, 1) if warm_ms else None,
        },
        "notes": (
            "ADVISORY HISTORY: the phase-doc figure 'first semantic query "
            "9,428 -> 322 ms (29x)' (docs/phases/performance-gap/task.md:40, "
            "P0-1) is context, NOT a gate -- no committed baseline ever carried "
            "a warm-time number, so this artifact is the first committed "
            "measurement and has no BEFORE to regress against; T022 minted it "
            "to give warm-time a committed re-measurement path. METHODOLOGY: "
            "two fresh subprocesses over one pre-built tiny embedded DB; the "
            "cold arm's first semantic_search pays the lazy embedder + "
            "cross-encoder loads inside the timed region, the warm arm calls "
            "warm_models_in_background() and JOINS the boot thread (the "
            "deterministic post-warm point; a real server overlaps warm-up "
            "with serving, so warm.first_query_ms is the floor a user sees "
            "once boot finishes). The cold arm runs fully ONLINE -- "
            "warm-up's HF-offline window exists only in the warm arm -- so "
            "cold.first_query_ms includes the HuggingFace Hub metadata "
            "round-trips that fire even on cached weights (the ~5s tax the "
            "phase doc attributes to the pre-warm-up path). env records the "
            "child-runtime values: the mint pins CAIRN_EMBED_BACKEND unset "
            "to measure the default boot configuration. CAIRN_WARM_MODELS is honored by "
            "warm_models_in_background itself -- the harness never bypasses "
            "the kill switch (value recorded in env; preflight refuses to "
            "mint under an explicit off). Single-shot wall-times: "
            "machine-bound (see machine_profile), treat cross-machine deltas "
            "as noise."
        ),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _note(f"wrote {out_path}")
    _note(
        f"cold first query {cold_ms} ms | warm first query {warm_ms} ms "
        f"(warm-up join {warm['warmup']['join_ms']} ms) | "
        f"speedup {payload['measurement']['speedup_cold_over_warm']}x"
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--mode",
        choices=("all", "build", "warm", "cold"),
        default="all",
        help="all (default) = full mint via fresh subprocesses; build/warm/cold "
        "run one stage in THIS process and emit one JSON line on stdout",
    )
    parser.add_argument(
        "--backend",
        choices=("local", "hash"),
        default="local",
        help="local (default) = the real model-load path the mint requires; "
        "hash = dep-free smoke path for tests (never loads a model)",
    )
    parser.add_argument("--db", help="graph db path (warm/cold modes)")
    parser.add_argument("--workroot", help="scratch dir (build mode; default mkdtemp)")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="the measured semantic query")
    parser.add_argument(
        "--out", default=str(DEFAULT_OUT), help=f"artifact destination (default {DEFAULT_OUT})"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing artifact (pre-commit re-mint only; D-010 spirit)",
    )
    args = parser.parse_args(argv)

    if args.mode == "all":
        mint(Path(args.out), args.backend, args.query, args.force)
    elif args.mode == "build":
        root = Path(args.workroot) if args.workroot else Path(tempfile.mkdtemp(prefix="cairn-warm-time-"))
        run_build(root, args.backend)
    else:
        if not args.db:
            parser.error(f"--mode {args.mode} requires --db (build one first: --mode build)")
        run_arm(args.mode, args.db, args.backend, args.query)
    return 0


if __name__ == "__main__":
    sys.exit(main())
