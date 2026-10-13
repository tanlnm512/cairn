# Spec: mcp-streamable-http

**Status**: approved       <!-- approved 2026-10-11: the user's
                                 /spec-execute command given directly
                                 at the approve gate — the explicit
                                 go-ahead named in its place -->
**Effort**: large          <!-- multi-area: server transports, daemon lifecycle
                                 (incl. Linux fallback + concurrency guard),
                                 auth key lifecycle, 8 agent installers, doctor,
                                 docs, and a breaking release — the full
                                 plan ∥ tech ∥ qa wave + a separate tasks wave -->
**Created**: 2026-10-11
**Branch**: `feat/mcp-streamable-http`

## What
Cairn's MCP server becomes a single always-on daemon speaking the
Streamable HTTP protocol on the local machine. Every coding agent
connects to the same URL with a bearer key and shares one warm,
live-updated code graph. The stdio and SSE transports, and every
per-agent server spawn they imply, are removed entirely.

## Why
Today every agent that wants cairn's 26 MCP tools spawns its own
`cairn serve` process: each pays full boot (catch-up scan, model
warmup, memory decay), each runs a private file watcher against the
same SQLite store, and each dies with its parent — so knowledge
recorded by one agent is cold and stale to the next, and the
codebase carries machinery (parent-death watchdog, stray sweeper,
spawn-probe) whose only job is to fight that failure class. One
shared daemon deletes the failure class instead of fighting it.

## Business value
- The multi-agent developer gets one always-fresh graph: decisions
  recorded by one agent are immediately visible to every other
  agent, with zero per-session cold starts. Success measure: two
  concurrent agents (e.g. Kilo + Claude Code) against one
  workspace, both served by one process (one pid, one port), with
  memory written by one readable by the other within one tool call.
- The single-agent user gets zero-setup attach: point the agent at
  cairn once; the first dial ensures the daemon is running. Success
  measure: from a cold machine, the first MCP request boots the
  daemon and succeeds without manual server management.
- The project deletes two transports, the stdio watchdog, the SSE
  branch, and the spawn-probe installer machinery — a permanently
  smaller MCP surface to maintain.

## User stories
### US1 — Share one daemon across agents (P1)
As the multi-agent developer, I want every coding agent to connect
to the same HTTP endpoint backed by one process, so that boot cost
is paid once and shared memory is instantly visible to all agents.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given the daemon is running, When a second agent connects
  and calls tools, Then both agents are served by the same process
  (no new server pid) and both see the same tool catalog (FR-001).
- AC2: Given the daemon is running read-write, When agent A records
  a memory via a write tool, Then agent B's next recall returns it
  (FR-002).
- AC3: Given any client attempts `cairn serve` with stdio or SSE
  transport, Then the command refuses with a pointer to the HTTP
  configuration (FR-003).

### US2 — Zero-setup first dial (P1)
As the single-agent local user, I want the first MCP request to
just work, so that I never manage a server manually.

**Acceptance criteria**:
- AC1: Given no daemon is running, When `cairn connect` runs, Then
  exactly one daemon starts detached and the URL is emitted (FR-004).
- AC2: Given two `cairn connect` invocations race, Then exactly one
  daemon exists afterwards (FR-004).
- AC3: Given the daemon crashed leaving an orphan, When the sweeper
  runs or connect is called, Then the orphan is evicted before or
  instead of a duplicate start (FR-008).

### US3 — Installers write URL configs (P1)
As the agent user running `cairn install-agents`, I want my
clients' MCP configs written in URL + key form, so that agents dial
the shared daemon instead of spawning processes.

**Acceptance criteria**:
- AC1: Given `cairn install-agents` runs, Then every supported
  client's registration is a remote/URL entry embedding the
  auto-generated bearer key (FR-006).
- AC2: Given a client registration is written, When verification
  runs, Then it probes the daemon health endpoint instead of
  spawning a process (FR-006).
- AC3: Given Claude Desktop (no URL support), Then the installer
  reports it as unsupported instead of writing a stdio config
  (FR-007).

### US4 — Migrate off stdio without archaeology (P2)
As an upgrading user with existing stdio registrations, I want
doctor to find and repoint them, so that the 0.23.0 break costs one
command.

**Acceptance criteria**:
- AC1: Given a client still has a cairn stdio registration, When
  `cairn doctor` runs, Then it flags the registration and `--fix`
  repoints it to the URL+key config (FR-009).
- AC2: Given the daemon serves requests, When any supported client
  dials with a stale or missing key on loopback, Then unkeyed
  loopback still succeeds and off-loopback without a valid key is
  refused (FR-005).

## Requirements
- **FR-001**: The system shall expose cairn's MCP tools over a
  Streamable HTTP endpoint at a single MCP path, with no stdio or
  SSE transport compiled into the serve path.
- **FR-002**: The system shall run the daemon read-write by default
  (live file watcher, boot catch-up, memory writes enabled); WHEN
  `--read-only` is passed, the system shall disable the watcher and
  write tools.
- **FR-003**: WHEN `cairn serve` is invoked with stdio- or SSE-specific options (including any removed `--transport` value), THEN the system shall exit non-zero with a message naming the HTTP replacement.
- **FR-004**: WHEN no daemon is listening on the configured port, THEN `cairn connect` shall start exactly one detached daemon instance (guarded against concurrent first starts) and emit the URL; WHEN a healthy daemon already listens, THEN `cairn connect` shall emit the URL without starting anything.
- **FR-005**: The system shall bind loopback by default on port
  9876 with the existing bind policy (Origin/DNS-rebinding
  protection; key mandatory off-loopback); `cairn init` shall
  generate and persist a `CAIRN_MCP_API_KEY`, and generated client
  configs shall embed it; unkeyed loopback dials shall remain
  permitted.
- **FR-006**: The system shall write every supported client's MCP
  registration in URL + key form, and registration verification
  shall probe the daemon health endpoint rather than spawning a
  process.
- **FR-007**: WHERE a client supports no remote/URL MCP registration (Claude Desktop), the installer shall report that client as unsupported and write no configuration for it.
- **FR-008**: WHEN the stray sweeper observes orphaned serve processes holding the store database open, THEN the system shall evict them (SIGTERM, escalating) as it does today, extended to the HTTP daemon.
- **FR-009**: WHEN `cairn doctor` finds cairn-owned stdio or SSE registrations, THEN it shall flag them and, under `--fix`, repoint them to the URL+key configuration.
- **FR-010**: The system shall provide daemon management commands
  (`serve start|stop|status|restart`) managing the HTTP daemon,
  with the existing launchd mechanism on macOS and a daemonize
  fallback elsewhere.
- **FR-011**: The system shall keep the MCP protocol surface
  implemented by the pinned SDK generation; no hand-rolled
  transport code shall be introduced (revision 2026-07-28 arrives
  via SDK bump only).

## Quality attributes
- **NFR-001**: Security — applicable: The system shall preserve the
  existing HTTP auth/bind policy unchanged (bearer middleware,
  healthz exemption, loopback-vs-wildcard transport security,
  pre-bind refusal of unkeyed non-loopback binds).
- **NFR-002**: Privacy — applicable: The generated API key shall be
  stored with user-only file permissions and never logged; existing
  credential-echo prohibitions hold.
- **NFR-003**: Performance — applicable: A warm daemon shall serve
  a second client's first tool call without re-running boot work
  (no catch-up scan, no model warmup) — the amortization is the
  point.
- **NFR-004**: Reliability — applicable: Concurrent `cairn connect` invocations shall converge to one daemon; a crashed daemon shall be replaceable by the next connect without manual cleanup.
- **NFR-005**: Observability — applicable: The daemon shall expose
  `/healthz` (existing) and its readiness line; `cairn connect`
  shall report whether it started or reused a daemon.
- **NFR-006**: Accessibility — not applicable: no new user-facing
  UI surface; CLI and config files only.

## Scope
**In**: server.py transport removal and HTTP-only `run()`;
read-write daemon default; `cairn connect`; serve daemon-manager on
HTTP with Linux fallback and start-race guard; auto-generated API
key at init; 8 installer config generators → URL+key; healthz-based
verification; doctor stdio detection + repoint; `config
--mcp-config` URL output; mcp.json.template, README, AGENTS.md,
docs sweep; deletion of stdio/SSE tests and their machinery;
specs/INDEX.md supersession note for remote-mcp-oauth.
**Out (deferred)**: OS-level socket activation packaging
(launchd/systemd units beyond the existing daemon manager);
MCP revision 2026-07-28 features (MRTR, subscriptions/listen,
Mcp-Method header validation) — SDK bump only; remote/team exposure
(TLS, OAuth, non-loopback hardening beyond the existing key gate);
per-tool permissions or rate limiting; Claude Desktop support.

## Assumptions & risks
- Assumption: bare `cairn serve` runs the foreground HTTP daemon on
  the default port; the `--transport` flag is removed entirely
  (intent Q&A, folded).
- Assumption: all 8 remaining clients accept URL-form MCP
  registrations in their current config surfaces (scout-verified
  shapes exist for each; installer tests pin them).
- Assumption: single-writer SQLite discipline holds because the
  daemon is the only writable server; concurrent agents are HTTP
  clients, not processes.
- Risk: SDK behavior drift in `streamable_http_app()` across the
  `mcp>=0.9.0,<2.0.0` pin range — mitigation: pin ceiling stays;
  parity/auth tests already exercise the app builder.
- Risk: breaking every existing stdio consumer on upgrade —
  mitigation: clean break is deliberate (intent Q5); doctor
  repoint + release notes carry migration.
- Risk: dormant `specs/remote-mcp-oauth` contradicts this spec's
  FR-003 — mitigation: INDEX.md supersession note ships with this
  spec (intent Q6 ruling).
