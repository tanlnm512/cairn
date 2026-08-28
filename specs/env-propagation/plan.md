# Plan: env-propagation

**Spec**: [spec.md](spec.md) | **Survey**: [survey.md](survey.md) (baseline 0.16.0 @ fe7a7f0, verify addendum green) | **Created**: 2026-08-28
**Branch**: `feat/env-propagation` | **Team**: solo (Stanley), one-PR-per-feature

## Milestones
<!-- Each milestone = a phase in task.md. Every FR appears exactly once.
     Per CONSTITUTION C-02, every phase opens with its failing-test-first
     task; per C-04, process-spawning tests use tmp_path and never patch the
     global subprocess.Popen. -->

| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | stdio registration env propagation | With `CAIRN_HOME=/custom`, `cairn install-agents --stdio` writes env-bearing stdio registrations across the 8 in-scope generators; with default home, output is byte-identical to today (AC1, AC5). Includes the Unknown #3 spike (external CLI env contract for global-scope claude/droid). | FR-001 | — |
| 2 | hook + LaunchAgent env propagation | Generated hook command strings carry the cairn-home assignment (claude/cursor settings + git post-commit); on macOS with custom home, `serve start` writes a LaunchAgent whose `EnvironmentVariables` includes `CAIRN_HOME` (AC2, AC3). | FR-002, FR-003 | — (shares `_common.py` with Phase 1 — sequence those tasks, see map) |
| 3 | store-open error clarity | A process pointed at a missing store dir fails with the resolved DB path, the env resolution chain, and the fix (set `CAIRN_HOME` / run `cairn init && cairn build`) — not a bare `sqlite3.OperationalError` (AC4). | FR-004 | — |
| 4 | machine-readable resolution probe | One command emits `{cairn_home, workspace, db, knowledge}` as JSON for the invoking environment (foundation for Phases 5-6). | FR-005 | — |
| 5 | install-time verification | `install-agents` spawns each written stdio registration's exact command+env (cwd = target workspace), compares the probe's resolved store with the install-time store, and prints per-client PASS/FAIL; a would-be-mismatch FAIL names both stores (AC6, AC7). | FR-006 | Phase 1, Phase 4 |
| 6 | doctor environment check | `cairn doctor` gains an environment check that FAILs the #70 machine state (populated custom store, empty default, SSE registration, no daemon, non-darwin) naming client/store mismatch + platform limitation; a healthy default install PASSes with all 9 existing checks unchanged (AC8, AC9). Updates the frozen check-sequence test (`tests/test_doctor.py:96-124`, 9 → 10 names). | FR-007 | Phase 4, Phase 5 |

Smallest-first note: Phase 1 is not the smallest but is the riskiest (8 generators, Unknown #3 external CLIs) and unblocks Phase 5, so it leads. Phases 2-4 are small and independent — pull any of them forward if Phase 1 blocks on the spike.

## Dependencies

```
P1 (stdio env) ──┬──────────────► P5 (install verify) ──► P6 (doctor env)
P4 (probe) ──────┴──► P5, and P4 ──► P6
P2 (hooks ∥ plist) — independent of all
P3 (store-open error) — independent of all
```

- **P1 → P5**: FR-006 verifies "each registration's exact command+env" — the env-block shape and the non-default-home helper are produced in P1 and consumed in P5 (survey: install loop `agent_install/__init__.py:299-304` writes what verification must spawn; `InstallResult` at `_common.py:36-44` grows the PASS/FAIL field).
- **P4 → P5**: FR-006 compares "the probe's resolved store" — no probe, no comparison source (spec names FR-005 in FR-006's contract).
- **P4 → P6 and P5 → P6**: the doctor's registration-consistency audit (survey: `system.py:1466-1476` check list gains one check; consistency machinery `detect.py:150-211` + `lifecycle.py:418-440` sse_responds) reuses the probe (P4) and the spawn-and-compare mechanism built for install verification (P5). Building P6 before P5 would duplicate that mechanism.
- **No other edges**: P2 and P3 touch disjoint subsystems from everything else and from each other.

## Parallelization map
<!-- Parallel is the default; serial entries must justify themselves.
     The task-breaker turns this into [P] markers and (after T###) chains. -->

**Independent areas — assume concurrent** (file lists prove disjointness):

| Area | Phase | Files touched |
|------|-------|---------------|
| A · stdio config generators | 1 | `src/cairn/agent_install/_common.py` (`mcp_config_json` :111-117 + new non-default-home helper), `clients/claude.py` (:89), `clients/cursor.py` (:52), `clients/droid.py` (:67), `clients/omp.py` (:58) — the 5 `mcp_config_json` callers confirmed via call graph — plus custom-shape generators `clients/zcode.py` (:48-52), `clients/opencode.py` (:53-54), `clients/kilo.py` (:28), `clients/agy.py` (:50-54). `claude_desktop.py` is **out of scope** (spec Out-list: Claude Desktop already pins `CAIRN_WORKSPACE`), even though survey counts it among the 9 generators. |
| B1 · hook command generators | 2 | `src/cairn/agent_install/_common.py` (`_claude_hook_command` :125-127; its 4 call sites in `clients/claude.py:45-63`, `clients/cursor.py:26-37`), `src/cairn/hooks/git_hooks.py` (:32-38). |
| B2 · LaunchAgent plist | 2 | `src/cairn/mcp_server/lifecycle.py` (`render_plist` :84-97 — single caller `serve_start` confirmed), `src/cairn/cli/serve.py` (:130-135). |
| C · store-open error | 3 | `src/cairn/mcp_server/server.py` (:213-234 boot guard), `src/cairn/graph/schema.py` (get_db path :729-754, per survey gap), `tests/test_server_robustness.py`. |
| D · resolution probe | 4 | `src/cairn/cli/core.py` (`config` command area :150-191; additive — `resolve_store`'s 29 existing callers are untouched). |
| E · install verification | 5 | `src/cairn/agent_install/__init__.py` (:299-304 loop), `src/cairn/agent_install/_common.py` (`InstallResult` :36-44), `src/cairn/cli/agents.py` (:157-174 report). |
| F · doctor environment check | 6 | `src/cairn/cli/system.py` (check list :1466-1476, `_result` :742-744, `--json` :1523-1524, exit contract :1527-1528), `tests/test_doctor.py` (:96-124 frozen sequence). |

- **A ∥ C ∥ D**: zero file overlap; fully concurrent.
- **B1 ∥ B2**: disjoint files; concurrent halves of Phase 2.
- **A vs B1**: both touch `_common.py` but different symbols (`mcp_config_json` vs `_claude_hook_command`) — low-conflict, but **same file ⇒ sequence the two tasks** (A's task before B1's, or vice versa; one chain owns `_common.py` until both land).
- **E vs A/B1**: E also touches `_common.py` (`InstallResult`) — E is already ordered after A by the dependency graph, so the shared file resolves itself; B1 and E never run concurrently.

**Strictly ordered — the exceptions (burden of proof met)**:
1. **A → E**: E spawns and judges the exact configs A writes; without A's env blocks there is nothing to verify and no FAIL case for AC7.
2. **D → E**: FR-006's comparison source is the probe's output.
3. **D → F and E → F**: F's stdio-consistency sub-audit reuses the probe (D) and the spawn-and-compare mechanism (E); F also consumes A's semantics (what a stale vs env-bearing registration looks like — the WARN/FAIL severity split). The frozen 10-name check-sequence test is touched exactly once, in F.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. Canonical
     local invocation: `uv run --extra test pytest ...` (orchestrator addendum). -->

- **After Phase 1**: `uv run --extra test pytest tests/test_install_uninstall_fidelity.py tests/test_clients.py -q` green with the new env-block tests added — existing default-home shape assertions untouched (AC5 byte-identical). Observable: `CAIRN_HOME=/custom uv run cairn install-agents --stdio <ws>` then the written `.mcp.json` (and per-shape variants) carry `env.CAIRN_HOME=/custom`. Spike verdict on Unknown #3 recorded (global-scope claude/droid: env embedded, or documented limitation + install-time warning).
- **After Phase 2**: `uv run --extra test pytest tests/test_install_uninstall_fidelity.py::TestHookIdempotency tests/test_port_dry_and_unload_fixes.py -q` green plus new tests asserting (a) the CAIRN_HOME assignment inside generated hook command strings (entrypoint-based uninstall matching at `merge.py:274-298` / `_common.py:130-139` still matches) and (b) plist `EnvironmentVariables` content — no such assertion exists today (survey gap). Observable: rendered hook string + plist dump show `CAIRN_HOME` when custom, nothing when default.
- **After Phase 3**: `uv run --extra test pytest tests/test_server_robustness.py::TestStoreExistenceCheck tests/test_doctor.py::test_schema_fail_unopenable_db -q` green plus a new test asserting the message contains the resolved DB path, the env values in effect, and the `CAIRN_HOME`/`cairn build` remediation (and does not interpolate the raw OperationalError text alone). Observable: point a spawn at a missing store dir → read the error.
- **After Phase 4**: probe surface emits the 4-field JSON, exit 0 — e.g. `uv run cairn config --json` (exact flag/command per tech-spec; survey shows today's surface is text-only `core.py:186-191` plus db-only `--db` :150-170). Observable: parse the output as JSON and find all four keys.
- **After Phase 5**: install fidelity suite green plus new verification tests (PASS path on a fresh install; FAIL path with env dropped naming both stores — AC7). Observable: run `install-agents --stdio` with a custom home and read per-client PASS/FAIL in the report (`agents.py:157-174` area).
- **After Phase 6**: `uv run --extra test pytest tests/test_doctor.py -q` green including the updated sequence test — now 10 names, existing 9 unchanged and in order. Observable: a #70-shaped fixture (populated custom store, empty default, SSE registration, no daemon, non-darwin) yields the environment check FAIL naming mismatch + platform; a healthy default install PASSes (AC8, AC9). `--json` output carries the new check; `report`'s scrubber path (`system.py:1593-1607`) still renders it.

## Risks & mitigations
- **Unknown #3 — external CLI env contract** (`claude mcp add --scope user`, `droid mcp add`): may not accept/persist env. → Day-1 spike inside Phase 1, before generator work; fallback is a documented limitation + install-time warning + doctor WARN for those two registrations. Plan assumption: the spike concludes within Phase 1.
- **Claude Desktop scope tension**: survey counts 9 stdio generators; the spec's Out-list excludes Claude Desktop changes. → Phase 1 scope is the 8 non-desktop generators; assumption recorded here for the approval gate.
- **SSE is the default transport** (survey surprise: `agent_install/__init__.py:287-288`, `agents.py:154`): FR-001's env applies to stdio only. → All Phase 1/5 demo and verify commands pass `--stdio` explicitly; SSE endpoint URLs stay env-free per spec scope.
- **Stale registrations written before this change** lack env blocks. → Spec risk ruling stands: doctor severity is mixed — FAIL only on provable wrong-store/unreachable, WARN on merely-missing env (Phase 6 must implement the split, not blanket-FAIL).
- **Import-time `CAIRN_HOME` binding** (`paths.py:31-33`; only `cli/uninstall.py:30-32` re-reads lazily): plist propagation works because the daemon is a fresh process, but the probe (D) and any in-process comparison (E) must agree on when env is read. → Tech-spec rules on lazy vs import-time read for the probe; flagged, not decided here.
- **C-04 test isolation** for Phases 4-6 (process spawning): never patch the global `subprocess.Popen`; tmp_path workspaces only so tests never touch real `~/.cairn`.

## Delivery
Branch `feat/env-propagation`; one commit per task (test-first per C-02); conventional commits; full C-01 workflow (pre-commit → PR with audit checklist → CI) per PR. PR cadence reflecting the user's Layer preference:
- **PR 1** = Phases 1-3 (Layer 0: propagation + message) — shippable and demoable on its own.
- **PR 2** = Phases 4-5 (Layer 1: probe + install-time verification).
- **PR 3** = Phase 6 (Layer 2: doctor environment check).

PRs stack or rebase sequentially; no PR merges without the previous one landing (P5 consumes P1+P4, P6 consumes P4+P5).
