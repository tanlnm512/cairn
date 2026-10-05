# Survey: grade-a-guardrails

**Created**: 2026-10-05 | **Baseline**: HEAD @ 2e7a7e7725e00f382e5192082de040f496ee0dd5
First survey for this spec. Evidence pasted verbatim from this session's
rg/read/pytest output. Existing persistent context (`specs/context/`) was read
first and is not rewritten.

## Items

```
item FR-001: "doctor --fix remediation of dead SSE agent-client registrations"
  evidence:   src/cairn/cli/system/doctor.py:1209-1211 — `@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")` / `@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")` — no `--fix` option exists.
              src/cairn/cli/system/doctor.py:1224-1226 — "Read-only -- never writes to the store."
              src/cairn/agent_install/__init__.py:132 — `def sse_daemon_reachable(sse_url: str | None = None) -> bool:`
              src/cairn/agent_install/__init__.py:145 — `url = sse_url or f"http://127.0.0.1:{lc.DEFAULT_PORT}/sse"`
              src/cairn/agent_install/_common.py:110 — `def mcp_config_json(transport: str = "stdio", sse_url: str | None = None) -> dict:`
              src/cairn/agent_install/merge.py:20 — `"""Write ``content`` to ``path`` atomically.` ; merge.py:41 — `os.replace(tmp_name, path)` ; merge.py:123 — `backup = path.with_suffix(path.suffix + ".bak")`
              No mtime-freshness refusal found: `rg -e "mtime|st_mtime" src/cairn` → no hits.
  status:     PARTIAL
  verify:     rg -n '"--fix"|st_mtime' src/cairn/cli/system/doctor.py src/cairn/agent_install && .venv/bin/pytest tests/test_doctor.py tests/test_atomic_config_writes.py -q
  gap:        No `doctor --fix` command, no ownership-scoped SSE→stdio rewrite, no mtime freshness window, no action-summary rendering. Atomic write + backup machinery already exists in agent_install/merge.py.
```

```
item FR-002: "resolution-quality report (exact/ambiguous/unresolved per language, --json)"
  evidence:   src/cairn/graph/schema.py:386 — `resolution_exact INTEGER, resolution_ambiguous INTEGER, resolution_unresolved INTEGER,` (build_runs table).
              src/cairn/graph/schema.py:1104-1105 — `"edges, resolution_exact, resolution_ambiguous, resolution_unresolved, "` (retained telemetry columns).
              src/cairn/cli/system/report.py:200-223 — `_build_report` returns keys `generated_at`, `versions`, `doctor`, `recent_errors`, `config` only; no resolution section.
              src/cairn/mcp_server/tools_graph.py:490 — `out.append(f"  {hop.file}:{line} {hop.symbol} [{hop.resolution}]")` (path listing only, not a per-language count report).
              No per-language edge resolution aggregation found: `rg "resolution" src/cairn/cli/system/report.py` → only line 192 config comment.
  status:     PARTIAL
  verify:     rg -n resolution src/cairn/cli/system/report.py src/cairn/graph/schema.py
  gap:        Raw per-edge `resolution` labels and aggregate build_runs counters exist; no report surface (doctor section / system report / dedicated flag) computes exact/ambiguous/unresolved counts+percentages per language, and no zero-edges store handling.
```

```
item FR-003: "per-tool p95 latency budget gate (≥10× observed) on main/merge-group bench job"
  evidence:   src/cairn/bench/perf_suite.py:178-202 — ops list includes `("find_definition", lambda: q.find_definition(conn, query_target, limit=5))`, `("search_symbols", ...)`, `("get_callers", ...)`, `("impact_analysis", ...)`, `("semantic_search", ...)`, `("explore", lambda: q.explore(conn, query_target))`.
              src/cairn/bench/perf_suite.py:205 — `timing, _ = time_call(fn, name=name, warmup=1, repeats=query_repeats)` (warm-up already applied).
              src/cairn/bench/timing.py:25 — `p95: float = 0.0` ; timing.py:53 — `"p95": qs[94],`
              .github/workflows/ci.yml:353 — "Every bench step is continue-on-error: a timing regression (bench exits" ; ci.yml "Run bench (fixed corpus, hash backend)" step is `continue-on-error: true` (read at HEAD).
              .github/workflows/ci.yml:245-249 — "Closure budget gate: enforcing, unlike the advisory bench job" (enforcing budgets exist only for scaling-suite closure/path/communities, not tool latency; tests/test_scaling_gate.py:70-106).
  status:     PARTIAL
  verify:     rg -n "find_definition|continue-on-error|p95" src/cairn/bench/perf_suite.py .github/workflows/ci.yml && rg -n p95 src/cairn/bench/timing.py
  gap:        No per-tool p95 budget constants, no ≥10× sizing, no hard-gate step in the main/merge-group bench job (bench timing is advisory via continue-on-error), no breach message naming tool/measured/budget.
```

```
item FR-004: "scheduled weekly workflow replaying checked-in high-signal CLI commands on a fixture repo"
  evidence:   ls .github/workflows → ci.yml, extension-release.yml, release.yml, review.yml (no weekly/scheduled workflow).
              rg -n -i "schedule|cron" .github/workflows → no hits.
  status:     TODO
  verify:     rg -n -i "schedule|cron" .github/workflows || true
  gap:        No scheduled workflow, no checked-in command list, no deterministic fixture repo wiring, no fail-fast red-on-first-failing-command step.
```

```
item FR-005: "make verify-protection via gh read-only against branch-protection + rulesets APIs"
  evidence:   Makefile `.PHONY: dist evals ci-local ci-local-all verify-no-code-change release help` (read at HEAD; no verify-protection target).
              rg -n "verify-protection|rulesets|branch-protection" across repo → hits only in specs/grade-a-guardrails/spec.md and spawn payload (spec text, not implementation).
              docs/release-checklist.md:124 — "branch-protected (changes must go through a PR with required status checks)," (documented statement only; no automated check).
  status:     TODO
  verify:    rg -n "verify-protection|rulesets|branch-protection" Makefile docs .github scripts
  gap:        No make target, no gh read-only script covering legacy branch-protection API + rulesets API, no checklist-settings coverage, not maintainer-run documented.
```

```
item NFR-001: "doctor --fix security: cairn-owned configs only, backup, no secrets, atomic"
  evidence:   src/cairn/agent_install/merge.py:20-41 — atomic sibling-temp + `os.replace` write; merge.py:123-143 — `.bak` backup with never-clobber of existing backups.
              tests/test_atomic_config_writes.py — `test_no_temp_file_leaked_on_success` (:26), `test_malformed_json_backed_up_not_clobbered` (:87), `test_non_object_key_dry_run_reports_backup_without_touching_disk` (:197).
              Ownership scoping for fix writes: unknown — verify (no `--fix` implementation exists to inspect).
              Secret handling in a fix path: unknown — verify.
  status:     PARTIAL
  verify:     .venv/bin/pytest tests/test_atomic_config_writes.py -q
  gap:        Atomic + backup + malformed-file preservation proven for the existing install path; ownership scoping and no-secret-logging for `doctor --fix` are unimplemented and untestable until FR-001 exists.
```

```
item NFR-002: "weekly workflow privacy: checked-in commands + local fixtures, no telemetry"
  evidence:   No scheduled workflow exists (FR-004 evidence: rg "schedule|cron" .github/workflows → no hits).
              Telemetry is local-buffered with optional export (src/cairn/telemetry/ exists; default behavior unknown — verify).
  status:     TODO
  verify:     rg -n -i "schedule|cron" .github/workflows || true
  gap:        Cannot hold before FR-004; no workflow to constrain, no no-egress assertion.
```

```
item NFR-003: "performance n/a — budget gate consumes existing perf-suite runs"
  evidence:   src/cairn/bench/perf_suite.py:178-205 — the six required tools are already the perf suite's measured query ops; no new hot path needed for a gate.
  status:     TODO
  verify:     rg -n "find_definition|explore" src/cairn/bench/perf_suite.py
  gap:        Tracks FR-003: the gate does not exist yet, though no new measurement hot path is required.
```

```
item NFR-004: "doctor --fix idempotent and crash-safe"
  evidence:   src/cairn/agent_install/merge.py:20-23 — "Writes to a sibling temp file then ``os.replace``s it into place, which is atomic on POSIX (and same-volume Windows). A crash (OOM, SIGKILL, full" ; merge.py:41 — `os.replace(tmp_name, path)`.
              Idempotence of a fix pass: unknown — verify (no `--fix` exists).
  status:     PARTIAL
  verify:     .venv/bin/pytest tests/test_atomic_config_writes.py -q
  gap:        Crash-safety primitive exists and is tested; second-run-zero-actions idempotence has no implementation or test.
```

```
item NFR-005: "every instrument machine-readable via --json"
  evidence:   src/cairn/cli/system/doctor.py:1210 — `@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")` ; doctor.py:1231 — `click.echo(json.dumps(results, indent=2))`.
              src/cairn/bench/perf_suite.py run supports `--json --save` (ci.yml bench step: `uv run --no-sync cairn bench --suite perf --embed-backend hash --repeats 3 \ --json --save bench-current.json`).
              Future instruments (resolution report, verify-protection, weekly replay) have no --json surface yet.
  status:     PARTIAL
  verify:     rg -n '"--json"' src/cairn/cli/system src/cairn/bench
  gap:        Existing doctor/bench are JSON-capable; FR-002/FR-004/FR-005 instruments must add their own machine-readable mode.
```

```
item NFR-006: "accessibility n/a — no UI surface touched"
  evidence:   src/cairn/cli/system/doctor.py:1209-1211 and report.py:200-223 — the touched surfaces are CLI text/JSON, no UI.
  status:     TODO
  verify:     rg -n "click.option" src/cairn/cli/system/doctor.py
  gap:        Not applicable per spec; tracked until the change set lands to confirm no UI file is touched.
```

## Supporting evidence

- Doctor read-only contract and check surface: src/cairn/cli/system/doctor.py:1145 — `def _run_doctor(db: str) -> list[dict]:` ; HEALTH_CHECKS tuple at doctor.py:42; `_check_environment` at doctor.py:1029; command at doctor.py:1211 `def doctor(db, as_json):` with docstring "Run 11 system health checks".
- Report bundle composition: src/cairn/cli/system/report.py:200-223 — `_build_report(db)` returns `{"generated_at", "versions", "doctor": _scrub_doctor(_run_doctor(db)), "recent_errors", "config"}`.
- Edge resolution storage: src/cairn/graph/schema.py:470-475 — "edges.resolution column tracks HOW an edge target was resolved" / `EDGE_RESOLUTION_MIGRATION = "ALTER TABLE edges ADD COLUMN resolution TEXT"`; per-build counters at schema.py:386.
- Config-write machinery consumers: `rg "_atomic_write_text" src/cairn/agent_install/merge.py` → call sites at merge.py:82, 206, 457, 485, 502, 566, 597 (all client-config writers).
- CI bench job: .github/workflows/ci.yml:353 advisory rule and `Run bench (fixed corpus, hash backend)` step `continue-on-error: true`; enforcing scaling budgets only in tests/test_scaling_gate.py:70-160.
- Workflow inventory (HEAD): .github/workflows/ = ci.yml, extension-release.yml, release.yml, review.yml.
- Makefile target inventory (HEAD): dist, evals, ci-local, ci-local-all, verify-no-code-change, release, help.

## Verify-command results (this session)

- `rg -n resolution src/cairn/cli/system/report.py src/cairn/graph/schema.py` — PASS (citations above).
- `rg -n -i "schedule|cron" .github/workflows` — PASS as negative evidence (no hits).
- `rg -n "verify-protection|rulesets|branch-protection" Makefile docs .github scripts` — PASS as negative evidence (spec docs only).
- `.venv/bin/pytest tests/test_atomic_config_writes.py -q` — PASS: `24 passed in 0.11s`.
- `.venv/bin/pytest tests/test_doctor.py tests/test_atomic_config_writes.py -q` — `7 failed, 63 passed`; the 7 doctor failures are sandbox `Permission` errors on socket-binding embed-server/environment tests (matches the recorded loopback-sandbox workaround class); re-run of the atomic-writes file alone is fully green.

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify commands.
- A number in an old doc is a claim, not evidence — re-count it.
