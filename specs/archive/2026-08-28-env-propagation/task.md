# Tasks: env-propagation

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Tech**: [tech-spec.md](tech-spec.md)
Status reflects code state per [survey.md](survey.md), not intent. Survey statuses: FR-004 PARTIAL, FR-005 PARTIAL, FR-001/002/003/006/007 TODO — so every task opens `- [ ]`; the FR-004/FR-005 tasks name the survey gap in their text.
**Before-audit**: passed @ fe7a7f0 (2026-08-28) — pre-commit --all-files green; core suite 26 passed/1 skipped. Local-run note: this machine's legacy flat `~/.cairn/lib` holds an ABI-incompatible numpy that shadows the venv's at import; baseline and all verify commands run under `CAIRN_LIB=/tmp/__no_such_lib__` to disable the legacy injection (CI unaffected — no `~/.cairn` there).

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1 | 8 | 8 |
| 2 | 4 | 4 |
| 3 | 3 | 3 |
| 4 | 2 | 2 |
| 5 | 3 | 3 |
| 6 | 3 | 3 |
| **Σ** | 23 | 23 |

## Phase 1: stdio registration env propagation (FR-001)
<!-- Checkpoint (plan): `uv run --extra test pytest tests/test_install_uninstall_fidelity.py tests/test_clients.py -q`
     green with the new env-block tests — existing default-home shape assertions untouched (AC5).
     Observable: `CAIRN_HOME=/custom uv run cairn install-agents --stdio` writes `env.CAIRN_HOME=/custom`
     into every file-written stdio registration. Spike verdict on Unknown #3 recorded. -->
<!-- Per CONSTITUTION C-02, every implementation task below is ordered after this phase's
     failing-test task (T001); the (after T###) markers name additional data dependencies.
     The Unknown #3 spike (T002) gates only T007 — the file-based generators' behavior is
     fixed by tech-spec regardless of the verdict (D-006 covers only the CLI-registered paths). -->
<!-- Scope ruling: plan.md Area A marked claude_desktop.py out of scope, but tech-spec D-003
     (later stage, orchestrator ruling) and TC-001 both include it — tech-spec governs (T006). -->
- [x] T001 [P] Write failing tests pinning the stdio env-block shape: embedded when non-default, absent when default (FR-001; TC-001, TC-002, TC-003)
  Files: tests/test_install_uninstall_fidelity.py, tests/test_clients.py.
  Pins: with `CAIRN_HOME` at a tmp custom home, the `mcp_config_json` stdio output and the zcode/opencode/kilo/agy shape variants carry `env.CAIRN_HOME`; with `CAIRN_HOME` unset — or explicitly set to the default path (spec Unknown #4 ruling) — output is byte-identical to today's env-less shapes (the subprocess fidelity cases survey cites at lines 513/577/590/631).
  C-02 opener: lands before this phase's implementation tasks and is expected red until they land.
  done 2026-08-28 — 12 env-block tests; 4 custom-home reds turned green by T004-T006 (fidelity+clients 87 passed)
- [x] T002 [P] Spike: probe the external registration CLIs' env contract and record the Unknown #3 verdict (FR-001; TC-017)
  Files: none — evidence-only; the verdict is recorded in this task's done-note.
  Exercise `claude mcp add cairn --scope user` and `droid mcp add cairn` (survey: claude.py:99-100, droid.py:58-59) with an env-bearing argv, then read back what each external tool actually persisted.
  Gates T007 only: verdict "env persistable" means T007 embeds via the verified flag; verdict "not persistable" means T007 ships the D-006 skip + WARN note. Plan risk: day-1 spike, must conclude within Phase 1.
  done 2026-08-28 — spike verdict: claude persists env via -e/--env (verified end-to-end, throwaway cleaned up); droid CLI absent on host; ruling D-011
- [x] T003 Implement the non-default-home predicate helpers in paths.py (after T001) (FR-001; TC-003)
  Files: src/cairn/paths.py, beside the `CAIRN_HOME` binding (paths.py:31-33).
  Consumes T001's pinned contract (env present iff non-default; default output unchanged). Symbols (tech-spec D-001/D-002): `cairn_home_is_default() -> bool` — expanded-absolute-path equality against the default home; `cairn_home_env() -> dict[str, str]` — `{}` when default, else `{"CAIRN_HOME": <expanded path>}`. Do NOT touch the import-time binding — `REGISTRY_FILE`/`CONFIG_FILE`/`SHARED_LIB` derive from it (paths.py:35/40/52); uninstall.py:30-32 stays the only lazy re-read.
  Baseline proof anchor: `uv run --extra test pytest tests/test_install_uninstall_fidelity.py -q` (61 passed, survey addendum).
  done 2026-08-28 — cairn_home_is_default/cairn_home_env land in paths.py; 3 behavior probes correct (custom/unset/set-to-default)
- [x] T004 Embed the env block in mcp_config_json's stdio branch (after T003) (FR-001; TC-001, TC-002)
  Files: src/cairn/agent_install/_common.py (`mcp_config_json`, :111-117).
  Consumes T003's `cairn_home_env() -> dict[str, str]`. Merge into the cairn entry dict only when transport is stdio and the dict is non-empty — the `{}` guard runs before any insertion or AC5 breaks. Covers the callers survey lists: claude.py:89 (.mcp.json), cursor.py:52, droid.py:67 (file fallback), omp.py:58. SSE branch (_common.py:105-110) untouched.
  Baseline proof anchor: `uv run --extra test pytest tests/test_install_uninstall_fidelity.py tests/test_clients.py -q` (61 passed, survey addendum).
  done 2026-08-28 — mcp_config_json stdio branch embeds env; family green in the 87-passed fidelity+clients run
- [x] T005 Embed the env block in the four custom-shape stdio generators (after T003) (FR-001; TC-001)
  Files: src/cairn/agent_install/clients/zcode.py (`zcode_mcp_config_json`, :48-52), src/cairn/agent_install/clients/opencode.py (:53-54), src/cairn/agent_install/clients/kilo.py (`kilo_mcp_config_json`, :28), src/cairn/agent_install/clients/agy.py (:50-54).
  Consumes T003's `cairn_home_env() -> dict[str, str]`; same `{}`-guard-first merge per shape (command+args, command ARRAY, nested mcp.servers). Disjoint files from T004 — the two run in parallel. Pitfall (tech-spec Area 2): whether these schemas honor an env key is not evidenced in survey — Phase 5 verification plus doctor sub-audit (b) is the designed catch; a client that provably drops the key gets a module docstring note and a reported gap.
  done 2026-08-28 — zcode/opencode/kilo/agy stdio shapes embed env; reported merge idempotence gap -> D-012/T023
- [x] T006 Add CAIRN_HOME alongside the CAIRN_WORKSPACE pin in the claude-desktop generator (after T003) (FR-001; TC-001)
  Files: src/cairn/agent_install/clients/claude_desktop.py (`mcp_config_json_desktop`, :32-35).
  Consumes T003's `cairn_home_env() -> dict[str, str]`. Tech-spec D-003: the env dict becomes the existing `CAIRN_WORKSPACE` pin plus `CAIRN_HOME` when non-default; the pin is never removed (desktop has no cwd/workspace notion, claude_desktop.py:26-27). Idempotence already compares env dicts (merge.py:256-258).
  done 2026-08-28 — desktop env = CAIRN_WORKSPACE pin + CAIRN_HOME when non-default; verified in all three home states
- [x] T007 Resolve env handling for the CLI-registered global claude/droid registrations (after T002) (FR-001; TC-017)
  Files: src/cairn/agent_install/clients/claude.py (:99-100), src/cairn/agent_install/clients/droid.py (:58-59).
  Consumes T002's verdict, recorded in its done-note: "persistable" means embed env via the verified external-CLI flag; "not persistable" means the D-006 degradation — no embedding attempt, a WARN note naming the gap and pointing at scope=workspace, and no spawn-verify for these two paths (Phase 5 honors the same skip; doctor sub-audit (b) still audits whatever check_installed can read).
  done 2026-08-28 — claude global argv gains -e CAIRN_HOME=<abs>; droid CLI keeps skip+WARN per D-011; pinned argv cases green

- [x] T023 Fix `_already_installed` env comparison for the zcode and opencode/kilo shapes (FR-001; TC-001) — added post-Phase-1 per D-012 (T005 finding)
  Files: src/cairn/agent_install/merge.py (the zcode branch and the opencode/kilo command-array branch of `_already_installed`), tests/test_atomic_config_writes.py.
  Pins: reinstall with a CHANGED custom CAIRN_HOME rewrites the registration (stale env replaced) in the zcode/opencode/kilo shapes; reinstall on default home removes the env key; unchanged env stays byte-stable (idempotent).
  done 2026-08-28 — env comparison in zcode + opencode/kilo _already_installed branches; 9 new cases green (atomic+fidelity+clients 97 passed)

## Phase 2: hook + LaunchAgent env propagation (FR-002, FR-003)
<!-- Checkpoint (plan): `uv run --extra test pytest tests/test_install_uninstall_fidelity.py::TestHookIdempotency tests/test_port_dry_and_unload_fixes.py -q`
     green plus new tests asserting the CAIRN_HOME assignment inside generated hook command strings
     (entrypoint-based uninstall matching at merge.py:274-298 / _common.py:130-139 still matches) and
     plist EnvironmentVariables content. Observable: rendered hook string + plist dump show CAIRN_HOME
     when custom, nothing when default. -->
<!-- T010 touches _common.py, which Phase 1's T004 also touches — plan's same-file rule is
     satisfied by phase order (Phase 2 starts after Phase 1 lands). -->
- [x] T008 [P] Write failing tests pinning the CAIRN_HOME assignment inside generated hook command strings and the git post-commit template (FR-002; TC-004)
  Files: tests/test_install_uninstall_fidelity.py (the hook-shape / TestHookIdempotency area).
  Pins: with a custom home, `_claude_hook_command` output carries a `CAIRN_HOME=<path> ` prefix (tech-spec D-009) in the claude shape (claude.py:45-63, merged at claude.py:133) and the cursor shape (cursor.py:26-37, merged at cursor.py:59); with the default home it adds nothing; uninstall matching on the `cairn.hooks.claude_hooks <entrypoint>` substring (merge.py:274-298, _common.py:130-139) still matches with the prefix present; the git `POST_COMMIT_TEMPLATE` (git_hooks.py:32-38) gains one quoted export line after the shebang.
  done 2026-08-28 — 9 hook tests (TestHookCairnHomePrefix); 4 red pinned for T010, uninstall-matching guards green
- [x] T009 [P] Write the first plist EnvironmentVariables assertions — survey gap: none exist today (FR-003; TC-005, TC-006)
  Files: tests/test_port_dry_and_unload_fixes.py.
  Pins: `render_plist` (lifecycle.py:84-97) includes `CAIRN_HOME` in EnvironmentVariables under a custom home and omits it under the default; the existing `PATH`/`CAIRN_WORKSPACE`/`CAIRN_DB`/`CAIRN_KNOWLEDGE` entries are unchanged (the automated half of TC-005/TC-006; loading the actual LaunchAgent stays manual-macOS per test.md).
  done 2026-08-28 — 3 plist tests (TestPlistEnvironmentVariables); custom-home red pinned for T011
- [x] T010 Implement hook env: prefix in _claude_hook_command and an export line in the git post-commit template (after T008) (FR-002; TC-004)
  Files: src/cairn/agent_install/_common.py (`_claude_hook_command`, :125-127), src/cairn/agent_install/clients/claude.py (:45-63), src/cairn/agent_install/clients/cursor.py (:26-37), src/cairn/hooks/git_hooks.py (`POST_COMMIT_TEMPLATE`, :32-38).
  Consumes T003's `cairn_home_is_default() -> bool` and `cairn_home_env() -> dict[str, str]` from src/cairn/paths.py. Tech-spec D-009: prepend `CAIRN_HOME=<path> ` inside the single command strings, shell-safe quoting (the git template interpolates repo names behind an allowlist regex, git_hooks.py:16 — quote the path the same way); one quoted `export CAIRN_HOME="<path>"` line after the template shebang; both conditional on non-default. claude_hooks.py runtime unchanged — env inheritance is proven at claude_hooks.py:44-51 (subprocess runs with no env kwarg).
  Proof anchor: `uv run --extra test pytest tests/test_install_uninstall_fidelity.py::TestHookIdempotency -q` (baseline green per addendum) plus T008's new tests.
  done 2026-08-28 — CAIRN_HOME prefix in _claude_hook_command + quoted export in git template; fidelity+clients 82 passed
- [x] T011 Implement plist env: render_plist sets EnvironmentVariables.CAIRN_HOME when non-default (after T009) (FR-003; TC-005, TC-006)
  Files: src/cairn/mcp_server/lifecycle.py (`render_plist`, :84-97). Caller `serve_start` (src/cairn/cli/serve.py:130-135) unchanged.
  Consumes T003's `cairn_home_env() -> dict[str, str]` from src/cairn/paths.py. Tech-spec D-010: consult the helper inside `render_plist` (it already reads `PATH` from os.environ inline); no signature change — sole caller serve_start per the tech-spec session graph query.
  Proof anchor: `uv run --extra test pytest tests/test_port_dry_and_unload_fixes.py -q` (6 passed baseline, survey addendum) plus T009's assertions.
  done 2026-08-28 — render_plist folds cairn_home_env() into EnvironmentVariables; port suite 9 passed

## Phase 3: store-open error clarity (FR-004)
<!-- Checkpoint (plan): `uv run --extra test pytest tests/test_server_robustness.py::TestStoreExistenceCheck tests/test_doctor.py::test_schema_fail_unopenable_db -q`
     green plus a new test asserting the message contains the resolved DB path, the env values in
     effect, and the CAIRN_HOME / cairn init + cairn build remediation. Observable: point a spawn at
     a missing store dir and read the error. -->
- [x] T012 [P] Write failing tests asserting the missing-store error names the resolved path, the env chain, and the remediation (FR-004; TC-007, TC-008)
  Files: tests/test_server_robustness.py (the TestStoreExistenceCheck area).
  Pins: a process pointed at a missing store directory exits 1 with output containing the resolved db path, the `CAIRN_HOME`/`CAIRN_WORKSPACE`/`CAIRN_DB`/`CAIRN_KNOWLEDGE` values in effect, and the set-`CAIRN_HOME` / `cairn init && cairn build` remediation — not the bare OperationalError text alone. Survival contracts pinned verbatim: the doctor detail keeps "cannot open database" (test_doctor.py:132-143) and the exit-code-1 contract (test_server_robustness.py:30-45).
  Survey gap (FR-004 PARTIAL): the boot guard (server.py:213-234) interpolates the raw exception and never prints db_path (already in scope at server.py:210) or the env values, and its remediation never mentions CAIRN_HOME; no equivalent guard exists on the CLI get_db path (schema.py:729-754 raises raw).
  done 2026-08-28 — 3 message-content reds pinned (server path x2 -> T013; CLI get_db -> T014); survival contracts green
- [x] T013 Implement the env-resolution-chain renderer in paths.py and enrich the server boot-guard message (after T012) (FR-004; TC-007)
  Files: src/cairn/paths.py (new helper), src/cairn/mcp_server/server.py (boot guard, :213-234).
  Contract for T014: src/cairn/paths.py gains `render_env_resolution_chain() -> str` — the `CAIRN_HOME`/`CAIRN_WORKSPACE`/`CAIRN_DB`/`CAIRN_KNOWLEDGE` values (or "unset") plus the resolved db path, per the chain at paths.py:285-302 (resolve_workspace) and paths.py:305-318 (resolve_store). Tech-spec D-008: the server message becomes resolved path + chain + CAIRN_HOME remediation, keeping the existing exit-1 behavior.
  done 2026-08-28 — render_env_resolution_chain() + enriched boot guard; 54 passed with T014's red intact
- [x] T014 Add the get_db parent-directory pre-check raising the enriched error (after T013) (FR-004; TC-008)
  Files: src/cairn/graph/schema.py (get_db path, :729-754; raw sqlite open at :751-754).
  Consumes T013's `render_env_resolution_chain() -> str` from src/cairn/paths.py. Tech-spec D-008: when the store's parent directory is missing, raise `sqlite3.OperationalError` (same type) whose text carries the resolved path + chain + remediation — the doctor prepends its own "cannot open database: " prefix (system.py:1426) and test_doctor.py:132-143 must survive unchanged.
  Proof anchor: `uv run --extra test pytest tests/test_server_robustness.py::TestStoreExistenceCheck tests/test_doctor.py::test_schema_fail_unopenable_db -q` (3 passed baseline, survey addendum).
  done 2026-08-28 — get_db parent-dir pre-check raising enriched OperationalError; server_robustness+doctor 55 passed

## Phase 4: machine-readable resolution probe (FR-005)
<!-- Checkpoint (plan): the probe surface emits the 4-field JSON, exit 0. Observable: parse the
     output as JSON and find all four keys. -->
- [x] T015 [P] Write failing tests for the machine-readable resolution probe (FR-005; TC-009)
  Files: tests/test_config_probe.py (new).
  Pins: `cairn config --json` exits 0 emitting a JSON object with the keys `cairn_home`, `workspace`, `db`, `knowledge` matching the environment in effect (custom and default home runs via tmp_path + env; C-04: no global subprocess patching, no eager cairn.cli import — drive through the CLI test runner); the probe is read-only — running it must NOT auto-register the cwd workspace (the resolve_store side effect, paths.py:305-318 docstring).
  Survey gap (FR-005 PARTIAL): `cairn config` prints all four values text-only (core.py:186-191) and `--db` covers one field (core.py:150-170); no JSON probe emitting all four exists.
  done 2026-08-28 — 4 probe tests red on missing --json (test_config_probe.py); collection clean
- [x] T016 Implement the `--json` flag on cairn config as the resolution probe (after T015) (FR-005; TC-009)
  Files: src/cairn/cli/core.py (the config command, :150-191).
  Consumes T015's pinned output contract. Tech-spec D-004: one `resolve_store()` call (`StorePaths`, paths.py:101-114) emitting the four spec keys; read-only — suppress the auto-register side effect, because the Phase 5/6 verifiers spawn it with arbitrary cwd; `--db` and the text output unchanged (`uv run cairn config --db` stays exit 0 printing only the db path).
  This command's exact output is the spawn-probe contract Phases 5-6 consume: keys `cairn_home`, `workspace`, `db`, `knowledge`.
  done 2026-08-28 — cairn config --json emits the 4 keys read-only; 4 passed; --db and text mode unchanged

## Phase 5: install-time verification (FR-006)
<!-- Checkpoint (plan): install fidelity suite green plus new verification tests (PASS path on a
     fresh install; FAIL path with env dropped naming both stores — AC7). Observable: run
     install-agents --stdio with a custom home and read per-client PASS/FAIL (agents.py:157-174 area). -->
<!-- Consumes Phase 1 (T003 helpers, T004-written configs) and Phase 4 (T016 probe) — plan's
     P1→P5 and P4→P5 edges are provided by phase order; no cross-phase (after) chains. -->
- [x] T017 [P] Write failing tests for install-time per-client verification (FR-006; TC-010, TC-011)
  Files: tests/test_install_uninstall_fidelity.py.
  Pins: after a stdio install under a custom home, every file-written client carries a verification verdict — PASS on a healthy install (TC-010); the FAIL path, reproducible with a PATH-shadowed cairn wrapper that drops `CAIRN_HOME` (TC-011's shape), names both the resolved and the intended store; dry_run never spawns; SSE and CLI-registered clients get no verdict (D-006 scope). C-04: tmp_path workspaces only; never patch the global subprocess.
  done 2026-08-28 — 5 install-verification tests (2 red on missing verdicts -> T019; 3 skipped-guards green by getattr)
- [x] T018 Add the defaulted verification fields to InstallResult (after T017) (FR-006; TC-010)
  Files: src/cairn/agent_install/_common.py (`InstallResult`, :36-44 — today client/written/skipped/notes only).
  Contract for T019: two defaulted fields — `verification_status: str = "skipped"` (values pass / fail / skipped) and `verification_detail: str = ""` (on fail: both store paths). Defaulted because `InstallResult` has 24 construction sites (9 installers, install_cross_tool, 2 uninstall, 12 in tests — tech-spec session count).
  done 2026-08-28 — InstallResult gains verification_status/verification_detail defaults; reds shift from AttributeError to status
- [x] T019 Implement the spawn-probe verify loop in install() plus per-client PASS/FAIL report rendering (after T018) (FR-006; TC-010, TC-011)
  Files: src/cairn/agent_install/__init__.py (the install() loop, :299-304), src/cairn/cli/agents.py (report printer, :157-174).
  Consumes T018's `verification_status`/`verification_detail` fields on `InstallResult` and Phase 4's probe contract (`cairn config --json` emitting `cairn_home`/`workspace`/`db`/`knowledge`). Tech-spec D-005: for each stdio file-written registration, read the written config back and spawn the registration's exact binary + env with probe args substituted for serve (`[command, "config", "--json"]`), cwd = target workspace; compare `db` + `workspace` against the installer process's `resolve_store()`; timeout the probe (~1s per spec assumption) and treat spawn failure as FAIL naming both stores; SSE and CLI-registered clients are skipped with a note; dry_run never spawns; `sse_daemon_reachable` (:184-210) untouched.
  Contract for T022: expose the spawn-and-compare as a reusable function in src/cairn/agent_install/__init__.py — `verify_registration(command: list[str], env: dict[str, str], cwd: Path, expected: dict[str, str]) -> tuple[str, str]` returning (status, detail) — so the doctor's consistency audit reuses it instead of duplicating the mechanism.
  Proof anchor: `uv run --extra test pytest tests/test_install_uninstall_fidelity.py -q` (baseline green per addendum) plus T017's tests.
  done 2026-08-28 — verify_registration + install()-loop verify + report rendering; fidelity+clients 87 passed

## Phase 6: doctor environment check (FR-007)
<!-- Checkpoint (plan): `uv run --extra test pytest tests/test_doctor.py -q` green including the
     updated sequence test — now 10 names, existing 9 unchanged and in order. Observable: a #70-shaped
     fixture yields the environment check FAIL naming mismatch + platform; a healthy default install
     PASSes (AC8, AC9). --json carries the new check; report's scrubber path (system.py:1593-1607)
     still renders it. -->
<!-- Consumes Phase 4 (probe) and Phase 5 (spawn-and-compare) — plan's P4→P6 and P5→P6 edges are
     provided by phase order; no cross-phase (after) chains. -->
- [x] T020 [P] Write failing tests for the doctor environment check and update the frozen sequence test to 10 names (FR-007; TC-012, TC-013, TC-014, TC-015)
  Files: tests/test_doctor.py.
  Pins: a #70-shaped fixture (populated custom store, empty default store, SSE registration, no daemon, non-darwin — built with tmp_path + monkeypatched env/paths, no global subprocess patching) yields an `environment` FAIL naming the client/store mismatch and the macOS-only lifecycle (TC-012's automated half); a healthy default install PASSes with the prior 9 checks unchanged in name and order — the sequence test at test_doctor.py:96-124 becomes 10 names with "environment" appended; the degraded db-unavailable return path carries the check too; a stale registration resolving the right store draws a WARN with re-run-install-agents advice and exit stays 0 (TC-015). TC-012/TC-014 remain manual-Linux observables in test.md; these are their automated fixture halves.
  done 2026-08-28 — 4 doctor env tests + both 10-name sequence pins in test_doctor.py; flagged test_report.py _DOCTOR_NAMES gap
- [x] T021 Implement _check_environment: store-existence, platform/transport, and binary-coherence sub-audits (after T020) (FR-007; TC-013, TC-014, TC-016)
  Files: src/cairn/cli/system.py (`_run_doctor` check list :1466-1476 plus the degraded db-unavailable return path; `_result` shape :742-744).
  Consumes T020's pinned expectations. Tech-spec D-007: append `_check_environment()` to BOTH return paths — the environment audit needs no db connection, so it must appear precisely when the store is broken; status = worst sub-audit (FAIL over WARN over PASS). Sub-audit (a): resolved-store existence, WARN when missing with hint `cairn init` + `cairn build` (the system.py:1451-1454 pattern — mixed ruling forbids a second FAIL). Sub-audit (c): SSE registration on non-darwin WARN naming the macOS-only lifecycle (`is_macos` lifecycle.py:27-28; message per lifecycle.py:383-386). Sub-audit (d): binary coherence — `resolve_cg_command` (_common.py:67-76) vs `cg_bin` (lifecycle.py:43-53) — WARN naming both on mismatch. Exit contract (system.py:1527-1528) and `--json` (system.py:1523-1524) need no change; details must stay scrub-safe through `_scrub_doctor` (system.py:1593-1607). The frozen sequence test (T020) goes green in this same change.
  done 2026-08-28 — _check_environment sub-audits a/b-lite/c/d on both _run_doctor paths; 43 passed, #70 FAIL left for T022; authorized pin: _DOCTOR_NAMES in tests/test_report.py gains 'environment'
- [x] T022 Implement the registration-consistency sub-audit: spawn-probe stdio registrations, SSE reachability, mixed severity (after T021) (FR-007; TC-012, TC-015, TC-016)
  Files: src/cairn/cli/system.py (the `_check_environment()` scaffold T021 added).
  Consumes T021's `_check_environment()` in src/cairn/cli/system.py, Phase 4's probe contract (`cairn config --json` four keys), and T019's reusable `verify_registration(command, env, cwd, expected) -> tuple[str, str]` from src/cairn/agent_install/__init__.py. Enumerate installed clients via `check_installed` (detect.py:150-211, per-client config paths detect.py:162-209); for each stdio registration run the spawn-probe against the doctor's own store: provable different-EXISTING-store FAIL naming both stores, unreachable SSE endpoint (`sse_responds`, lifecycle.py:418-440) FAIL, merely-missing env on a stale registration WARN advising re-run `cairn install-agents` — the spec risk ruling forbids blanket-FAIL. Probes are read-only and timeout-bounded.
  done 2026-08-28 — registration-consistency sub-audit (enumerate via check_installed, spawn-probe via verify_registration, sse_responds, mixed severity); doctor+report 57 passed

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      `done <date> — <test/command that proves it>`
- Dropped: `- [ ] ~~T023~~ dropped <date> (D-###)` — never delete the line;
      dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
      chained tasks note `(after T###)` and name the exact interface they
      consume from their upstream — symbols, signatures, file formats; serial
      runs need a reason, parallel runs need none
- Fix rounds append `(fix <n>/5)` to the entry — the cap survives resume
      only if the count lives here, in the status holder
- Every task cites its FR-### and at least one TC-### from test.md; tasks
      with no FR are scope creep — fix the spec first
