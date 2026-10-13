# Plan: mcp-streamable-http

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-11

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | Requirements | Depends on |
|-------|-----------|---------------------|--------------|------------|
| 1 | HTTP-only read-write daemon core | Bare `cairn serve` runs the foreground read-write Streamable HTTP daemon on `127.0.0.1:9876/mcp`; stdio/SSE serve options exit non-zero naming the HTTP replacement; two concurrent HTTP clients share one warm process (write by one readable by the other); stray sweeper runs on the HTTP path | FR-001, FR-002, FR-003, FR-008, NFR-003 (constraints: FR-011, NFR-001 unchanged) | — |
| 2 | Daemon manager on HTTP | `serve start\|stop\|status\|restart` manage the read-write HTTP daemon — launchd on macOS, daemonize fallback elsewhere; concurrent `serve start` invocations converge to one daemon (single-flight guard); probes hit `/healthz`, not the SSE TCP check | FR-010 | Phase 1 |
| 3 | `cairn connect` | Cold connect starts exactly one detached daemon and emits the URL ("started"); racing connects converge to one; warm connect reports "reused"; a crashed daemon is replaced by the next connect without manual cleanup | FR-004, NFR-004, NFR-005 | Phase 2 |
| 4 | Key lifecycle + URL+key installers | Fresh `cairn init` generates and persists `CAIRN_MCP_API_KEY` (user-only mode, never logged); all 8 supported clients get URL+key registrations verified via `/healthz`; claude-desktop reported unsupported with no config written; `config --mcp-config` prints the URL+key shape | FR-005, FR-006, FR-007, NFR-002 | Phase 1 |
| 5 | Doctor repoint + docs/release sweep | `cairn doctor` flags cairn-owned stdio/SSE registrations; `--fix` rewrites them to URL+key; mcp.json.template, README, AGENTS.md, docs carry no live stdio/SSE instructions; specs/INDEX.md records the remote-mcp-oauth supersession | FR-009 | Phases 2–4 |

NFR-006 is not applicable (spec.md) — no milestone carries it. FR-011 and NFR-001 are DONE at baseline (survey) and enter Phase 1 as preservation constraints: the SDK app builder stays the only transport surface and the auth/bind test suite must stay green through every phase.

## Dependencies
Phase 1 freezes the daemon surface everything else dials (single `/mcp` path, read-write default, readiness line) and removes the transports, so it gates all later phases. Phase 2 reworks the manager against that surface (and shares `cli/serve.py` with Phase 1's flag removal — one atomic transport removal, then the manager). Phase 3's connect consumes the detached single-flight start + healthz probe machinery Phase 2 produces. Phase 4 needs only the frozen daemon surface, not the manager, so it runs as a parallel lane beside Phases 2–3. Phase 5 consumes Phase 4's URL+key config generator and key resolution for its repoint, and its docs sweep is only truthful once every surface has landed — it closes the plan.

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->
- Independent (concurrent after Phase 1):
  - Lane A — daemon manager → connect (Phases 2–3): `src/cairn/cli/serve.py` (lifecycle subcommands), `src/cairn/mcp_server/lifecycle.py` (plist args, healthz probe, daemonize fallback, start lock), new `src/cairn/cli/connect.py`, one import line in `src/cairn/cli/__init__.py`, `tests/test_stray_sweeper_safety.py` + new serve/connect tests.
  - Lane B — key + installers (Phase 4): `src/cairn/cli/core.py` (init key step, `config --mcp-config`), `src/cairn/mcp_server/auth.py`, `src/cairn/cli/agents.py`, `src/cairn/agent_install/**`, `tests/test_clients.py`, `tests/test_auth.py`, `tests/test_cli_init_rail.py`.
  - Disjointness is checkable: no file appears in both lanes. Intra-lane-B chain only: key persistence (core.py/auth.py) lands before the config generators embed it (`agent_install/_common.py` consumes key resolution).
- Strictly ordered:
  - Phase 1 → Phase 2 — shared file `cli/serve.py` (foreground flag removal vs manager subcommands), and the plist/args/probes target the HTTP-only daemon Phase 1 produces (today `render_plist` pins `serve run --port` + `--read-only`, `lifecycle.py:77-80`; `serve.py:131-134` passes `transport` into `run()` — the removal is one atomic change).
  - Phase 2 → Phase 3 — Phase 2 produces the detached single-flight start primitive and `/healthz` probe; connect consumes them (no lock machinery exists today, survey NFR-004).
  - Phase 1 → Phase 4 — installer configs and healthz verification consume the daemon surface Phase 1 freezes (URL/path, readiness, read-write semantics).
  - Phase 4 → Phase 5, and Phases 2–3 → Phase 5 — doctor's `--fix` writes the config shape Phase 4's generator emits (today it repoints dead SSE→stdio, `doctor.py:1006`); the docs/release sweep finalizes only after every surface lands.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: `grep -n 'transport ==' src/cairn/mcp_server/server.py` → no output (three branches gone, survey FR-001); `grep -n 'Choice(\["stdio"' src/cairn/cli/serve.py` → no output; `cairn serve --transport stdio` (sandboxed `CAIRN_HOME`) exits non-zero with a message naming the HTTP replacement; `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_http_transport.py tests/test_auth.py -q` → green with the stdio-parity case replaced by a direct 26-tool catalog pin, plus a new failing-first warm-daemon test proving one process serves a second client with no re-boot (NFR-003, un-pinned at baseline).
- **After Phase 2**: `cairn serve status` reports HEALTHY with pid and the `http://…/mcp` URL; two concurrent `cairn serve start` runs end with exactly one daemon (second reports already-running); on a non-macOS host `serve start` uses the daemonize fallback and stop/restart work (no more exit-1, `serve.py:147-150`); `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_stray_sweeper_safety.py -q` → green.
- **After Phase 3**: cold `cairn connect` emits the URL and "started" with exactly one daemon pid; two racing connects converge to one daemon, both emitting the same URL; warm connect reports "reused"; after killing the daemon, the next connect replaces it (orphan swept first) — NFR-004/NFR-005 pinned by failing-first tests.
- **After Phase 4**: fresh `CAIRN_HOME=<tmp> cairn init` leaves a key file with mode 0600 (`stat` check) and no key material in any output/log; `cairn install-agents` writes URL+key entries for the 8 supported clients and reports claude-desktop unsupported with no file written (`CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_clients.py -q` green on the new shapes); verification output shows a `/healthz` probe, not a spawned process; `cairn config --mcp-config` prints the URL+key snippet (`core.py:210` region).
- **After Phase 5**: `cairn doctor` flags a planted cairn-owned stdio registration and `--fix` rewrites it to URL+key (`CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_doctor_fix.py -q` green on the reversed direction); `grep -rn "stdio" README.md AGENTS.md docs/ mcp.json.template` shows no live stdio instructions; `specs/INDEX.md` carries the remote-mcp-oauth supersession note (currently only "draft" at `specs/INDEX.md:8`).

## Risks & mitigations
- Risk: SDK behavior drift in `streamable_http_app()` across the `mcp>=0.9.0,<2.0.0` pin range → mitigation: pin ceiling stays; the HTTP/auth suite already exercises the SDK app builder (spec risk; survey FR-011/NFR-001 evidence) and runs at every checkpoint.
- Risk: breaking every existing stdio consumer at upgrade → mitigation: clean break is deliberate (intent Q5); Phase 5 doctor repoint + release notes carry migration; INDEX.md supersession note resolves the dormant `remote-mcp-oauth` FR-005 conflict (intent Q6).
- Risk: read-write daemon reintroduces SQLite writer contention → mitigation: the daemon is the only writable server (spec assumption); sweeper installed on the HTTP path in Phase 1 (FR-008 gap: `_install_stray_sweeper` never called there) plus the existing start-time sweep evicts WAL holders.
- Risk: concurrent-first-start races → mitigation: stdlib-only single-flight guard (fcntl/O_EXCL class) built in Phase 2, consumed in Phase 3; any external lock/daemonize dependency is a C-03 violation unless tech-spec records a `D-###` decision — none is planned.
- Assumption (survey gap, `unknown — verify` on survey FR-007): Claude Desktop's remote/URL MCP capability is outside this repo; the plan proceeds on the spec ruling (report unsupported, write nothing) and no repo-side evidence can flip it.
- Assumption (spec): the 8 remaining clients accept URL-form registrations; installer tests pin each shape so drift surfaces at Phase 4's checkpoint.

## Delivery
Branch `feat/mcp-streamable-http` (spec.md). Solo (no team context recorded): one PR per milestone, stacked in phase order, each landing via `branch → pre-commit run --all-files → conventional commit → push → PR (audit checklist) → watch CI` (C-01); never pushed to `main`. Every milestone opens with its failing tests (C-02) — the checkpoint commands above are each phase's acceptance evidence. No new runtime dependencies are planned; any addition stops for a tech-spec `D-###` decision (C-03). Test-isolation rules (C-04) bind every new test: no eager `cairn.cli`/`cairn.mcp_server` imports, no global `subprocess.Popen` patches, no `tmp_path` workspaces leaking into `~/.cairn`.
