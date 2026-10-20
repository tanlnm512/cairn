# Baselines: Python implementation (rust-migration P0-2)

**Frozen.** These are the numbers the Rust migration compares against
(plan.md P0-2, gates G1/G2). Measured 2026-10-18 with cairn 0.22.0 (Python
implementation) on the machine stamped below. Never overwrite: re-measures
belong in a new section or a new file, never a mutation of these.

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
