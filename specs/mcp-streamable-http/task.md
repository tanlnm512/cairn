# Tasks: mcp-streamable-http

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
**Lifecycle**: v2
Status reflects code state per [survey.md](survey.md), not intent.
**Delivered**: pending — delivery evidence; the orchestrator writes `commit @ <sha>` here
**Waves**: 1 = Phase 1 · 2 = Phases 2–4 as two lanes (A: Phase 2 → Phase 3 manager+connect; B: Phase 4 key+installers, gated on Phase 1 only) · 3 = Phase 5

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1 | 5 | 5 |
| 2 | 3 | 3 |
| 3 | 2 | 2 |
| 4 | 7 | 7 |
| 5 | 3 | 3 |
| **Σ** | 20 | 20 |

## Phase 1: HTTP-only read-write daemon core (FR-001, FR-002, FR-003, FR-008, NFR-003)
<!-- Checkpoint (plan): no transport branches left in src/cairn/mcp_server/server.py; no stdio Choice in src/cairn/cli/serve.py; serve --transport stdio exits non-zero naming the HTTP replacement; transport+auth suites green with the 26-tool catalog pin; warm-daemon pin green -->
- [x] T001 (implemented) [P] Repin the transport suite red-first for the HTTP-only surface — rewrite the six flag-mapping rows of test_serve_foreground_maps_flags_to_run for bare `cairn serve`, replace the stdio-parity side of test_http_tool_catalog_matches_stdio with a direct 26-tool catalog pin (independent source: the @mcp.tool registry count re-counted in survey.md), add the refusal pin for every removed `--transport` value (non-zero exit + message naming the HTTP replacement) (FR-001, FR-003; D-001, D-002)
  - done 2026-10-13 — done 2026-10-12 — tests/test_http_transport.py 51 passed incl. 6 flag-mapping rows, 6 refusal tombstones, 26-tool catalog pin (proofs TC-001 SURFACE_OK, TC-003 TRANSPORT_REFUSED_OK)
  - Touches:
    - `tests/test_http_transport.py`
  - Tests red-first (C-02): TC-001, TC-002, TC-003 — refusal and default-shape rows fail while the options are first-class (survey FR-003 gap); catalog pin goes green at T003, refusal at T004
  - partial: survey FR-001 — the HTTP branch serves /mcp and is test-pinned (42 passed); remaining: pins for the removed surface
- [x] T002 (implemented) [P] Migrate the boot/read-write suites to HTTP boots and add the warm-daemon pin — move the boot-guard, read-only-blocks-writes, and daemon-liveness cases in tests/test_core_smoke.py plus the boot class in tests/test_server_robustness.py off `run(transport="stdio")` onto bare HTTP boots, delete test_watchdog_drains_before_os_exit with the stdio watchdog, add the two-client no-reboot pin (second session's first call answered by the same pid, no catch-up or model-warmup re-run) (FR-002, NFR-003; D-001, D-003)
  - done 2026-10-13 — done 2026-10-12 — test_core_smoke.py + test_server_robustness.py green incl. TestReadWriteDaemonDefault and TestWarmDaemonAmortization live pins (TC-004 SHARED_RW_OK, TC-005, TC-021 WARM_SECOND_CLIENT_OK)
  - Touches:
    - `tests/test_core_smoke.py`
    - `tests/test_server_robustness.py`
  - Tests red-first (C-02): TC-004, TC-005, TC-006, TC-021 — read-write-default and warm-amortization cases fail while HTTP serving defaults read-only (survey FR-002); boots and warm pin go green at T003, read-write default and --read-only opt-out at T004
  - partial: survey NFR-003 — boot work already runs once per process ahead of serving; remaining: no test pins second-client amortization (survey gap)
- [x] T003 (implemented) (after T001) (after T002) Collapse run() to the HTTP-only body — in src/cairn/mcp_server/server.py delete the transport parameter, the sse and stdio branches, and _install_exit_watchdog; today's http branch (SDK streamable_http_app under uvicorn, stdout readiness line naming the served /mcp URL) becomes the entire body; install _install_stray_sweeper on the sole serving path (FR-001, FR-008, FR-011, NFR-001; D-001, D-010)
  - done 2026-10-13 — done 2026-10-12 — run() signature (port, host, api_key, stateless); zero transport branches in server.py; stray sweeper on sole path; TC-001 SURFACE_OK
  - Touches:
    - `src/cairn/mcp_server/server.py`
  - Interface produced: `run(port=None, host=None, api_key=None, stateless=False)` — the transport kwarg is gone; _build_http_app, _BearerAuthMiddleware, _transport_security_for, _require_http_api_key stay untouched (NFR-001 preservation); the SDK builder stays the only transport surface (FR-011)
  - Tests: turns T001's catalog pin and T002's migrated boots and warm pin green; TC-010 sweeper-safety suite stays green (standing guard)
  - Verify anchor: CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_http_transport.py tests/test_auth.py -q
  - partial: survey FR-001 — three transports compiled in, stdio the default; survey FR-008 — _install_stray_sweeper never called on the http path; both gaps close here
- [x] T004 (implemented) (after T003) Flip the serve CLI to the read-write daemon with transport tombstones — in src/cairn/cli/serve.py delete the read-only auto-default in _serve_foreground (read-write becomes the default, `--read-only` the explicit opt-out), drop the transport plumbing into run(), keep `--port` as the HTTP daemon's port, and hide `--transport` as a tombstone option on serve and serve run that prints the daemon URL form plus the connect hint and exits non-zero for any value (FR-002, FR-003; D-002, D-003)
  - done 2026-10-13 — done 2026-10-12 — serve CLI read-write default, --read-only opt-out, --transport tombstone naming URL + connect hint; TC-003/TC-004/TC-005 green
  - Touches:
    - `src/cairn/cli/serve.py`
  - Interface consumed: `run(port=None, host=None, api_key=None, stateless=False)` from T003
  - Tests: turns T001's refusal pin (TC-003) and T002's read-write-default and `--read-only` opt-out pins (TC-004, TC-005) green
  - Verify anchor: CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_http_transport.py tests/test_auth.py -q
- [x] T005 Hold the SDK-surface and auth/bind standing guards green through every phase — FR-011 and NFR-001 are DONE at baseline per survey; this entry is the preservation constraint the phases re-check at every checkpoint, ticked only at spec close with both guards passing (FR-011, NFR-001)
  - done 2026-10-13 — done 2026-10-12 — standing guards green at spec close: TC-010 stray-sweeper safety 34 passed; TC-019 SDK_SURFACE_OK; TC-020 auth/bind/healthz 51 passed (delivery-pass proofs, this session)
  - Touches:
    - `tests/test_http_transport.py`
    - `tests/test_auth.py`
  - Guards: TC-019 (no hand-rolled transport), TC-020 (auth/bind/healthz suites), TC-011 (bind policy: keyed/unkeyed loopback, pre-bind refusal) — serial by design: a standing guard spanning all phases, not a parallelizable unit
  - baseline-done: survey FR-011/NFR-001 — CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_http_transport.py tests/test_auth.py -q → 42 passed; unticked only because spec.md Status is draft (approval gate blocks ticks)

## Phase 2: Daemon manager on HTTP (FR-010)
<!-- Checkpoint (plan): serve status reports HEALTHY with pid and http URL; concurrent serve start converges to one daemon; daemonize fallback off-macOS (no exit 1); sweeper safety suite green -->
- [x] T006 (implemented) (fix 3/5) [P] Repin the manager suites red-first — repin render_plist expectations for the read-write default (no `--read-only` arg, non-sse label) and add lifecycle tests for the healthz-based probes, the daemonize fallback, and start-race convergence in a new tests/test_serve_manager.py (FR-010; D-005, D-008)
  - done 2026-10-13 — done 2026-10-12 — tests/test_serve_manager.py 12 passed + plist read-write pins (TC-017 MANAGE_OK in proofs)
  - Touches:
    - `tests/test_port_dry_and_unload_fixes.py`
    - `tests/test_serve_manager.py`
  - Tests red-first (C-02): TC-017 (macOS manager cycle), TC-018 (daemonize fallback, manual on Linux) — fail against today's macOS-only SSE manager
  - partial: survey FR-010 — all four subcommands exist but manage the read-only SSE daemon and start exits 1 off-macOS
- [x] T007 (implemented) (after T006) Rework lifecycle probes and the plist for the HTTP daemon — add mcp_url and healthz_responds helpers (healthz is auth-exempt, so probes work against keyed daemons), flip render_plist's read-only default and stop appending `--read-only`, migrate the LaunchAgent label and log name off sse naming with a one-time legacy-label unload before the first new load; sse_url/sse_responds stay only until the last callers flip (T008, T015) (FR-010; D-005, D-008)
  - done 2026-10-13 — done 2026-10-12 — mcp_url/healthz_responds helpers + non-sse plist label with legacy unload; probe pins green in test_serve_manager.py
  - Touches:
    - `src/cairn/mcp_server/lifecycle.py`
  - Interface produced: `mcp_url(port, host) -> str` (URL ending /mcp) and `healthz_responds(port, host, timeout) -> bool` (GET healthz, 200 = pass)
  - Tests: turns T006's probe and plist pins green
  - Verify anchor: CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_port_dry_and_unload_fixes.py -q
  - partial: survey FR-010 — render_plist pins serve run with `--read-only`; probes are the sse TCP pair
- [x] T008 (implemented) (fix 2/5) (after T007) Drive the manager subcommands off one ensure-running core — add the single ensure-running core to lifecycle.py (macOS: sweep-then-write-plist/load/wait as today; elsewhere: fork, setsid, stdio to a CAIRN_HOME log, exec serve run on the port, pid to a pidfile) guarded by an exclusive flock on a CAIRN_HOME lockfile that covers the start sequence only; rewire serve_start/serve_stop/serve_status/serve_restart onto it (healthz probes, no macOS-only exit 1, restart keeps its stop+start composition, start sweeps strays before load); admit the pidfile pid into find_strays' protected set only after cmdline re-verification (FR-010, FR-008, NFR-004; D-004, D-005, D-010, D-013)
  - done 2026-10-13 — done 2026-10-12 — ensure_daemon_running core, flock single-flight, pidfile + D-023 matcher, cg_bin argv[0] preference, respawn wait-first, launchctl timeouts; TC-007/TC-008/TC-009 all OK in proofs; 66 passed across manager/connect/sweeper suites
  - Touches:
    - `src/cairn/mcp_server/lifecycle.py`
    - `src/cairn/cli/serve.py`
  - Interface produced: `ensure_daemon_running(port, host) -> (pid, mcp_url, started_flag)` shared by serve start and connect; stdlib only (fcntl, os — D-013)
  - Tests: turns T006's manager-cycle and fallback pins green; TC-010 stays green
  - Verify anchor: CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_stray_sweeper_safety.py -q
  - partial: survey FR-010 — no daemonize fallback exists; survey NFR-004 — no lock machinery anywhere in cli/ or mcp_server/

## Phase 3: cairn connect (FR-004, NFR-004, NFR-005)
<!-- Checkpoint (plan): cold connect starts one detached daemon and emits URL plus started; racing connects converge to one; warm connect reports reused; a killed daemon is replaced by the next connect -->
- [x] T009 (implemented) [P] Write the connect suite red-first — pin: cold connect starts exactly one detached daemon and emits the URL with a started report; two racing connects converge to one daemon, both emitting the same URL; warm connect reports reused; a killed daemon is replaced by the next connect with the orphan swept first (monkeypatched lifecycle boundaries per C-04 isolation) (FR-004, NFR-004, NFR-005; D-004)
  - done 2026-10-13 — done 2026-10-12 — tests/test_connect.py 4 passed (cold/warm/racing/killed-daemon pins)
  - Touches:
    - `tests/test_connect.py`
  - Tests red-first (C-02): TC-007, TC-008, TC-009 — no connect command exists (survey FR-004), so every case fails at invocation
  - partial: survey NFR-005 — healthz and the readiness line exist; started-vs-reused reporting does not
- [x] T010 (implemented) (after T008) (after T009) Add the connect command — new module exposing a top-level connect command: GET healthz on the configured host:port; healthy → emit the mcp URL and report reused; otherwise take the flock, sweep strays, run the ensure-running core, wait bounded for healthz, emit the URL and report started; a concurrent loser blocks on the flock bounded, re-probes, reports reused; register the command with one import line in the CLI package init (FR-004, FR-008, NFR-004, NFR-005; D-004)
  - done 2026-10-13 — done 2026-10-12 — cairn connect command registered; TC-007 CONNECT_START_REUSE_OK, TC-008 CONVERGE_CRASH_OK
  - Touches:
    - `src/cairn/cli/connect.py`
    - `src/cairn/cli/__init__.py`
  - Interface consumed: `ensure_daemon_running(port, host)` from T008 and `mcp_url` / `healthz_responds` from T007
  - Tests: turns T009's pins green (TC-007, TC-008, TC-009)
  - partial: survey NFR-005 gap — started-vs-reused reporting starts here; FR-004 is TODO at baseline (no command, no guard)

## Phase 4: Key lifecycle + URL+key installers (FR-005, FR-006, FR-007, NFR-002)
<!-- Checkpoint (plan): fresh init persists a 0600 key file with no key material in any output or log; install-agents writes URL+key entries for the 8 supported clients and reports claude-desktop unsupported with no file written; verification probes healthz; config --mcp-config prints the URL+key snippet -->
- [x] T011 (implemented) (after T003) [P] Pin the key lifecycle red-first — extend the init-rail suite: fresh init leaves a key file under CAIRN_HOME with mode 0600 and no key material in rail output or logs; generation is idempotent (an existing key is preserved); add key-file helper cases to the auth suite (absent file → None, present file → value, resolve_api_key semantics untouched) (FR-005, NFR-002; D-006)
  - done 2026-10-13 — done 2026-10-12 — key lifecycle pins green: init writes 0600 key file, idempotent, no leak; 26 passed across init-rail + auth suites
  - Touches:
    - `tests/test_cli_init_rail.py`
    - `tests/test_auth.py`
  - Tests red-first (C-02): TC-012 (init generates the key, config snippet carries it), TC-013 (user-only permissions, never in logs or health payload)
  - [P] within the wave; (after T003) is the cross-phase gate — Lane B needs only the frozen daemon surface
  - partial: survey FR-005 — bind policy exists and is pinned, but init generates no key; survey NFR-002 — no persisted key file exists, so permission discipline is unverifiable
- [x] T012 (implemented) [P] Repin the installer suites to URL+key shapes — repin the generator outputs for the 8 supported clients (URL at the /mcp path, embedded key, explicit type field kept per client surface), pin claude-desktop as reported-unsupported with no file written, pin verification as a healthz probe rather than a spawn; repin the zcode legacy-config migration to the URL+key shape (FR-006, FR-007; D-007, D-008, D-009)
  - done 2026-10-13 — done 2026-10-12 — installer pins green: 8 URL+key client shapes, desktop unsupported, healthz verification (test_clients 13, fidelity 85; TC-014/TC-015)
  - Touches:
    - `tests/test_clients.py`
    - `tests/test_install_uninstall_fidelity.py`
  - Tests red-first (C-02): TC-014 (8 URL+key registrations, healthz-based verification), TC-015 (desktop unsupported)
- [x] T013 (implemented) (fix 1/5) (after T011) Generate and persist the API key at init — add the key step to init: generate a token_urlsafe value once (only when the file is absent — init stays idempotent), persist it under CAIRN_HOME with mode 0600 via os.open; add the key-file read helper to the auth module for the generators, config output, and doctor; resolve_api_key stays untouched so the serve path never auto-loads the file (pre-bind refusal and unkeyed loopback dials keep firing) (FR-005, NFR-002; D-006, D-013)
  - done 2026-10-13 — done 2026-10-12 — init generates/persists key (O_EXCL, 0600, call-time home); TC-012 INIT_KEY_OK, TC-013 KEY_PRIVACY_OK
  - Touches:
    - `src/cairn/cli/core.py`
    - `src/cairn/mcp_server/auth.py`
  - Interface produced: `persisted_api_key() -> str | None` reading the 0600 file under CAIRN_HOME
  - Tests: turns T011's pins green (TC-012, TC-013)
  - Verify anchor: CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_cli_init_rail.py tests/test_auth.py -q
  - partial: survey FR-005 gap — init neither generates nor persists a key; survey NFR-002 — no permission-setting code exists (verified by grep)
- [x] T014 (implemented) (fix 1/5) (after T012) (after T013) Flip the config generators to URL+key — delete the stdio branches: URL entries point at the default daemon URL ending /mcp and embed the persisted key via each client's URL-config surface (explicit type field kept, value per client surface); drop the transport/sse_url parameters from install(); remove the sse/stdio/sse-url flags from install-agents and flip its daemon gate onto the healthz probe, naming connect in the hint (FR-006; D-007, D-008)
  - done 2026-10-13 — done 2026-10-12 — URL+key generators for 8 clients, CLI argv forms, healthz daemon gate; git-hooks AND gate restored with red-first pins (TestGitHooksInstallGate 3 passed); TC-014 INSTALL_URL_KEY_OK
  - Touches:
    - `src/cairn/agent_install/_common.py`
    - `src/cairn/agent_install/clients/`
    - `src/cairn/agent_install/__init__.py` (Touches correction — install() lives there; T014 wave)
    - `src/cairn/cli/agents.py`
  - Interface consumed: `persisted_api_key()` from T013; daemon URL shape from the Phase-1 surface
  - Tests: turns T012's shape pins green (TC-014)
  - Verify anchor: CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_clients.py -q
- [x] T015 (implemented) (after T014) Verify registrations via healthz, never a process — delete the spawn-probe (config --json subprocess) from verify_registration; make the verification-results pass probe GET healthz derived from each registration's URL (http.client, bounded timeout, 200 = pass) instead of skipping URL entries; replace the bare TCP connect daemon check with the healthz probe; delete the sse_url/sse_responds pair once their last callers are gone (FR-006; D-008)
  - done 2026-10-13 — done 2026-10-12 — verification probes healthz from registration URLs, spawn-probe deleted; TC-014 second-install unreachable half green
  - Touches:
    - `src/cairn/agent_install/__init__.py`
    - `src/cairn/mcp_server/lifecycle.py`
  - Tests: completes TC-014 — the second install run with the daemon down reports unreachable, proving the probe targets daemon health
  - Verify anchor: CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_clients.py -q
- [x] T016 (implemented) (after T014) Report Claude Desktop unsupported — the desktop installer entry becomes an unsupported report: detection still lists the client, the result carries the explicit unsupported note, no configuration file is written; uninstall keeps removing legacy entries (FR-007; D-009)
  - done 2026-10-13 — done 2026-10-12 — claude-desktop reported unsupported, nothing written, uninstall still strips legacy; TC-015 DESKTOP_UNSUPPORTED_OK
  - Touches:
    - `src/cairn/agent_install/clients/claude_desktop.py`
  - Tests: turns T012's desktop pin green (TC-015)
- [x] T017 (implemented) (after T013) Print the URL+key MCP config — flip the mcp-config branch in the config command to URL+key JSON (keyless URL form when no key file exists, since loopback serves unkeyed) and update the echoed hint beside it (FR-005; D-006, D-012)
  - done 2026-10-13 — done 2026-10-12 — config --mcp-config prints URL+key (keyless form without key file); TC-012 snippet assertion green
  - Touches:
    - `src/cairn/cli/core.py`
  - Tests: TC-012's snippet assertion — URL plus embedded credential, no command-style spawn
  - partial: survey FR-005 gap half — generated client configs carry no key; this branch prints the stdio shape today

## Phase 5: Doctor repoint + docs/release sweep (FR-009)
<!-- Checkpoint (plan): doctor flags a planted cairn-owned stdio registration and --fix rewrites it to URL+key; no live stdio/SSE instructions in README/AGENTS/docs/template; specs/INDEX.md carries the remote-mcp-oauth supersession note -->
- [x] T018 (implemented) [P] Repin the doctor fix suite to the URL+key direction — flag cairn-owned stdio/SSE registrations by transport class; the repointed-to-stdio action literal and rewritten stdio shapes flip to URL+key; desktop entries get a removal hint and are never repointed; the freshness refusal, backup-before-write, secret-redaction, and workspace-owned-skip assertions survive the flip (FR-009; D-011)
  - done 2026-10-13 — done 2026-10-12 — doctor fix suite repinned and green (test_doctor_fix.py 64 passed; TC-016 DOCTOR_REPOINT_OK)
  - Touches:
    - `tests/test_doctor_fix.py`
  - Tests red-first (C-02): TC-016 — today's fix repoints dead SSE to stdio (the opposite direction), so the repinned cases fail
  - Verify anchor: CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_doctor_fix.py -q
- [x] T019 (implemented) (fix 1/5) (after T014) (after T018) Repoint doctor to URL+key — registration findings flag by transport class (stdio spawn-shaped, SSE endpoint); the audit file tables gain the Claude Desktop config path flagged with a removal hint, never repointed; the fix summary and --fix help flip wording; the fix rewrites registrations to the URL+key form via the same generators the installers use, with the same key reader (FR-009; D-011, D-006)
  - done 2026-10-13 — done 2026-10-12 — doctor flags by transport class, --fix repoints to URL+key via generators, desktop removal hint; TC-016 DOCTOR_REPOINT_OK; 64 passed doctor suites
  - Touches:
    - `src/cairn/cli/system/doctor.py`
    - `src/cairn/cli/system/doctor_fix.py`
    - `tests/test_doctor.py` (Touches correction — three SSE-era pins flip with the doctor rework; T014 wave fallout)
    - `tests/test_memory_recurrence.py` (Touches correction — one-line install-kwarg fixture fix, T014 fallout, orchestrator-applied)
  - Interface consumed: T014's URL+key generator shapes and T013's `persisted_api_key()`
  - Tests: turns T018's pins green (TC-016)
- [x] T020 (implemented) [P] Sweep config/template/docs/CI surface to the daemon — rewrite the template to the URL+key shape; strip live stdio/SSE instructions from the README, AGENTS, and CLI/configuration docs; drop the explicit http transport flag from the Dockerfile ENTRYPOINT and the CI smoke steps (the env-var key flow stays — it is load-bearing for the keyless-refusal smoke); record the remote-mcp-oauth supersession note in the spec index (FR-009; D-012)
  - done 2026-10-13 — done 2026-10-12 — grep -rn "stdio" README.md AGENTS.md docs/ mcp.json.template: zero live stdio instructions; Docker/CI flags dropped; INDEX supersession note in place
  - Touches:
    - `mcp.json.template`
    - `README.md`
    - `AGENTS.md`
    - `docs/cli-reference.md`
    - `docs/configuration.md`
    - `docs/architecture.md` (Touches correction — sweep mandate's grep anchor)
    - `docs/deployment.md` (Touches correction — sweep mandate's grep anchor)
    - `docs/mcp-tools.md` (Touches correction — sweep mandate's grep anchor)
    - `Dockerfile`
    - `.github/workflows/ci.yml`
    - `specs/INDEX.md`
  - Verify anchor: grep -rn "stdio" README.md AGENTS.md docs/ mcp.json.template → no live stdio instructions remain
  - [P] within the phase; cross-phase ordering is the phase gate itself (the sweep is truthful only after every surface has landed)

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `(implemented)` landed —
      implementation evidence durable before the one all-at-once tick ·
      `- [x]` done + proof note: `done <date> — <test/command that proves it>`
- Dropped: `- [ ] ~~T004~~ dropped <date> (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)` and name the exact interface they
  consume from their upstream — symbols, signatures, file formats; serial
  runs need a reason, parallel runs need none
- Fix rounds append `(fix <n>/5)` to the entry — the cap survives resume
  only if the count lives here, in the status holder. From round 2 on, an
  implementer's scratch note (what was tried, why it failed) may live at
  `notes/T###.md` — the one file an implementer may write under specs/,
  never read by check.py, never counted as status
- Every task cites FR-### or an applicable NFR-###; a task with neither is
  scope creep — fix the spec first
- Every code task carries a `Touches:` block (repo-relative files,
  directories, or globs); `[P]` tasks whose touches overlap are chained, not
  spawned together
- Status derivation (this spec): a `partial:` line names the survey.md gap
  the entry closes; `baseline-done:` marks a DONE-at-baseline standing
  constraint whose tick lands at spec close; no entry ticks while spec.md
  Status is draft (approval gate)
- C-02 pairing: every implementation task names its failing-test-first TC
  ids from test.md and which upstream red-pin task turns green with it;
  standing guards (TC-010, TC-019, TC-020) stay green throughout
