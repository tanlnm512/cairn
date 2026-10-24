# Baselines: Python implementation (rust-migration P0-2)

**Frozen.** These are the numbers the Rust migration compares against
(plan.md P0-2, gates G1/G2). Measured 2026-10-18 with cairn 0.22.0 (Python
implementation) on the machine stamped below. Never overwrite: re-measures
belong in a new section or a new file, never a mutation of these.
The Rust-engine re-measure (4d-4) is appended as its own section at the
end of this file.

## Method

Harness: `scripts/rust_migration/baseline.py` (run with the repo venv
python). One run per corpus:

1. **Full build** — `build_graph(workspace, db)` + `build_dataflow_index`,
   the `cairn build` shape, into a scratch store (`tempfile.mkdtemp`), never
   the live store. `cairn build`'s default dataflow budget caps at 2000
   public symbols (the builder prints the partial-coverage warning; this is
   the default deployment shape, kept).
2. **Closure** — `build_transitive_closure(conn)` timed on the persisted
   store; the `-wal` sidecar is polled every 5 ms for a peak, with the
   post-commit sidecar size as the single-burst floor (the 256 MB page cache
   lets one transaction spill to WAL in one burst at commit, which a poller
   can miss).
3. **Embed** — `embed_all` with `CAIRN_EMBED_BACKEND=hash` (dep-free,
   deterministic; the same pin every `cairn bench` suite uses), so
   `semantic_search` measures a real vector path. `CAIRN_RERANK=0` pinned.
4. **Daemon latency** — a fresh `cairn.mcp_server.server.run()` child
   (streamable HTTP, stateful, loopback, keyless) bound to the scratch store;
   one MCP session (`initialize` -> `notifications/initialized`), then
   `tools/list` (must be 26) and per tool 1 untimed warmup + 20 timed
   `tools/call` over one keep-alive HTTP connection. p95 = nearest-rank.
   Write tools (`knowledge_delete`, `knowledge_status`) re-create their
   fixture with an untimed `knowledge_add` before each timed call; all 26
   tools returned success on every timed call (0 errors, all corpora).
   The knowledge bundle for the cairn self corpus is a copy of the live
   `.knowledge` (487 files), so compass/wiki/memory tools measure real
   content; corpus stores start with an empty bundle (that is their real
   state).
5. **Daemon RSS** — `ps -o rss=` on the daemon pid every 200 ms
   (peak + final, KiB).

Environment pins: `CAIRN_EMBED_BACKEND=hash`, `CAIRN_RERANK=0`. Everything
else default (watcher on, freshness guard on). Machine stamp (identical for
all three runs): cairn 0.22.0, `macOS-27.0.1-arm64-arm-64bit`, arm64,
10 CPUs.

Read the self-store tool numbers before comparing: the per-call
**graph-freshness guard** (`refresh_for_query` -> `_detect_changed`,
`src/cairn/graph/watcher.py`) re-stats every indexed file AND re-walks the
repo source tree for new files before each graph read. On the self store
that is ~654 stat calls plus a gitignore-aware tree walk per tool call,
costing ~300 ms — that floor is the Python implementation's real per-call
cost (knowledge-layer tools that skip the guard run at ~1-14 ms on the same
store), not a measurement artifact. The Rust daemon will be measured by the
identical method on the identical stores.

Reproduce (same machine class required for comparability):

    .venv/bin/python scripts/rust_migration/baseline.py --label cairn-self --repeats 20 --out <out>.json
    .venv/bin/python scripts/rust_migration/baseline.py --workspace benchmarks/datasource/t2/yarl --label yarl --repeats 20 --out <out>.json
    .venv/bin/python scripts/rust_migration/baseline.py --workspace benchmarks/datasource/ds2/second-corpus/attrs-26.1.0 --label attrs --repeats 20 --out <out>.json

Corpora without a `.git` marker (yarl, attrs) are copied to scratch and given
the marker before building; the tree hash below is computed on the in-repo
corpus (identical content).

## Summary

| metric | cairn self | yarl | attrs |
|--------|-----------:|-----:|------:|
| build (build_graph) s | 3.019 | 0.264 | 0.376 |
| dataflow index s | 3.421 | 0.136 | 0.191 |
| transitive closure s | 3.023 | 0.048 | 0.049 |
| closure rows | 458,663 | 13,159 | 11,010 |
| peak WAL bytes | 108,969,912 | 2,962,312 | 2,537,952 |
| embed (hash) s | 1.207 | 0.072 | 0.169 |
| symbols / edges | 10,819 / 66,904 | 1,090 / 4,112 | 1,722 / 6,239 |
| files indexed | 654 | 24 | 50 |
| daemon boot s | 1.863 | 0.577 | 0.567 |
| daemon RSS peak KiB | 205,296 | 91,280 | 102,016 |
| daemon RSS final KiB | 158,032 | 91,280 | 102,016 |

Live self-store reference (`cairn config --db`,
`/Users/tanle/.cairn/acc48fd52e4c6e8d/.kg`, 176.5 MB): 10,357 symbols /
67,305 edges / 409,368 transitive edges — same order of magnitude as the
fresh scratch build above (the live store was built from an earlier commit).

## Corpora

- **cairn self** — `/Users/tanle/Projects/cairn` @ git `6e099068b771637db6c73ca5e083f62c1f310d95`
  (75 dirty entries in the worktree at measure time; the harness script and
  this doc are part of the dirt). 17,011 `.py` files / 7.3 M lines in the raw
  repo walk (the reused `corpus_stats` helper counts `.venv`; the scanner
  indexed 654 files).
- **yarl** — `benchmarks/datasource/t2/yarl`,
  tree_hash `b2ac9f50845b86bdc14388365490e714dad5cb57a0a4896e8879fc9e8745b974`,
  24 `.py` files / 9,848 lines.
- **attrs** — `benchmarks/datasource/ds2/second-corpus/attrs-26.1.0`
  (attrs 26.1.0 sdist extract, sha256-pinned provenance in
  `provenance.json`), tree_hash
  `ad6eec778ba82da2ac4493676f990c6d155e7b0634900c36a54da0de2d515097`,
  50 `.py` files / 19,039 lines.

## Per-tool latency (ms, 20 timed calls each, nearest-rank)

| tool | self p50 | self p95 | yarl p50 | yarl p95 | attrs p50 | attrs p95 |
|------|---------:|---------:|---------:|---------:|----------:|----------:|
| `find_definition` | 342.9 | 375.9 | 6.22 | 6.83 | 12.62 | 13.51 |
| `get_callers` | 328.5 | 339.7 | 8.27 | 8.75 | 14.81 | 16.05 |
| `get_callees` | 326.2 | 330.2 | 8.06 | 8.35 | 15.02 | 15.81 |
| `impact_analysis` | 343.6 | 349.3 | 11.13 | 11.57 | 17.27 | 19.43 |
| `path` | 337.5 | 343.1 | 8.12 | 8.58 | 14.47 | 15.69 |
| `explore` | 2,595.1 | 2,693.3 | 17.59 | 19.20 | 32.03 | 33.55 |
| `semantic_search` | 370.1 | 393.0 | 12.70 | 13.11 | 20.06 | 21.36 |
| `search_symbols` | 329.9 | 334.2 | 9.44 | 9.79 | 14.79 | 15.69 |
| `repo_map` | 381.7 | 404.0 | 10.84 | 11.18 | 19.13 | 19.96 |
| `file_api` | 323.8 | 337.2 | 6.73 | 7.87 | 12.01 | 12.35 |
| `cross_repo_deps` | 335.1 | 346.0 | 6.50 | 6.64 | 12.28 | 13.26 |
| `visualize_graph` | 323.7 | 326.1 | 6.21 | 6.51 | 11.40 | 12.04 |
| `get_compass` | 13.43 | 13.82 | 1.23 | 1.35 | 1.45 | 1.67 |
| `search_knowledge` | 509.2 | 514.6 | 1.22 | 1.38 | 1.45 | 1.79 |
| `ask_compass` | 1,026.9 | 1,037.2 | 2.82 | 2.94 | 3.16 | 3.38 |
| `trace_flow` | 1.27 | 1.48 | 1.33 | 1.67 | 1.56 | 2.00 |
| `generate_flow` | 1.83 | 2.02 | 1.85 | 1.98 | 4.55 | 4.98 |
| `federated_search` | 1.06 | 1.21 | 1.17 | 1.32 | 1.23 | 1.41 |
| `knowledge_search` | 13.53 | 14.06 | 1.24 | 1.41 | 1.46 | 1.58 |
| `trace_workflow` | 13.55 | 13.75 | 1.33 | 1.47 | 1.48 | 1.68 |
| `recall_memory` | 676.8 | 683.1 | 1.32 | 1.45 | 2.63 | 2.86 |
| `record_memory` | 536.6 | 555.8 | 15.84 | 29.82 | 16.48 | 32.07 |
| `wiki_generate` | 179.6 | 243.5 | 30.63 | 51.79 | 46.44 | 75.03 |
| `knowledge_add` | 2.12 | 2.27 | 2.14 | 2.31 | 2.13 | 2.34 |
| `knowledge_delete` | 6.91 | 7.11 | 6.96 | 7.93 | 7.01 | 7.41 |
| `knowledge_status` | 5.45 | 5.80 | 5.58 | 6.15 | 5.59 | 5.82 |

## Raw artifacts

### cairn self

```json
{
  "schema": "cairn-rust-baseline/1",
  "suite": "rust-migration-baseline",
  "impl": "python",
  "label": "cairn-self",
  "timestamp": "2026-10-18T15:52:56.474284+00:00",
  "harness": "scripts/rust_migration/baseline.py",
  "reproduce": ".venv/bin/python scripts/rust_migration/baseline.py --workspace /Users/tanle/Projects/cairn --label cairn-self --repeats 20",
  "dataset": {
    "name": "benchmark-datasource",
    "version": "DS-v1",
    "tree_hash": "65e3df398b96f2cd31230ee17765fc01b0bc603e4e27914f3c31f41f061f87bb",
    "identity_size": 300
  },
  "cairn_version": "0.22.0",
  "machine_profile": {
    "arch": "arm64",
    "cpu": "arm",
    "cpu_count": 10,
    "os": "macOS-27.0.1-arm64-arm-64bit",
    "runner_class": "reference-local"
  },
  "method": {
    "build": "build_graph(workspace, db) + build_dataflow_index (the `cairn build` shape)",
    "closure": "build_transitive_closure(conn) timed; WAL sidecar polled every 0.005s, with the post-commit sidecar size as the single-burst floor for the peak",
    "latency": "per-tool tools/call over streamable HTTP (keep-alive), 20 timed calls each after 1 untimed warmup; write tools re-create their fixture untimed per call",
    "rss": "ps -o rss= on the daemon pid every 0.2s",
    "env_pins": {
      "CAIRN_EMBED_BACKEND": "hash",
      "CAIRN_RERANK": "0"
    },
    "daemon": {
      "transport": "streamable-http",
      "stateless": false,
      "host": "127.0.0.1"
    },
    "knowledge_bundle_seeded_files": 487
  },
  "corpus": {
    "workspace": "/Users/tanle/Projects/cairn",
    "identity_method": "git_commit",
    "git_commit": "6e099068b771637db6c73ca5e083f62c1f310d95",
    "git_dirty_entries": 75,
    "py_files": 17011,
    "py_lines": 7308357,
    "py_bytes": 271565946
  },
  "build": {
    "seconds": 3.019,
    "dataflow_seconds": 3.421,
    "dataflow_symbols": 2000,
    "repos": 1,
    "files": 654,
    "symbols": 10819,
    "edges": 66904,
    "imports": 8325,
    "skipped": 241,
    "resolution": {
      "exact": 22414,
      "ambiguous": 4890,
      "unresolved": 29466
    },
    "lsp_upgraded": 0,
    "scip_edges": null,
    "parse_errors": 0
  },
  "closure": {
    "seconds": 3.023,
    "rows": 458663,
    "peak_wal_bytes": 108969912,
    "sampled_peak_wal_bytes": 108969912,
    "wal_end_bytes": 108969912,
    "db_bytes": 152838144
  },
  "embed": {
    "seconds": 1.207,
    "backend_effective": "hash",
    "embedded": 10819,
    "total": 10819
  },
  "daemon": {
    "boot_seconds": 1.863,
    "rss": {
      "peak_kb": 205296,
      "final_kb": 158032
    }
  },
  "tools": [
    {
      "tool": "find_definition",
      "args": {
        "name": "__init__",
        "limit": 5
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 342.881,
      "p95_ms": 375.88,
      "mean_ms": 348.368,
      "min_ms": 323.128,
      "max_ms": 380.301,
      "samples_ms": [
        366.469,
        369.295,
        365.168,
        363.713,
        375.88,
        380.301,
        364.054,
        366.572,
        362.038,
        358.879,
        342.881,
        333.61,
        324.504,
        327.031,
        323.128,
        330.687,
        329.367,
        324.957,
        332.528,
        326.301
      ]
    },
    {
      "tool": "get_callers",
      "args": {
        "name": "__init__",
        "limit": 50
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 328.502,
      "p95_ms": 339.684,
      "mean_ms": 330.851,
      "min_ms": 324.321,
      "max_ms": 342.914,
      "samples_ms": [
        330.778,
        328.073,
        327.972,
        326.685,
        328.502,
        329.569,
        332.806,
        334.469,
        328.082,
        324.321,
        331.761,
        336.92,
        339.684,
        328.757,
        335.311,
        327.618,
        326.906,
        342.914,
        327.61,
        328.277
      ]
    },
    {
      "tool": "get_callees",
      "args": {
        "name": "__init__",
        "limit": 50
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 326.246,
      "p95_ms": 330.164,
      "mean_ms": 326.558,
      "min_ms": 324.398,
      "max_ms": 331.461,
      "samples_ms": [
        325.571,
        326.508,
        326.054,
        325.169,
        326.612,
        327.152,
        324.512,
        325.809,
        330.164,
        325.346,
        326.398,
        326.938,
        324.398,
        325.7,
        324.451,
        327.026,
        327.725,
        326.246,
        331.461,
        327.923
      ]
    },
    {
      "tool": "impact_analysis",
      "args": {
        "name": "str",
        "depth": 3,
        "limit": 100
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 343.581,
      "p95_ms": 349.275,
      "mean_ms": 344.438,
      "min_ms": 340.091,
      "max_ms": 355.682,
      "samples_ms": [
        341.014,
        348.199,
        341.614,
        343.581,
        343.585,
        344.079,
        344.424,
        355.682,
        345.667,
        340.091,
        344.097,
        349.275,
        345.896,
        345.258,
        343.012,
        342.211,
        342.234,
        342.946,
        342.961,
        342.937
      ]
    },
    {
      "tool": "path",
      "args": {
        "from_pattern": "codeLens",
        "to_pattern": "LENS_REQUEST_DEPTH",
        "max_depth": 4
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 337.535,
      "p95_ms": 343.12,
      "mean_ms": 338.026,
      "min_ms": 335.527,
      "max_ms": 346.522,
      "samples_ms": [
        338.167,
        337.011,
        337.647,
        335.972,
        338.708,
        336.728,
        338.323,
        336.853,
        337.294,
        343.12,
        336.293,
        337.535,
        335.527,
        338.254,
        338.919,
        346.522,
        337.609,
        335.89,
        336.22,
        337.931
      ]
    },
    {
      "tool": "explore",
      "args": {
        "query": "__init__"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 2595.107,
      "p95_ms": 2693.338,
      "mean_ms": 2605.88,
      "min_ms": 2575.284,
      "max_ms": 2713.774,
      "samples_ms": [
        2598.627,
        2581.501,
        2593.648,
        2580.115,
        2600.975,
        2585.32,
        2713.774,
        2597.195,
        2593.557,
        2602.667,
        2582.793,
        2590.053,
        2575.284,
        2693.338,
        2651.248,
        2597.344,
        2581.71,
        2595.107,
        2607.101,
        2596.244
      ]
    },
    {
      "tool": "semantic_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 10
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 370.06,
      "p95_ms": 392.971,
      "mean_ms": 373.653,
      "min_ms": 359.392,
      "max_ms": 405.798,
      "samples_ms": [
        361.142,
        376.177,
        405.798,
        392.341,
        384.779,
        381.172,
        382.053,
        392.971,
        372.006,
        364.006,
        370.06,
        382.063,
        362.665,
        361.727,
        362.244,
        378.539,
        359.392,
        360.72,
        361.767,
        361.439
      ]
    },
    {
      "tool": "search_symbols",
      "args": {
        "pattern": "__in*"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 329.882,
      "p95_ms": 334.174,
      "mean_ms": 330.877,
      "min_ms": 328.436,
      "max_ms": 341.157,
      "samples_ms": [
        328.766,
        341.157,
        330.519,
        331.676,
        334.174,
        333.986,
        333.734,
        330.065,
        329.266,
        328.436,
        331.168,
        329.184,
        328.671,
        329.882,
        328.807,
        329.1,
        328.801,
        329.888,
        330.739,
        329.521
      ]
    },
    {
      "tool": "repo_map",
      "args": {},
      "errors": [],
      "calls": 20,
      "p50_ms": 381.745,
      "p95_ms": 404.026,
      "mean_ms": 384.415,
      "min_ms": 369.849,
      "max_ms": 416.461,
      "samples_ms": [
        385.589,
        369.849,
        373.445,
        385.458,
        371.935,
        372.299,
        385.061,
        381.745,
        404.026,
        402.543,
        416.461,
        392.158,
        389.065,
        403.463,
        379.395,
        372.556,
        383.873,
        375.182,
        372.541,
        371.665
      ]
    },
    {
      "tool": "file_api",
      "args": {
        "path": ".github/scripts/bench_compare.py",
        "repo": "cairn"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 323.772,
      "p95_ms": 337.202,
      "mean_ms": 324.815,
      "min_ms": 321.344,
      "max_ms": 337.74,
      "samples_ms": [
        323.046,
        337.202,
        324.601,
        322.105,
        324.084,
        322.849,
        322.823,
        321.344,
        321.888,
        324.875,
        323.017,
        323.814,
        323.055,
        324.148,
        323.772,
        325.418,
        323.999,
        324.216,
        322.306,
        337.74
      ]
    },
    {
      "tool": "cross_repo_deps",
      "args": {
        "repo": "cairn"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 335.067,
      "p95_ms": 345.985,
      "mean_ms": 336.453,
      "min_ms": 332.191,
      "max_ms": 346.424,
      "samples_ms": [
        337.728,
        346.424,
        334.069,
        333.646,
        333.962,
        336.124,
        333.736,
        338.644,
        335.154,
        334.679,
        345.985,
        334.538,
        332.191,
        338.872,
        335.18,
        337.976,
        335.743,
        335.067,
        335.002,
        334.35
      ]
    },
    {
      "tool": "visualize_graph",
      "args": {
        "scope": "symbol",
        "symbol": "str",
        "depth": 2
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 323.713,
      "p95_ms": 326.092,
      "mean_ms": 324.053,
      "min_ms": 320.958,
      "max_ms": 327.909,
      "samples_ms": [
        322.964,
        323.632,
        323.085,
        324.48,
        326.092,
        324.365,
        323.713,
        324.118,
        322.446,
        324.153,
        323.278,
        323.021,
        320.958,
        323.012,
        325.822,
        323.341,
        327.909,
        325.258,
        324.858,
        324.555
      ]
    },
    {
      "tool": "get_compass",
      "args": {
        "module": "graph"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 13.427,
      "p95_ms": 13.824,
      "mean_ms": 13.499,
      "min_ms": 13.345,
      "max_ms": 14.029,
      "samples_ms": [
        13.445,
        13.367,
        13.701,
        13.401,
        13.44,
        13.824,
        14.029,
        13.481,
        13.438,
        13.451,
        13.413,
        13.771,
        13.375,
        13.415,
        13.398,
        13.427,
        13.407,
        13.4,
        13.345,
        13.456
      ]
    },
    {
      "tool": "search_knowledge",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 10
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 509.17,
      "p95_ms": 514.639,
      "mean_ms": 510.162,
      "min_ms": 507.32,
      "max_ms": 520.534,
      "samples_ms": [
        509.686,
        510.463,
        507.32,
        508.098,
        511.284,
        508.157,
        508.819,
        509.26,
        507.502,
        510.535,
        509.252,
        508.767,
        509.17,
        508.647,
        513.196,
        514.639,
        520.534,
        508.669,
        508.559,
        510.677
      ]
    },
    {
      "tool": "ask_compass",
      "args": {
        "query": "how does the build resolve call edges"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1026.869,
      "p95_ms": 1037.202,
      "mean_ms": 1029.037,
      "min_ms": 1021.542,
      "max_ms": 1043.414,
      "samples_ms": [
        1026.306,
        1031.479,
        1030.259,
        1026.869,
        1027.353,
        1031.768,
        1030.346,
        1026.308,
        1037.202,
        1024.194,
        1024.972,
        1025.373,
        1043.414,
        1021.542,
        1024.475,
        1029.509,
        1035.923,
        1025.446,
        1026.704,
        1031.298
      ]
    },
    {
      "tool": "trace_flow",
      "args": {
        "entry": "str"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.272,
      "p95_ms": 1.479,
      "mean_ms": 1.315,
      "min_ms": 1.22,
      "max_ms": 1.481,
      "samples_ms": [
        1.481,
        1.479,
        1.33,
        1.324,
        1.259,
        1.432,
        1.317,
        1.266,
        1.296,
        1.401,
        1.258,
        1.255,
        1.242,
        1.399,
        1.233,
        1.272,
        1.234,
        1.384,
        1.226,
        1.22
      ]
    },
    {
      "tool": "generate_flow",
      "args": {
        "entry": "str"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.832,
      "p95_ms": 2.024,
      "mean_ms": 1.876,
      "min_ms": 1.759,
      "max_ms": 2.035,
      "samples_ms": [
        2.035,
        1.878,
        1.886,
        2.016,
        1.823,
        1.813,
        1.964,
        1.832,
        1.82,
        1.816,
        2.024,
        1.854,
        1.82,
        1.8,
        1.94,
        1.781,
        1.772,
        1.759,
        2.013,
        1.867
      ]
    },
    {
      "tool": "federated_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 20
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.065,
      "p95_ms": 1.21,
      "mean_ms": 1.101,
      "min_ms": 1.036,
      "max_ms": 1.286,
      "samples_ms": [
        1.111,
        1.286,
        1.149,
        1.101,
        1.065,
        1.052,
        1.21,
        1.111,
        1.077,
        1.078,
        1.059,
        1.057,
        1.209,
        1.059,
        1.05,
        1.046,
        1.036,
        1.173,
        1.047,
        1.043
      ]
    },
    {
      "tool": "knowledge_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 20
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 13.533,
      "p95_ms": 14.065,
      "mean_ms": 13.652,
      "min_ms": 13.361,
      "max_ms": 15.046,
      "samples_ms": [
        13.631,
        13.941,
        13.756,
        13.823,
        13.633,
        13.52,
        13.56,
        13.558,
        13.455,
        13.391,
        13.478,
        13.52,
        13.414,
        13.361,
        13.383,
        13.571,
        15.046,
        14.065,
        13.533,
        13.393
      ]
    },
    {
      "tool": "trace_workflow",
      "args": {
        "ref": "nonexistent-baseline-probe"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 13.55,
      "p95_ms": 13.751,
      "mean_ms": 13.599,
      "min_ms": 13.438,
      "max_ms": 14.242,
      "samples_ms": [
        13.576,
        13.55,
        13.643,
        13.649,
        13.504,
        13.569,
        13.477,
        13.751,
        13.472,
        13.574,
        13.522,
        14.242,
        13.699,
        13.541,
        13.496,
        13.741,
        13.438,
        13.461,
        13.488,
        13.583
      ]
    },
    {
      "tool": "recall_memory",
      "args": {
        "query": "str"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 676.797,
      "p95_ms": 683.087,
      "mean_ms": 677.895,
      "min_ms": 673.52,
      "max_ms": 685.696,
      "samples_ms": [
        675.138,
        679.696,
        675.747,
        676.729,
        673.52,
        676.007,
        680.445,
        677.031,
        674.773,
        675.392,
        676.454,
        683.087,
        678.206,
        682.725,
        676.797,
        678.905,
        678.516,
        685.696,
        679.258,
        673.773
      ]
    },
    {
      "tool": "record_memory",
      "args": {
        "type": "pattern",
        "title": "baseline probe cairn rec 20",
        "body": "rust-migration baseline probe memory"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 536.614,
      "p95_ms": 555.755,
      "mean_ms": 539.68,
      "min_ms": 528.798,
      "max_ms": 570.334,
      "samples_ms": [
        531.346,
        530.031,
        539.939,
        531.619,
        534.906,
        533.209,
        528.798,
        536.614,
        531.502,
        535.674,
        534.94,
        541.733,
        540.161,
        541.338,
        543.501,
        545.054,
        542.205,
        544.941,
        570.334,
        555.755
      ]
    },
    {
      "tool": "wiki_generate",
      "args": {
        "repo": "cairn",
        "pages": 5,
        "force": true
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 179.564,
      "p95_ms": 243.486,
      "mean_ms": 183.416,
      "min_ms": 115.093,
      "max_ms": 251.108,
      "samples_ms": [
        115.093,
        123.129,
        129.283,
        137.62,
        142.825,
        151.854,
        158.421,
        165.254,
        172.775,
        179.564,
        186.899,
        194.091,
        201.645,
        208.432,
        215.331,
        221.429,
        231.642,
        238.436,
        243.486,
        251.108
      ]
    },
    {
      "tool": "knowledge_add",
      "args": {
        "title": "baseline probe cairn add 20",
        "body": "rust-migration baseline probe document",
        "doc_type": "decision"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 2.115,
      "p95_ms": 2.265,
      "mean_ms": 2.139,
      "min_ms": 2.032,
      "max_ms": 2.328,
      "samples_ms": [
        2.264,
        2.115,
        2.328,
        2.11,
        2.174,
        2.16,
        2.127,
        2.238,
        2.13,
        2.091,
        2.074,
        2.215,
        2.137,
        2.059,
        2.054,
        2.037,
        2.265,
        2.095,
        2.079,
        2.032
      ]
    },
    {
      "tool": "knowledge_delete",
      "args": {},
      "errors": [],
      "calls": 20,
      "p50_ms": 6.908,
      "p95_ms": 7.112,
      "mean_ms": 6.897,
      "min_ms": 6.672,
      "max_ms": 7.166,
      "samples_ms": [
        6.996,
        7.112,
        6.828,
        7.012,
        6.979,
        6.962,
        6.812,
        6.927,
        6.727,
        7.006,
        6.883,
        7.166,
        6.772,
        6.923,
        6.743,
        6.871,
        6.703,
        6.929,
        6.672,
        6.908
      ]
    },
    {
      "tool": "knowledge_status",
      "args": {
        "new_status": "superseded"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 5.451,
      "p95_ms": 5.801,
      "mean_ms": 5.536,
      "min_ms": 5.289,
      "max_ms": 6.987,
      "samples_ms": [
        5.647,
        6.987,
        5.728,
        5.338,
        5.403,
        5.527,
        5.344,
        5.451,
        5.306,
        5.51,
        5.398,
        5.458,
        5.383,
        5.453,
        5.289,
        5.518,
        5.336,
        5.801,
        5.328,
        5.518
      ]
    }
  ],
  "live_store_reference": {
    "db_path": "/Users/tanle/.cairn/acc48fd52e4c6e8d/.kg",
    "db_bytes": 176500736,
    "symbols": 10357,
    "edges": 67305,
    "transitive_edges": 409368
  }
}
```

### yarl

```json
{
  "schema": "cairn-rust-baseline/1",
  "suite": "rust-migration-baseline",
  "impl": "python",
  "label": "yarl",
  "timestamp": "2026-10-18T15:54:24.623881+00:00",
  "harness": "scripts/rust_migration/baseline.py",
  "reproduce": ".venv/bin/python scripts/rust_migration/baseline.py --workspace /Users/tanle/Projects/cairn/benchmarks/datasource/t2/yarl --label yarl --repeats 20",
  "dataset": {
    "name": "benchmark-datasource",
    "version": "DS-v1",
    "tree_hash": "65e3df398b96f2cd31230ee17765fc01b0bc603e4e27914f3c31f41f061f87bb",
    "identity_size": 300
  },
  "cairn_version": "0.22.0",
  "machine_profile": {
    "arch": "arm64",
    "cpu": "arm",
    "cpu_count": 10,
    "os": "macOS-27.0.1-arm64-arm-64bit",
    "runner_class": "reference-local"
  },
  "method": {
    "build": "build_graph(workspace, db) + build_dataflow_index (the `cairn build` shape)",
    "closure": "build_transitive_closure(conn) timed; WAL sidecar polled every 0.005s, with the post-commit sidecar size as the single-burst floor for the peak",
    "latency": "per-tool tools/call over streamable HTTP (keep-alive), 20 timed calls each after 1 untimed warmup; write tools re-create their fixture untimed per call",
    "rss": "ps -o rss= on the daemon pid every 0.2s",
    "env_pins": {
      "CAIRN_EMBED_BACKEND": "hash",
      "CAIRN_RERANK": "0"
    },
    "daemon": {
      "transport": "streamable-http",
      "stateless": false,
      "host": "127.0.0.1"
    },
    "knowledge_bundle_seeded_files": 0
  },
  "corpus": {
    "workspace": "/Users/tanle/Projects/cairn/benchmarks/datasource/t2/yarl",
    "identity_method": "tree_hash",
    "tree_hash": "b2ac9f50845b86bdc14388365490e714dad5cb57a0a4896e8879fc9e8745b974",
    "py_files": 24,
    "py_lines": 9848,
    "py_bytes": 311104
  },
  "build": {
    "seconds": 0.264,
    "dataflow_seconds": 0.136,
    "dataflow_symbols": 899,
    "repos": 1,
    "files": 24,
    "symbols": 1090,
    "edges": 4112,
    "imports": 181,
    "skipped": 0,
    "resolution": {
      "exact": 1388,
      "ambiguous": 213,
      "unresolved": 1445
    },
    "lsp_upgraded": 0,
    "scip_edges": null,
    "parse_errors": 0
  },
  "closure": {
    "seconds": 0.048,
    "rows": 13159,
    "peak_wal_bytes": 2962312,
    "sampled_peak_wal_bytes": 1021792,
    "wal_end_bytes": 2962312,
    "db_bytes": 5685248
  },
  "embed": {
    "seconds": 0.072,
    "backend_effective": "hash",
    "embedded": 1090,
    "total": 1090
  },
  "daemon": {
    "boot_seconds": 0.577,
    "rss": {
      "peak_kb": 91280,
      "final_kb": 91280
    }
  },
  "tools": [
    {
      "tool": "find_definition",
      "args": {
        "name": "test_from_ascii_query_parts",
        "limit": 5
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 6.225,
      "p95_ms": 6.835,
      "mean_ms": 6.301,
      "min_ms": 6.033,
      "max_ms": 6.899,
      "samples_ms": [
        6.899,
        6.552,
        6.328,
        6.439,
        6.298,
        6.225,
        6.258,
        6.249,
        6.306,
        6.176,
        6.13,
        6.094,
        6.25,
        6.033,
        6.211,
        6.166,
        6.835,
        6.218,
        6.183,
        6.173
      ]
    },
    {
      "tool": "get_callers",
      "args": {
        "name": "test_from_ascii_query_parts",
        "limit": 50
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 8.27,
      "p95_ms": 8.752,
      "mean_ms": 8.332,
      "min_ms": 8.142,
      "max_ms": 8.941,
      "samples_ms": [
        8.291,
        8.941,
        8.312,
        8.335,
        8.142,
        8.144,
        8.194,
        8.752,
        8.452,
        8.308,
        8.217,
        8.568,
        8.145,
        8.228,
        8.308,
        8.27,
        8.497,
        8.167,
        8.177,
        8.192
      ]
    },
    {
      "tool": "get_callees",
      "args": {
        "name": "test_from_ascii_query_parts",
        "limit": 50
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 8.063,
      "p95_ms": 8.353,
      "mean_ms": 8.099,
      "min_ms": 7.674,
      "max_ms": 8.602,
      "samples_ms": [
        8.188,
        8.082,
        8.034,
        8.328,
        8.063,
        8.015,
        8.012,
        8.35,
        7.997,
        8.087,
        8.095,
        8.027,
        8.353,
        8.602,
        8.109,
        8.042,
        8.02,
        8.163,
        7.674,
        7.729
      ]
    },
    {
      "tool": "impact_analysis",
      "args": {
        "name": "str",
        "depth": 3,
        "limit": 100
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 11.126,
      "p95_ms": 11.567,
      "mean_ms": 11.155,
      "min_ms": 10.632,
      "max_ms": 12.281,
      "samples_ms": [
        11.261,
        11.484,
        11.126,
        11.157,
        10.894,
        10.959,
        11.004,
        11.205,
        10.877,
        11.175,
        10.632,
        12.281,
        11.221,
        11.19,
        11.006,
        11.147,
        10.78,
        11.567,
        11.067,
        11.065
      ]
    },
    {
      "tool": "path",
      "args": {
        "from_pattern": "test_cache",
        "to_pattern": "teardown_module",
        "max_depth": 4
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 8.115,
      "p95_ms": 8.581,
      "mean_ms": 8.199,
      "min_ms": 7.929,
      "max_ms": 8.661,
      "samples_ms": [
        7.988,
        7.929,
        8.209,
        8.661,
        8.239,
        8.09,
        8.489,
        8.247,
        8.354,
        8.268,
        8.157,
        8.491,
        8.115,
        8.581,
        8.08,
        8.026,
        8.007,
        8.012,
        7.959,
        8.072
      ]
    },
    {
      "tool": "explore",
      "args": {
        "query": "test_from_ascii_query_parts"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 17.589,
      "p95_ms": 19.203,
      "mean_ms": 18.243,
      "min_ms": 17.135,
      "max_ms": 30.058,
      "samples_ms": [
        18.151,
        17.756,
        17.589,
        17.708,
        17.64,
        30.058,
        17.993,
        17.474,
        17.62,
        17.864,
        17.443,
        17.221,
        17.329,
        17.135,
        17.298,
        17.361,
        17.232,
        17.154,
        19.203,
        17.628
      ]
    },
    {
      "tool": "semantic_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 10
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 12.704,
      "p95_ms": 13.114,
      "mean_ms": 12.8,
      "min_ms": 12.576,
      "max_ms": 14.084,
      "samples_ms": [
        12.712,
        12.674,
        12.708,
        12.644,
        12.576,
        12.759,
        12.645,
        12.655,
        12.633,
        12.641,
        12.737,
        12.912,
        12.676,
        14.084,
        13.114,
        12.779,
        12.913,
        12.72,
        12.704,
        12.704
      ]
    },
    {
      "tool": "search_symbols",
      "args": {
        "pattern": "test*"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 9.439,
      "p95_ms": 9.785,
      "mean_ms": 9.491,
      "min_ms": 9.311,
      "max_ms": 9.969,
      "samples_ms": [
        9.432,
        9.501,
        9.518,
        9.337,
        9.489,
        9.969,
        9.543,
        9.49,
        9.394,
        9.311,
        9.536,
        9.37,
        9.409,
        9.62,
        9.785,
        9.43,
        9.346,
        9.479,
        9.421,
        9.439
      ]
    },
    {
      "tool": "repo_map",
      "args": {},
      "errors": [],
      "calls": 20,
      "p50_ms": 10.836,
      "p95_ms": 11.182,
      "mean_ms": 10.947,
      "min_ms": 10.716,
      "max_ms": 12.416,
      "samples_ms": [
        11.182,
        10.939,
        10.78,
        10.944,
        11.013,
        10.897,
        10.916,
        10.833,
        10.774,
        10.906,
        10.838,
        10.828,
        12.416,
        10.989,
        10.836,
        10.716,
        10.77,
        10.797,
        10.76,
        10.799
      ]
    },
    {
      "tool": "file_api",
      "args": {
        "path": "tests/test_cache.py",
        "repo": "corpus"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 6.728,
      "p95_ms": 7.872,
      "mean_ms": 6.937,
      "min_ms": 6.556,
      "max_ms": 8.152,
      "samples_ms": [
        6.626,
        6.556,
        7.471,
        6.746,
        6.78,
        6.605,
        6.896,
        6.711,
        6.692,
        6.706,
        6.775,
        6.731,
        6.693,
        6.728,
        6.65,
        6.689,
        8.152,
        7.15,
        7.872,
        7.507
      ]
    },
    {
      "tool": "cross_repo_deps",
      "args": {
        "repo": "corpus"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 6.504,
      "p95_ms": 6.642,
      "mean_ms": 6.51,
      "min_ms": 6.381,
      "max_ms": 6.745,
      "samples_ms": [
        6.52,
        6.444,
        6.745,
        6.532,
        6.47,
        6.571,
        6.584,
        6.426,
        6.485,
        6.642,
        6.509,
        6.432,
        6.381,
        6.561,
        6.44,
        6.428,
        6.474,
        6.507,
        6.544,
        6.504
      ]
    },
    {
      "tool": "visualize_graph",
      "args": {
        "scope": "symbol",
        "symbol": "str",
        "depth": 2
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 6.212,
      "p95_ms": 6.511,
      "mean_ms": 6.267,
      "min_ms": 6.042,
      "max_ms": 6.54,
      "samples_ms": [
        6.375,
        6.212,
        6.186,
        6.164,
        6.401,
        6.042,
        6.153,
        6.54,
        6.361,
        6.401,
        6.118,
        6.16,
        6.176,
        6.213,
        6.486,
        6.191,
        6.196,
        6.23,
        6.234,
        6.511
      ]
    },
    {
      "tool": "get_compass",
      "args": {
        "module": "graph"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.226,
      "p95_ms": 1.354,
      "mean_ms": 1.24,
      "min_ms": 1.171,
      "max_ms": 1.358,
      "samples_ms": [
        1.23,
        1.257,
        1.226,
        1.358,
        1.234,
        1.234,
        1.231,
        1.195,
        1.354,
        1.228,
        1.202,
        1.216,
        1.194,
        1.354,
        1.207,
        1.185,
        1.205,
        1.204,
        1.314,
        1.171
      ]
    },
    {
      "tool": "search_knowledge",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 10
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.215,
      "p95_ms": 1.375,
      "mean_ms": 1.244,
      "min_ms": 1.193,
      "max_ms": 1.378,
      "samples_ms": [
        1.212,
        1.193,
        1.359,
        1.212,
        1.203,
        1.234,
        1.211,
        1.375,
        1.221,
        1.207,
        1.213,
        1.229,
        1.342,
        1.208,
        1.232,
        1.225,
        1.194,
        1.217,
        1.378,
        1.215
      ]
    },
    {
      "tool": "ask_compass",
      "args": {
        "query": "how does the build resolve call edges"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 2.824,
      "p95_ms": 2.944,
      "mean_ms": 2.847,
      "min_ms": 2.772,
      "max_ms": 2.988,
      "samples_ms": [
        2.858,
        2.815,
        2.824,
        2.804,
        2.801,
        2.988,
        2.803,
        2.804,
        2.773,
        2.896,
        2.788,
        2.867,
        2.898,
        2.851,
        2.869,
        2.944,
        2.852,
        2.8,
        2.772,
        2.933
      ]
    },
    {
      "tool": "trace_flow",
      "args": {
        "entry": "str"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.332,
      "p95_ms": 1.673,
      "mean_ms": 1.416,
      "min_ms": 1.274,
      "max_ms": 2.085,
      "samples_ms": [
        1.367,
        1.34,
        1.409,
        1.332,
        1.295,
        1.274,
        1.453,
        1.306,
        1.304,
        1.282,
        1.424,
        1.286,
        1.296,
        2.085,
        1.673,
        1.655,
        1.369,
        1.326,
        1.317,
        1.517
      ]
    },
    {
      "tool": "generate_flow",
      "args": {
        "entry": "str"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.846,
      "p95_ms": 1.979,
      "mean_ms": 1.87,
      "min_ms": 1.784,
      "max_ms": 1.99,
      "samples_ms": [
        1.855,
        1.867,
        1.979,
        1.847,
        1.785,
        1.784,
        1.975,
        1.844,
        1.84,
        1.833,
        1.978,
        1.851,
        1.826,
        1.842,
        1.96,
        1.819,
        1.827,
        1.846,
        1.99,
        1.86
      ]
    },
    {
      "tool": "federated_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 20
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.166,
      "p95_ms": 1.323,
      "mean_ms": 1.185,
      "min_ms": 1.085,
      "max_ms": 1.356,
      "samples_ms": [
        1.211,
        1.356,
        1.195,
        1.183,
        1.184,
        1.136,
        1.323,
        1.166,
        1.136,
        1.189,
        1.146,
        1.131,
        1.31,
        1.113,
        1.085,
        1.126,
        1.173,
        1.251,
        1.159,
        1.137
      ]
    },
    {
      "tool": "knowledge_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 20
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.241,
      "p95_ms": 1.406,
      "mean_ms": 1.259,
      "min_ms": 1.171,
      "max_ms": 1.406,
      "samples_ms": [
        1.376,
        1.275,
        1.241,
        1.218,
        1.326,
        1.187,
        1.18,
        1.189,
        1.171,
        1.211,
        1.406,
        1.235,
        1.208,
        1.24,
        1.249,
        1.406,
        1.26,
        1.271,
        1.262,
        1.276
      ]
    },
    {
      "tool": "trace_workflow",
      "args": {
        "ref": "nonexistent-baseline-probe"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.335,
      "p95_ms": 1.465,
      "mean_ms": 1.36,
      "min_ms": 1.31,
      "max_ms": 1.499,
      "samples_ms": [
        1.351,
        1.31,
        1.31,
        1.356,
        1.446,
        1.346,
        1.316,
        1.361,
        1.328,
        1.499,
        1.327,
        1.348,
        1.318,
        1.35,
        1.455,
        1.324,
        1.319,
        1.329,
        1.335,
        1.465
      ]
    },
    {
      "tool": "recall_memory",
      "args": {
        "query": "str"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.323,
      "p95_ms": 1.454,
      "mean_ms": 1.343,
      "min_ms": 1.29,
      "max_ms": 1.482,
      "samples_ms": [
        1.305,
        1.32,
        1.454,
        1.306,
        1.335,
        1.327,
        1.3,
        1.482,
        1.303,
        1.304,
        1.29,
        1.342,
        1.433,
        1.332,
        1.345,
        1.323,
        1.298,
        1.428,
        1.314,
        1.328
      ]
    },
    {
      "tool": "record_memory",
      "args": {
        "type": "pattern",
        "title": "baseline probe corpus rec 20",
        "body": "rust-migration baseline probe memory"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 15.837,
      "p95_ms": 29.824,
      "mean_ms": 18.483,
      "min_ms": 8.174,
      "max_ms": 31.774,
      "samples_ms": [
        8.174,
        8.968,
        9.655,
        11.929,
        13.437,
        10.049,
        14.523,
        15.103,
        12.748,
        18.131,
        15.837,
        20.224,
        21.453,
        23.231,
        24.767,
        27.478,
        28.165,
        24.2,
        29.824,
        31.774
      ]
    },
    {
      "tool": "wiki_generate",
      "args": {
        "repo": "corpus",
        "pages": 5,
        "force": true
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 30.634,
      "p95_ms": 51.793,
      "mean_ms": 32.463,
      "min_ms": 10.479,
      "max_ms": 53.957,
      "samples_ms": [
        10.479,
        12.708,
        15.407,
        17.411,
        20.337,
        22.863,
        25.211,
        27.36,
        28.824,
        30.634,
        33.579,
        35.12,
        39.137,
        40.188,
        42.554,
        44.398,
        46.92,
        50.373,
        51.793,
        53.957
      ]
    },
    {
      "tool": "knowledge_add",
      "args": {
        "title": "baseline probe corpus add 20",
        "body": "rust-migration baseline probe document",
        "doc_type": "decision"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 2.143,
      "p95_ms": 2.314,
      "mean_ms": 2.178,
      "min_ms": 2.087,
      "max_ms": 2.365,
      "samples_ms": [
        2.137,
        2.232,
        2.141,
        2.263,
        2.177,
        2.138,
        2.184,
        2.116,
        2.149,
        2.365,
        2.117,
        2.108,
        2.195,
        2.143,
        2.294,
        2.139,
        2.102,
        2.087,
        2.15,
        2.314
      ]
    },
    {
      "tool": "knowledge_delete",
      "args": {},
      "errors": [],
      "calls": 20,
      "p50_ms": 6.962,
      "p95_ms": 7.934,
      "mean_ms": 7.05,
      "min_ms": 6.733,
      "max_ms": 8.465,
      "samples_ms": [
        7.089,
        6.832,
        8.465,
        7.934,
        7.308,
        6.803,
        7.054,
        6.782,
        6.962,
        6.791,
        7.02,
        6.733,
        7.052,
        6.823,
        6.979,
        6.768,
        7.031,
        6.756,
        6.984,
        6.838
      ]
    },
    {
      "tool": "knowledge_status",
      "args": {
        "new_status": "superseded"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 5.58,
      "p95_ms": 6.15,
      "mean_ms": 5.641,
      "min_ms": 5.386,
      "max_ms": 6.747,
      "samples_ms": [
        5.467,
        5.593,
        5.386,
        5.571,
        5.421,
        5.666,
        5.443,
        5.589,
        5.458,
        5.615,
        5.463,
        5.588,
        5.438,
        5.58,
        5.411,
        6.747,
        6.15,
        5.725,
        5.667,
        5.846
      ]
    }
  ],
  "live_store_reference": {
    "db_path": "/Users/tanle/.cairn/acc48fd52e4c6e8d/.kg",
    "db_bytes": 176500736,
    "symbols": 10357,
    "edges": 67305,
    "transitive_edges": 409368
  }
}
```

### attrs

```json
{
  "schema": "cairn-rust-baseline/1",
  "suite": "rust-migration-baseline",
  "impl": "python",
  "label": "attrs",
  "timestamp": "2026-10-18T15:49:06.370519+00:00",
  "harness": "scripts/rust_migration/baseline.py",
  "reproduce": ".venv/bin/python scripts/rust_migration/baseline.py --workspace /Users/tanle/Projects/cairn/benchmarks/datasource/ds2/second-corpus/attrs-26.1.0 --label attrs --repeats 20",
  "dataset": {
    "name": "benchmark-datasource",
    "version": "DS-v1",
    "tree_hash": "65e3df398b96f2cd31230ee17765fc01b0bc603e4e27914f3c31f41f061f87bb",
    "identity_size": 300
  },
  "cairn_version": "0.22.0",
  "machine_profile": {
    "arch": "arm64",
    "cpu": "arm",
    "cpu_count": 10,
    "os": "macOS-27.0.1-arm64-arm-64bit",
    "runner_class": "reference-local"
  },
  "method": {
    "build": "build_graph(workspace, db) + build_dataflow_index (the `cairn build` shape)",
    "closure": "build_transitive_closure(conn) timed; WAL sidecar polled every 0.005s, with the post-commit sidecar size as the single-burst floor for the peak",
    "latency": "per-tool tools/call over streamable HTTP (keep-alive), 20 timed calls each after 1 untimed warmup; write tools re-create their fixture untimed per call",
    "rss": "ps -o rss= on the daemon pid every 0.2s",
    "env_pins": {
      "CAIRN_EMBED_BACKEND": "hash",
      "CAIRN_RERANK": "0"
    },
    "daemon": {
      "transport": "streamable-http",
      "stateless": false,
      "host": "127.0.0.1"
    },
    "knowledge_bundle_seeded_files": 0
  },
  "corpus": {
    "workspace": "/Users/tanle/Projects/cairn/benchmarks/datasource/ds2/second-corpus/attrs-26.1.0",
    "identity_method": "tree_hash",
    "tree_hash": "ad6eec778ba82da2ac4493676f990c6d155e7b0634900c36a54da0de2d515097",
    "py_files": 50,
    "py_lines": 19039,
    "py_bytes": 527216
  },
  "build": {
    "seconds": 0.376,
    "dataflow_seconds": 0.191,
    "dataflow_symbols": 894,
    "repos": 1,
    "files": 50,
    "symbols": 1722,
    "edges": 6239,
    "imports": 399,
    "skipped": 0,
    "resolution": {
      "exact": 1316,
      "ambiguous": 636,
      "unresolved": 2615
    },
    "lsp_upgraded": 0,
    "scip_edges": null,
    "parse_errors": 0
  },
  "closure": {
    "seconds": 0.049,
    "rows": 11010,
    "peak_wal_bytes": 2537952,
    "sampled_peak_wal_bytes": 980592,
    "wal_end_bytes": 2537952,
    "db_bytes": 6877184
  },
  "embed": {
    "seconds": 0.169,
    "backend_effective": "hash",
    "embedded": 1722,
    "total": 1722
  },
  "daemon": {
    "boot_seconds": 0.567,
    "rss": {
      "peak_kb": 102016,
      "final_kb": 102016
    }
  },
  "tools": [
    {
      "tool": "find_definition",
      "args": {
        "name": "B",
        "limit": 5
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 12.619,
      "p95_ms": 13.507,
      "mean_ms": 12.728,
      "min_ms": 11.92,
      "max_ms": 14.142,
      "samples_ms": [
        13.507,
        13.29,
        14.142,
        12.798,
        12.619,
        13.197,
        12.499,
        12.323,
        12.832,
        12.227,
        12.273,
        12.055,
        12.926,
        12.772,
        13.273,
        12.737,
        12.58,
        12.275,
        11.92,
        12.325
      ]
    },
    {
      "tool": "get_callers",
      "args": {
        "name": "B",
        "limit": 50
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 14.809,
      "p95_ms": 16.049,
      "mean_ms": 15.036,
      "min_ms": 14.201,
      "max_ms": 16.186,
      "samples_ms": [
        14.978,
        14.674,
        14.855,
        14.363,
        14.201,
        14.58,
        14.748,
        14.665,
        14.806,
        15.172,
        14.6,
        15.763,
        16.186,
        15.569,
        15.856,
        16.049,
        14.809,
        14.568,
        15.435,
        14.838
      ]
    },
    {
      "tool": "get_callees",
      "args": {
        "name": "B",
        "limit": 50
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 15.021,
      "p95_ms": 15.812,
      "mean_ms": 15.207,
      "min_ms": 14.53,
      "max_ms": 17.972,
      "samples_ms": [
        14.65,
        14.588,
        15.253,
        14.777,
        14.836,
        14.53,
        14.797,
        14.759,
        15.279,
        15.812,
        15.46,
        15.06,
        15.777,
        15.252,
        15.34,
        15.021,
        17.972,
        14.836,
        14.938,
        15.21
      ]
    },
    {
      "tool": "impact_analysis",
      "args": {
        "name": "ib",
        "depth": 3,
        "limit": 100
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 17.265,
      "p95_ms": 19.433,
      "mean_ms": 17.63,
      "min_ms": 16.672,
      "max_ms": 19.567,
      "samples_ms": [
        19.433,
        19.081,
        18.192,
        18.024,
        18.174,
        18.121,
        18.126,
        19.567,
        17.264,
        17.281,
        17.321,
        17.094,
        16.929,
        16.99,
        16.672,
        16.803,
        16.755,
        16.747,
        16.756,
        17.265
      ]
    },
    {
      "tool": "path",
      "args": {
        "from_pattern": "__init__",
        "to_pattern": "AttrsInstance",
        "max_depth": 4
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 14.466,
      "p95_ms": 15.688,
      "mean_ms": 15.359,
      "min_ms": 14.003,
      "max_ms": 28.775,
      "samples_ms": [
        14.297,
        14.29,
        14.171,
        14.116,
        14.063,
        14.003,
        14.606,
        15.688,
        15.382,
        14.673,
        15.116,
        15.541,
        15.444,
        28.775,
        14.376,
        14.726,
        14.34,
        14.68,
        14.437,
        14.466
      ]
    },
    {
      "tool": "explore",
      "args": {
        "query": "B"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 32.035,
      "p95_ms": 33.548,
      "mean_ms": 31.995,
      "min_ms": 30.216,
      "max_ms": 33.619,
      "samples_ms": [
        31.031,
        32.461,
        31.306,
        31.333,
        32.809,
        31.576,
        32.628,
        31.741,
        32.491,
        31.41,
        30.216,
        31.67,
        31.082,
        32.035,
        33.548,
        32.065,
        33.619,
        32.09,
        32.577,
        32.203
      ]
    },
    {
      "tool": "semantic_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 10
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 20.062,
      "p95_ms": 21.359,
      "mean_ms": 20.275,
      "min_ms": 19.388,
      "max_ms": 22.327,
      "samples_ms": [
        22.327,
        20.062,
        19.472,
        20.113,
        19.963,
        20.06,
        20.131,
        20.062,
        19.628,
        20.195,
        19.942,
        20.302,
        21.359,
        21.139,
        20.307,
        21.151,
        20.064,
        19.869,
        19.959,
        19.388
      ]
    },
    {
      "tool": "search_symbols",
      "args": {
        "pattern": "B*"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 14.787,
      "p95_ms": 15.692,
      "mean_ms": 14.874,
      "min_ms": 14.142,
      "max_ms": 16.324,
      "samples_ms": [
        14.572,
        16.324,
        14.528,
        14.521,
        15.054,
        14.787,
        14.714,
        14.865,
        14.359,
        14.559,
        14.8,
        14.771,
        14.348,
        14.142,
        15.346,
        15.692,
        15.244,
        14.818,
        15.213,
        14.819
      ]
    },
    {
      "tool": "repo_map",
      "args": {},
      "errors": [],
      "calls": 20,
      "p50_ms": 19.132,
      "p95_ms": 19.956,
      "mean_ms": 19.228,
      "min_ms": 18.726,
      "max_ms": 20.391,
      "samples_ms": [
        19.049,
        19.237,
        19.343,
        19.471,
        19.208,
        19.434,
        19.157,
        19.956,
        19.054,
        18.726,
        18.849,
        18.907,
        18.944,
        18.832,
        19.197,
        19.132,
        18.922,
        20.391,
        18.851,
        19.906
      ]
    },
    {
      "tool": "file_api",
      "args": {
        "path": "src/attr/__init__.py",
        "repo": "corpus"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 12.012,
      "p95_ms": 12.352,
      "mean_ms": 11.998,
      "min_ms": 11.432,
      "max_ms": 12.448,
      "samples_ms": [
        12.136,
        12.295,
        12.448,
        11.956,
        12.345,
        12.201,
        12.012,
        11.872,
        12.352,
        11.53,
        11.605,
        11.432,
        11.636,
        11.876,
        12.074,
        11.814,
        12.194,
        11.958,
        12.089,
        12.126
      ]
    },
    {
      "tool": "cross_repo_deps",
      "args": {
        "repo": "corpus"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 12.281,
      "p95_ms": 13.263,
      "mean_ms": 12.989,
      "min_ms": 11.871,
      "max_ms": 25.502,
      "samples_ms": [
        12.068,
        12.483,
        12.556,
        12.26,
        12.19,
        25.502,
        12.281,
        12.179,
        12.486,
        12.3,
        12.032,
        11.871,
        12.136,
        13.263,
        12.743,
        12.419,
        12.076,
        12.465,
        12.329,
        12.143
      ]
    },
    {
      "tool": "visualize_graph",
      "args": {
        "scope": "symbol",
        "symbol": "ib",
        "depth": 2
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 11.403,
      "p95_ms": 12.036,
      "mean_ms": 11.477,
      "min_ms": 11.042,
      "max_ms": 12.076,
      "samples_ms": [
        11.579,
        11.403,
        11.428,
        11.395,
        11.042,
        11.31,
        11.515,
        11.143,
        11.289,
        11.149,
        11.091,
        11.058,
        11.685,
        11.846,
        12.076,
        11.746,
        11.492,
        12.036,
        11.391,
        11.869
      ]
    },
    {
      "tool": "get_compass",
      "args": {
        "module": "graph"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.451,
      "p95_ms": 1.669,
      "mean_ms": 1.461,
      "min_ms": 1.309,
      "max_ms": 1.694,
      "samples_ms": [
        1.363,
        1.364,
        1.531,
        1.49,
        1.501,
        1.309,
        1.412,
        1.513,
        1.409,
        1.371,
        1.453,
        1.4,
        1.514,
        1.364,
        1.669,
        1.51,
        1.512,
        1.694,
        1.451,
        1.392
      ]
    },
    {
      "tool": "search_knowledge",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 10
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.446,
      "p95_ms": 1.792,
      "mean_ms": 1.571,
      "min_ms": 1.296,
      "max_ms": 3.335,
      "samples_ms": [
        1.376,
        1.422,
        1.516,
        3.335,
        1.792,
        1.595,
        1.446,
        1.611,
        1.44,
        1.386,
        1.334,
        1.634,
        1.67,
        1.503,
        1.463,
        1.411,
        1.343,
        1.499,
        1.296,
        1.353
      ]
    },
    {
      "tool": "ask_compass",
      "args": {
        "query": "how does the build resolve call edges"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3.157,
      "p95_ms": 3.379,
      "mean_ms": 3.162,
      "min_ms": 3.007,
      "max_ms": 3.493,
      "samples_ms": [
        3.379,
        3.118,
        3.055,
        3.007,
        3.182,
        3.011,
        3.015,
        3.212,
        3.312,
        3.493,
        3.161,
        3.21,
        3.239,
        3.194,
        3.144,
        3.157,
        3.07,
        3.017,
        3.211,
        3.06
      ]
    },
    {
      "tool": "trace_flow",
      "args": {
        "entry": "ib"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.564,
      "p95_ms": 2.005,
      "mean_ms": 1.653,
      "min_ms": 1.361,
      "max_ms": 2.27,
      "samples_ms": [
        1.505,
        1.605,
        1.419,
        1.485,
        1.564,
        1.368,
        1.361,
        1.51,
        1.409,
        1.376,
        1.37,
        1.583,
        1.665,
        1.883,
        2.27,
        1.935,
        1.847,
        1.923,
        2.005,
        1.969
      ]
    },
    {
      "tool": "generate_flow",
      "args": {
        "entry": "ib"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 4.554,
      "p95_ms": 4.978,
      "mean_ms": 4.6,
      "min_ms": 4.052,
      "max_ms": 5.274,
      "samples_ms": [
        5.274,
        4.726,
        4.682,
        4.515,
        4.685,
        4.404,
        4.529,
        4.646,
        4.813,
        4.978,
        4.554,
        4.359,
        4.543,
        4.607,
        4.648,
        4.567,
        4.456,
        4.447,
        4.052,
        4.517
      ]
    },
    {
      "tool": "federated_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 20
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.234,
      "p95_ms": 1.414,
      "mean_ms": 1.271,
      "min_ms": 1.18,
      "max_ms": 1.422,
      "samples_ms": [
        1.334,
        1.23,
        1.342,
        1.29,
        1.234,
        1.327,
        1.336,
        1.422,
        1.2,
        1.18,
        1.216,
        1.391,
        1.331,
        1.255,
        1.183,
        1.181,
        1.19,
        1.414,
        1.181,
        1.18
      ]
    },
    {
      "tool": "knowledge_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 20
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.459,
      "p95_ms": 1.58,
      "mean_ms": 1.476,
      "min_ms": 1.383,
      "max_ms": 1.603,
      "samples_ms": [
        1.556,
        1.498,
        1.448,
        1.603,
        1.47,
        1.445,
        1.472,
        1.41,
        1.58,
        1.439,
        1.383,
        1.425,
        1.41,
        1.527,
        1.483,
        1.459,
        1.521,
        1.409,
        1.559,
        1.419
      ]
    },
    {
      "tool": "trace_workflow",
      "args": {
        "ref": "nonexistent-baseline-probe"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.484,
      "p95_ms": 1.684,
      "mean_ms": 1.521,
      "min_ms": 1.423,
      "max_ms": 1.756,
      "samples_ms": [
        1.488,
        1.462,
        1.602,
        1.458,
        1.453,
        1.488,
        1.481,
        1.684,
        1.468,
        1.466,
        1.423,
        1.547,
        1.635,
        1.464,
        1.453,
        1.535,
        1.587,
        1.756,
        1.484,
        1.484
      ]
    },
    {
      "tool": "recall_memory",
      "args": {
        "query": "ib"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 2.634,
      "p95_ms": 2.862,
      "mean_ms": 2.66,
      "min_ms": 2.476,
      "max_ms": 3.007,
      "samples_ms": [
        2.79,
        2.646,
        2.63,
        2.634,
        2.605,
        2.754,
        2.563,
        2.476,
        2.495,
        2.637,
        2.733,
        2.507,
        2.562,
        2.516,
        2.602,
        2.862,
        3.007,
        2.79,
        2.689,
        2.697
      ]
    },
    {
      "tool": "record_memory",
      "args": {
        "type": "pattern",
        "title": "baseline probe corpus rec 20",
        "body": "rust-migration baseline probe memory"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 16.485,
      "p95_ms": 32.068,
      "mean_ms": 19.951,
      "min_ms": 8.465,
      "max_ms": 33.565,
      "samples_ms": [
        8.465,
        9.823,
        10.892,
        12.674,
        14.079,
        11.475,
        15.757,
        16.029,
        13.255,
        19.149,
        16.485,
        21.912,
        25.212,
        25.394,
        26.804,
        29.492,
        30.188,
        26.301,
        32.068,
        33.565
      ]
    },
    {
      "tool": "wiki_generate",
      "args": {
        "repo": "corpus",
        "pages": 5,
        "force": true
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 46.438,
      "p95_ms": 75.027,
      "mean_ms": 47.561,
      "min_ms": 14.376,
      "max_ms": 78.791,
      "samples_ms": [
        14.376,
        17.896,
        22.312,
        25.127,
        29.113,
        31.909,
        35.835,
        38.684,
        44.074,
        46.438,
        48.558,
        51.78,
        58.834,
        60.571,
        64.246,
        66.648,
        69.07,
        71.94,
        75.027,
        78.791
      ]
    },
    {
      "tool": "knowledge_add",
      "args": {
        "title": "baseline probe corpus add 20",
        "body": "rust-migration baseline probe document",
        "doc_type": "decision"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 2.134,
      "p95_ms": 2.337,
      "mean_ms": 2.161,
      "min_ms": 2.056,
      "max_ms": 2.368,
      "samples_ms": [
        2.193,
        2.134,
        2.256,
        2.235,
        2.162,
        2.126,
        2.098,
        2.258,
        2.104,
        2.136,
        2.083,
        2.067,
        2.056,
        2.368,
        2.146,
        2.1,
        2.086,
        2.337,
        2.144,
        2.128
      ]
    },
    {
      "tool": "knowledge_delete",
      "args": {},
      "errors": [],
      "calls": 20,
      "p50_ms": 7.01,
      "p95_ms": 7.412,
      "mean_ms": 7.056,
      "min_ms": 6.817,
      "max_ms": 7.447,
      "samples_ms": [
        6.958,
        7.447,
        7.124,
        7.211,
        6.817,
        6.954,
        6.825,
        6.943,
        6.849,
        7.079,
        6.891,
        7.01,
        7.412,
        7.374,
        6.912,
        7.084,
        7.039,
        7.147,
        6.949,
        7.105
      ]
    },
    {
      "tool": "knowledge_status",
      "args": {
        "new_status": "superseded"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 5.591,
      "p95_ms": 5.819,
      "mean_ms": 5.57,
      "min_ms": 5.343,
      "max_ms": 5.862,
      "samples_ms": [
        5.703,
        5.49,
        5.627,
        5.47,
        5.65,
        5.407,
        5.343,
        5.63,
        5.497,
        5.594,
        5.429,
        5.623,
        5.424,
        5.696,
        5.591,
        5.862,
        5.819,
        5.569,
        5.379,
        5.595
      ]
    }
  ],
  "live_store_reference": {
    "db_path": "/Users/tanle/.cairn/acc48fd52e4c6e8d/.kg",
    "db_bytes": 176500736,
    "symbols": 10357,
    "edges": 67305,
    "transitive_edges": 409368
  }
}
```

## Rust engine measurements (4d-4)

Measured 2026-10-24 with cairn 0.23.0 on the machine stamped below, by the
same harness extended with `--impl rust`. Nothing above this heading changed;
this section adds a same-day Python control because neither the machine nor
the self corpus equals the P0-2 run.

### Method

Identical to the frozen method except:

1. **Engine under test** — `--impl rust` boots
   `rust/target/release/cairn serve run --db <scratch store> --port <free> --host 127.0.0.1`
   (cairn-intel 0.23.0, release profile `strip = true`, built from this
   tree's `rust/` source; the binary that produced the artifacts was kept
   aside and is byte-identical — sha256
   `3fb93972e806c33413513de056527547c8e9288385a70413a91dc3358cddd281` — to the
   rebuild from the same source after the `TCP_NODELAY` try-and-revert in
   item 2 of the attribution below, which is why `rust/target/release/cairn`
   carries a later mtime than the artifacts)
   with the same child env (`CAIRN_HOME`, `CAIRN_DB`, `CAIRN_KNOWLEDGE`,
   `CAIRN_WORKSPACE`; pins `CAIRN_EMBED_BACKEND=hash`, `CAIRN_RERANK=0`).
   The store is still built by the Python engine (`build_graph` +
   `build_dataflow_index` + hash embed), so both daemons serve byte-identical
   stores; the build/closure/embed rows below are Python numbers in both
   columns (the closure gate is `closure_bench.py`, below).
2. **Same-day Python control** — every corpus was measured twice back to back
   (python, then rust), sequentially, never concurrently.
3. **RSS floor** — the 200 ms sampler's final reading floors the recorded
   peak (a run can end between ticks).
4. **Closure** — `closure_bench.py --edge-factor 5` over a generated 1000-file
   medium corpus (`cairn.bench.corpus.generate_corpus(root, 1000, complexity="medium")`,
   structural edges multiplied by `scaling_suite.CLOSURE_EDGE_FACTOR = 5`
   with `synthesize_structural_edges`): the scaling suite's closure gate
   point, the only in-repo artifact of the "5× edge counts" blowup case.
   Plus the self corpus at 1×. The store is built by the Rust builder, each
   closure runs on its own copy, `transitive_edges` content parity gates the
   run.

Machine stamp (all runs): Apple M4 Pro, 12 CPUs, 24 GiB,
`macOS-27.0.1-arm64-arm-64bit-Mach-O` (build 26A434). The P0-2 machine had
10 CPUs.

Corpora: cairn self @ git `542a03e85a3abbd6da152d77bb9bd4bb112c05fc` (6 dirty
entries; 848 files / 11,828 symbols / 71,100 edges, against 654 / 10,819 /
66,904 at P0-2 — the growth is Python source: tracked `.py` files outside
`benchmarks/datasource/` went 788 → 965 between the two commits, while
`rust/` stays out of the self index through the tracked root `cairn.json`
exclude: the Python scanner on this tree yields 848 indexable files, none
under `rust/`, with 415 `rust/` files skipped as `config_exclude`); the live
knowledge bundle seeded for self holds 3 files (P0-2: 487), so the self
knowledge-layer rows are not comparable to P0-2, only to the same-day
control. yarl and attrs: tree hashes identical to P0-2 (`b2ac9f50…8745b974`,
`ad6eec77…d515097`).

Reproduce:

    .venv/bin/python scripts/rust_migration/baseline.py --impl rust --label cairn-self --repeats 20 --out <out>.json
    .venv/bin/python scripts/rust_migration/baseline.py --impl rust --workspace benchmarks/datasource/t2/yarl --label yarl --repeats 20 --out <out>.json
    .venv/bin/python scripts/rust_migration/baseline.py --impl rust --workspace benchmarks/datasource/ds2/second-corpus/attrs-26.1.0 --label attrs --repeats 20 --out <out>.json
    .venv/bin/python scripts/rust_migration/closure_bench.py --workspace <generate_corpus(…, 1000, complexity="medium")> --edge-factor 5
    .venv/bin/python scripts/rust_migration/closure_bench.py

(drop `--impl rust` for the Python control.)

### Gate verdicts

The legs below belong to G1 (plan §5: "daemon RSS ≤ 60% of Python's; no tool
regresses p95 latency") and G2 (plan §6: "closure ≥5× faster on the blowup
case"); 4d-4 (plan §15) is the task that records them, and the Gate verdicts
table in `PROGRESS.md` carries the cross-reference.

| leg | cairn self | yarl | attrs | verdict |
|---|---:|---:|---:|---|
| G1: daemon RSS ≤ 60% of Python, peak vs peak | 58.7% | 29.6% | 30.8% | **MET** (self clears the line by 1.3 points) |
| G1: daemon RSS, final vs final | 39.3% | 27.2% | 27.5% | MET |
| G1: per-tool p95 no worse, vs the same-day Python control | 21/26 tools | 11/26 | 17/26 | **NOT MET** as stated |
| G1: per-tool p95 no worse, vs the frozen P0-2 numbers | 20/26 | 12/26 | 14/26 | NOT MET |
| G2: closure ≥ 5× on the blowup case (wall) | 1.14× (1000-file ×5), 1.32× (self ×1) | — | — | **NOT MET** |

Against the frozen P0-2 self peak (205,296 KiB) the Rust peak is 61.0%, but
that store had 654 files; the same-store ratio is the comparable one.

What the numbers mean (every claim measured in this session):

1. **Self-store graph tools: Rust p95 is 100–500× lower** because the Python
   daemon's per-call freshness guard (`refresh_for_query` → `_detect_changed`,
   `src/cairn/graph/watcher.py`) re-stats the 848 indexed files and re-walks
   the source tree on every graph-tool call; on this checkout that costs
   ~3.5 s per call (measured directly on the self store: `_detect_changed`
   3.51 s warm, of which the gitignore-aware `iter_source_files` walk over
   this 243,186-entry tree is 3.37 s, yielding the 848 indexed files; the
   graph tools' p50 of ~3.63 s is that guard plus the query). The Rust daemon has
   no per-call guard (`rust/crates/cairn-daemon/src/tools.rs` dispatch opens
   the store and answers). This is the asymmetry the frozen method already
   documents: each engine's real per-call cost on this workspace.
2. **Small-store tail: the Rust daemon opens a fresh read-only connection per
   call** (`tools.rs:71` → `cairn_store::conn::open_read_only`); the Python
   daemon pools one per thread with a `(st_dev, st_ino)` swap check
   (`src/cairn/mcp_server/_server_core.py:108`, `_conn`). Rust medians are
   lower on most graph tools, but ~7% of calls pay a 10–25 ms stall, which is
   what lifts p95 above Python on yarl/attrs. Attribution: 300 raw `sqlite3`
   open→query→close cycles on the same yarl store from Python reproduce the
   profile exactly (p50 3.85 ms, p95 17.5 ms, 22/300 above 3× median,
   unchanged with `mmap_size` 0 or 256 MB); the same query on a persistent
   connection takes 8 µs. Setting `TCP_NODELAY` on the daemon listener was
   tried and changed nothing (p95 18.1 ms, 23/300); reverted. Follow-up: port
   the pooled read connection with the inode swap check into the Rust daemon
   (a twin of existing Python behaviour; it must honour the 4b-13 store-swap
   semantics).
3. **`path` on the self store** is a steady 437 ms in Rust (`status` →
   `createStatusBarItem`, depth 4; all 20 samples within 433–454 ms). The
   Python gross number is guard-dominated, so this method yields no clean
   Python net figure for it; flagged for a dedicated comparison.
4. **KM/flow tools with a higher Rust p50 on every corpus**: `trace_flow`
   (2.4–4.1 ms vs 0.9–1.0), `ask_compass` (4.1–7.1 vs 1.9–5.5),
   `knowledge_search` (3.4–6.0 vs 1.0–1.3). Small in absolute terms,
   consistent across corpora and repeat counts.
5. **Write tools**: Rust medians are lower (`record_memory` 9–16 ms vs 33–66),
   but `record_memory` on self carries two 230–257 ms samples;
   `knowledge_add`/`knowledge_delete`/`knowledge_status` p95 sit below Python
   on self and attrs and within 2.3 ms of it on yarl.
6. **Closure is insert-bound in both engines.** On the ×5 store (31,001
   symbols / 733,000 edges, 803,934 closure rows, content identical) Python
   spends 3.20 s of ~4.3 s in the `transitive_edges` insert + commit (BFS
   0.97 s, sort 0.17 s); the whole Rust run is 3.44–3.50 s, i.e. at that
   floor. Both engines run the same depth-bounded BFS (`CLOSURE_MAX_DEPTH = 4`;
   Rust reports `levels=4`), so the super-linear blowup the ≥5× target was set
   against no longer reproduces on the Python side (4 s on the gate point whose
   budget is 60 s). The Rust gain is memory: peak WAL 58.3 MB vs 186.9 MB (×5)
   and 26.9 MB vs 115.2 MB (self), `peak_frontier` 273,120 / 222,019.
7. **RSS shape**: on the self store the Rust daemon boots at 13,888 KiB, peaks
   at 124,096 KiB inside `explore` (next: `wiki_generate`, 103,360 KiB) and
   settles at 63,920 KiB after the full script (100 ms external sampler
   correlated with the harness's per-tool progress lines, 3 repeats). The
   peak is a per-call transient of the heaviest tool, not the resident
   footprint.

### Summary

| metric | self python | self rust | yarl python | yarl rust | attrs python | attrs rust |
|--------|-----------:|----------:|------------:|----------:|-------------:|-----------:|
| build (build_graph) s | 7.365 | 7.513 | 0.319 | 0.551 | 0.461 | 0.352 |
| dataflow index s | 2.233 | 2.384 | 0.102 | 0.105 | 0.122 | 0.122 |
| transitive closure s | 2.876 | 3.07 | 0.034 | 0.035 | 0.037 | 0.037 |
| closure rows | 484,153 | 484,153 | 12,467 | 12,467 | 12,539 | 12,539 |
| peak WAL bytes | 115,244,672 | 115,261,152 | 2,793,392 | 2,793,392 | 2,888,152 | 2,884,032 |
| embed (hash) s | 1.163 | 1.267 | 0.052 | 0.053 | 0.105 | 0.104 |
| symbols / edges | 11,828 / 71,100 | 11,828 / 71,100 | 1,090 / 4,112 | 1,090 / 4,112 | 1,722 / 6,239 | 1,722 / 6,239 |
| files indexed | 848 | 848 | 24 | 24 | 50 | 50 |
| knowledge files seeded | 3 | 3 | 0 | 0 | 0 | 0 |
| daemon boot s | 9.076 | 0.28 | 0.601 | 0.296 | 0.603 | 0.276 |
| daemon RSS peak KiB | 213,424 | 125,232 | 93,392 | 27,600 | 101,776 | 31,392 |
| daemon RSS final KiB | 164,864 | 64,720 | 93,392 | 25,392 | 100,352 | 27,552 |
| tool errors (26 tools x 20) | 0 | 0 | 0 | 0 | 0 | 0 |

`symbols / edges` is the build summary's parser-emitted count, as in the
frozen table; the `edges` table additionally holds the materialized
`kind='imports'` rows, so it counts 76,294 for self (71,100 + 5,194; the
summary's `imports` 8,591 is the `imports` table) — the figure the closure
bench below reports from the same corpus, not a Python-vs-Rust builder
divergence.

| corpus | python peak KiB | rust peak KiB | rust/python | python final KiB | rust final KiB | rust/python |
|--------|----------------:|--------------:|------------:|-----------------:|---------------:|------------:|
| cairn-self | 213,424 | 125,232 | 58.7% | 164,864 | 64,720 | 39.3% |
| yarl | 93,392 | 27,600 | 29.6% | 93,392 | 25,392 | 27.2% |
| attrs | 101,776 | 31,392 | 30.8% | 100,352 | 27,552 | 27.5% |

### Per-tool latency (ms, 20 timed calls each, nearest-rank)

`P0-2 python p95` is the frozen number above (different machine; for self also
a different corpus and knowledge bundle). The verdict columns compare Rust
p95 against the same-day Python p95 and against the frozen P0-2 p95.

#### cairn-self

| tool | P0-2 python p95 | python p50 | python p95 | rust p50 | rust p95 | rust p95 / python p95 | vs P0-2 |
|------|----------------:|-----------:|-----------:|---------:|---------:|----------------------:|:-------:|
| `find_definition` | 375.9 | 3,572.8 | 3,649.7 | 7.54 | 19.32 | 0.005 | ok |
| `get_callers` | 339.7 | 3,674.0 | 3,716.9 | 4.36 | 12.46 | 0.003 | ok |
| `get_callees` | 330.2 | 3,700.5 | 3,737.7 | 4.04 | 16.37 | 0.004 | ok |
| `impact_analysis` | 349.3 | 3,697.8 | 3,734.8 | 5.47 | 7.52 | 0.002 | ok |
| `path` | 343.1 | 3,657.5 | 3,717.8 | 437.0 | 450.4 | 0.121 | **worse** |
| `explore` | 2,693.3 | 4,682.7 | 4,747.5 | 1,030.6 | 1,070.7 | 0.226 | ok |
| `semantic_search` | 393.0 | 3,686.8 | 3,712.8 | 32.44 | 34.00 | 0.009 | ok |
| `search_symbols` | 334.2 | 3,637.3 | 3,700.3 | 8.78 | 34.95 | 0.009 | ok |
| `repo_map` | 404.0 | 3,676.2 | 3,730.2 | 29.11 | 33.16 | 0.009 | ok |
| `file_api` | 337.2 | 3,630.7 | 3,673.1 | 3.63 | 7.50 | 0.002 | ok |
| `cross_repo_deps` | 346.0 | 3,646.4 | 3,686.8 | 10.71 | 21.42 | 0.006 | ok |
| `visualize_graph` | 326.1 | 3,694.1 | 3,728.3 | 3.27 | 16.47 | 0.004 | ok |
| `get_compass` | 13.82 | 1.17 | 1.39 | 0.44 | 0.50 | 0.359 | ok |
| `search_knowledge` | 514.6 | 2.08 | 2.27 | 0.47 | 0.53 | 0.234 | ok |
| `ask_compass` | 1,037.2 | 5.45 | 6.01 | 7.13 | 10.73 | 1.785 **worse** | ok |
| `trace_flow` | 1.48 | 0.95 | 1.16 | 3.32 | 14.61 | 12.649 **worse** | **worse** |
| `generate_flow` | 2.02 | 1.28 | 1.44 | 1.39 | 1.79 | 1.249 **worse** | ok |
| `federated_search` | 1.21 | 0.82 | 1.08 | 0.15 | 0.17 | 0.160 | ok |
| `knowledge_search` | 14.06 | 1.22 | 1.32 | 5.05 | 17.94 | 13.604 **worse** | **worse** |
| `trace_workflow` | 13.75 | 1.25 | 1.28 | 0.58 | 0.77 | 0.600 | ok |
| `recall_memory` | 683.1 | 2.98 | 3.22 | 2.34 | 2.69 | 0.835 | ok |
| `record_memory` | 555.8 | 32.63 | 43.77 | 16.39 | 231.4 | 5.288 **worse** | ok |
| `wiki_generate` | 243.5 | 220.2 | 264.7 | 148.0 | 162.4 | 0.614 | ok |
| `knowledge_add` | 2.27 | 7.80 | 26.27 | 3.65 | 15.96 | 0.607 | **worse** |
| `knowledge_delete` | 7.11 | 13.82 | 20.08 | 6.63 | 9.41 | 0.469 | **worse** |
| `knowledge_status` | 5.80 | 14.64 | 24.94 | 10.17 | 20.12 | 0.807 | **worse** |

no-worse count: 21/26 vs the same-day python control, 20/26 vs the frozen P0-2 numbers.

#### yarl

| tool | P0-2 python p95 | python p50 | python p95 | rust p50 | rust p95 | rust p95 / python p95 | vs P0-2 |
|------|----------------:|-----------:|-----------:|---------:|---------:|----------------------:|:-------:|
| `find_definition` | 6.83 | 5.09 | 6.07 | 8.04 | 20.60 | 3.395 **worse** | **worse** |
| `get_callers` | 8.75 | 6.44 | 6.90 | 4.98 | 6.95 | 1.008 **worse** | ok |
| `get_callees` | 8.35 | 6.39 | 6.57 | 4.24 | 16.12 | 2.455 **worse** | **worse** |
| `impact_analysis` | 11.57 | 7.92 | 8.73 | 4.30 | 4.69 | 0.537 | ok |
| `path` | 8.58 | 6.04 | 7.02 | 3.70 | 18.50 | 2.636 **worse** | **worse** |
| `explore` | 19.20 | 12.66 | 13.21 | 16.87 | 19.44 | 1.472 **worse** | **worse** |
| `semantic_search` | 13.11 | 9.35 | 9.72 | 6.92 | 25.86 | 2.662 **worse** | **worse** |
| `search_symbols` | 9.79 | 7.40 | 8.03 | 3.28 | 9.87 | 1.229 **worse** | **worse** |
| `repo_map` | 11.18 | 8.20 | 8.42 | 5.01 | 10.17 | 1.208 **worse** | ok |
| `file_api` | 7.87 | 5.44 | 5.65 | 2.42 | 3.74 | 0.661 | ok |
| `cross_repo_deps` | 6.64 | 5.27 | 5.67 | 2.63 | 4.88 | 0.860 | ok |
| `visualize_graph` | 6.51 | 5.20 | 5.30 | 3.47 | 4.62 | 0.872 | ok |
| `get_compass` | 1.35 | 0.95 | 0.98 | 0.22 | 0.26 | 0.260 | ok |
| `search_knowledge` | 1.38 | 0.96 | 1.19 | 0.21 | 0.22 | 0.186 | ok |
| `ask_compass` | 2.94 | 1.91 | 2.15 | 4.14 | 9.47 | 4.396 **worse** | **worse** |
| `trace_flow` | 1.67 | 0.90 | 1.11 | 2.44 | 9.92 | 8.943 **worse** | **worse** |
| `generate_flow` | 1.98 | 1.19 | 1.39 | 1.24 | 1.58 | 1.138 **worse** | ok |
| `federated_search` | 1.32 | 0.82 | 0.99 | 0.20 | 0.22 | 0.218 | ok |
| `knowledge_search` | 1.41 | 0.96 | 1.04 | 6.00 | 27.20 | 26.152 **worse** | **worse** |
| `trace_workflow` | 1.47 | 1.00 | 1.03 | 0.43 | 0.71 | 0.689 | ok |
| `recall_memory` | 1.45 | 1.10 | 1.32 | 1.98 | 2.27 | 1.720 **worse** | **worse** |
| `record_memory` | 29.82 | 34.81 | 48.64 | 9.44 | 12.67 | 0.261 | ok |
| `wiki_generate` | 51.79 | 55.47 | 68.66 | 38.96 | 64.22 | 0.935 | **worse** |
| `knowledge_add` | 2.31 | 6.07 | 7.45 | 5.78 | 9.79 | 1.315 **worse** | **worse** |
| `knowledge_delete` | 7.93 | 12.51 | 17.75 | 12.53 | 15.17 | 0.855 | **worse** |
| `knowledge_status` | 6.15 | 14.21 | 27.21 | 13.95 | 28.95 | 1.064 **worse** | **worse** |

no-worse count: 11/26 vs the same-day python control, 12/26 vs the frozen P0-2 numbers.

#### attrs

| tool | P0-2 python p95 | python p50 | python p95 | rust p50 | rust p95 | rust p95 / python p95 | vs P0-2 |
|------|----------------:|-----------:|-----------:|---------:|---------:|----------------------:|:-------:|
| `find_definition` | 13.51 | 8.05 | 11.24 | 7.39 | 29.56 | 2.629 **worse** | **worse** |
| `get_callers` | 16.05 | 9.27 | 9.65 | 4.75 | 9.40 | 0.975 | ok |
| `get_callees` | 15.81 | 9.19 | 9.58 | 4.31 | 22.03 | 2.300 **worse** | **worse** |
| `impact_analysis` | 19.43 | 11.17 | 12.14 | 3.36 | 12.88 | 1.061 **worse** | ok |
| `path` | 15.69 | 9.13 | 9.39 | 5.10 | 7.01 | 0.747 | ok |
| `explore` | 33.55 | 19.95 | 20.90 | 10.79 | 11.88 | 0.568 | ok |
| `semantic_search` | 21.36 | 15.15 | 15.39 | 8.24 | 10.17 | 0.661 | ok |
| `search_symbols` | 15.69 | 10.00 | 10.46 | 4.81 | 23.62 | 2.258 **worse** | **worse** |
| `repo_map` | 19.96 | 13.54 | 14.13 | 6.21 | 7.69 | 0.544 | ok |
| `file_api` | 12.35 | 8.27 | 8.87 | 2.51 | 19.27 | 2.174 **worse** | **worse** |
| `cross_repo_deps` | 13.26 | 8.22 | 8.72 | 2.73 | 3.42 | 0.392 | ok |
| `visualize_graph` | 12.04 | 7.96 | 8.16 | 3.37 | 14.65 | 1.797 **worse** | **worse** |
| `get_compass` | 1.67 | 0.94 | 0.96 | 0.39 | 0.54 | 0.564 | ok |
| `search_knowledge` | 1.79 | 0.94 | 0.96 | 0.23 | 0.40 | 0.414 | ok |
| `ask_compass` | 3.38 | 1.98 | 2.12 | 4.67 | 9.66 | 4.568 **worse** | **worse** |
| `trace_flow` | 2.00 | 0.94 | 0.98 | 4.08 | 13.80 | 14.012 **worse** | **worse** |
| `generate_flow` | 4.98 | 8.50 | 15.21 | 9.84 | 11.79 | 0.775 | **worse** |
| `federated_search` | 1.41 | 1.08 | 1.31 | 0.14 | 0.20 | 0.155 | ok |
| `knowledge_search` | 1.58 | 1.25 | 1.46 | 3.40 | 15.85 | 10.851 **worse** | **worse** |
| `trace_workflow` | 1.68 | 1.20 | 1.51 | 0.42 | 0.46 | 0.302 | ok |
| `recall_memory` | 2.86 | 1.93 | 2.31 | 1.69 | 2.05 | 0.888 | ok |
| `record_memory` | 32.07 | 33.07 | 51.74 | 9.84 | 27.99 | 0.541 | ok |
| `wiki_generate` | 75.03 | 81.06 | 96.31 | 40.81 | 51.43 | 0.534 | ok |
| `knowledge_add` | 2.34 | 5.68 | 17.86 | 3.72 | 4.24 | 0.237 | **worse** |
| `knowledge_delete` | 7.41 | 16.85 | 28.55 | 10.22 | 11.49 | 0.402 | **worse** |
| `knowledge_status` | 5.82 | 21.42 | 42.84 | 11.76 | 19.06 | 0.445 | **worse** |

no-worse count: 17/26 vs the same-day python control, 14/26 vs the frozen P0-2 numbers.

### Supplementary: yarl at 100 timed calls

Same method, `--repeats 100`, python then rust. Every tool that opens the
store keeps a 10–20 ms p95 over a lower median; the tools that never open it
(`get_compass`, `search_knowledge`, `federated_search`, `trace_workflow`) stay
under 0.5 ms — the n=20 tail is not sampling noise.

| tool | python p50 | python p95 | rust p50 | rust p95 | rust max |
|------|-----------:|-----------:|---------:|---------:|---------:|
| `find_definition` | 5.00 | 6.29 | 4.10 | 20.54 | 33.81 |
| `get_callers` | 6.38 | 6.65 | 3.69 | 12.93 | 21.72 |
| `get_callees` | 6.20 | 6.63 | 3.66 | 16.28 | 20.27 |
| `impact_analysis` | 7.65 | 8.00 | 4.41 | 18.27 | 22.91 |
| `path` | 6.91 | 7.23 | 4.24 | 14.11 | 24.73 |
| `explore` | 13.99 | 14.51 | 5.65 | 6.34 | 9.05 |
| `semantic_search` | 9.55 | 9.92 | 5.59 | 22.62 | 29.34 |
| `search_symbols` | 6.60 | 9.04 | 3.34 | 16.23 | 24.36 |
| `repo_map` | 7.99 | 8.39 | 4.38 | 15.01 | 23.25 |
| `file_api` | 5.31 | 5.83 | 3.31 | 10.78 | 17.59 |
| `cross_repo_deps` | 5.09 | 5.38 | 4.10 | 9.91 | 22.76 |
| `visualize_graph` | 4.97 | 5.36 | 3.65 | 17.30 | 20.90 |
| `get_compass` | 0.93 | 1.17 | 0.22 | 0.49 | 0.68 |
| `search_knowledge` | 0.93 | 1.16 | 0.24 | 0.28 | 1.58 |
| `ask_compass` | 1.86 | 2.11 | 4.23 | 11.15 | 13.31 |
| `trace_flow` | 0.87 | 1.09 | 3.85 | 13.86 | 19.67 |
| `generate_flow` | 1.13 | 1.36 | 1.19 | 1.84 | 3.21 |
| `federated_search` | 0.81 | 1.02 | 0.14 | 0.15 | 0.18 |
| `knowledge_search` | 0.95 | 1.18 | 2.44 | 12.43 | 15.96 |
| `trace_workflow` | 0.98 | 1.20 | 0.31 | 0.39 | 0.60 |
| `recall_memory` | 1.08 | 1.31 | 1.22 | 1.42 | 1.65 |
| `record_memory` | 65.91 | 118.2 | 14.10 | 251.5 | 277.9 |
| `wiki_generate` | 134.1 | 217.0 | 42.02 | 214.7 | 311.6 |
| `knowledge_add` | 5.20 | 16.98 | 4.04 | 13.29 | 22.82 |
| `knowledge_delete` | 11.01 | 21.07 | 9.24 | 11.37 | 31.16 |
| `knowledge_status` | 14.50 | 25.86 | 11.54 | 18.43 | 22.15 |

RSS peak: python 100,736 KiB, rust 28,096 KiB.

### Closure bench outputs

1000-file medium corpus, ×5 structural edges (`--edge-factor 5`):

```json
{
  "suite": "rust-migration-closure-bench/1",
  "corpus": "/tmp/4d4/blowup/benchrepo",
  "edge_factor": 5,
  "symbols": 31001,
  "edges": 733000,
  "python": {
    "seconds": 3.987,
    "rows": 803934,
    "peak_wal_bytes": 186870872,
    "sampled_peak_wal_bytes": 186870872,
    "wal_end_bytes": 186870872,
    "db_bytes": 409956352
  },
  "rust": {
    "seconds": 3.503,
    "rows": 803934,
    "levels": 4,
    "peak_frontier": 273120,
    "peak_wal_bytes": 58343352,
    "sampled_peak_wal_bytes": 58343352,
    "wal_end_bytes": 0,
    "db_bytes": 408068096
  },
  "ratio_python_over_rust": 1.14,
  "content_parity": true
}
```

cairn self, ×1 (`edges` is the `edges` table count, materialized imports
included; see the note under Summary):

```json
{
  "suite": "rust-migration-closure-bench/1",
  "corpus": "/Users/lnmtan/Projects/others/cairn",
  "edge_factor": 1,
  "symbols": 11828,
  "edges": 76294,
  "python": {
    "seconds": 2.312,
    "rows": 484153,
    "peak_wal_bytes": 115154032,
    "sampled_peak_wal_bytes": 115154032,
    "wal_end_bytes": 115154032,
    "db_bytes": 165425152
  },
  "rust": {
    "seconds": 1.752,
    "rows": 484153,
    "levels": 4,
    "peak_frontier": 222019,
    "peak_wal_bytes": 26899512,
    "sampled_peak_wal_bytes": 26899512,
    "wal_end_bytes": 0,
    "db_bytes": 163647488
  },
  "ratio_python_over_rust": 1.32,
  "content_parity": true
}
```

### Raw artifacts (4d-4)

#### cairn-self — python

```json
{
  "schema": "cairn-rust-baseline/1",
  "suite": "rust-migration-baseline",
  "impl": "python",
  "label": "cairn-self",
  "timestamp": "2026-10-24T16:37:32.559121+00:00",
  "harness": "scripts/rust_migration/baseline.py",
  "reproduce": ".venv/bin/python scripts/rust_migration/baseline.py --workspace /Users/lnmtan/Projects/others/cairn --label cairn-self --repeats 20",
  "dataset": {
    "name": "benchmark-datasource",
    "version": "DS-v1",
    "tree_hash": "65e3df398b96f2cd31230ee17765fc01b0bc603e4e27914f3c31f41f061f87bb",
    "identity_size": 300
  },
  "cairn_version": "0.23.0",
  "machine_profile": {
    "arch": "arm64",
    "cpu": "arm",
    "cpu_count": 12,
    "os": "macOS-27.0.1-arm64-arm-64bit-Mach-O",
    "runner_class": "reference-local"
  },
  "method": {
    "build": "build_graph(workspace, db) + build_dataflow_index (the `cairn build` shape)",
    "closure": "build_transitive_closure(conn) timed; WAL sidecar polled every 0.005s, with the post-commit sidecar size as the single-burst floor for the peak",
    "latency": "per-tool tools/call over streamable HTTP (keep-alive), 20 timed calls each after 1 untimed warmup; write tools re-create their fixture untimed per call",
    "rss": "ps -o rss= on the daemon pid every 0.2s",
    "env_pins": {
      "CAIRN_EMBED_BACKEND": "hash",
      "CAIRN_RERANK": "0"
    },
    "daemon": {
      "transport": "streamable-http",
      "stateless": false,
      "host": "127.0.0.1"
    },
    "knowledge_bundle_seeded_files": 3
  },
  "corpus": {
    "workspace": "/Users/lnmtan/Projects/others/cairn",
    "identity_method": "git_commit",
    "git_commit": "542a03e85a3abbd6da152d77bb9bd4bb112c05fc",
    "git_dirty_entries": 6,
    "py_files": 25486,
    "py_lines": 9175493,
    "py_bytes": 320998649
  },
  "build": {
    "seconds": 7.365,
    "dataflow_seconds": 2.233,
    "dataflow_symbols": 2000,
    "repos": 1,
    "files": 848,
    "symbols": 11828,
    "edges": 71100,
    "imports": 8591,
    "skipped": 21510,
    "resolution": {
      "exact": 23383,
      "ambiguous": 5780,
      "unresolved": 30988
    },
    "lsp_upgraded": 0,
    "scip_edges": null,
    "parse_errors": 0
  },
  "closure": {
    "seconds": 2.876,
    "rows": 484153,
    "peak_wal_bytes": 115244672,
    "sampled_peak_wal_bytes": 115244672,
    "wal_end_bytes": 115244672,
    "db_bytes": 168767488
  },
  "embed": {
    "seconds": 1.163,
    "backend_effective": "hash",
    "embedded": 11828,
    "total": 11828
  },
  "daemon": {
    "impl": "python",
    "boot_seconds": 9.076,
    "rss": {
      "peak_kb": 213424,
      "final_kb": 164864
    }
  },
  "tools": [
    {
      "tool": "find_definition",
      "args": {
        "name": "test_flush_factory_failure_never_raises_and_retains",
        "limit": 5
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3572.809,
      "p95_ms": 3649.676,
      "mean_ms": 3586.098,
      "min_ms": 3524.024,
      "max_ms": 3650.68,
      "samples_ms": [
        3567.3,
        3554.066,
        3557.39,
        3531.284,
        3535.763,
        3557.091,
        3524.024,
        3629.833,
        3630.094,
        3545.883,
        3572.809,
        3574.515,
        3528.682,
        3598.802,
        3583.529,
        3643.555,
        3649.676,
        3637.513,
        3649.461,
        3650.68
      ]
    },
    {
      "tool": "get_callers",
      "args": {
        "name": "test_flush_factory_failure_never_raises_and_retains",
        "limit": 50
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3674.025,
      "p95_ms": 3716.936,
      "mean_ms": 3679.454,
      "min_ms": 3645.153,
      "max_ms": 3720.119,
      "samples_ms": [
        3655.942,
        3669.728,
        3645.153,
        3674.025,
        3699.9,
        3679.01,
        3663.472,
        3664.86,
        3705.717,
        3668.01,
        3679.837,
        3716.936,
        3698.862,
        3648.928,
        3692.973,
        3653.654,
        3657.383,
        3713.867,
        3680.696,
        3720.119
      ]
    },
    {
      "tool": "get_callees",
      "args": {
        "name": "test_flush_factory_failure_never_raises_and_retains",
        "limit": 50
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3700.459,
      "p95_ms": 3737.716,
      "mean_ms": 3695.153,
      "min_ms": 3555.677,
      "max_ms": 3772.995,
      "samples_ms": [
        3555.677,
        3624.966,
        3699.165,
        3656.506,
        3701.48,
        3678.236,
        3721.091,
        3719.604,
        3700.459,
        3772.995,
        3717.177,
        3683.37,
        3702.218,
        3692.417,
        3720.354,
        3726.931,
        3686.826,
        3737.716,
        3723.256,
        3682.612
      ]
    },
    {
      "tool": "impact_analysis",
      "args": {
        "name": "str",
        "depth": 3,
        "limit": 100
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3697.828,
      "p95_ms": 3734.786,
      "mean_ms": 3701.024,
      "min_ms": 3663.259,
      "max_ms": 3762.212,
      "samples_ms": [
        3722.577,
        3664.948,
        3710.647,
        3681.355,
        3734.786,
        3687.511,
        3694.653,
        3714.433,
        3718.354,
        3678.523,
        3663.259,
        3705.799,
        3730.099,
        3679.688,
        3697.828,
        3680.88,
        3704.13,
        3678.591,
        3762.212,
        3710.214
      ]
    },
    {
      "tool": "path",
      "args": {
        "from_pattern": "bench_compare",
        "to_pattern": "_load",
        "max_depth": 4
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3657.483,
      "p95_ms": 3717.83,
      "mean_ms": 3606.369,
      "min_ms": 3437.371,
      "max_ms": 3735.425,
      "samples_ms": [
        3717.83,
        3692.065,
        3660.853,
        3668.612,
        3437.371,
        3525.048,
        3503.91,
        3609.402,
        3511.172,
        3508.307,
        3490.852,
        3491.205,
        3519.452,
        3735.425,
        3671.232,
        3700.927,
        3677.449,
        3665.029,
        3683.756,
        3657.483
      ]
    },
    {
      "tool": "explore",
      "args": {
        "query": "test_flush_factory_failure_never_raises_and_retains"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 4682.682,
      "p95_ms": 4747.497,
      "mean_ms": 4691.737,
      "min_ms": 4609.221,
      "max_ms": 4897.102,
      "samples_ms": [
        4897.102,
        4747.497,
        4746.889,
        4673.074,
        4669.487,
        4682.682,
        4675.553,
        4682.896,
        4703.818,
        4614.714,
        4609.221,
        4718.448,
        4684.203,
        4708.302,
        4665.251,
        4719.485,
        4634.496,
        4665.881,
        4648.466,
        4687.276
      ]
    },
    {
      "tool": "semantic_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 10
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3686.802,
      "p95_ms": 3712.843,
      "mean_ms": 3680.939,
      "min_ms": 3629.787,
      "max_ms": 3745.02,
      "samples_ms": [
        3687.772,
        3693.312,
        3633.866,
        3694.639,
        3653.115,
        3686.416,
        3666.336,
        3745.02,
        3694.308,
        3686.802,
        3629.787,
        3693.485,
        3712.843,
        3674.971,
        3690.293,
        3701.695,
        3676.203,
        3693.167,
        3632.509,
        3672.241
      ]
    },
    {
      "tool": "search_symbols",
      "args": {
        "pattern": "test*"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3637.334,
      "p95_ms": 3700.305,
      "mean_ms": 3644.256,
      "min_ms": 3582.79,
      "max_ms": 3716.341,
      "samples_ms": [
        3634.122,
        3675.926,
        3676.782,
        3609.88,
        3621.325,
        3645.695,
        3637.334,
        3651.013,
        3625.102,
        3648.499,
        3636.829,
        3626.02,
        3664.491,
        3613.329,
        3582.79,
        3700.305,
        3626.381,
        3648.461,
        3644.496,
        3716.341
      ]
    },
    {
      "tool": "repo_map",
      "args": {},
      "errors": [],
      "calls": 20,
      "p50_ms": 3676.18,
      "p95_ms": 3730.153,
      "mean_ms": 3685.342,
      "min_ms": 3643.727,
      "max_ms": 3742.752,
      "samples_ms": [
        3676.18,
        3644.367,
        3709.884,
        3684.811,
        3656.776,
        3673.268,
        3678.224,
        3686.989,
        3694.774,
        3643.727,
        3742.752,
        3669.353,
        3675.54,
        3722.046,
        3730.153,
        3674.323,
        3698.506,
        3673.588,
        3708.151,
        3663.427
      ]
    },
    {
      "tool": "file_api",
      "args": {
        "path": ".github/scripts/bench_compare.py",
        "repo": "cairn"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3630.71,
      "p95_ms": 3673.104,
      "mean_ms": 3633.677,
      "min_ms": 3565.908,
      "max_ms": 3697.288,
      "samples_ms": [
        3650.477,
        3638.008,
        3565.908,
        3611.264,
        3617.31,
        3587.144,
        3653.47,
        3628.25,
        3641.151,
        3635.615,
        3610.295,
        3671.208,
        3651.381,
        3597.392,
        3630.71,
        3623.877,
        3623.452,
        3673.104,
        3666.228,
        3697.288
      ]
    },
    {
      "tool": "cross_repo_deps",
      "args": {
        "repo": "cairn"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3646.387,
      "p95_ms": 3686.843,
      "mean_ms": 3652.06,
      "min_ms": 3596.165,
      "max_ms": 3710.227,
      "samples_ms": [
        3596.165,
        3646.387,
        3645.844,
        3599.361,
        3710.227,
        3684.591,
        3637.076,
        3682.626,
        3680.131,
        3680.163,
        3643.417,
        3630.565,
        3658.038,
        3658.103,
        3627.69,
        3681.487,
        3607.784,
        3675.19,
        3686.843,
        3609.509
      ]
    },
    {
      "tool": "visualize_graph",
      "args": {
        "scope": "symbol",
        "symbol": "str",
        "depth": 2
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3694.054,
      "p95_ms": 3728.337,
      "mean_ms": 3692.46,
      "min_ms": 3644.308,
      "max_ms": 3771.676,
      "samples_ms": [
        3715.954,
        3771.676,
        3728.337,
        3672.025,
        3644.308,
        3711.822,
        3655.159,
        3689.142,
        3700.643,
        3651.481,
        3694.072,
        3697.671,
        3684.124,
        3714.909,
        3664.33,
        3663.832,
        3694.054,
        3706.633,
        3712.482,
        3676.536
      ]
    },
    {
      "tool": "get_compass",
      "args": {
        "module": "graph"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.169,
      "p95_ms": 1.388,
      "mean_ms": 1.203,
      "min_ms": 1.137,
      "max_ms": 1.425,
      "samples_ms": [
        1.425,
        1.31,
        1.226,
        1.243,
        1.194,
        1.205,
        1.173,
        1.178,
        1.175,
        1.169,
        1.153,
        1.147,
        1.388,
        1.169,
        1.152,
        1.152,
        1.137,
        1.146,
        1.157,
        1.154
      ]
    },
    {
      "tool": "search_knowledge",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 10
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 2.077,
      "p95_ms": 2.266,
      "mean_ms": 2.098,
      "min_ms": 1.865,
      "max_ms": 2.424,
      "samples_ms": [
        1.911,
        1.908,
        1.865,
        1.94,
        2.032,
        1.892,
        2.266,
        2.19,
        2.077,
        2.221,
        2.146,
        2.077,
        2.127,
        2.014,
        2.002,
        2.424,
        2.245,
        2.22,
        2.187,
        2.211
      ]
    },
    {
      "tool": "ask_compass",
      "args": {
        "query": "how does the build resolve call edges"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 5.446,
      "p95_ms": 6.012,
      "mean_ms": 5.535,
      "min_ms": 5.167,
      "max_ms": 6.204,
      "samples_ms": [
        5.85,
        5.552,
        5.403,
        5.657,
        5.754,
        6.204,
        6.012,
        5.992,
        5.628,
        5.648,
        5.446,
        5.248,
        5.689,
        5.225,
        5.272,
        5.273,
        5.187,
        5.167,
        5.288,
        5.201
      ]
    },
    {
      "tool": "trace_flow",
      "args": {
        "entry": "str"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.952,
      "p95_ms": 1.155,
      "mean_ms": 0.996,
      "min_ms": 0.906,
      "max_ms": 1.224,
      "samples_ms": [
        1.062,
        1.155,
        1.053,
        0.969,
        0.938,
        1.224,
        0.952,
        1.033,
        0.962,
        0.947,
        0.994,
        0.963,
        0.929,
        0.931,
        0.943,
        0.944,
        0.924,
        1.152,
        0.939,
        0.906
      ]
    },
    {
      "tool": "generate_flow",
      "args": {
        "entry": "str"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.278,
      "p95_ms": 1.435,
      "mean_ms": 1.311,
      "min_ms": 1.236,
      "max_ms": 1.561,
      "samples_ms": [
        1.338,
        1.435,
        1.376,
        1.391,
        1.326,
        1.303,
        1.278,
        1.353,
        1.238,
        1.561,
        1.254,
        1.304,
        1.248,
        1.292,
        1.236,
        1.251,
        1.263,
        1.246,
        1.252,
        1.27
      ]
    },
    {
      "tool": "federated_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 20
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.822,
      "p95_ms": 1.082,
      "mean_ms": 0.849,
      "min_ms": 0.802,
      "max_ms": 1.088,
      "samples_ms": [
        1.082,
        0.821,
        0.838,
        0.859,
        0.814,
        0.826,
        0.802,
        0.819,
        0.808,
        0.814,
        0.822,
        0.828,
        0.834,
        0.813,
        0.832,
        0.828,
        0.825,
        1.088,
        0.809,
        0.818
      ]
    },
    {
      "tool": "knowledge_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 20
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.221,
      "p95_ms": 1.319,
      "mean_ms": 1.239,
      "min_ms": 1.193,
      "max_ms": 1.51,
      "samples_ms": [
        1.206,
        1.193,
        1.231,
        1.217,
        1.214,
        1.214,
        1.231,
        1.24,
        1.237,
        1.23,
        1.319,
        1.51,
        1.235,
        1.211,
        1.206,
        1.231,
        1.21,
        1.229,
        1.221,
        1.203
      ]
    },
    {
      "tool": "trace_workflow",
      "args": {
        "ref": "nonexistent-baseline-probe"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.245,
      "p95_ms": 1.28,
      "mean_ms": 1.261,
      "min_ms": 1.228,
      "max_ms": 1.477,
      "samples_ms": [
        1.252,
        1.249,
        1.233,
        1.238,
        1.251,
        1.265,
        1.235,
        1.477,
        1.244,
        1.276,
        1.264,
        1.28,
        1.28,
        1.244,
        1.228,
        1.236,
        1.245,
        1.234,
        1.245,
        1.251
      ]
    },
    {
      "tool": "recall_memory",
      "args": {
        "query": "str"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 2.982,
      "p95_ms": 3.224,
      "mean_ms": 3.011,
      "min_ms": 2.912,
      "max_ms": 3.426,
      "samples_ms": [
        2.978,
        2.996,
        3.426,
        2.982,
        3.016,
        2.95,
        3.034,
        2.998,
        2.948,
        3.041,
        2.943,
        2.991,
        2.984,
        2.921,
        2.912,
        2.94,
        2.982,
        3.224,
        2.977,
        2.969
      ]
    },
    {
      "tool": "record_memory",
      "args": {
        "type": "pattern",
        "title": "baseline probe cairn rec 20",
        "body": "rust-migration baseline probe memory"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 32.633,
      "p95_ms": 43.77,
      "mean_ms": 33.804,
      "min_ms": 19.418,
      "max_ms": 52.324,
      "samples_ms": [
        19.418,
        34.814,
        25.128,
        32.379,
        34.062,
        26.09,
        22.723,
        27.914,
        27.973,
        26.923,
        30.908,
        35.901,
        39.421,
        41.187,
        43.77,
        40.934,
        43.294,
        32.633,
        38.294,
        52.324
      ]
    },
    {
      "tool": "wiki_generate",
      "args": {
        "repo": "cairn",
        "pages": 5,
        "force": true
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 220.202,
      "p95_ms": 264.705,
      "mean_ms": 211.337,
      "min_ms": 155.112,
      "max_ms": 276.421,
      "samples_ms": [
        155.112,
        164.827,
        169.319,
        176.134,
        159.244,
        155.429,
        171.431,
        207.14,
        216.647,
        229.823,
        220.202,
        220.547,
        229.407,
        229.245,
        231.932,
        241.631,
        254.226,
        253.326,
        264.705,
        276.421
      ]
    },
    {
      "tool": "knowledge_add",
      "args": {
        "title": "baseline probe cairn add 20",
        "body": "rust-migration baseline probe document",
        "doc_type": "decision"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 7.804,
      "p95_ms": 26.274,
      "mean_ms": 23.401,
      "min_ms": 6.512,
      "max_ms": 287.409,
      "samples_ms": [
        287.409,
        10.452,
        8.297,
        8.442,
        7.729,
        8.198,
        7.56,
        8.045,
        8.599,
        7.804,
        26.274,
        10.022,
        6.637,
        6.55,
        6.628,
        6.616,
        6.512,
        6.557,
        6.564,
        23.117
      ]
    },
    {
      "tool": "knowledge_delete",
      "args": {},
      "errors": [],
      "calls": 20,
      "p50_ms": 13.82,
      "p95_ms": 20.084,
      "mean_ms": 14.718,
      "min_ms": 11.032,
      "max_ms": 22.938,
      "samples_ms": [
        14.728,
        15.445,
        22.938,
        15.064,
        14.973,
        14.617,
        17.383,
        11.032,
        14.396,
        13.55,
        12.643,
        13.077,
        12.854,
        13.763,
        13.015,
        13.85,
        20.084,
        13.542,
        13.588,
        13.82
      ]
    },
    {
      "tool": "knowledge_status",
      "args": {
        "new_status": "superseded"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 14.644,
      "p95_ms": 24.944,
      "mean_ms": 16.385,
      "min_ms": 10.841,
      "max_ms": 26.41,
      "samples_ms": [
        19.396,
        15.515,
        15.983,
        14.644,
        15.387,
        26.41,
        13.853,
        14.147,
        14.867,
        22.731,
        14.357,
        13.815,
        24.944,
        12.851,
        13.501,
        13.709,
        14.909,
        10.841,
        13.313,
        22.53
      ]
    }
  ],
  "live_store_reference": {
    "db_path": "/Users/lnmtan/.cairn/79428b9d734aac21/.kg",
    "db_bytes": 91181056,
    "symbols": 13730,
    "edges": 99027,
    "transitive_edges": 0
  }
}
```

#### cairn-self — rust

```json
{
  "schema": "cairn-rust-baseline/1",
  "suite": "rust-migration-baseline",
  "impl": "rust",
  "label": "cairn-self",
  "timestamp": "2026-10-24T16:38:36.015950+00:00",
  "harness": "scripts/rust_migration/baseline.py",
  "reproduce": ".venv/bin/python scripts/rust_migration/baseline.py --impl rust --workspace /Users/lnmtan/Projects/others/cairn --label cairn-self --repeats 20",
  "dataset": {
    "name": "benchmark-datasource",
    "version": "DS-v1",
    "tree_hash": "65e3df398b96f2cd31230ee17765fc01b0bc603e4e27914f3c31f41f061f87bb",
    "identity_size": 300
  },
  "cairn_version": "0.23.0",
  "machine_profile": {
    "arch": "arm64",
    "cpu": "arm",
    "cpu_count": 12,
    "os": "macOS-27.0.1-arm64-arm-64bit-Mach-O",
    "runner_class": "reference-local"
  },
  "method": {
    "build": "build_graph(workspace, db) + build_dataflow_index (the `cairn build` shape)",
    "closure": "build_transitive_closure(conn) timed; WAL sidecar polled every 0.005s, with the post-commit sidecar size as the single-burst floor for the peak",
    "latency": "per-tool tools/call over streamable HTTP (keep-alive), 20 timed calls each after 1 untimed warmup; write tools re-create their fixture untimed per call",
    "rss": "ps -o rss= on the daemon pid every 0.2s",
    "env_pins": {
      "CAIRN_EMBED_BACKEND": "hash",
      "CAIRN_RERANK": "0"
    },
    "daemon": {
      "transport": "streamable-http",
      "stateless": false,
      "host": "127.0.0.1"
    },
    "knowledge_bundle_seeded_files": 3,
    "store": "python build_graph + dataflow + hash embed (identical-stores doctrine); the rust daemon is the measured engine"
  },
  "corpus": {
    "workspace": "/Users/lnmtan/Projects/others/cairn",
    "identity_method": "git_commit",
    "git_commit": "542a03e85a3abbd6da152d77bb9bd4bb112c05fc",
    "git_dirty_entries": 6,
    "py_files": 25486,
    "py_lines": 9175493,
    "py_bytes": 320998649
  },
  "build": {
    "seconds": 7.513,
    "dataflow_seconds": 2.384,
    "dataflow_symbols": 2000,
    "repos": 1,
    "files": 848,
    "symbols": 11828,
    "edges": 71100,
    "imports": 8591,
    "skipped": 21510,
    "resolution": {
      "exact": 23383,
      "ambiguous": 5780,
      "unresolved": 30988
    },
    "lsp_upgraded": 0,
    "scip_edges": null,
    "parse_errors": 0
  },
  "closure": {
    "seconds": 3.07,
    "rows": 484153,
    "peak_wal_bytes": 115261152,
    "sampled_peak_wal_bytes": 115261152,
    "wal_end_bytes": 115261152,
    "db_bytes": 168742912
  },
  "embed": {
    "seconds": 1.267,
    "backend_effective": "hash",
    "embedded": 11828,
    "total": 11828
  },
  "daemon": {
    "impl": "rust",
    "boot_seconds": 0.28,
    "rss": {
      "peak_kb": 125232,
      "final_kb": 64720
    }
  },
  "tools": [
    {
      "tool": "find_definition",
      "args": {
        "name": "test_decay_with_conn_reaps_orphaned_embedding",
        "limit": 5
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 7.544,
      "p95_ms": 19.321,
      "mean_ms": 9.064,
      "min_ms": 4.561,
      "max_ms": 32.972,
      "samples_ms": [
        11.808,
        19.321,
        6.499,
        6.297,
        6.781,
        8.821,
        7.972,
        8.091,
        7.423,
        7.979,
        7.782,
        7.87,
        7.544,
        7.63,
        32.972,
        6.604,
        5.23,
        4.561,
        4.848,
        5.24
      ]
    },
    {
      "tool": "get_callers",
      "args": {
        "name": "test_decay_with_conn_reaps_orphaned_embedding",
        "limit": 50
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 4.363,
      "p95_ms": 12.463,
      "mean_ms": 5.909,
      "min_ms": 3.948,
      "max_ms": 17.206,
      "samples_ms": [
        5.382,
        12.22,
        17.206,
        4.363,
        4.344,
        4.026,
        4.031,
        4.3,
        4.147,
        12.463,
        4.092,
        3.948,
        4.139,
        4.202,
        4.608,
        4.735,
        4.599,
        5.521,
        4.913,
        4.933
      ]
    },
    {
      "tool": "get_callees",
      "args": {
        "name": "test_decay_with_conn_reaps_orphaned_embedding",
        "limit": 50
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 4.04,
      "p95_ms": 16.374,
      "mean_ms": 5.755,
      "min_ms": 3.7,
      "max_ms": 18.897,
      "samples_ms": [
        4.411,
        18.897,
        5.515,
        4.085,
        3.766,
        3.758,
        3.889,
        3.741,
        3.736,
        3.7,
        3.794,
        3.763,
        4.04,
        4.33,
        16.374,
        4.102,
        3.811,
        4.133,
        4.467,
        10.787
      ]
    },
    {
      "tool": "impact_analysis",
      "args": {
        "name": "str",
        "depth": 3,
        "limit": 100
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 5.475,
      "p95_ms": 7.52,
      "mean_ms": 6.039,
      "min_ms": 4.644,
      "max_ms": 15.479,
      "samples_ms": [
        6.718,
        7.52,
        5.502,
        4.997,
        5.25,
        5.909,
        5.208,
        6.055,
        5.784,
        4.911,
        5.236,
        15.479,
        5.992,
        5.538,
        4.644,
        4.652,
        4.924,
        5.19,
        5.785,
        5.475
      ]
    },
    {
      "tool": "path",
      "args": {
        "from_pattern": "status",
        "to_pattern": "createStatusBarItem",
        "max_depth": 4
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 436.987,
      "p95_ms": 450.416,
      "mean_ms": 438.22,
      "min_ms": 433.166,
      "max_ms": 454.184,
      "samples_ms": [
        436.371,
        438.002,
        435.459,
        435.334,
        437.03,
        437.876,
        438.224,
        436.879,
        437.796,
        435.554,
        436.693,
        437.259,
        436.153,
        437.08,
        436.987,
        433.166,
        437.732,
        436.211,
        450.416,
        454.184
      ]
    },
    {
      "tool": "explore",
      "args": {
        "query": "test_decay_with_conn_reaps_orphaned_embedding"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1030.568,
      "p95_ms": 1070.71,
      "mean_ms": 1045.386,
      "min_ms": 1022.576,
      "max_ms": 1206.888,
      "samples_ms": [
        1048.911,
        1030.239,
        1070.71,
        1206.888,
        1065.06,
        1034.076,
        1044.46,
        1041.871,
        1042.586,
        1035.087,
        1030.568,
        1027.989,
        1027.189,
        1025.423,
        1040.795,
        1028.657,
        1028.054,
        1027.292,
        1029.297,
        1022.576
      ]
    },
    {
      "tool": "semantic_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 10
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 32.439,
      "p95_ms": 34.001,
      "mean_ms": 33.198,
      "min_ms": 26.675,
      "max_ms": 62.907,
      "samples_ms": [
        32.038,
        32.066,
        30.271,
        30.609,
        32.924,
        33.631,
        33.261,
        32.822,
        62.907,
        30.187,
        29.8,
        32.748,
        33.002,
        33.04,
        34.001,
        26.675,
        29.494,
        28.885,
        32.439,
        33.151
      ]
    },
    {
      "tool": "search_symbols",
      "args": {
        "pattern": "test*"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 8.779,
      "p95_ms": 34.95,
      "mean_ms": 12.59,
      "min_ms": 7.545,
      "max_ms": 43.373,
      "samples_ms": [
        13.76,
        11.911,
        34.95,
        8.523,
        15.173,
        8.445,
        8.515,
        8.303,
        7.545,
        8.499,
        8.132,
        7.573,
        8.779,
        9.43,
        10.206,
        9.723,
        10.044,
        10.203,
        43.373,
        8.708
      ]
    },
    {
      "tool": "repo_map",
      "args": {},
      "errors": [],
      "calls": 20,
      "p50_ms": 29.107,
      "p95_ms": 33.156,
      "mean_ms": 30.143,
      "min_ms": 24.294,
      "max_ms": 49.835,
      "samples_ms": [
        26.451,
        25.712,
        24.294,
        29.107,
        31.192,
        32.856,
        32.957,
        24.873,
        25.685,
        28.577,
        30.304,
        32.806,
        31.722,
        33.156,
        26.343,
        26.736,
        27.652,
        49.835,
        30.419,
        32.188
      ]
    },
    {
      "tool": "file_api",
      "args": {
        "path": ".github/scripts/bench_compare.py",
        "repo": "cairn"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3.633,
      "p95_ms": 7.5,
      "mean_ms": 5.236,
      "min_ms": 2.839,
      "max_ms": 24.379,
      "samples_ms": [
        7.5,
        7.151,
        7.002,
        4.65,
        4.51,
        3.299,
        3.662,
        2.839,
        3.408,
        24.379,
        3.782,
        3.983,
        3.588,
        3.546,
        3.611,
        3.499,
        3.381,
        3.633,
        3.625,
        3.677
      ]
    },
    {
      "tool": "cross_repo_deps",
      "args": {
        "repo": "cairn"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 10.714,
      "p95_ms": 21.422,
      "mean_ms": 11.931,
      "min_ms": 9.452,
      "max_ms": 23.371,
      "samples_ms": [
        21.422,
        11.624,
        11.122,
        10.898,
        10.756,
        10.436,
        10.796,
        23.371,
        10.332,
        10.498,
        10.201,
        9.452,
        10.659,
        9.891,
        10.714,
        10.229,
        9.501,
        10.969,
        13.666,
        12.085
      ]
    },
    {
      "tool": "visualize_graph",
      "args": {
        "scope": "symbol",
        "symbol": "str",
        "depth": 2
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3.271,
      "p95_ms": 16.474,
      "mean_ms": 5.396,
      "min_ms": 2.692,
      "max_ms": 29.706,
      "samples_ms": [
        5.469,
        4.154,
        29.706,
        3.358,
        3.168,
        3.172,
        3.382,
        3.011,
        4.307,
        3.776,
        3.446,
        3.271,
        3.084,
        3.297,
        3.013,
        3.064,
        2.986,
        3.085,
        16.474,
        2.692
      ]
    },
    {
      "tool": "get_compass",
      "args": {
        "module": "graph"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.436,
      "p95_ms": 0.498,
      "mean_ms": 0.425,
      "min_ms": 0.287,
      "max_ms": 0.508,
      "samples_ms": [
        0.498,
        0.508,
        0.47,
        0.475,
        0.476,
        0.492,
        0.468,
        0.465,
        0.473,
        0.47,
        0.436,
        0.319,
        0.287,
        0.353,
        0.371,
        0.403,
        0.384,
        0.383,
        0.386,
        0.385
      ]
    },
    {
      "tool": "search_knowledge",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 10
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.47,
      "p95_ms": 0.53,
      "mean_ms": 0.476,
      "min_ms": 0.446,
      "max_ms": 0.563,
      "samples_ms": [
        0.475,
        0.563,
        0.494,
        0.473,
        0.47,
        0.48,
        0.48,
        0.451,
        0.446,
        0.45,
        0.447,
        0.453,
        0.53,
        0.49,
        0.477,
        0.47,
        0.472,
        0.47,
        0.467,
        0.469
      ]
    },
    {
      "tool": "ask_compass",
      "args": {
        "query": "how does the build resolve call edges"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 7.134,
      "p95_ms": 10.733,
      "mean_ms": 8.215,
      "min_ms": 6.573,
      "max_ms": 10.867,
      "samples_ms": [
        6.573,
        6.795,
        9.985,
        6.823,
        10.19,
        6.891,
        10.188,
        7.039,
        10.867,
        6.923,
        10.733,
        6.913,
        8.434,
        7.134,
        9.459,
        6.673,
        9.231,
        8.583,
        7.036,
        7.834
      ]
    },
    {
      "tool": "trace_flow",
      "args": {
        "entry": "str"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3.318,
      "p95_ms": 14.61,
      "mean_ms": 4.721,
      "min_ms": 2.73,
      "max_ms": 21.296,
      "samples_ms": [
        3.518,
        2.979,
        3.124,
        2.901,
        14.61,
        3.378,
        3.361,
        3.318,
        3.191,
        3.227,
        3.375,
        3.441,
        3.4,
        3.287,
        3.289,
        3.346,
        3.378,
        21.296,
        3.264,
        2.73
      ]
    },
    {
      "tool": "generate_flow",
      "args": {
        "entry": "str"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.389,
      "p95_ms": 1.793,
      "mean_ms": 1.477,
      "min_ms": 1.251,
      "max_ms": 1.851,
      "samples_ms": [
        1.709,
        1.793,
        1.756,
        1.668,
        1.851,
        1.553,
        1.596,
        1.546,
        1.433,
        1.531,
        1.389,
        1.333,
        1.304,
        1.277,
        1.251,
        1.274,
        1.286,
        1.284,
        1.355,
        1.341
      ]
    },
    {
      "tool": "federated_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 20
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.153,
      "p95_ms": 0.173,
      "mean_ms": 0.158,
      "min_ms": 0.146,
      "max_ms": 0.191,
      "samples_ms": [
        0.165,
        0.191,
        0.166,
        0.156,
        0.153,
        0.156,
        0.155,
        0.15,
        0.146,
        0.151,
        0.148,
        0.173,
        0.152,
        0.151,
        0.161,
        0.167,
        0.153,
        0.156,
        0.152,
        0.152
      ]
    },
    {
      "tool": "knowledge_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 20
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 5.053,
      "p95_ms": 17.944,
      "mean_ms": 18.836,
      "min_ms": 4.358,
      "max_ms": 242.579,
      "samples_ms": [
        242.579,
        7.302,
        5.818,
        5.675,
        13.774,
        13.594,
        4.772,
        4.527,
        4.358,
        9.415,
        4.8,
        4.863,
        4.799,
        5.043,
        4.903,
        17.944,
        6.533,
        6.316,
        4.653,
        5.053
      ]
    },
    {
      "tool": "trace_workflow",
      "args": {
        "ref": "nonexistent-baseline-probe"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.584,
      "p95_ms": 0.768,
      "mean_ms": 0.631,
      "min_ms": 0.508,
      "max_ms": 0.797,
      "samples_ms": [
        0.797,
        0.741,
        0.724,
        0.751,
        0.768,
        0.755,
        0.729,
        0.665,
        0.593,
        0.584,
        0.602,
        0.56,
        0.577,
        0.53,
        0.525,
        0.579,
        0.551,
        0.508,
        0.55,
        0.535
      ]
    },
    {
      "tool": "recall_memory",
      "args": {
        "query": "str"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 2.336,
      "p95_ms": 2.692,
      "mean_ms": 2.34,
      "min_ms": 1.784,
      "max_ms": 2.737,
      "samples_ms": [
        2.737,
        2.646,
        2.672,
        2.692,
        2.385,
        2.464,
        2.664,
        2.462,
        2.593,
        2.583,
        2.261,
        2.336,
        2.277,
        2.262,
        2.197,
        1.873,
        2.078,
        1.965,
        1.784,
        1.873
      ]
    },
    {
      "tool": "record_memory",
      "args": {
        "type": "pattern",
        "title": "baseline probe cairn rec 20",
        "body": "rust-migration baseline probe memory"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 16.386,
      "p95_ms": 231.438,
      "mean_ms": 39.376,
      "min_ms": 7.674,
      "max_ms": 256.973,
      "samples_ms": [
        7.674,
        8.664,
        8.071,
        9.314,
        256.973,
        32.62,
        19.045,
        231.438,
        25.056,
        22.343,
        23.027,
        19.581,
        18.505,
        18.354,
        13.03,
        16.324,
        16.386,
        13.045,
        13.351,
        14.708
      ]
    },
    {
      "tool": "wiki_generate",
      "args": {
        "repo": "cairn",
        "pages": 5,
        "force": true
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 148.045,
      "p95_ms": 162.429,
      "mean_ms": 146.342,
      "min_ms": 129.781,
      "max_ms": 166.991,
      "samples_ms": [
        166.991,
        137.114,
        148.212,
        151.223,
        129.781,
        146.676,
        142.729,
        148.663,
        148.077,
        150.153,
        140.362,
        150.948,
        148.106,
        162.429,
        133.673,
        139.465,
        148.045,
        148.674,
        146.815,
        138.702
      ]
    },
    {
      "tool": "knowledge_add",
      "args": {
        "title": "baseline probe cairn add 20",
        "body": "rust-migration baseline probe document",
        "doc_type": "decision"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3.654,
      "p95_ms": 15.956,
      "mean_ms": 5.055,
      "min_ms": 3.07,
      "max_ms": 20.056,
      "samples_ms": [
        3.907,
        3.654,
        4.705,
        3.517,
        3.865,
        3.785,
        3.54,
        4.143,
        3.939,
        4.027,
        20.056,
        3.436,
        3.24,
        3.144,
        3.143,
        3.099,
        3.126,
        3.07,
        15.956,
        3.756
      ]
    },
    {
      "tool": "knowledge_delete",
      "args": {},
      "errors": [],
      "calls": 20,
      "p50_ms": 6.629,
      "p95_ms": 9.411,
      "mean_ms": 6.984,
      "min_ms": 5.932,
      "max_ms": 9.559,
      "samples_ms": [
        6.439,
        6.573,
        6.522,
        6.353,
        6.799,
        6.394,
        7.451,
        6.679,
        7.284,
        5.992,
        8.119,
        5.932,
        6.629,
        6.139,
        6.001,
        6.725,
        9.559,
        6.874,
        7.811,
        9.411
      ]
    },
    {
      "tool": "knowledge_status",
      "args": {
        "new_status": "superseded"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 10.17,
      "p95_ms": 20.118,
      "mean_ms": 12.176,
      "min_ms": 8.173,
      "max_ms": 20.419,
      "samples_ms": [
        14.326,
        8.567,
        8.173,
        8.545,
        20.419,
        9.031,
        9.161,
        10.02,
        17.463,
        11.242,
        11.632,
        16.647,
        10.005,
        10.17,
        16.572,
        8.873,
        9.354,
        11.918,
        20.118,
        11.291
      ]
    }
  ],
  "live_store_reference": {
    "db_path": "/Users/lnmtan/.cairn/79428b9d734aac21/.kg",
    "db_bytes": 91181056,
    "symbols": 13730,
    "edges": 99027,
    "transitive_edges": 0
  }
}
```

#### yarl — python

```json
{
  "schema": "cairn-rust-baseline/1",
  "suite": "rust-migration-baseline",
  "impl": "python",
  "label": "yarl",
  "timestamp": "2026-10-24T16:38:42.397420+00:00",
  "harness": "scripts/rust_migration/baseline.py",
  "reproduce": ".venv/bin/python scripts/rust_migration/baseline.py --workspace /Users/lnmtan/Projects/others/cairn/benchmarks/datasource/t2/yarl --label yarl --repeats 20",
  "dataset": {
    "name": "benchmark-datasource",
    "version": "DS-v1",
    "tree_hash": "65e3df398b96f2cd31230ee17765fc01b0bc603e4e27914f3c31f41f061f87bb",
    "identity_size": 300
  },
  "cairn_version": "0.23.0",
  "machine_profile": {
    "arch": "arm64",
    "cpu": "arm",
    "cpu_count": 12,
    "os": "macOS-27.0.1-arm64-arm-64bit-Mach-O",
    "runner_class": "reference-local"
  },
  "method": {
    "build": "build_graph(workspace, db) + build_dataflow_index (the `cairn build` shape)",
    "closure": "build_transitive_closure(conn) timed; WAL sidecar polled every 0.005s, with the post-commit sidecar size as the single-burst floor for the peak",
    "latency": "per-tool tools/call over streamable HTTP (keep-alive), 20 timed calls each after 1 untimed warmup; write tools re-create their fixture untimed per call",
    "rss": "ps -o rss= on the daemon pid every 0.2s",
    "env_pins": {
      "CAIRN_EMBED_BACKEND": "hash",
      "CAIRN_RERANK": "0"
    },
    "daemon": {
      "transport": "streamable-http",
      "stateless": false,
      "host": "127.0.0.1"
    },
    "knowledge_bundle_seeded_files": 0
  },
  "corpus": {
    "workspace": "/Users/lnmtan/Projects/others/cairn/benchmarks/datasource/t2/yarl",
    "identity_method": "tree_hash",
    "tree_hash": "b2ac9f50845b86bdc14388365490e714dad5cb57a0a4896e8879fc9e8745b974",
    "py_files": 24,
    "py_lines": 9848,
    "py_bytes": 311104
  },
  "build": {
    "seconds": 0.319,
    "dataflow_seconds": 0.102,
    "dataflow_symbols": 899,
    "repos": 1,
    "files": 24,
    "symbols": 1090,
    "edges": 4112,
    "imports": 181,
    "skipped": 0,
    "resolution": {
      "exact": 1388,
      "ambiguous": 213,
      "unresolved": 1445
    },
    "lsp_upgraded": 0,
    "scip_edges": null,
    "parse_errors": 0
  },
  "closure": {
    "seconds": 0.034,
    "rows": 12467,
    "peak_wal_bytes": 2793392,
    "sampled_peak_wal_bytes": 997072,
    "wal_end_bytes": 2793392,
    "db_bytes": 5521408
  },
  "embed": {
    "seconds": 0.052,
    "backend_effective": "hash",
    "embedded": 1090,
    "total": 1090
  },
  "daemon": {
    "impl": "python",
    "boot_seconds": 0.601,
    "rss": {
      "peak_kb": 93392,
      "final_kb": 93392
    }
  },
  "tools": [
    {
      "tool": "find_definition",
      "args": {
        "name": "test_quote_space",
        "limit": 5
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 5.088,
      "p95_ms": 6.068,
      "mean_ms": 5.265,
      "min_ms": 4.997,
      "max_ms": 6.18,
      "samples_ms": [
        6.18,
        6.068,
        5.619,
        5.42,
        5.466,
        5.336,
        5.161,
        5.142,
        5.094,
        5.088,
        5.064,
        4.997,
        5.048,
        5.329,
        5.072,
        5.016,
        5.073,
        5.038,
        5.055,
        5.041
      ]
    },
    {
      "tool": "get_callers",
      "args": {
        "name": "test_quote_space",
        "limit": 50
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 6.439,
      "p95_ms": 6.899,
      "mean_ms": 6.55,
      "min_ms": 6.301,
      "max_ms": 7.784,
      "samples_ms": [
        6.35,
        6.419,
        6.406,
        6.439,
        6.388,
        6.393,
        6.381,
        6.728,
        6.371,
        6.451,
        6.652,
        6.454,
        7.784,
        6.899,
        6.642,
        6.301,
        6.524,
        6.546,
        6.434,
        6.441
      ]
    },
    {
      "tool": "get_callees",
      "args": {
        "name": "test_quote_space",
        "limit": 50
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 6.389,
      "p95_ms": 6.567,
      "mean_ms": 6.411,
      "min_ms": 6.242,
      "max_ms": 6.65,
      "samples_ms": [
        6.49,
        6.349,
        6.65,
        6.549,
        6.321,
        6.445,
        6.457,
        6.567,
        6.359,
        6.389,
        6.316,
        6.398,
        6.482,
        6.242,
        6.304,
        6.317,
        6.299,
        6.313,
        6.463,
        6.517
      ]
    },
    {
      "tool": "impact_analysis",
      "args": {
        "name": "str",
        "depth": 3,
        "limit": 100
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 7.925,
      "p95_ms": 8.729,
      "mean_ms": 8.044,
      "min_ms": 7.811,
      "max_ms": 9.185,
      "samples_ms": [
        7.962,
        9.185,
        8.265,
        7.991,
        7.906,
        7.908,
        7.865,
        7.974,
        7.811,
        8.729,
        7.815,
        7.9,
        8.107,
        7.853,
        7.925,
        7.812,
        8.065,
        7.973,
        7.935,
        7.908
      ]
    },
    {
      "tool": "path",
      "args": {
        "from_pattern": "test_normalize_path",
        "to_pattern": "test_normalize_path",
        "max_depth": 4
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 6.041,
      "p95_ms": 7.021,
      "mean_ms": 6.245,
      "min_ms": 5.768,
      "max_ms": 7.351,
      "samples_ms": [
        6.168,
        6.87,
        6.954,
        7.021,
        7.351,
        6.679,
        6.069,
        6.083,
        6.401,
        6.041,
        5.911,
        5.932,
        5.85,
        5.983,
        5.865,
        5.822,
        6.188,
        5.768,
        5.972,
        5.975
      ]
    },
    {
      "tool": "explore",
      "args": {
        "query": "test_quote_space"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 12.664,
      "p95_ms": 13.207,
      "mean_ms": 12.705,
      "min_ms": 12.346,
      "max_ms": 13.814,
      "samples_ms": [
        12.88,
        12.63,
        12.709,
        12.898,
        12.804,
        12.771,
        12.612,
        12.493,
        13.814,
        13.207,
        12.412,
        12.404,
        12.381,
        12.505,
        12.506,
        12.67,
        12.697,
        12.687,
        12.664,
        12.346
      ]
    },
    {
      "tool": "semantic_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 10
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 9.347,
      "p95_ms": 9.717,
      "mean_ms": 9.448,
      "min_ms": 9.227,
      "max_ms": 10.221,
      "samples_ms": [
        9.294,
        9.401,
        9.318,
        9.324,
        9.496,
        9.509,
        9.567,
        10.221,
        9.578,
        9.344,
        9.395,
        9.342,
        9.347,
        9.261,
        9.717,
        9.28,
        9.299,
        9.521,
        9.227,
        9.513
      ]
    },
    {
      "tool": "search_symbols",
      "args": {
        "pattern": "test*"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 7.401,
      "p95_ms": 8.033,
      "mean_ms": 7.518,
      "min_ms": 7.245,
      "max_ms": 8.813,
      "samples_ms": [
        7.341,
        7.366,
        7.563,
        7.371,
        7.414,
        7.28,
        7.401,
        7.288,
        7.578,
        8.813,
        7.716,
        8.033,
        7.392,
        7.484,
        7.5,
        7.498,
        7.307,
        7.44,
        7.328,
        7.245
      ]
    },
    {
      "tool": "repo_map",
      "args": {},
      "errors": [],
      "calls": 20,
      "p50_ms": 8.2,
      "p95_ms": 8.421,
      "mean_ms": 8.259,
      "min_ms": 8.049,
      "max_ms": 9.349,
      "samples_ms": [
        8.234,
        8.328,
        8.166,
        8.277,
        8.1,
        8.147,
        8.049,
        8.2,
        8.202,
        8.141,
        8.22,
        8.157,
        8.176,
        8.421,
        8.085,
        9.349,
        8.219,
        8.194,
        8.288,
        8.232
      ]
    },
    {
      "tool": "file_api",
      "args": {
        "path": "tests/test_cache.py",
        "repo": "corpus"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 5.439,
      "p95_ms": 5.652,
      "mean_ms": 5.482,
      "min_ms": 5.359,
      "max_ms": 5.679,
      "samples_ms": [
        5.405,
        5.531,
        5.463,
        5.474,
        5.652,
        5.415,
        5.526,
        5.429,
        5.593,
        5.613,
        5.428,
        5.442,
        5.397,
        5.679,
        5.397,
        5.439,
        5.572,
        5.386,
        5.437,
        5.359
      ]
    },
    {
      "tool": "cross_repo_deps",
      "args": {
        "repo": "corpus"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 5.269,
      "p95_ms": 5.673,
      "mean_ms": 5.338,
      "min_ms": 4.988,
      "max_ms": 6.498,
      "samples_ms": [
        5.265,
        5.293,
        5.278,
        5.372,
        5.44,
        5.264,
        5.63,
        6.498,
        5.673,
        5.271,
        5.267,
        5.374,
        5.318,
        5.074,
        4.988,
        5.039,
        5.204,
        5.094,
        5.269,
        5.153
      ]
    },
    {
      "tool": "visualize_graph",
      "args": {
        "scope": "symbol",
        "symbol": "str",
        "depth": 2
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 5.201,
      "p95_ms": 5.297,
      "mean_ms": 5.208,
      "min_ms": 5.042,
      "max_ms": 5.542,
      "samples_ms": [
        5.079,
        5.139,
        5.134,
        5.542,
        5.259,
        5.158,
        5.219,
        5.178,
        5.201,
        5.286,
        5.119,
        5.21,
        5.265,
        5.297,
        5.042,
        5.178,
        5.26,
        5.238,
        5.228,
        5.137
      ]
    },
    {
      "tool": "get_compass",
      "args": {
        "module": "graph"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.951,
      "p95_ms": 0.984,
      "mean_ms": 0.971,
      "min_ms": 0.935,
      "max_ms": 1.286,
      "samples_ms": [
        0.949,
        0.951,
        0.978,
        0.953,
        0.957,
        0.951,
        0.984,
        0.95,
        0.943,
        0.957,
        0.95,
        0.964,
        0.94,
        0.955,
        0.935,
        0.975,
        0.965,
        1.286,
        0.936,
        0.95
      ]
    },
    {
      "tool": "search_knowledge",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 10
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.963,
      "p95_ms": 1.187,
      "mean_ms": 1.045,
      "min_ms": 0.942,
      "max_ms": 2.243,
      "samples_ms": [
        0.967,
        0.967,
        0.963,
        0.957,
        0.959,
        0.971,
        0.943,
        2.243,
        1.122,
        0.981,
        0.987,
        0.952,
        0.99,
        1.187,
        0.949,
        0.964,
        0.952,
        0.959,
        0.945,
        0.942
      ]
    },
    {
      "tool": "ask_compass",
      "args": {
        "query": "how does the build resolve call edges"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.909,
      "p95_ms": 2.154,
      "mean_ms": 1.943,
      "min_ms": 1.855,
      "max_ms": 2.2,
      "samples_ms": [
        2.154,
        1.952,
        1.99,
        1.907,
        1.954,
        2.2,
        1.951,
        1.925,
        1.936,
        1.899,
        1.958,
        1.892,
        1.909,
        1.903,
        1.892,
        1.894,
        1.915,
        1.883,
        1.895,
        1.855
      ]
    },
    {
      "tool": "trace_flow",
      "args": {
        "entry": "str"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.903,
      "p95_ms": 1.109,
      "mean_ms": 0.929,
      "min_ms": 0.879,
      "max_ms": 1.159,
      "samples_ms": [
        0.917,
        1.159,
        0.933,
        0.927,
        0.96,
        0.887,
        0.932,
        0.884,
        0.897,
        0.901,
        0.898,
        0.88,
        0.89,
        0.907,
        1.109,
        0.914,
        0.903,
        0.879,
        0.886,
        0.915
      ]
    },
    {
      "tool": "generate_flow",
      "args": {
        "entry": "str"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.188,
      "p95_ms": 1.392,
      "mean_ms": 1.221,
      "min_ms": 1.147,
      "max_ms": 1.561,
      "samples_ms": [
        1.255,
        1.202,
        1.182,
        1.188,
        1.187,
        1.265,
        1.561,
        1.186,
        1.193,
        1.147,
        1.237,
        1.168,
        1.198,
        1.148,
        1.167,
        1.154,
        1.19,
        1.188,
        1.392,
        1.203
      ]
    },
    {
      "tool": "federated_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 20
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.823,
      "p95_ms": 0.995,
      "mean_ms": 0.849,
      "min_ms": 0.806,
      "max_ms": 1.062,
      "samples_ms": [
        0.819,
        0.84,
        0.85,
        0.836,
        0.826,
        0.823,
        0.82,
        0.832,
        0.936,
        0.995,
        0.831,
        0.822,
        0.81,
        1.062,
        0.83,
        0.814,
        0.811,
        0.808,
        0.806,
        0.813
      ]
    },
    {
      "tool": "knowledge_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 20
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.959,
      "p95_ms": 1.04,
      "mean_ms": 0.976,
      "min_ms": 0.93,
      "max_ms": 1.218,
      "samples_ms": [
        1.04,
        0.979,
        0.966,
        0.949,
        0.944,
        0.937,
        0.938,
        0.93,
        1.218,
        0.95,
        0.933,
        0.954,
        0.98,
        0.981,
        0.971,
        0.958,
        0.975,
        0.982,
        0.959,
        0.969
      ]
    },
    {
      "tool": "trace_workflow",
      "args": {
        "ref": "nonexistent-baseline-probe"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.0,
      "p95_ms": 1.034,
      "mean_ms": 1.02,
      "min_ms": 0.981,
      "max_ms": 1.331,
      "samples_ms": [
        1.018,
        1.0,
        1.019,
        1.01,
        1.331,
        0.999,
        1.02,
        1.022,
        0.994,
        1.003,
        1.034,
        1.012,
        0.99,
        0.991,
        1.001,
        1.0,
        0.994,
        0.99,
        0.984,
        0.981
      ]
    },
    {
      "tool": "recall_memory",
      "args": {
        "query": "str"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.098,
      "p95_ms": 1.318,
      "mean_ms": 1.122,
      "min_ms": 1.072,
      "max_ms": 1.361,
      "samples_ms": [
        1.112,
        1.361,
        1.093,
        1.1,
        1.097,
        1.094,
        1.095,
        1.109,
        1.116,
        1.1,
        1.104,
        1.106,
        1.072,
        1.098,
        1.106,
        1.092,
        1.072,
        1.097,
        1.318,
        1.093
      ]
    },
    {
      "tool": "record_memory",
      "args": {
        "type": "pattern",
        "title": "baseline probe corpus rec 20",
        "body": "rust-migration baseline probe memory"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 34.814,
      "p95_ms": 48.637,
      "mean_ms": 43.472,
      "min_ms": 20.255,
      "max_ms": 224.204,
      "samples_ms": [
        20.255,
        36.579,
        27.556,
        224.204,
        40.047,
        23.196,
        30.469,
        34.814,
        26.928,
        27.499,
        20.888,
        34.092,
        38.666,
        41.779,
        40.241,
        43.009,
        42.046,
        32.414,
        36.12,
        48.637
      ]
    },
    {
      "tool": "wiki_generate",
      "args": {
        "repo": "corpus",
        "pages": 5,
        "force": true
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 55.473,
      "p95_ms": 68.655,
      "mean_ms": 54.044,
      "min_ms": 33.643,
      "max_ms": 69.467,
      "samples_ms": [
        33.643,
        35.091,
        39.801,
        62.279,
        43.295,
        40.53,
        42.379,
        48.057,
        55.183,
        55.473,
        58.714,
        65.291,
        64.313,
        59.611,
        52.872,
        57.287,
        62.559,
        68.655,
        69.467,
        66.376
      ]
    },
    {
      "tool": "knowledge_add",
      "args": {
        "title": "baseline probe corpus add 20",
        "body": "rust-migration baseline probe document",
        "doc_type": "decision"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 6.071,
      "p95_ms": 7.446,
      "mean_ms": 6.963,
      "min_ms": 4.964,
      "max_ms": 23.075,
      "samples_ms": [
        7.446,
        4.964,
        5.251,
        6.17,
        6.125,
        6.752,
        7.232,
        5.466,
        5.876,
        6.609,
        6.469,
        6.851,
        5.274,
        5.702,
        6.621,
        23.075,
        6.071,
        5.821,
        5.751,
        5.726
      ]
    },
    {
      "tool": "knowledge_delete",
      "args": {},
      "errors": [],
      "calls": 20,
      "p50_ms": 12.506,
      "p95_ms": 17.754,
      "mean_ms": 13.323,
      "min_ms": 11.99,
      "max_ms": 18.101,
      "samples_ms": [
        17.754,
        12.506,
        13.417,
        12.332,
        14.537,
        12.739,
        12.765,
        13.985,
        12.045,
        12.109,
        12.374,
        12.218,
        11.99,
        12.411,
        18.101,
        13.141,
        12.495,
        13.431,
        12.426,
        13.68
      ]
    },
    {
      "tool": "knowledge_status",
      "args": {
        "new_status": "superseded"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 14.207,
      "p95_ms": 27.214,
      "mean_ms": 15.621,
      "min_ms": 10.949,
      "max_ms": 28.636,
      "samples_ms": [
        14.312,
        13.662,
        14.569,
        27.214,
        14.006,
        14.207,
        14.404,
        28.636,
        14.848,
        14.637,
        16.704,
        18.478,
        13.072,
        13.524,
        13.041,
        10.949,
        12.144,
        19.655,
        11.51,
        12.842
      ]
    }
  ],
  "live_store_reference": {
    "db_path": "/Users/lnmtan/.cairn/79428b9d734aac21/.kg",
    "db_bytes": 91181056,
    "symbols": 13730,
    "edges": 99027,
    "transitive_edges": 0
  }
}
```

#### yarl — rust

```json
{
  "schema": "cairn-rust-baseline/1",
  "suite": "rust-migration-baseline",
  "impl": "rust",
  "label": "yarl",
  "timestamp": "2026-10-24T16:38:48.169343+00:00",
  "harness": "scripts/rust_migration/baseline.py",
  "reproduce": ".venv/bin/python scripts/rust_migration/baseline.py --impl rust --workspace /Users/lnmtan/Projects/others/cairn/benchmarks/datasource/t2/yarl --label yarl --repeats 20",
  "dataset": {
    "name": "benchmark-datasource",
    "version": "DS-v1",
    "tree_hash": "65e3df398b96f2cd31230ee17765fc01b0bc603e4e27914f3c31f41f061f87bb",
    "identity_size": 300
  },
  "cairn_version": "0.23.0",
  "machine_profile": {
    "arch": "arm64",
    "cpu": "arm",
    "cpu_count": 12,
    "os": "macOS-27.0.1-arm64-arm-64bit-Mach-O",
    "runner_class": "reference-local"
  },
  "method": {
    "build": "build_graph(workspace, db) + build_dataflow_index (the `cairn build` shape)",
    "closure": "build_transitive_closure(conn) timed; WAL sidecar polled every 0.005s, with the post-commit sidecar size as the single-burst floor for the peak",
    "latency": "per-tool tools/call over streamable HTTP (keep-alive), 20 timed calls each after 1 untimed warmup; write tools re-create their fixture untimed per call",
    "rss": "ps -o rss= on the daemon pid every 0.2s",
    "env_pins": {
      "CAIRN_EMBED_BACKEND": "hash",
      "CAIRN_RERANK": "0"
    },
    "daemon": {
      "transport": "streamable-http",
      "stateless": false,
      "host": "127.0.0.1"
    },
    "knowledge_bundle_seeded_files": 0,
    "store": "python build_graph + dataflow + hash embed (identical-stores doctrine); the rust daemon is the measured engine"
  },
  "corpus": {
    "workspace": "/Users/lnmtan/Projects/others/cairn/benchmarks/datasource/t2/yarl",
    "identity_method": "tree_hash",
    "tree_hash": "b2ac9f50845b86bdc14388365490e714dad5cb57a0a4896e8879fc9e8745b974",
    "py_files": 24,
    "py_lines": 9848,
    "py_bytes": 311104
  },
  "build": {
    "seconds": 0.551,
    "dataflow_seconds": 0.105,
    "dataflow_symbols": 899,
    "repos": 1,
    "files": 24,
    "symbols": 1090,
    "edges": 4112,
    "imports": 181,
    "skipped": 0,
    "resolution": {
      "exact": 1388,
      "ambiguous": 213,
      "unresolved": 1445
    },
    "lsp_upgraded": 0,
    "scip_edges": null,
    "parse_errors": 0
  },
  "closure": {
    "seconds": 0.035,
    "rows": 12467,
    "peak_wal_bytes": 2793392,
    "sampled_peak_wal_bytes": 889952,
    "wal_end_bytes": 2793392,
    "db_bytes": 5529600
  },
  "embed": {
    "seconds": 0.053,
    "backend_effective": "hash",
    "embedded": 1090,
    "total": 1090
  },
  "daemon": {
    "impl": "rust",
    "boot_seconds": 0.296,
    "rss": {
      "peak_kb": 27600,
      "final_kb": 25392
    }
  },
  "tools": [
    {
      "tool": "find_definition",
      "args": {
        "name": "_run",
        "limit": 5
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 8.037,
      "p95_ms": 20.603,
      "mean_ms": 10.216,
      "min_ms": 4.823,
      "max_ms": 45.275,
      "samples_ms": [
        12.193,
        20.603,
        7.696,
        8.231,
        7.929,
        8.021,
        7.607,
        8.37,
        8.073,
        8.225,
        9.025,
        9.522,
        8.803,
        8.037,
        45.275,
        6.203,
        4.969,
        4.947,
        5.762,
        4.823
      ]
    },
    {
      "tool": "get_callers",
      "args": {
        "name": "_run",
        "limit": 50
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 4.983,
      "p95_ms": 6.952,
      "mean_ms": 5.953,
      "min_ms": 4.524,
      "max_ms": 23.408,
      "samples_ms": [
        4.524,
        5.196,
        4.881,
        4.883,
        5.173,
        5.057,
        5.194,
        5.169,
        5.183,
        23.408,
        6.952,
        4.648,
        5.109,
        5.043,
        4.698,
        4.612,
        4.655,
        4.937,
        4.983,
        4.756
      ]
    },
    {
      "tool": "get_callees",
      "args": {
        "name": "_run",
        "limit": 50
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 4.24,
      "p95_ms": 16.122,
      "mean_ms": 6.198,
      "min_ms": 3.582,
      "max_ms": 22.716,
      "samples_ms": [
        5.325,
        4.701,
        22.716,
        6.183,
        4.513,
        4.084,
        4.086,
        4.155,
        4.11,
        4.204,
        4.766,
        4.502,
        4.196,
        7.082,
        16.122,
        3.85,
        3.582,
        3.82,
        7.718,
        4.24
      ]
    },
    {
      "tool": "impact_analysis",
      "args": {
        "name": "str",
        "depth": 3,
        "limit": 100
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 4.297,
      "p95_ms": 4.691,
      "mean_ms": 4.583,
      "min_ms": 2.98,
      "max_ms": 17.272,
      "samples_ms": [
        4.297,
        4.348,
        4.395,
        4.471,
        4.358,
        3.493,
        3.29,
        4.691,
        4.445,
        17.272,
        4.522,
        4.33,
        4.364,
        4.169,
        3.113,
        3.04,
        3.163,
        2.98,
        2.992,
        3.93
      ]
    },
    {
      "tool": "path",
      "args": {
        "from_pattern": "test_normalize_path",
        "to_pattern": "test_normalize_path",
        "max_depth": 4
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3.698,
      "p95_ms": 18.504,
      "mean_ms": 5.814,
      "min_ms": 3.074,
      "max_ms": 21.701,
      "samples_ms": [
        3.388,
        3.534,
        21.701,
        4.95,
        5.048,
        4.415,
        3.952,
        3.133,
        3.074,
        3.091,
        3.246,
        4.463,
        5.145,
        4.26,
        18.504,
        3.349,
        3.698,
        3.101,
        10.769,
        3.464
      ]
    },
    {
      "tool": "explore",
      "args": {
        "query": "_run"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 16.873,
      "p95_ms": 19.437,
      "mean_ms": 17.36,
      "min_ms": 16.481,
      "max_ms": 22.229,
      "samples_ms": [
        22.229,
        19.437,
        17.894,
        17.059,
        16.481,
        16.623,
        16.787,
        17.298,
        17.066,
        16.788,
        16.858,
        16.803,
        17.228,
        17.867,
        17.086,
        16.695,
        16.688,
        16.873,
        16.554,
        16.883
      ]
    },
    {
      "tool": "semantic_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 10
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 6.922,
      "p95_ms": 25.862,
      "mean_ms": 8.834,
      "min_ms": 4.854,
      "max_ms": 31.536,
      "samples_ms": [
        8.758,
        7.532,
        7.635,
        7.103,
        25.862,
        6.962,
        5.84,
        5.171,
        5.555,
        4.884,
        4.854,
        5.323,
        6.64,
        6.788,
        7.773,
        6.922,
        7.126,
        8.077,
        31.536,
        6.347
      ]
    },
    {
      "tool": "search_symbols",
      "args": {
        "pattern": "_run*"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3.281,
      "p95_ms": 9.872,
      "mean_ms": 4.553,
      "min_ms": 2.901,
      "max_ms": 21.918,
      "samples_ms": [
        3.474,
        3.683,
        3.558,
        3.512,
        3.39,
        3.396,
        4.257,
        21.918,
        3.121,
        3.025,
        3.221,
        3.091,
        2.901,
        3.009,
        9.872,
        3.281,
        3.316,
        2.961,
        3.04,
        3.028
      ]
    },
    {
      "tool": "repo_map",
      "args": {},
      "errors": [],
      "calls": 20,
      "p50_ms": 5.014,
      "p95_ms": 10.173,
      "mean_ms": 6.08,
      "min_ms": 4.012,
      "max_ms": 21.251,
      "samples_ms": [
        6.103,
        5.114,
        10.173,
        6.179,
        4.702,
        5.014,
        5.616,
        4.887,
        5.268,
        4.474,
        4.421,
        4.513,
        21.251,
        5.177,
        4.012,
        5.13,
        5.159,
        4.706,
        4.941,
        4.751
      ]
    },
    {
      "tool": "file_api",
      "args": {
        "path": "tests/test_cache.py",
        "repo": "corpus"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 2.423,
      "p95_ms": 3.737,
      "mean_ms": 3.366,
      "min_ms": 2.292,
      "max_ms": 19.486,
      "samples_ms": [
        2.485,
        2.568,
        2.566,
        2.835,
        19.486,
        2.516,
        3.737,
        2.423,
        2.302,
        2.563,
        2.45,
        2.33,
        2.334,
        2.292,
        2.377,
        2.395,
        2.429,
        2.388,
        2.42,
        2.415
      ]
    },
    {
      "tool": "cross_repo_deps",
      "args": {
        "repo": "corpus"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 2.631,
      "p95_ms": 4.881,
      "mean_ms": 4.008,
      "min_ms": 2.434,
      "max_ms": 16.704,
      "samples_ms": [
        2.58,
        2.434,
        2.528,
        2.629,
        2.681,
        2.628,
        2.566,
        2.534,
        2.631,
        2.566,
        2.537,
        3.923,
        16.704,
        4.356,
        4.338,
        4.8,
        4.881,
        4.13,
        4.598,
        4.113
      ]
    },
    {
      "tool": "visualize_graph",
      "args": {
        "scope": "symbol",
        "symbol": "str",
        "depth": 2
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3.469,
      "p95_ms": 4.618,
      "mean_ms": 3.965,
      "min_ms": 2.329,
      "max_ms": 17.459,
      "samples_ms": [
        3.437,
        3.379,
        3.469,
        3.677,
        17.459,
        3.991,
        3.555,
        3.629,
        3.477,
        3.822,
        4.088,
        4.618,
        3.561,
        2.524,
        2.549,
        2.39,
        2.592,
        2.39,
        2.374,
        2.329
      ]
    },
    {
      "tool": "get_compass",
      "args": {
        "module": "graph"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.222,
      "p95_ms": 0.256,
      "mean_ms": 0.224,
      "min_ms": 0.197,
      "max_ms": 0.259,
      "samples_ms": [
        0.222,
        0.208,
        0.197,
        0.214,
        0.221,
        0.228,
        0.233,
        0.256,
        0.226,
        0.223,
        0.208,
        0.259,
        0.249,
        0.226,
        0.228,
        0.211,
        0.209,
        0.221,
        0.221,
        0.223
      ]
    },
    {
      "tool": "search_knowledge",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 10
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.215,
      "p95_ms": 0.221,
      "mean_ms": 0.211,
      "min_ms": 0.187,
      "max_ms": 0.222,
      "samples_ms": [
        0.221,
        0.222,
        0.216,
        0.215,
        0.219,
        0.221,
        0.217,
        0.22,
        0.216,
        0.219,
        0.21,
        0.205,
        0.201,
        0.212,
        0.221,
        0.187,
        0.187,
        0.208,
        0.207,
        0.196
      ]
    },
    {
      "tool": "ask_compass",
      "args": {
        "query": "how does the build resolve call edges"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 4.141,
      "p95_ms": 9.469,
      "mean_ms": 4.932,
      "min_ms": 3.398,
      "max_ms": 10.792,
      "samples_ms": [
        4.686,
        3.998,
        4.141,
        3.727,
        3.398,
        10.792,
        3.851,
        4.435,
        3.957,
        3.496,
        9.066,
        4.281,
        4.3,
        4.351,
        3.712,
        4.468,
        9.469,
        3.782,
        3.964,
        4.759
      ]
    },
    {
      "tool": "trace_flow",
      "args": {
        "entry": "str"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 2.443,
      "p95_ms": 9.918,
      "mean_ms": 3.888,
      "min_ms": 2.312,
      "max_ms": 19.811,
      "samples_ms": [
        2.571,
        2.462,
        9.918,
        2.506,
        2.469,
        2.435,
        2.443,
        2.397,
        2.377,
        2.434,
        2.368,
        2.312,
        2.346,
        2.379,
        2.441,
        2.475,
        19.811,
        4.023,
        3.813,
        3.786
      ]
    },
    {
      "tool": "generate_flow",
      "args": {
        "entry": "str"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.239,
      "p95_ms": 1.584,
      "mean_ms": 1.314,
      "min_ms": 1.098,
      "max_ms": 1.601,
      "samples_ms": [
        1.601,
        1.584,
        1.55,
        1.515,
        1.489,
        1.334,
        1.391,
        1.306,
        1.306,
        1.204,
        1.493,
        1.201,
        1.149,
        1.119,
        1.098,
        1.152,
        1.219,
        1.239,
        1.153,
        1.182
      ]
    },
    {
      "tool": "federated_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 20
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.198,
      "p95_ms": 0.217,
      "mean_ms": 0.199,
      "min_ms": 0.173,
      "max_ms": 0.259,
      "samples_ms": [
        0.201,
        0.259,
        0.211,
        0.208,
        0.217,
        0.186,
        0.186,
        0.201,
        0.199,
        0.198,
        0.177,
        0.198,
        0.19,
        0.203,
        0.173,
        0.18,
        0.193,
        0.215,
        0.193,
        0.199
      ]
    },
    {
      "tool": "knowledge_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 20
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 5.998,
      "p95_ms": 27.198,
      "mean_ms": 17.015,
      "min_ms": 2.795,
      "max_ms": 210.305,
      "samples_ms": [
        2.795,
        210.305,
        6.652,
        8.777,
        5.267,
        5.595,
        5.726,
        5.317,
        6.182,
        6.007,
        6.041,
        6.21,
        6.009,
        27.198,
        6.055,
        4.738,
        4.699,
        5.108,
        5.616,
        5.998
      ]
    },
    {
      "tool": "trace_workflow",
      "args": {
        "ref": "nonexistent-baseline-probe"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.43,
      "p95_ms": 0.712,
      "mean_ms": 0.515,
      "min_ms": 0.348,
      "max_ms": 0.732,
      "samples_ms": [
        0.674,
        0.62,
        0.622,
        0.627,
        0.52,
        0.533,
        0.689,
        0.732,
        0.712,
        0.644,
        0.407,
        0.411,
        0.377,
        0.348,
        0.391,
        0.385,
        0.366,
        0.406,
        0.409,
        0.43
      ]
    },
    {
      "tool": "recall_memory",
      "args": {
        "query": "str"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.98,
      "p95_ms": 2.267,
      "mean_ms": 1.996,
      "min_ms": 1.643,
      "max_ms": 2.273,
      "samples_ms": [
        2.087,
        2.267,
        2.197,
        1.994,
        1.98,
        2.099,
        2.059,
        2.119,
        1.957,
        1.912,
        2.265,
        2.273,
        1.949,
        2.001,
        1.897,
        1.773,
        1.643,
        1.803,
        1.879,
        1.772
      ]
    },
    {
      "tool": "record_memory",
      "args": {
        "type": "pattern",
        "title": "baseline probe corpus rec 20",
        "body": "rust-migration baseline probe memory"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 9.436,
      "p95_ms": 12.674,
      "mean_ms": 9.895,
      "min_ms": 6.273,
      "max_ms": 13.19,
      "samples_ms": [
        6.273,
        8.825,
        9.264,
        9.088,
        8.973,
        8.522,
        9.788,
        11.528,
        9.061,
        10.045,
        11.349,
        9.27,
        10.246,
        11.295,
        12.674,
        10.036,
        10.568,
        8.47,
        9.436,
        13.19
      ]
    },
    {
      "tool": "wiki_generate",
      "args": {
        "repo": "corpus",
        "pages": 5,
        "force": true
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 38.956,
      "p95_ms": 64.215,
      "mean_ms": 47.989,
      "min_ms": 24.459,
      "max_ms": 262.33,
      "samples_ms": [
        26.737,
        25.05,
        24.459,
        24.874,
        28.515,
        25.847,
        28.905,
        28.876,
        28.337,
        262.33,
        64.215,
        51.934,
        45.304,
        41.975,
        38.956,
        40.392,
        42.321,
        43.518,
        42.147,
        45.08
      ]
    },
    {
      "tool": "knowledge_add",
      "args": {
        "title": "baseline probe corpus add 20",
        "body": "rust-migration baseline probe document",
        "doc_type": "decision"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 5.777,
      "p95_ms": 9.792,
      "mean_ms": 6.379,
      "min_ms": 4.631,
      "max_ms": 11.031,
      "samples_ms": [
        5.205,
        5.486,
        5.456,
        5.461,
        5.551,
        11.031,
        6.361,
        6.087,
        6.208,
        6.392,
        5.777,
        5.174,
        5.587,
        9.792,
        6.114,
        5.924,
        6.954,
        9.564,
        4.631,
        4.818
      ]
    },
    {
      "tool": "knowledge_delete",
      "args": {},
      "errors": [],
      "calls": 20,
      "p50_ms": 12.526,
      "p95_ms": 15.173,
      "mean_ms": 12.696,
      "min_ms": 9.692,
      "max_ms": 16.432,
      "samples_ms": [
        13.707,
        10.912,
        10.931,
        11.228,
        11.324,
        11.176,
        12.147,
        9.692,
        12.526,
        10.887,
        15.173,
        14.552,
        14.368,
        13.509,
        16.432,
        14.741,
        14.05,
        11.384,
        12.611,
        12.566
      ]
    },
    {
      "tool": "knowledge_status",
      "args": {
        "new_status": "superseded"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 13.949,
      "p95_ms": 28.948,
      "mean_ms": 16.769,
      "min_ms": 11.51,
      "max_ms": 32.938,
      "samples_ms": [
        13.376,
        15.319,
        32.938,
        15.572,
        15.601,
        16.146,
        28.948,
        15.1,
        11.51,
        12.457,
        24.578,
        13.949,
        13.916,
        21.632,
        13.234,
        12.781,
        19.63,
        12.868,
        12.937,
        12.896
      ]
    }
  ],
  "live_store_reference": {
    "db_path": "/Users/lnmtan/.cairn/79428b9d734aac21/.kg",
    "db_bytes": 91181056,
    "symbols": 13730,
    "edges": 99027,
    "transitive_edges": 0
  }
}
```

#### attrs — python

```json
{
  "schema": "cairn-rust-baseline/1",
  "suite": "rust-migration-baseline",
  "impl": "python",
  "label": "attrs",
  "timestamp": "2026-10-24T16:38:57.139071+00:00",
  "harness": "scripts/rust_migration/baseline.py",
  "reproduce": ".venv/bin/python scripts/rust_migration/baseline.py --workspace /Users/lnmtan/Projects/others/cairn/benchmarks/datasource/ds2/second-corpus/attrs-26.1.0 --label attrs --repeats 20",
  "dataset": {
    "name": "benchmark-datasource",
    "version": "DS-v1",
    "tree_hash": "65e3df398b96f2cd31230ee17765fc01b0bc603e4e27914f3c31f41f061f87bb",
    "identity_size": 300
  },
  "cairn_version": "0.23.0",
  "machine_profile": {
    "arch": "arm64",
    "cpu": "arm",
    "cpu_count": 12,
    "os": "macOS-27.0.1-arm64-arm-64bit-Mach-O",
    "runner_class": "reference-local"
  },
  "method": {
    "build": "build_graph(workspace, db) + build_dataflow_index (the `cairn build` shape)",
    "closure": "build_transitive_closure(conn) timed; WAL sidecar polled every 0.005s, with the post-commit sidecar size as the single-burst floor for the peak",
    "latency": "per-tool tools/call over streamable HTTP (keep-alive), 20 timed calls each after 1 untimed warmup; write tools re-create their fixture untimed per call",
    "rss": "ps -o rss= on the daemon pid every 0.2s",
    "env_pins": {
      "CAIRN_EMBED_BACKEND": "hash",
      "CAIRN_RERANK": "0"
    },
    "daemon": {
      "transport": "streamable-http",
      "stateless": false,
      "host": "127.0.0.1"
    },
    "knowledge_bundle_seeded_files": 0
  },
  "corpus": {
    "workspace": "/Users/lnmtan/Projects/others/cairn/benchmarks/datasource/ds2/second-corpus/attrs-26.1.0",
    "identity_method": "tree_hash",
    "tree_hash": "ad6eec778ba82da2ac4493676f990c6d155e7b0634900c36a54da0de2d515097",
    "py_files": 50,
    "py_lines": 19039,
    "py_bytes": 527216
  },
  "build": {
    "seconds": 0.461,
    "dataflow_seconds": 0.122,
    "dataflow_symbols": 894,
    "repos": 1,
    "files": 50,
    "symbols": 1722,
    "edges": 6239,
    "imports": 399,
    "skipped": 0,
    "resolution": {
      "exact": 1316,
      "ambiguous": 636,
      "unresolved": 2615
    },
    "lsp_upgraded": 0,
    "scip_edges": null,
    "parse_errors": 0
  },
  "closure": {
    "seconds": 0.037,
    "rows": 12539,
    "peak_wal_bytes": 2888152,
    "sampled_peak_wal_bytes": 869352,
    "wal_end_bytes": 2888152,
    "db_bytes": 7274496
  },
  "embed": {
    "seconds": 0.105,
    "backend_effective": "hash",
    "embedded": 1722,
    "total": 1722
  },
  "daemon": {
    "impl": "python",
    "boot_seconds": 0.603,
    "rss": {
      "peak_kb": 101776,
      "final_kb": 100352
    }
  },
  "tools": [
    {
      "tool": "find_definition",
      "args": {
        "name": "GT",
        "limit": 5
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 8.047,
      "p95_ms": 11.243,
      "mean_ms": 8.789,
      "min_ms": 7.857,
      "max_ms": 12.418,
      "samples_ms": [
        12.418,
        11.243,
        10.103,
        9.723,
        9.279,
        9.204,
        8.788,
        8.86,
        8.036,
        8.047,
        8.035,
        8.53,
        8.134,
        8.019,
        7.857,
        7.906,
        7.904,
        7.892,
        7.876,
        7.919
      ]
    },
    {
      "tool": "get_callers",
      "args": {
        "name": "GT",
        "limit": 50
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 9.266,
      "p95_ms": 9.648,
      "mean_ms": 9.324,
      "min_ms": 9.129,
      "max_ms": 10.184,
      "samples_ms": [
        10.184,
        9.353,
        9.274,
        9.287,
        9.197,
        9.31,
        9.648,
        9.269,
        9.214,
        9.279,
        9.171,
        9.143,
        9.129,
        9.241,
        9.223,
        9.21,
        9.266,
        9.494,
        9.333,
        9.264
      ]
    },
    {
      "tool": "get_callees",
      "args": {
        "name": "GT",
        "limit": 50
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 9.193,
      "p95_ms": 9.58,
      "mean_ms": 9.267,
      "min_ms": 9.069,
      "max_ms": 10.063,
      "samples_ms": [
        9.58,
        10.063,
        9.231,
        9.11,
        9.174,
        9.165,
        9.116,
        9.231,
        9.269,
        9.193,
        9.187,
        9.229,
        9.156,
        9.308,
        9.069,
        9.144,
        9.157,
        9.196,
        9.519,
        9.24
      ]
    },
    {
      "tool": "impact_analysis",
      "args": {
        "name": "ib",
        "depth": 3,
        "limit": 100
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 11.17,
      "p95_ms": 12.137,
      "mean_ms": 11.245,
      "min_ms": 10.902,
      "max_ms": 12.207,
      "samples_ms": [
        11.223,
        11.18,
        12.207,
        11.249,
        12.137,
        11.088,
        11.214,
        11.17,
        11.17,
        11.09,
        11.168,
        11.127,
        11.193,
        11.169,
        10.902,
        11.32,
        11.196,
        11.016,
        11.066,
        11.02
      ]
    },
    {
      "tool": "path",
      "args": {
        "from_pattern": "_config",
        "to_pattern": "set_run_validators",
        "max_depth": 4
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 9.135,
      "p95_ms": 9.394,
      "mean_ms": 9.199,
      "min_ms": 9.094,
      "max_ms": 9.906,
      "samples_ms": [
        9.906,
        9.105,
        9.166,
        9.144,
        9.183,
        9.106,
        9.135,
        9.394,
        9.226,
        9.205,
        9.135,
        9.094,
        9.262,
        9.183,
        9.178,
        9.126,
        9.125,
        9.107,
        9.102,
        9.094
      ]
    },
    {
      "tool": "explore",
      "args": {
        "query": "GT"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 19.947,
      "p95_ms": 20.903,
      "mean_ms": 19.848,
      "min_ms": 18.424,
      "max_ms": 21.713,
      "samples_ms": [
        19.311,
        18.475,
        18.874,
        18.654,
        18.605,
        18.424,
        19.223,
        19.28,
        19.911,
        21.713,
        20.903,
        19.947,
        20.398,
        20.682,
        20.865,
        20.57,
        19.99,
        20.235,
        20.199,
        20.703
      ]
    },
    {
      "tool": "semantic_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 10
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 15.147,
      "p95_ms": 15.389,
      "mean_ms": 15.008,
      "min_ms": 14.111,
      "max_ms": 15.578,
      "samples_ms": [
        15.183,
        15.249,
        15.001,
        15.296,
        15.312,
        15.383,
        14.894,
        14.885,
        15.12,
        15.219,
        15.147,
        15.162,
        15.073,
        15.578,
        15.389,
        15.216,
        14.305,
        14.235,
        14.111,
        14.403
      ]
    },
    {
      "tool": "search_symbols",
      "args": {
        "pattern": "GT*"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 9.996,
      "p95_ms": 10.459,
      "mean_ms": 9.954,
      "min_ms": 9.258,
      "max_ms": 11.58,
      "samples_ms": [
        10.262,
        10.21,
        10.219,
        10.229,
        10.459,
        10.156,
        10.358,
        10.307,
        10.229,
        11.58,
        9.996,
        9.437,
        9.379,
        9.379,
        9.631,
        9.258,
        9.756,
        9.466,
        9.382,
        9.383
      ]
    },
    {
      "tool": "repo_map",
      "args": {},
      "errors": [],
      "calls": 20,
      "p50_ms": 13.541,
      "p95_ms": 14.126,
      "mean_ms": 14.05,
      "min_ms": 12.744,
      "max_ms": 22.958,
      "samples_ms": [
        13.557,
        22.958,
        14.125,
        13.716,
        13.96,
        13.703,
        13.932,
        14.126,
        13.895,
        13.541,
        13.455,
        13.52,
        13.556,
        13.471,
        13.533,
        13.438,
        13.262,
        13.397,
        12.744,
        13.114
      ]
    },
    {
      "tool": "file_api",
      "args": {
        "path": "src/attr/__init__.py",
        "repo": "corpus"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 8.271,
      "p95_ms": 8.866,
      "mean_ms": 8.377,
      "min_ms": 8.165,
      "max_ms": 9.526,
      "samples_ms": [
        8.261,
        8.317,
        9.526,
        8.866,
        8.307,
        8.298,
        8.25,
        8.295,
        8.203,
        8.527,
        8.239,
        8.271,
        8.258,
        8.432,
        8.165,
        8.312,
        8.25,
        8.192,
        8.259,
        8.315
      ]
    },
    {
      "tool": "cross_repo_deps",
      "args": {
        "repo": "corpus"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 8.218,
      "p95_ms": 8.725,
      "mean_ms": 8.301,
      "min_ms": 8.106,
      "max_ms": 9.351,
      "samples_ms": [
        8.161,
        8.144,
        8.187,
        8.395,
        8.13,
        8.207,
        9.351,
        8.725,
        8.217,
        8.106,
        8.27,
        8.224,
        8.164,
        8.218,
        8.175,
        8.23,
        8.366,
        8.226,
        8.223,
        8.291
      ]
    },
    {
      "tool": "visualize_graph",
      "args": {
        "scope": "symbol",
        "symbol": "ib",
        "depth": 2
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 7.955,
      "p95_ms": 8.156,
      "mean_ms": 7.987,
      "min_ms": 7.869,
      "max_ms": 8.553,
      "samples_ms": [
        7.9,
        7.938,
        7.987,
        7.918,
        7.955,
        8.117,
        8.008,
        7.869,
        7.87,
        7.887,
        8.156,
        7.987,
        8.553,
        7.967,
        7.959,
        7.941,
        7.978,
        7.877,
        7.956,
        7.908
      ]
    },
    {
      "tool": "get_compass",
      "args": {
        "module": "graph"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.941,
      "p95_ms": 0.959,
      "mean_ms": 0.953,
      "min_ms": 0.903,
      "max_ms": 1.228,
      "samples_ms": [
        0.957,
        0.938,
        0.95,
        0.949,
        0.959,
        0.936,
        1.228,
        0.92,
        0.95,
        0.95,
        0.952,
        0.941,
        0.938,
        0.938,
        0.941,
        0.942,
        0.921,
        0.93,
        0.903,
        0.922
      ]
    },
    {
      "tool": "search_knowledge",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 10
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.937,
      "p95_ms": 0.958,
      "mean_ms": 0.947,
      "min_ms": 0.913,
      "max_ms": 1.16,
      "samples_ms": [
        0.927,
        0.956,
        1.16,
        0.958,
        0.928,
        0.94,
        0.938,
        0.928,
        0.937,
        0.921,
        0.943,
        0.913,
        0.948,
        0.921,
        0.923,
        0.947,
        0.924,
        0.955,
        0.947,
        0.924
      ]
    },
    {
      "tool": "ask_compass",
      "args": {
        "query": "how does the build resolve call edges"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.979,
      "p95_ms": 2.115,
      "mean_ms": 2.009,
      "min_ms": 1.924,
      "max_ms": 2.268,
      "samples_ms": [
        2.115,
        2.015,
        2.013,
        1.993,
        1.942,
        2.094,
        1.924,
        1.961,
        1.976,
        1.979,
        1.968,
        1.943,
        1.969,
        1.94,
        2.268,
        2.107,
        2.04,
        2.007,
        1.928,
        1.993
      ]
    },
    {
      "tool": "trace_flow",
      "args": {
        "entry": "ib"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.936,
      "p95_ms": 0.985,
      "mean_ms": 0.953,
      "min_ms": 0.901,
      "max_ms": 1.196,
      "samples_ms": [
        0.964,
        0.985,
        0.928,
        0.939,
        0.958,
        0.914,
        1.196,
        0.936,
        0.938,
        0.929,
        0.901,
        0.925,
        0.936,
        0.932,
        0.958,
        0.953,
        0.934,
        0.939,
        0.932,
        0.96
      ]
    },
    {
      "tool": "generate_flow",
      "args": {
        "entry": "ib"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 8.502,
      "p95_ms": 15.208,
      "mean_ms": 9.375,
      "min_ms": 6.759,
      "max_ms": 15.524,
      "samples_ms": [
        8.095,
        6.759,
        8.107,
        7.332,
        8.799,
        15.524,
        10.157,
        8.054,
        9.091,
        8.091,
        7.902,
        12.091,
        8.53,
        8.257,
        8.202,
        8.575,
        8.502,
        9.817,
        10.411,
        15.208
      ]
    },
    {
      "tool": "federated_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 20
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.079,
      "p95_ms": 1.31,
      "mean_ms": 1.108,
      "min_ms": 0.954,
      "max_ms": 1.456,
      "samples_ms": [
        1.31,
        1.235,
        1.217,
        1.191,
        1.18,
        1.102,
        1.097,
        1.084,
        1.079,
        1.093,
        1.079,
        1.456,
        1.039,
        1.037,
        1.043,
        1.031,
        0.998,
        0.964,
        0.975,
        0.954
      ]
    },
    {
      "tool": "knowledge_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 20
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.246,
      "p95_ms": 1.461,
      "mean_ms": 1.281,
      "min_ms": 1.18,
      "max_ms": 1.584,
      "samples_ms": [
        1.461,
        1.402,
        1.414,
        1.319,
        1.338,
        1.584,
        1.267,
        1.274,
        1.257,
        1.252,
        1.246,
        1.211,
        1.208,
        1.207,
        1.227,
        1.205,
        1.183,
        1.195,
        1.18,
        1.189
      ]
    },
    {
      "tool": "trace_workflow",
      "args": {
        "ref": "nonexistent-baseline-probe"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.202,
      "p95_ms": 1.515,
      "mean_ms": 1.283,
      "min_ms": 1.146,
      "max_ms": 2.111,
      "samples_ms": [
        1.232,
        1.515,
        1.211,
        1.19,
        1.202,
        1.179,
        1.211,
        2.111,
        1.355,
        1.355,
        1.287,
        1.225,
        1.163,
        1.181,
        1.192,
        1.163,
        1.182,
        1.154,
        1.405,
        1.146
      ]
    },
    {
      "tool": "recall_memory",
      "args": {
        "query": "ib"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.926,
      "p95_ms": 2.306,
      "mean_ms": 2.003,
      "min_ms": 1.895,
      "max_ms": 2.4,
      "samples_ms": [
        1.978,
        2.4,
        2.306,
        2.153,
        2.028,
        1.969,
        1.987,
        1.953,
        1.924,
        1.896,
        1.94,
        1.902,
        1.895,
        1.926,
        2.267,
        1.896,
        1.905,
        1.926,
        1.912,
        1.898
      ]
    },
    {
      "tool": "record_memory",
      "args": {
        "type": "pattern",
        "title": "baseline probe corpus rec 20",
        "body": "rust-migration baseline probe memory"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 33.071,
      "p95_ms": 51.738,
      "mean_ms": 36.563,
      "min_ms": 15.637,
      "max_ms": 64.482,
      "samples_ms": [
        15.637,
        27.683,
        32.185,
        32.791,
        33.071,
        24.155,
        32.802,
        35.938,
        28.862,
        34.613,
        30.619,
        32.152,
        35.689,
        42.259,
        64.482,
        45.612,
        48.2,
        33.141,
        49.632,
        51.738
      ]
    },
    {
      "tool": "wiki_generate",
      "args": {
        "repo": "corpus",
        "pages": 5,
        "force": true
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 81.064,
      "p95_ms": 96.308,
      "mean_ms": 85.133,
      "min_ms": 59.426,
      "max_ms": 218.658,
      "samples_ms": [
        218.658,
        81.064,
        71.299,
        64.028,
        60.636,
        59.426,
        64.928,
        65.604,
        66.065,
        80.659,
        79.421,
        82.332,
        81.498,
        84.465,
        85.473,
        85.443,
        92.992,
        96.308,
        86.676,
        95.676
      ]
    },
    {
      "tool": "knowledge_add",
      "args": {
        "title": "baseline probe corpus add 20",
        "body": "rust-migration baseline probe document",
        "doc_type": "decision"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 5.678,
      "p95_ms": 17.865,
      "mean_ms": 7.539,
      "min_ms": 5.032,
      "max_ms": 21.125,
      "samples_ms": [
        6.03,
        6.234,
        6.727,
        5.678,
        17.865,
        5.952,
        5.963,
        5.544,
        6.107,
        14.073,
        5.66,
        5.485,
        5.487,
        5.659,
        5.731,
        5.568,
        5.628,
        21.125,
        5.032,
        5.239
      ]
    },
    {
      "tool": "knowledge_delete",
      "args": {},
      "errors": [],
      "calls": 20,
      "p50_ms": 16.849,
      "p95_ms": 28.552,
      "mean_ms": 30.516,
      "min_ms": 11.706,
      "max_ms": 266.572,
      "samples_ms": [
        12.334,
        11.981,
        11.812,
        11.706,
        12.109,
        12.709,
        14.486,
        11.821,
        266.572,
        19.977,
        24.498,
        25.184,
        27.399,
        22.778,
        22.605,
        26.224,
        12.749,
        16.849,
        17.98,
        28.552
      ]
    },
    {
      "tool": "knowledge_status",
      "args": {
        "new_status": "superseded"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 21.419,
      "p95_ms": 42.84,
      "mean_ms": 37.316,
      "min_ms": 16.106,
      "max_ms": 300.6,
      "samples_ms": [
        21.391,
        23.269,
        19.139,
        19.725,
        22.665,
        16.179,
        16.106,
        16.295,
        300.6,
        32.063,
        27.248,
        42.84,
        27.307,
        21.242,
        21.419,
        22.628,
        35.9,
        18.487,
        19.772,
        22.038
      ]
    }
  ],
  "live_store_reference": {
    "db_path": "/Users/lnmtan/.cairn/79428b9d734aac21/.kg",
    "db_bytes": 91181056,
    "symbols": 13730,
    "edges": 99027,
    "transitive_edges": 0
  }
}
```

#### attrs — rust

```json
{
  "schema": "cairn-rust-baseline/1",
  "suite": "rust-migration-baseline",
  "impl": "rust",
  "label": "attrs",
  "timestamp": "2026-10-24T16:39:02.519996+00:00",
  "harness": "scripts/rust_migration/baseline.py",
  "reproduce": ".venv/bin/python scripts/rust_migration/baseline.py --impl rust --workspace /Users/lnmtan/Projects/others/cairn/benchmarks/datasource/ds2/second-corpus/attrs-26.1.0 --label attrs --repeats 20",
  "dataset": {
    "name": "benchmark-datasource",
    "version": "DS-v1",
    "tree_hash": "65e3df398b96f2cd31230ee17765fc01b0bc603e4e27914f3c31f41f061f87bb",
    "identity_size": 300
  },
  "cairn_version": "0.23.0",
  "machine_profile": {
    "arch": "arm64",
    "cpu": "arm",
    "cpu_count": 12,
    "os": "macOS-27.0.1-arm64-arm-64bit-Mach-O",
    "runner_class": "reference-local"
  },
  "method": {
    "build": "build_graph(workspace, db) + build_dataflow_index (the `cairn build` shape)",
    "closure": "build_transitive_closure(conn) timed; WAL sidecar polled every 0.005s, with the post-commit sidecar size as the single-burst floor for the peak",
    "latency": "per-tool tools/call over streamable HTTP (keep-alive), 20 timed calls each after 1 untimed warmup; write tools re-create their fixture untimed per call",
    "rss": "ps -o rss= on the daemon pid every 0.2s",
    "env_pins": {
      "CAIRN_EMBED_BACKEND": "hash",
      "CAIRN_RERANK": "0"
    },
    "daemon": {
      "transport": "streamable-http",
      "stateless": false,
      "host": "127.0.0.1"
    },
    "knowledge_bundle_seeded_files": 0,
    "store": "python build_graph + dataflow + hash embed (identical-stores doctrine); the rust daemon is the measured engine"
  },
  "corpus": {
    "workspace": "/Users/lnmtan/Projects/others/cairn/benchmarks/datasource/ds2/second-corpus/attrs-26.1.0",
    "identity_method": "tree_hash",
    "tree_hash": "ad6eec778ba82da2ac4493676f990c6d155e7b0634900c36a54da0de2d515097",
    "py_files": 50,
    "py_lines": 19039,
    "py_bytes": 527216
  },
  "build": {
    "seconds": 0.352,
    "dataflow_seconds": 0.122,
    "dataflow_symbols": 894,
    "repos": 1,
    "files": 50,
    "symbols": 1722,
    "edges": 6239,
    "imports": 399,
    "skipped": 0,
    "resolution": {
      "exact": 1316,
      "ambiguous": 636,
      "unresolved": 2615
    },
    "lsp_upgraded": 0,
    "scip_edges": null,
    "parse_errors": 0
  },
  "closure": {
    "seconds": 0.037,
    "rows": 12539,
    "peak_wal_bytes": 2884032,
    "sampled_peak_wal_bytes": 1186616,
    "wal_end_bytes": 2884032,
    "db_bytes": 7270400
  },
  "embed": {
    "seconds": 0.104,
    "backend_effective": "hash",
    "embedded": 1722,
    "total": 1722
  },
  "daemon": {
    "impl": "rust",
    "boot_seconds": 0.276,
    "rss": {
      "peak_kb": 31392,
      "final_kb": 27552
    }
  },
  "tools": [
    {
      "tool": "find_definition",
      "args": {
        "name": "test_validate_respects_run_validators_config",
        "limit": 5
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 7.393,
      "p95_ms": 29.563,
      "mean_ms": 10.304,
      "min_ms": 5.84,
      "max_ms": 39.207,
      "samples_ms": [
        9.432,
        7.972,
        7.393,
        7.147,
        7.482,
        7.427,
        29.563,
        5.84,
        8.554,
        12.553,
        7.364,
        7.127,
        6.576,
        6.043,
        6.237,
        7.225,
        7.951,
        7.893,
        39.207,
        7.101
      ]
    },
    {
      "tool": "get_callers",
      "args": {
        "name": "test_validate_respects_run_validators_config",
        "limit": 50
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 4.745,
      "p95_ms": 9.403,
      "mean_ms": 6.182,
      "min_ms": 3.86,
      "max_ms": 28.129,
      "samples_ms": [
        9.403,
        4.913,
        4.561,
        4.494,
        4.784,
        4.937,
        5.468,
        4.745,
        4.633,
        4.728,
        5.138,
        5.537,
        6.275,
        28.129,
        4.462,
        3.86,
        4.07,
        4.182,
        4.455,
        4.873
      ]
    },
    {
      "tool": "get_callees",
      "args": {
        "name": "test_validate_respects_run_validators_config",
        "limit": 50
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 4.309,
      "p95_ms": 22.03,
      "mean_ms": 6.065,
      "min_ms": 2.838,
      "max_ms": 23.374,
      "samples_ms": [
        4.405,
        4.309,
        4.206,
        4.705,
        4.855,
        23.374,
        3.878,
        3.978,
        3.914,
        3.824,
        4.467,
        4.517,
        5.296,
        5.636,
        22.03,
        4.316,
        3.971,
        3.929,
        2.844,
        2.838
      ]
    },
    {
      "tool": "impact_analysis",
      "args": {
        "name": "ib",
        "depth": 3,
        "limit": 100
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3.364,
      "p95_ms": 12.881,
      "mean_ms": 4.651,
      "min_ms": 3.001,
      "max_ms": 16.046,
      "samples_ms": [
        4.362,
        4.567,
        4.169,
        3.355,
        3.283,
        12.881,
        3.364,
        3.785,
        3.001,
        3.083,
        3.325,
        3.755,
        3.406,
        3.22,
        3.695,
        3.273,
        4.002,
        3.348,
        16.046,
        3.111
      ]
    },
    {
      "tool": "path",
      "args": {
        "from_pattern": "__init__",
        "to_pattern": "AttrsInstance",
        "max_depth": 4
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 5.098,
      "p95_ms": 7.014,
      "mean_ms": 6.14,
      "min_ms": 4.148,
      "max_ms": 24.162,
      "samples_ms": [
        6.555,
        6.467,
        5.921,
        5.156,
        5.358,
        5.098,
        5.514,
        4.778,
        4.921,
        5.193,
        4.507,
        24.162,
        4.148,
        7.014,
        4.171,
        4.556,
        4.892,
        5.265,
        4.401,
        4.728
      ]
    },
    {
      "tool": "explore",
      "args": {
        "query": "test_validate_respects_run_validators_config"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 10.787,
      "p95_ms": 11.882,
      "mean_ms": 10.911,
      "min_ms": 10.134,
      "max_ms": 13.127,
      "samples_ms": [
        13.127,
        11.882,
        11.513,
        10.949,
        10.515,
        10.277,
        10.134,
        10.615,
        10.853,
        11.18,
        11.189,
        11.044,
        11.008,
        10.837,
        10.787,
        10.523,
        10.715,
        10.402,
        10.207,
        10.472
      ]
    },
    {
      "tool": "semantic_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 10
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 8.244,
      "p95_ms": 10.174,
      "mean_ms": 9.708,
      "min_ms": 6.967,
      "max_ms": 38.562,
      "samples_ms": [
        8.87,
        8.442,
        7.179,
        8.34,
        8.244,
        9.069,
        8.797,
        9.213,
        9.197,
        9.516,
        10.174,
        38.562,
        7.781,
        7.393,
        7.099,
        6.967,
        7.347,
        7.439,
        7.156,
        7.366
      ]
    },
    {
      "tool": "search_symbols",
      "args": {
        "pattern": "test*"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 4.806,
      "p95_ms": 23.617,
      "mean_ms": 6.684,
      "min_ms": 4.113,
      "max_ms": 25.499,
      "samples_ms": [
        4.51,
        5.099,
        23.617,
        5.112,
        4.606,
        4.595,
        4.697,
        4.678,
        4.827,
        4.946,
        4.875,
        4.843,
        4.806,
        4.859,
        4.927,
        25.499,
        4.582,
        4.274,
        4.113,
        4.214
      ]
    },
    {
      "tool": "repo_map",
      "args": {},
      "errors": [],
      "calls": 20,
      "p50_ms": 6.209,
      "p95_ms": 7.688,
      "mean_ms": 6.961,
      "min_ms": 5.862,
      "max_ms": 16.809,
      "samples_ms": [
        7.688,
        7.625,
        7.188,
        7.397,
        6.495,
        6.062,
        6.282,
        6.023,
        6.123,
        5.947,
        16.809,
        5.862,
        6.319,
        6.49,
        6.219,
        6.123,
        6.076,
        6.095,
        6.209,
        6.195
      ]
    },
    {
      "tool": "file_api",
      "args": {
        "path": "src/attr/__init__.py",
        "repo": "corpus"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 2.514,
      "p95_ms": 19.274,
      "mean_ms": 4.268,
      "min_ms": 2.331,
      "max_ms": 19.703,
      "samples_ms": [
        3.129,
        3.281,
        19.703,
        2.764,
        2.635,
        2.596,
        2.525,
        2.473,
        2.63,
        2.588,
        2.514,
        2.435,
        2.458,
        2.456,
        2.396,
        19.274,
        2.426,
        2.354,
        2.331,
        2.393
      ]
    },
    {
      "tool": "cross_repo_deps",
      "args": {
        "repo": "corpus"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 2.728,
      "p95_ms": 3.42,
      "mean_ms": 3.389,
      "min_ms": 2.631,
      "max_ms": 14.791,
      "samples_ms": [
        3.42,
        3.346,
        2.761,
        2.711,
        2.635,
        2.662,
        2.674,
        2.685,
        2.636,
        2.806,
        14.791,
        2.835,
        2.793,
        2.741,
        2.784,
        2.741,
        2.728,
        2.701,
        2.631,
        2.696
      ]
    },
    {
      "tool": "visualize_graph",
      "args": {
        "scope": "symbol",
        "symbol": "ib",
        "depth": 2
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3.366,
      "p95_ms": 14.655,
      "mean_ms": 4.833,
      "min_ms": 2.284,
      "max_ms": 19.947,
      "samples_ms": [
        2.294,
        2.284,
        14.655,
        3.298,
        3.127,
        3.279,
        3.374,
        3.305,
        3.248,
        3.319,
        3.366,
        3.356,
        3.502,
        3.521,
        3.936,
        3.526,
        19.947,
        4.912,
        4.915,
        3.502
      ]
    },
    {
      "tool": "get_compass",
      "args": {
        "module": "graph"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.389,
      "p95_ms": 0.541,
      "mean_ms": 0.414,
      "min_ms": 0.335,
      "max_ms": 0.543,
      "samples_ms": [
        0.537,
        0.443,
        0.389,
        0.373,
        0.373,
        0.367,
        0.336,
        0.341,
        0.357,
        0.338,
        0.335,
        0.438,
        0.543,
        0.541,
        0.51,
        0.469,
        0.406,
        0.4,
        0.394,
        0.389
      ]
    },
    {
      "tool": "search_knowledge",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 10
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.233,
      "p95_ms": 0.397,
      "mean_ms": 0.257,
      "min_ms": 0.184,
      "max_ms": 0.405,
      "samples_ms": [
        0.405,
        0.397,
        0.384,
        0.365,
        0.24,
        0.207,
        0.194,
        0.184,
        0.185,
        0.249,
        0.233,
        0.239,
        0.244,
        0.246,
        0.227,
        0.233,
        0.217,
        0.231,
        0.23,
        0.235
      ]
    },
    {
      "tool": "ask_compass",
      "args": {
        "query": "how does the build resolve call edges"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 4.668,
      "p95_ms": 9.661,
      "mean_ms": 5.611,
      "min_ms": 3.964,
      "max_ms": 10.951,
      "samples_ms": [
        4.374,
        4.155,
        4.79,
        8.491,
        4.414,
        5.565,
        9.661,
        4.382,
        4.724,
        4.899,
        9.241,
        3.971,
        4.668,
        4.487,
        4.823,
        10.951,
        4.576,
        4.572,
        5.508,
        3.964
      ]
    },
    {
      "tool": "trace_flow",
      "args": {
        "entry": "ib"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 4.076,
      "p95_ms": 13.802,
      "mean_ms": 4.932,
      "min_ms": 3.24,
      "max_ms": 14.861,
      "samples_ms": [
        4.312,
        13.802,
        3.512,
        4.04,
        4.211,
        3.653,
        3.62,
        4.531,
        3.429,
        3.338,
        4.136,
        4.076,
        4.24,
        4.105,
        14.861,
        3.382,
        4.091,
        3.979,
        4.091,
        3.24
      ]
    },
    {
      "tool": "generate_flow",
      "args": {
        "entry": "ib"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 9.84,
      "p95_ms": 11.791,
      "mean_ms": 10.183,
      "min_ms": 8.611,
      "max_ms": 13.246,
      "samples_ms": [
        13.246,
        10.891,
        9.371,
        11.791,
        10.858,
        11.332,
        10.091,
        9.98,
        9.694,
        9.84,
        9.916,
        9.676,
        10.396,
        9.447,
        9.627,
        9.62,
        10.66,
        8.611,
        9.436,
        9.172
      ]
    },
    {
      "tool": "federated_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 20
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.145,
      "p95_ms": 0.203,
      "mean_ms": 0.156,
      "min_ms": 0.135,
      "max_ms": 0.216,
      "samples_ms": [
        0.195,
        0.153,
        0.151,
        0.203,
        0.157,
        0.163,
        0.148,
        0.143,
        0.139,
        0.14,
        0.165,
        0.177,
        0.216,
        0.135,
        0.14,
        0.135,
        0.145,
        0.138,
        0.141,
        0.144
      ]
    },
    {
      "tool": "knowledge_search",
      "args": {
        "query": "how does the build resolve call edges",
        "limit": 20
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3.402,
      "p95_ms": 15.853,
      "mean_ms": 4.639,
      "min_ms": 2.592,
      "max_ms": 16.552,
      "samples_ms": [
        2.812,
        2.982,
        2.863,
        2.592,
        3.047,
        3.804,
        16.552,
        3.454,
        5.336,
        2.962,
        3.169,
        3.548,
        3.172,
        3.659,
        3.402,
        3.072,
        15.853,
        3.581,
        3.46,
        3.46
      ]
    },
    {
      "tool": "trace_workflow",
      "args": {
        "ref": "nonexistent-baseline-probe"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 0.417,
      "p95_ms": 0.457,
      "mean_ms": 0.414,
      "min_ms": 0.351,
      "max_ms": 0.468,
      "samples_ms": [
        0.468,
        0.457,
        0.45,
        0.437,
        0.432,
        0.43,
        0.433,
        0.428,
        0.401,
        0.36,
        0.351,
        0.417,
        0.408,
        0.407,
        0.419,
        0.407,
        0.42,
        0.406,
        0.381,
        0.36
      ]
    },
    {
      "tool": "recall_memory",
      "args": {
        "query": "ib"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 1.688,
      "p95_ms": 2.047,
      "mean_ms": 1.703,
      "min_ms": 1.358,
      "max_ms": 2.17,
      "samples_ms": [
        2.015,
        1.858,
        2.047,
        2.17,
        1.893,
        2.033,
        1.847,
        1.709,
        1.635,
        1.688,
        1.719,
        1.594,
        1.477,
        1.42,
        1.515,
        1.498,
        1.358,
        1.411,
        1.403,
        1.776
      ]
    },
    {
      "tool": "record_memory",
      "args": {
        "type": "pattern",
        "title": "baseline probe corpus rec 20",
        "body": "rust-migration baseline probe memory"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 9.837,
      "p95_ms": 27.99,
      "mean_ms": 24.818,
      "min_ms": 6.624,
      "max_ms": 265.555,
      "samples_ms": [
        6.624,
        8.486,
        8.335,
        8.662,
        8.212,
        9.376,
        8.031,
        10.257,
        13.012,
        7.436,
        9.837,
        9.447,
        9.942,
        9.918,
        265.555,
        27.99,
        25.302,
        19.04,
        16.452,
        14.437
      ]
    },
    {
      "tool": "wiki_generate",
      "args": {
        "repo": "corpus",
        "pages": 5,
        "force": true
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 40.815,
      "p95_ms": 51.432,
      "mean_ms": 42.572,
      "min_ms": 37.031,
      "max_ms": 51.601,
      "samples_ms": [
        48.273,
        51.601,
        51.432,
        48.177,
        42.518,
        41.053,
        39.181,
        39.223,
        40.423,
        37.031,
        42.824,
        42.177,
        45.574,
        43.972,
        40.105,
        40.815,
        40.213,
        39.651,
        37.882,
        39.306
      ]
    },
    {
      "tool": "knowledge_add",
      "args": {
        "title": "baseline probe corpus add 20",
        "body": "rust-migration baseline probe document",
        "doc_type": "decision"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 3.719,
      "p95_ms": 4.241,
      "mean_ms": 3.738,
      "min_ms": 3.227,
      "max_ms": 4.247,
      "samples_ms": [
        3.53,
        3.35,
        3.797,
        4.218,
        3.719,
        4.241,
        3.585,
        3.423,
        3.251,
        3.448,
        3.227,
        3.428,
        3.238,
        4.247,
        4.06,
        3.9,
        3.902,
        4.024,
        4.104,
        4.069
      ]
    },
    {
      "tool": "knowledge_delete",
      "args": {},
      "errors": [],
      "calls": 20,
      "p50_ms": 10.216,
      "p95_ms": 11.487,
      "mean_ms": 10.128,
      "min_ms": 7.348,
      "max_ms": 11.837,
      "samples_ms": [
        9.993,
        10.058,
        10.283,
        11.837,
        11.311,
        10.795,
        11.47,
        10.33,
        10.686,
        10.875,
        10.637,
        8.825,
        7.348,
        11.487,
        7.699,
        9.097,
        9.432,
        10.035,
        10.146,
        10.216
      ]
    },
    {
      "tool": "knowledge_status",
      "args": {
        "new_status": "superseded"
      },
      "errors": [],
      "calls": 20,
      "p50_ms": 11.761,
      "p95_ms": 19.062,
      "mean_ms": 12.975,
      "min_ms": 7.936,
      "max_ms": 21.919,
      "samples_ms": [
        10.882,
        13.076,
        17.62,
        13.256,
        13.463,
        13.043,
        10.517,
        11.761,
        21.919,
        10.268,
        9.465,
        10.245,
        17.795,
        11.433,
        12.145,
        19.062,
        11.051,
        11.243,
        7.936,
        13.318
      ]
    }
  ],
  "live_store_reference": {
    "db_path": "/Users/lnmtan/.cairn/79428b9d734aac21/.kg",
    "db_bytes": 91181056,
    "symbols": 13730,
    "edges": 99027,
    "transitive_edges": 0
  }
}
```
