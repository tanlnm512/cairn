# Survey: benchmark-datasource

**Created**: 2026-08-16 | **Baseline**: v0.11.0 @ 66882c2 (main)
Phase-A output — the single source of truth for code state. Every citation
below is pasted verbatim from grep/read output in the session that wrote it.

## Items

```
item FR-001: "version the synthetic T1 corpus via benchmarks/datasource/manifest.json + CI content-hash"
  evidence:   src/cairn/bench/corpus.py:19 `DEFAULT_SEED = 0xC0DE`;
              corpus.py:22-28 `def generate_corpus(root: Path, n_files: int, *, complexity: str = "medium", seed: int = DEFAULT_SEED) -> Path`;
              corpus.py:43-48 `profiles = {"low": (3, 3, 3), "medium": (5, 5, 3), "high": (8, 8, 6)}` (`(n_classes, n_methods, n_calls)`);
              corpus.py:50-52 `repo = root / "benchrepo"` + `(repo / ".git").mkdir(exist_ok=True)  # scanner marker`;
              corpus.py:90/95 module files written via `write_text`; `__init__.py` empty. A regenerated tree depends ONLY on (seed, n_files, complexity); no generator-version tag is recorded anywhere. No manifest concept exists: `grep -rn "manifest" src/cairn --include="*.py"` → 0 matches (only eval.py:4 docstring word "datasets").
              CI (.github/workflows/ci.yml:261-321, job `bench:` "Bench (advisory baseline comparison)"): generates the corpus ad-hoc inside `cairn bench` at step "Run bench (fixed corpus, hash backend)" — `cairn bench --suite perf --n-files 60 --complexity medium --embed-backend hash --repeats 3 --json --save bench-current.json` (ci.yml:295-297). Baseline mechanics: actions/cache@v4 with `path: bench-baseline.json`, `key: bench-baseline-v1-${{ github.run_id }}` (always misses), `restore-keys: bench-baseline-v1-` (ci.yml:285-290); refreshed post-run by `cp bench-current.json bench-baseline.json` (ci.yml:307-312). NO content-hash assertion of the corpus anywhere in ci.yml (only `hashFiles('.pre-commit-config.yaml')` for the pre-commit env cache, ci.yml:126).
  status:     PARTIAL
  verify:     `grep -rn "manifest" src/cairn --include="*.py" | grep -v __pycache__` → no output (exit 1 for content; only eval.py:4 "datasets" docstring via separate grep). `grep -n "hash" .github/workflows/ci.yml` → only pip cache + hashFiles('.pre-commit-config.yaml') + actions/cache steps. PASSED (confirms absence).
  gap:        No `benchmarks/datasource/manifest.json`, no generator git-sha/expected-counts recording, no path-order-independent content-hash check in CI. The deterministic generator substrate (seed/profiles) exists and is load-bearing.
```

```
item FR-002: "vendor a ≤3MB real snapshot with provenance + NOTICE + size guard"
  evidence:   `ls /Users/tanle/Projects/cairn/benchmarks` → "NO benchmarks/ dir" (exit 1; dir does not exist at 66882c2).
              NOTICE (82 lines) documents ONLY runtime pip dependencies by license family + optional extras + the bge-m3 model note ("cairn does not redistribute the model weights", NOTICE:79-82). No vendored-content/attribution section exists.
              Vendored fixtures today live under tests/: `du -sh tests/fixtures tests/goldens tests/eval` → `176K tests/fixtures`, `8.0K tests/goldens`, `8.0K tests/eval`. Convention: tests/fixtures/golden/<lang>/{sample.<ext>, expected.json} for 13+ languages + tests/fixtures/golden/regenerate.py ("Regenerate expected golden JSON files for parser regression testing. Usage: .venv/bin/python -m tests.fixtures.golden.regenerate <lang|all>"). No provenance manifests (these are hand-written snippets, not upstream code).
              CI size checks: none in ci.yml. Only generic guard is pre-commit `check-added-large-files args: [--maxkb=500]` (.pre-commit-config.yaml, pre-commit/pre-commit-hooks v5.0.0), enforced server-side by the `pre-commit` CI job (`pre-commit run --all-files`, ci.yml:129-130). No budget scoped to any benchmarks/ tree.
  status:     TODO
  verify:     `ls /Users/tanle/Projects/cairn/benchmarks 2>/dev/null || echo "NO benchmarks/ dir"` → "NO benchmarks/ dir". PASSED (confirms absence).
  gap:        Everything: snapshot dir, provenance manifest (upstream repo/commit/license), NOTICE attribution section, 5MB datasource size budget in CI, build+query smoke test.
```

```
item FR-003: "ground-truth set consumed by src/cairn/eval.py + validator"
  evidence:   src/cairn/eval.py (167 lines) public surface: `DEFAULT_QUERIES_PATH = _resolve_default_queries_path()` (eval.py:48, resolves `<repo>/tests/eval/queries.yaml` or `<pkg>/tests/eval/queries.yaml`); `load_eval_queries(path=None) -> List[Dict]` (yaml.safe_load); `evaluate_l1_query(conn, query, expect, k=10) -> (recall_at_k, reciprocal_rank)` — tries `qmod.semantic_search` first, falls back `qmod.search_symbols`; pass = any `exp.lower() in name.lower()` substring in top-k (eval.py:81); `evaluate_l5_query(conn, bundle_root, query, expect, k=10)` — `OKFBundle(bundle_root).search(query, limit=k)`, matches on concept_id, returns 0.0/0.0 if bundle_root missing (eval.py:97-98); `run_evaluation(conn, bundle_root=None, queries_path=None, corpus_filter="all", k=10) -> {"L1": {"count","recall_at_10","mrr"}, "L5": {...}}` (binary recall@k averaged, MRR = mean 1/rank).
              Ground truth TODAY = tests/eval/queries.yaml; ran `load_eval_queries()` → "total 40 L1 30 L5 10", entry keys `['corpus', 'expect', 'query']`. FR asks ≥50 L1 + ≥20 L5 with rationale — current set is 30/10, no rationale field, no validator script (scripts/ has no verify_ground_truth; only ci-local.sh, hooks/, install*.sh, measure_memory_health.py, regen_scip_pb2.sh, run_skill_evals.py, uninstall.sh, verify_no_code_change.py).
              CLI entry: cli/system.py:491-527 `@main.command(name="eval")` eval_cmd — flags `--db` (default `DEFAULT_DB_PATH` = graph/schema.py:373 `resolve_store().db`), `--knowledge` (cli/main.py:19 `DEFAULT_KNOWLEDGE_PATH`), `--corpus {L1,L5,all}`, `--queries`, `--json`.
              What a run reports on cairn's own repo (recorded, docs/benchmarks.md:63-64): "L1 recall@10 = 0.0, L5 recall@10 = 0.0. This is an honest finding ... the query set ... targets generic codebase shapes, not cairn's specific symbol names".
              Tests: no dedicated eval test file (`grep -rn "run_evaluation\|load_eval_queries\|evaluate_l1" tests/` → 0 hits); only tests/test_cli_smoke.py:42-44 invokes `["eval", "--db", db_path, "--knowledge", knowledge_dir, "--json"]` against a get_db-initialized empty DB and asserts exit 0 + "L1" in output.
  status:     PARTIAL
  verify:     Ran `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "from cairn.eval import load_eval_queries; ..."` → total 40 L1 30 L5 10. Ran `.venv/bin/python -m pytest tests/test_cli_smoke.py -p no:cacheprovider -q` → "2 passed in 68.37s" (covers cairn eval end-to-end on a tmp DB). Also ran `run_evaluation` against a bare `sqlite3.connect(':memory:')` → raises `sqlite3.OperationalError: no such table: symbols` (eval.py:71 fallback path) — the harness presumes a schema-initialized DB. Full `cairn eval` on THIS workspace not run: it requires a built graph DB (state change; read-only session) — cited docs/benchmarks.md:63 recorded result instead. PASSED.
  gap:        Ground-truth set is not maintained data (generic shapes, 30/10 counts, no hand-verified expected symbols, no rationale, not under benchmarks/datasource/t2/ground_truth/), and no validator script re-verifies expectations against a fresh build.
```

```
item FR-004: "stamp artifacts with dataset+cairn version+machine profile; baselines/DS-v1; --baseline resolution"
  evidence:   src/cairn/bench/report.py:51-59 `PerfReport.to_dict()` returns ONLY `{corpus, db_path, db_size_mb, symbols, edges, ops}` — no timestamp, no cairn version, no machine profile. The timestamp is stamped at the CLI layer only: cli/bench.py:122/157 `payload["timestamp"] = datetime.now(timezone.utc).isoformat()` ("Stamp the machine-readable payload so a saved baseline records when it was measured").
              compare semantics: report.py:148-170 `compare_reports(baseline, current, threshold=0.15)` — gates on `median_ms`, `regressed` = `delta > threshold`; ops missing from baseline are skipped (`if base_ms is None or base_ms == 0: continue`). Agent analogue: agent_suite.py:521-545 `compare_agent_reports(baseline, current, threshold=0.15)` gates on per-task `cairn.est_tokens`.
              CLI flag surface (cli/bench.py:29-96): `--suite {perf,scaling,agent}`, `--workspace`, `--sizes` (default "100,500,1000,5000"), `--n-files` (default 300), `--complexity {low,medium,high}`, `--embed-backend` (default hash), `--json`, `--save`, `--compare`, `--threshold` (0.15), `--repeats` (3), `--runs` (3). NO `--baseline` flag exists. `--compare` exits 2 on regression (cli/bench.py:205 `sys.exit(2)  # CI signal: regressions found`).
              Version single-symbol: src/cairn/__init__.py:2 `__version__ = "0.11.0"` (verified importable: `import cairn; cairn.__version__` → "0.11.0", resolves to the src checkout); pyproject.toml:7 `version = "0.11.0"`. Existing version+platform stamping lives in diagnostics only: cli/system.py:1329 `_report_versions(conn)` returns `{"cairn": __version__, "python": ..., "platform": platform.platform(), "sqlite": ..., "db_schema_user_version": ...}` (system.py:1343-1349), consumed at system.py:1430 (cairn report/doctor path) — NOT in any bench artifact.
              AgentReport differs from PerfReport (agent_suite.py:441-449): fields `corpus, seed (=DEFAULT_SEED), runs (=3), embed_backend, tasks: List[TaskEffort]` — no db_path/db_size_mb/symbols/edges/ops; adds computed `totals` + `chars_per_token: 4` in to_dict.
              No `benchmarks/baselines/` directory exists (benchmarks/ absent, see FR-002).
  status:     PARTIAL
  verify:     Ran `grep -n "timestamp\|version\|profile\|baseline" src/cairn/cli/bench.py` → only payload["timestamp"] lines + --save/--compare/--threshold options; no version/profile stamping, no --baseline. Ran `.venv/bin/python -c "import cairn; print(cairn.__version__)"` → "cairn 0.11.0 /Users/tanle/Projects/cairn/src/cairn/__init__.py". PASSED.
  gap:        No dataset-version/cairn-version/machine-profile stamping in artifacts; no committed baselines/DS-v1; no `--baseline <DS-version>` resolution; no machine-profile mismatch warning.
```

```
item FR-005: "generate the three _fill_ table families in docs/benchmarks.md between sentinels"
  evidence:   Exact inventory (ran `grep -n "_fill_" docs/benchmarks.md` — 15 lines, `grep -o "_fill_" docs/benchmarks.md | wc -l` → 47 occurrences):
                - retrieval-quality family (lines 60-61): `| L1 | 30 | _fill_ | _fill_ |` and `| L5 | 10 | _fill_ | _fill_ |` — 4 fills; header at line 58 `| corpus | samples | recall@10 | mrr |`.
                - perf family (lines 109-117): rows build (total)/build.parse/build.resolve/embed_all with 1 fill each (4), rows find_definition/search_symbols/get_callers/get_callees/impact_analysis with 3 fills each (15) — 19 fills; header line 107 `| operation | median (ms) | p95 (ms) | ops/sec |`.
                - scaling family (lines 143-146): sizes 100/500/1000/5000 × 6 fills each — 24 fills; header line 141 `| files | symbols | build (s) | embed (s) | DB MB | resolve | peak MB |`.
              NO sentinel markers exist in benchmarks.md today (no BEGIN/END comments around any table).
              No doc-generation script exists: `grep -rn "benchmarks.md" scripts/ src/ .github/` → only a PKG-INFO link mention; scripts/ contains ci-local.sh, hooks/, install-dev-hooks.sh, install-hooks.sh, install.sh, measure_memory_health.py, regen_scip_pb2.sh, run_skill_evals.py, uninstall.sh, verify_no_code_change.py — none write docs.
              Agent-effort regeneration story (already landed): numbers were hand-typed from manual runs — `git log --oneline -- docs/benchmarks.md` → 78bcd34 "feat(bench): agent-effort suite vs a grep/read control (EVID-1)" (also 2b48753 "docs(bench): fill Python-corpus numbers + methodology post"). docs/benchmarks.md:279-280 records the discipline: "Two consecutive full runs of the table above produced identical call and token counts in both arms." There is no generator; regeneration = re-running `cairn bench --suite agent` by hand.
  status:     TODO
  verify:     Ran `grep -c "_fill_" docs/benchmarks.md` → 15; `grep -o "_fill_" docs/benchmarks.md | wc -l` → 47; `grep -rn "benchmarks.md" scripts/ src/ .github/ | grep -v __pycache__` → only src/cairn_intel.egg-info/PKG-INFO link. PASSED.
  gap:        All three families unfilled; no sentinel markers; no table generator; no byte-idempotency regen; no CI check against hand edits.
```

```
item FR-006: "T3 manifest pinning external repos + local command"
  evidence:   No fetch/manifest/pinning machinery for repos exists anywhere: `grep -rn "git clone" src/cairn --include="*.py"` → 0 matches; no "manifest" in src (FR-001). The only network fetch paths are: cli/upgrade.py:39-50 `_pypi_latest()` (urllib to https://pypi.org/pypi/cairn-intel/json) + `_reinstall` via uv/pipx/pip (upgrade.py:82-100); scripts/install.sh installs from PyPI. No code clones/pins external repositories.
              Network-free-CI constraint evidence: `grep -rni "offline\|network" .github/workflows/ci.yml` → 0 matches — no CI job asserts offline. Every job installs from PyPI (`pip install -e ...`, e.g. ci.yml:42/67/169/278). The constraint is doctrine, not CI enforcement: docs/phases/benchmark-datasource/spec.md:137 "Fetching T3 content in CI (network-free CI is a hard constraint)" and the tier table "scheduled/local scale runs only (no CI network)" (spec.md:56); specs/benchmark-datasource/spec.md Scope/Out: "T3 content in CI (network-free CI is hard)".
  status:     TODO
  verify:     Ran `grep -rni "offline\|network" .github/workflows/ci.yml` → no output (exit 1). Ran `grep -rn "git clone\|clone(" src/cairn --include="*.py" | head` → no output. PASSED (confirms absence).
  gap:        No T3 manifest schema, no pinned (url + commit) entries, no ≥2 scale points, no documented local fetch command, no recording of manifest entry in results.
```

## Supporting evidence

Bench stack symbols (all in src/cairn/bench/; bench/__init__.py exports generate_corpus, corpus_stats, run_perf_suite, run_scaling_suite, PerfReport, ScalingReport, ScalingPoint, compare_reports, TimingResult, MemoryResult, time_call, percentiles, peak_memory — agent_suite symbols imported directly):

- corpus.py — `DEFAULT_SEED = 0xC0DE` (:19); `generate_corpus(root: Path, n_files: int, *, complexity: str = "medium", seed: int = DEFAULT_SEED) -> Path` (:22); `corpus_stats(repo) -> {"files","lines","bytes"}` (:99).
- timing.py — `time_call(fn, *, name="", warmup=1, repeats=5) -> (TimingResult, T)` (:66); `TimingResult` fields `name, samples, median, p50, p95, p99, mean, minimum, maximum` + property `ops_per_sec` (:19-39); `percentiles(samples)`; `peak_memory(fn) -> (MemoryResult, T)` (:114), `MemoryResult.peak_mb`.
- report.py — `OpTiming(name, timing)` → dict keys `name, median_ms, p50_ms, p95_ms, p99_ms, ops_per_sec` (:29-37); `PerfReport` fields `corpus, db_path, db_size_mb, symbols, edges, ops` (:44-49), to_dict keys as in FR-004; `ScalingPoint(n_files, symbols, build_seconds, embed_seconds, db_size_mb, resolve_rate, peak_memory_mb=0.0)` → dict keys `n_files, symbols, build_s, embed_s, db_mb, resolve_rate, peak_mem_mb` (:90-110); `ScalingReport.points` → `{"points": [...]}`; `compare_reports(baseline, current, threshold=0.15)` (:148).
- perf_suite.py — `run_perf_suite(workspace: str, db_path: str, *, embed_backend="hash", warmup=1, repeats=3, query_repeats=5, progress=None) -> PerfReport` (:74). Op names emitted: `build (total)`, `build.<phase>` (scan/parse/insert/resolve/persist from build_graph progress events, :36-44), `embed_all`, `build.derived.closure` (build_transitive_closure), then query battery `find_definition, search_symbols, get_callers, get_callees, impact_analysis, impact_analysis_wide, semantic_search, explore` (:204-239) — the exact rows the FR-005 perf table needs. Mutates/restores env CAIRN_DB + CAIRN_EMBED_BACKEND and calls `embeddings.reset_backend_cache()` (:98-116).
- scaling_suite.py — `run_scaling_suite(root: Path, *, sizes=(100, 500, 1000, 5000), complexity="medium", embed_backend="hash", progress=None) -> ScalingReport` (:33); per-size corpus under `root/size_<n>/`, own throwaway `bench.db`, single-shot timings under one `peak_memory` trace; `_resolve_rate(build_stats)` = (exact+ambiguous)/total (:23-30).
- agent_suite.py — `run_agent_suite(workspace: str, db_path: str, *, runs=3, seed=DEFAULT_SEED, embed_backend="hash", progress=None) -> AgentReport` (:551); pins `CAIRN_RERANK=0` (:590); `ArmEffort(tool_calls, chars, est_tokens, wall_seconds)` → dict `tool_calls, chars, est_tokens, wall_ms` (:392-406); `TaskEffort(label, question, cairn, control)` + reduction `calls_pct/tokens_pct/time_ratio` (:410-438); `AgentReport.to_dict()` adds `seed, runs, embed_backend, chars_per_token (=4), tasks, totals` (:459-475); `CHARS_PER_TOKEN = 4` (:58); control arm caps alternation greps at `_MAX_GREP_NAMES = 40` (:63); result cap mirrors `mcp_server.metric_buffering.MAX_RESULT_CHARS` (:71-75); six task labels: definition-lookup, caller-enumeration, blast-radius-depth3, entry-to-leaf-flow, concept-search, common-name-impact (:283-319); `compare_agent_reports(baseline, current, threshold=0.15)` (:521).

eval.py public functions — `load_eval_queries(path: Optional[Path] = None)`, `evaluate_l1_query(conn, query, expect, k=10)`, `evaluate_l5_query(conn, bundle_root, query, expect, k=10)`, `run_evaluation(conn, bundle_root=None, queries_path=None, corpus_filter="all", k=10)`; report dict `{"L1": {"count", "recall_at_10", "mrr"}, "L5": {...}}` (eval.py:155-167). CLI: cli/system.py:491 `@main.command(name="eval")` eval_cmd(db, knowledge, corpus, queries_path, as_json).

CI `bench` job full step list (ci.yml:261-361): 1) actions/checkout@v5; 2) actions/setup-python@v6 (3.12, pip cache); 3) "Install" `pip install -e .`; 4) "Restore rolling bench baseline" actions/cache@v4 (path bench-baseline.json; key `bench-baseline-v1-${{ github.run_id }}`; restore-keys `bench-baseline-v1-`); 5) "Run bench (fixed corpus, hash backend)" (continue-on-error) — `cairn bench --suite perf --n-files 60 --complexity medium --embed-backend hash --repeats 3 --json --save bench-current.json`; 6) "Compare vs baseline (advisory summary)" (always, continue-on-error) — `python .github/scripts/bench_compare.py` (advisory THRESHOLD=0.25, never exits non-zero, writes bench-comment.md + step summary, marker `<!-- cairn-bench-advisory -->`); 7) "Refresh baseline payload for cache save" — `cp bench-current.json bench-baseline.json`; 8) "Upload bench result" actions/upload-artifact@v6 (name bench-result); 9) "Post advisory PR comment" actions/github-script@v7 (PRs only, updates/creates comment keyed on the marker).

Version/machine-profile substrate — `cairn.__version__` = "0.11.0" (src/cairn/__init__.py:2, matches pyproject.toml:7); nearest existing profile stamp is cli/system.py:1329 `_report_versions` (`cairn/python/platform/sqlite/db_schema_user_version`). No `platform.machine()`/`os.cpu_count()` stamping exists in bench (cpu_count appears only in graph/builder.py:215 for worker sizing).

Counts re-verified this session — _fill_: 47 occurrences / 15 lines / 3 families (docs/benchmarks.md:60-61,109-117,143-146). Eval fixture: 40 queries = 30 L1 + 10 L5, keys {corpus, expect, query}. NOTICE: 82 lines, deps-only. tests/fixtures: 176K. Bench-related tests: tests/test_bench.py + tests/test_agent_suite.py → "34 passed in 4.56s"; tests/test_cli_smoke.py (includes cairn eval smoke) → "2 passed in 68.37s".

Claims NOT re-verified (from prior docs, marked as claims): "closure build 340 s at 1,000 files" (docs/phases/benchmark-datasource/spec.md:22-23) — unknown — verify (would require a scaling run, minutes-scale); "rerank-gate calibration fell back to cairn's own src/" (same doc) — unknown — verify (no code pointer found this session).
