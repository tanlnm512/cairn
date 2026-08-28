# Spec: env-propagation

**Status**: done            <!-- approved 2026-08-28; all 23 tasks ticked, TC proofs green, closing audit passed -->

**Created**: 2026-08-28
**Branch**: `feat/env-propagation`
**Issue**: tanlnm512/cairn#70 (root-cause class: environment wiring, not store corruption)

## What
Cairn installs that keep their store in a custom location (`CAIRN_HOME` ≠ `~/.cairn`) work end-to-end: every client MCP registration, agent hook, and daemon definition that cairn generates carries the environment needed to resolve the same store; `install-agents` verifies its own output at install time; and `cairn doctor` detects wiring drift (wrong store, unreachable daemon, platform/transport mismatch) with actionable messages.

## Why
The env that selects a store (`CAIRN_HOME`, `CAIRN_WORKSPACE`, `CAIRN_DB`/`CAIRN_KNOWLEDGE`) is propagated across only one of the five process-spawn boundaries (launchd → daemon, and even that omits `CAIRN_HOME`). On any machine using a custom `CAIRN_HOME` — e.g. the Linux deployment in issue #70 — MCP clients silently resolve an empty default store, hooks write to the wrong store, and the failure surfaces as a raw `sqlite3.OperationalError` or an apparently empty knowledge graph, far from the config that caused it. Nothing verifies the machine's wiring: `cairn doctor`'s 8 checks audit the store's internals, not whether clients can actually reach that store.

## Business value
- Users with custom install locations get a working integration on first install instead of a debugging session (issue #70 cost one).
- `cairn doctor` becomes the single command that answers "why can't my agent use cairn" for environment/platform causes — measurable as a new doctor check that FAILs on the exact #70 machine state.
- Generated configs stay minimal on default installs (no churn for the standard case).

## User stories

### US1 — Custom-store installs work everywhere (P1)
As a user with `CAIRN_HOME` at a custom path, I want every cairn-generated integration artifact to resolve the same populated store, so MCP tools, hooks, and the SSE daemon all see my graph.

**Acceptance criteria**:
- AC1: Given `CAIRN_HOME=/custom` and a workspace with a built store, when `cairn install-agents` writes stdio registrations, then each embeds `env.CAIRN_HOME=/custom` and a client-spawned process resolves `/custom`'s store. (FR-001)
- AC2: Given the same install, when hooks fire, then the hook-spawned `cairn update` targets the same store. (FR-002)
- AC3: Given macOS + custom `CAIRN_HOME`, when `cairn serve start` writes the LaunchAgent, then the daemon resolves `config.json` and shared libs under `/custom`. (FR-003)
- AC4: Given a process whose resolved store directory does not exist, when it fails to open the DB, then the error names the resolved path, the env resolution in effect, and the fix. (FR-004)
- AC5: Given default `CAIRN_HOME` (unset), generated configs contain no env block — byte-identical to today's output. (FR-001)

### US2 — Install-time verification (P1)
As a user running `install-agents`, I want the installer to verify each written registration actually resolves the target store, so misconfiguration fails at install time instead of silently months later.

**Acceptance criteria**:
- AC6: When `install-agents` finishes writing stdio registrations, then each registration's exact command+env is spawned (cwd = target workspace) and its resolved store compared with the install target, PASS/FAIL surfaced per client. (FR-005, FR-006)
- AC7: Given a registration that would resolve a different store (env dropped, wrong binary on PATH), the install reports FAIL naming both stores. (FR-006)

### US3 — Doctor detects wiring drift (P2)
As a user or agent diagnosing "cairn unavailable", I want `cairn doctor` to audit the machine's wiring, so the root cause is named by one command.

**Acceptance criteria**:
- AC8: Given the #70 machine state (populated custom store, empty default store, SSE registration, no daemon, Linux), `cairn doctor` FAILs the environment check naming the client/store mismatch and the platform limitation. (FR-007)
- AC9: Given a healthy default install, the environment check PASSes and no existing check changes. (FR-007)

## Requirements
- **FR-001**: WHEN `install-agents` writes a stdio MCP registration and `CAIRN_HOME` resolves to a non-default path at install time, the system SHALL embed `env: {CAIRN_HOME: <path>}` in that registration; WHERE `CAIRN_HOME` is default, it SHALL NOT add an env block. Non-default is judged by expanded absolute path equality against `Path.home()/.cairn` (a `CAIRN_HOME` explicitly set to the default path counts as default — ruling on survey Unknown #4).
- **FR-002**: WHEN `install-agents` writes hook command strings and `CAIRN_HOME` is non-default, the system SHALL embed the cairn-home assignment into each generated hook command so hook-spawned processes inherit it.
- **FR-003**: WHEN `cairn serve start` renders the LaunchAgent plist and `CAIRN_HOME` is non-default, the system SHALL include `CAIRN_HOME` in the plist's `EnvironmentVariables`.
- **FR-004**: WHEN a cairn process cannot open the store database because the resolved store directory does not exist, the system SHALL emit an error naming the resolved DB path, the env resolution chain in effect, and the remediation (set `CAIRN_HOME` / run `cairn build`) — not a bare `sqlite3.OperationalError`.
- **FR-005**: The system SHALL provide a machine-readable resolution probe that reports `cairn_home`, `workspace`, `db` path, and `knowledge` path for the invoking process environment.
- **FR-006**: WHEN `install-agents` finishes writing stdio registrations, the system SHALL verify each registration by spawning its exact command with its exact env (cwd = target workspace), comparing the probe's resolved store with the install-time store, and surfacing per-client PASS/FAIL.
- **FR-007**: `cairn doctor` SHALL include an environment check auditing (a) resolved-store existence, (b) client-registration consistency for installed clients (stdio: simulated resolution vs the doctor's store; SSE: endpoint reachability), (c) platform/transport supportability (SSE registration on non-darwin ⇒ WARN naming the macOS-only lifecycle), and (d) binary coherence (PATH `cairn` vs the running install).

## Scope
**In**: install-agents stdio/hook env propagation; LaunchAgent plist `CAIRN_HOME`; store-open error message; resolution probe; install-time verification; `cairn doctor` environment check (text + `--json` integration).
**Out (deferred)**: Linux daemon lifecycle for `serve start` (systemd units) — error-message clarity only; changing default transports; propagating behavioral env beyond `CAIRN_HOME` (embed knobs, rerank, etc.); Claude Desktop changes (already pins `CAIRN_WORKSPACE`).

## Assumptions & risks
- Assumption: `CAIRN_HOME` (not a `CAIRN_WORKSPACE` pin) is the right propagation key for cwd-having clients — it preserves multi-workspace registry resolution; desktop-style clients without cwd keep the existing `CAIRN_WORKSPACE` pin.
- Assumption: config-env + cwd is the deterministic spawn contract cairn owns; env a client injects on its own is out of verification scope.
- Assumption: verification probes may spawn one cairn process per client (~1s each) — acceptable at install/doctor time, never on the hot path.
- Risk: configs written before this change lack the env block; users must re-run `install-agents` — resolved (orchestrator default, no user answer): doctor uses mixed severity — FAIL only when a registration provably resolves a different existing store or an SSE endpoint is unreachable; WARN for merely-missing env on stale registrations.
- Default taken (clarify pass unanswered): scope = all 3 layers; Linux systemd lifecycle out of scope; drift severity mixed; all four CONSTITUTION articles adopted. Override at the approval gate if any is wrong.
