# Test Cases: env-propagation

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-28
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

Observable surface: the `cairn` command line (exit codes, stdout text and
`--json`), and the content of generated integration files as a user opens
and reads them (workspace config files such as `.mcp.json` or
`.claude/settings.json`, global configs such as the Claude Desktop
registration, and the macOS LaunchAgent definition a user finds under
`~/Library/LaunchAgents`). Cases that need a specific host (macOS launchd,
Linux) are marked **manual**; everything else is scriptable end to end
(**auto**) using temporary folders, environment variables, and PATH
shadows. Cases marked **guard** pin behavior that must not change.

Canonical local invocation for automated suites:
`uv run --extra test pytest <path> -q`.

## TC-001 — Custom store location appears in every file-written agent registration
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** a machine whose `CAIRN_HOME` points at a custom folder holding a built store, and a project workspace opened from that shell
- **When** the user runs `cairn install-agents --stdio`
- **Then** every client registration written as a file for that workspace (`.mcp.json`, `.cursor/mcp.json`, `.zcode/config.json`, `.factory/mcp.json`, `opencode.json`, `kilo.json`, `.omp/mcp.json`, and the Claude Desktop registration in its global config) carries an environment entry setting `CAIRN_HOME` to the custom folder
- **Pass condition**: open each generated file as a user; every stdio cairn entry contains an environment block with `CAIRN_HOME` equal to the custom folder's expanded absolute path, and no registration names a different location.

## TC-002 — Guard: default installs generate byte-identical configs with no environment entries
- **Story**: US1 · **Traces to**: FR-001, AC5 (also guards the default-home shapes of FR-002 and FR-003)
- **Given** a machine with `CAIRN_HOME` unset (default store location), a standard workspace, and release 0.16.0 (the version before this change) available for comparison
- **When** `cairn install-agents --stdio` runs on the changed build (hooks included)
- **Then** the generated files contain no environment entries at all and are byte-for-byte identical to what the pre-change release produced for the same setup
- **Pass condition**: generate the same client set with release 0.16.0 and with the changed build in identical fresh environments; a recursive diff of the two generated config trees is empty. Automated regression guard: `uv run --extra test pytest tests/test_install_uninstall_fidelity.py tests/test_clients.py -q` stays green (61 passed at baseline per survey addendum).

## TC-003 — Home explicitly set to the default location counts as default
- **Story**: US1 · **Traces to**: FR-001, AC5
- **Given** `CAIRN_HOME` is set, but to the user's default store location (`~/.cairn`) — the spec rules this counts as default
- **When** `cairn install-agents --stdio` runs
- **Then** no environment entry is added to any generated registration
- **Pass condition**: the generated files are identical (recursive diff empty) to those from a run with `CAIRN_HOME` unset on the same setup; no `CAIRN_HOME` environment entry appears anywhere.

## TC-004 — Hook-fired work lands in the custom store
- **Story**: US1 · **Traces to**: FR-002, AC2
- **Given** a custom `CAIRN_HOME` with a built store, after `cairn install-agents` has installed agent hooks (e.g. in `.claude/settings.json` and `.cursor/hooks.json`)
- **When** a hook fires in the client — or the user copies the exact generated hook command into a fresh shell that has only their normal environment (no `CAIRN_HOME` exported) and runs it
- **Then** the hook-spawned cairn work (the graph update) is applied to the custom store, not the default one
- **Pass condition**: before firing, note the custom store's latest activity (`cairn status` with `CAIRN_HOME` exported) or the store file's modification time; after the hook command runs in the clean shell, the custom store shows the new activity and the default location is unchanged. Reading the generated hook file as a user, the command line embeds the `CAIRN_HOME` assignment.

## TC-005 — macOS daemon serves the custom store (manual — macOS)
- **Story**: US1 · **Traces to**: FR-003, AC3
- **Given** macOS with `CAIRN_HOME` at a custom folder holding a built store and configuration
- **When** the user runs `cairn serve start`
- **Then** the generated LaunchAgent definition includes `CAIRN_HOME` in its environment variables, and the running daemon resolves its configuration and shared libraries under the custom folder
- **Pass condition**: read the LaunchAgent definition under `~/Library/LaunchAgents` — its environment variables contain `CAIRN_HOME` = the custom path; once the daemon is loaded and running, query the served endpoint (e.g. `curl` the SSE URL) and confirm responses reflect the custom store's graph content, not an empty default store.

## TC-006 — Guard: macOS default home gains no daemon environment entry (manual — macOS)
- **Story**: US1 · **Traces to**: FR-003, AC5
- **Given** macOS with `CAIRN_HOME` unset and release 0.16.0 available for comparison
- **When** `cairn serve start` runs on the changed build
- **Then** the LaunchAgent definition's environment variables do not include `CAIRN_HOME` and match the pre-change rendering exactly
- **Pass condition**: diff the generated LaunchAgent definition against one produced by release 0.16.0 on the same setup — identical.

## TC-007 — Missing store on an agent-spawned process: error names path, environment, and fix
- **Story**: US1 · **Traces to**: FR-004, AC4
- **Given** a registration written for a custom `CAIRN_HOME` whose folder does not contain a built store (folder absent or never built)
- **When** the client spawns its cairn integration — or the user runs the recorded command with the recorded environment from the generated config
- **Then** the process exits with an error naming the resolved store file path, the environment resolution in effect (the `CAIRN_HOME`/workspace/db/knowledge values it saw), and the remediation (point `CAIRN_HOME` at the built store, or build the store) — instead of a raw database error
- **Pass condition**: capture stderr from the spawned command: it contains the resolved path, the environment values, and the remediation; exit code 1; no raw database-engine error text appears. Automated guard that the existing clean-exit behavior is preserved: `uv run --extra test pytest tests/test_server_robustness.py::TestStoreExistenceCheck tests/test_doctor.py::test_schema_fail_unopenable_db -q` stays green (3 passed at baseline per survey addendum).

## TC-008 — Missing store on direct CLI use: same actionable error
- **Story**: US1 · **Traces to**: FR-004, AC4 (edge: custom home whose store does not exist yet)
- **Given** a shell where `CAIRN_HOME` points at a new, empty folder and the user is inside a workspace
- **When** the user runs a command that must read the store (e.g. `cairn status`) and it cannot open the database because the folder does not exist
- **Then** the error names the resolved store path, the environment in effect, and the remediation — with no raw traceback
- **Pass condition**: run the command; the output contains the three elements (path, environment, fix); exit code is non-zero; no stack trace or bare database-engine error string appears.

## TC-009 — Resolution probe reports all four locations, machine-readable
- **Story**: US2 · **Traces to**: FR-005
- **Given** a shell with `CAIRN_HOME` at a custom folder and the workspace resolved from the current directory
- **When** the user runs cairn's resolution probe (the documented machine-readable command, discoverable from `cairn --help`)
- **Then** the output is a single machine-readable (JSON) document reporting `cairn_home`, `workspace`, `db`, and `knowledge`, each matching the environment in effect
- **Pass condition**: pipe the probe output through a JSON parser; the four fields are present and equal the environment-resolved paths (cross-check against the human-readable `cairn config` output). Repeating the run with `CAIRN_HOME` unset reports the default home. Regression guard: `uv run cairn config --db` still prints exactly the db path and exits 0 (verified at baseline per survey addendum).

## TC-010 — Installer verifies every registration and reports per-client PASS
- **Story**: US2 · **Traces to**: FR-006, FR-005, AC6
- **Given** a custom `CAIRN_HOME` with a built store on a healthy machine
- **When** `cairn install-agents --stdio` runs
- **Then** after writing, the install report shows — for each installed client — that the registration's exact command and environment were actually run from inside the workspace and resolved the target store; every client is marked PASS
- **Pass condition**: run the install; the stdout report lists a PASS verdict for every installed client with no client missing a verdict, and the command exits 0.

## TC-011 — Registration resolving a different store fails install verification, naming both stores
- **Story**: US2 · **Traces to**: FR-006, AC7 (edges: env dropped; a different `cairn` binary first on PATH)
- **Given** a machine where a written registration would resolve a store different from the install target — reproducible by shadowing the `cairn` command on PATH with a wrapper that drops `CAIRN_HOME` before running the real binary
- **When** `cairn install-agents --stdio` runs
- **Then** the affected client's verdict is FAIL and the report names both the store it actually resolved and the store that was intended; other clients keep their own verdicts
- **Pass condition**: the stdout report shows FAIL for the affected client with both store paths printed; unaffected clients still show PASS; the report does not collapse to a single overall pass/fail.

## TC-012 — Incident machine: doctor names the wiring fault (manual — Linux)
- **Story**: US3 · **Traces to**: FR-007, AC8
- **Given** a Linux machine replicating the reported incident: a populated custom `CAIRN_HOME` store, an empty store at the default location, a client holding an SSE registration, and no daemon running
- **When** the user runs `cairn doctor` and `cairn doctor --json`
- **Then** the environment check FAILs, and its detail names (a) the client whose registration points at a store other than the machine's populated store and (b) the platform limitation (the SSE daemon lifecycle is macOS-only); the JSON output marks the environment check FAIL; the command exits 1
- **Pass condition**: text output shows the environment check failed with both facts named in its detail/hint; in `--json`, the environment entry has status FAIL; exit code is 1.

## TC-013 — Guard: healthy default install — environment check passes, existing checks untouched
- **Story**: US3 · **Traces to**: FR-007, AC9
- **Given** a healthy default install (default store location, built store, macOS)
- **When** the user runs `cairn doctor --json`
- **Then** the environment check PASSes, and every pre-existing check is unchanged in name, order, and outcome
- **Pass condition**: `--json` output lists the environment check with status PASS alongside the nine prior checks (`schema`, `embeddings`, `ann`, `embed_server`, `freshness`, `parse_errors`, `concurrency`, `tool_health`, `config`) in the same order as before; exit code is 0. Automated regression guard: `uv run --extra test pytest tests/test_doctor.py -q` stays green (40 passed at baseline per survey addendum).

## TC-014 — SSE registration on a non-macOS host draws a platform warning (manual — Linux)
- **Story**: US3 · **Traces to**: FR-007, AC8 (edge: platform/transport mismatch)
- **Given** a non-macOS machine with a client holding an SSE registration, regardless of whether the endpoint answers
- **When** `cairn doctor` runs
- **Then** the environment check reports a warning naming the affected registration and stating that the SSE daemon lifecycle is macOS-only
- **Pass condition**: text output shows a WARN-level environment finding naming the macOS-only lifecycle; `--json` shows the same finding with severity WARN. A warning by itself does not fail the doctor run (exit stays 0 unless a FAIL is also present).

## TC-015 — Stale registration without an environment entry but resolving the right store: warned, not failed
- **Story**: US3 · **Traces to**: FR-007, AC8/AC9 (edge: configs written before this change)
- **Given** a machine whose store is at the default location and a client registration written by the previous release (no environment entry) that still resolves that same store
- **When** `cairn doctor` runs
- **Then** the environment check issues a warning advising the user to re-run the installer, and does not fail the check
- **Pass condition**: `--json` shows the environment finding for that client with severity WARN (not FAIL); exit code is 0 when no other FAIL exists.

## TC-016 — Different cairn binary first on PATH: doctor reports the incoherence
- **Story**: US3 · **Traces to**: FR-007 (edge: binary coherence)
- **Given** a machine where the `cairn` command resolved first from PATH is not the installation the user is running (e.g. an older copy earlier in PATH)
- **When** `cairn doctor` runs
- **Then** the environment check reports the incoherence, naming both the PATH-resolved binary and the running installation, so the user can reconcile them
- **Pass condition**: text/`--json` environment detail names both binaries. If the mismatch provably redirects a client to a different existing store, severity escalates per the published rule and TC-011/TC-012 observables apply.

## TC-017 — Client registered through its own external tool
- **Story**: US1/US2 · **Traces to**: FR-001, FR-006 (edge: registration written by an external CLI, not a cairn-written file)
- **Given** a custom `CAIRN_HOME` and a global-scope install for a client that registers via its own external command (the external tool writes the registration; cairn does not write the file directly)
- **When** `cairn install-agents` runs with global scope for that client
- **Then** the resulting registration — as the external tool itself reports it — carries the `CAIRN_HOME` environment entry, and the install's per-client verdict reflects the truth
- **Pass condition**: the external tool's own show/list command displays the cairn registration including the environment entry; the install report shows a verdict for that client consistent with what the tool reports. If the external tool cannot persist an environment entry, this case fails by design and the install must NOT report PASS for that client — the open spec question (whether external registration tools accept an environment block at all) is then resolved as "no", and the verdict must say so.

## Coverage matrix

Every FR appears; an FR with no TC would be a spec smell (`⚠ MISSING`).

| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002, TC-003, TC-017 | auto |
| FR-002      | TC-002 (guard), TC-004 | auto |
| FR-003      | TC-005, TC-006 | manual (macOS) |
| FR-004      | TC-007, TC-008 | auto |
| FR-005      | TC-009, TC-010 | auto |
| FR-006      | TC-010, TC-011, TC-017 | auto |
| FR-007      | TC-012, TC-013, TC-014, TC-015, TC-016 | auto + manual (Linux) |

No FR is untestable through the observable surface — none marked `⚠ MISSING`.

### Acceptance-criteria trace

| AC | Story | Test cases |
|----|-------|------------|
| AC1 | US1 | TC-001, TC-010 |
| AC2 | US1 | TC-004 |
| AC3 | US1 | TC-005 |
| AC4 | US1 | TC-007, TC-008 |
| AC5 | US1 | TC-002, TC-003, TC-006 |
| AC6 | US2 | TC-010 |
| AC7 | US2 | TC-011 |
| AC8 | US3 | TC-012, TC-014, TC-015 |
| AC9 | US3 | TC-013 |
