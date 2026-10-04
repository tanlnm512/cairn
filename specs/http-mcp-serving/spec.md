# Spec: http-mcp-serving

**Status**: draft
**Effort**: large
**Created**: 2026-10-04
**Branch**: `feat/http-mcp-serving`

## What
A third serving transport for the cairn MCP server: `cairn serve --transport
http` speaks Streamable HTTP — the MCP ecosystem's current remote default —
with bearer API-key authentication, an optional stateless mode for
load-balanced/CI deployments, and a machine-readable `/healthz` endpoint. A
minimal container image and deployment docs make the server runnable as a
dedicated service. Local stdio and SSE transports are untouched.

## Why
Cairn's serving surface today is loopback-only and unauthenticated: per-client
stdio and a shared SSE daemon. That blocks container deployments (the
user's stated goal for cairn), cloud-hosted agents, and any multi-machine
team use. The pinned `mcp` SDK ships Streamable HTTP end to end; the
remaining work is transport wiring, an auth gate in front of the existing
tool registry, and deployment packaging. This spec is the first milestone of
the remote-mcp-oauth direction: it deliberately ships API-key auth now and
leaves OAuth 2.1 (that spec's FR-002–004) as the next milestone.

## Business value
Cairn becomes deployable as a shared service: one container serves multiple
agents/machines against one store, with a health probe for orchestrators.
Success: an authenticated HTTP client answers all tools identically to
stdio; an unauthenticated request is rejected before touching the store; a
container runs the server with a volume-mounted store and passes a
`/healthz` check.

## User stories
### US1 — HTTP transport (P1)
As a platform engineer, I want the MCP tools served over Streamable HTTP so
that agents on other machines share one cairn store.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given a running HTTP server, When an authenticated MCP client
  connects and invokes any tool, Then answers are identical to stdio mode
  for the same store.
- AC2: Given a running HTTP server, When `/healthz` is probed, Then it
  returns a machine-readable healthy/unhealthy response without auth.

### US2 — API-key auth (P1)
As a security reviewer, I want bearer-key authentication on the HTTP surface
so that only holders of the key can touch the store.

**Acceptance criteria**:
- AC1: Given a request without (or with a wrong) credential, When any
  endpoint except `/healthz` is invoked, Then the server rejects it before
  any store access.
- AC2: Given the key resolved from `CAIRN_MCP_API_KEY` or `--api-key`
  (flag takes precedence), When the server starts, Then the credential
  source is resolved and documented.

### US3 — Binding policy (P1)
As a local user, I want the loopback-first default preserved so that nothing
changes unless explicitly requested.

**Acceptance criteria**:
- AC1: Given no host override, When the HTTP server starts, Then it binds
  loopback only.
- AC2: Given a non-loopback bind, When the server starts without a
  configured key, Then startup is refused; keyless operation is allowed
  only on loopback binds.

### US4 — Stateless mode (P2)
As a CI/operator, I want a stateless HTTP mode with no server-side session
affinity, so that requests can load-balance across replicas.

**Acceptance criteria**:
- AC1: Given `--stateless`, When sequential requests arrive without session
  state, Then tool calls succeed independently.

### US5 — Container packaging (P2)
As an operator, I want a minimal image and deployment docs so that the
server runs as a dedicated container service.

**Acceptance criteria**:
- AC1: Given the Dockerfile, When the image is built and run with a
  volume-mounted store and the documented env, Then `/healthz` passes and
  an authenticated client completes a tool call.

### US6 — Local transports unchanged (P1)
As an existing local user, I want stdio and SSE behavior byte-identical, so
that the new transport carries zero migration cost.

**Acceptance criteria**:
- AC1: Given no HTTP flags, When `cairn serve` runs, Then behavior matches
  today's stdio/SSE paths (existing pins stay green untouched).

## Requirements
- **FR-001**: The system shall provide `cairn serve --transport http` (with
  `--host`/`--port`) exposing the same MCP tool registry over Streamable
  HTTP, superseding neither the stdio nor the `--port` SSE paths.
- **FR-002**: WHEN a key is required (non-loopback binds), the HTTP surface
  shall require a bearer API key on every endpoint except the health probe;
  the key shall be sourced from the
  `CAIRN_MCP_API_KEY` environment variable or the `--api-key` flag, with
  the flag taking precedence. WHEN a key is required and neither source
  provides one, the system shall refuse to start with a clear error.
- **FR-003**: WHEN a request arrives without a valid credential, the system
  shall reject it with an auth error before any store access.
- **FR-004**: The system shall bind loopback by default, with keyless
  operation allowed only on loopback binds; WHERE a non-loopback host is
  configured, the system shall require an API key and shall apply the SDK's
  transport-security (Host-header) protections appropriate to the bind.
- **FR-005**: The system shall provide `/healthz` returning a
  machine-readable health payload (store reachable, read-only mode,
  degradation count) without requiring authentication.
- **FR-006**: WHERE `--stateless` is passed, the system shall run the
  Streamable HTTP session manager in stateless mode.
- **FR-007**: The HTTP server shall default to read-only mode (consistent
  with the SSE daemon tri-state), overridable by the existing explicit
  read-only flag.
- **FR-008**: The system shall ship a Dockerfile (slim base, non-root,
  `CAIRN_HOME=/data` volume, HTTP entrypoint) and deployment documentation
  covering local, networked, and container serving.

## Quality attributes
- **NFR-001**: Security — applicable: WHEN any HTTP request arrives, the
  system shall enforce the auth gate and Host-header protections before
  dispatch; secrets shall never appear in argv-rendered logs.
- **NFR-002**: Privacy — applicable: the HTTP surface shall expose only the
  same store data the local transports expose; no telemetry or store
  metadata leaks through the health probe beyond its documented payload.
- **NFR-003**: Performance — applicable: WHEN tools are invoked over HTTP,
  the system shall not add per-call store-access overhead beyond session
  handling (no per-request reopen regression vs SSE).
- **NFR-004**: Reliability — applicable: WHEN the store is unavailable, the
  server shall report unhealthy on `/healthz` and answer tool calls with a
  clean error, not a crash.
- **NFR-005**: Observability — applicable: the server shall log lifecycle
  milestones (bind, auth failures at WARN, shutdown) at appropriate levels
  without request-level noise.
- **NFR-006**: Accessibility — not applicable: no UI surface is touched.

## Scope
**In**: Streamable HTTP transport wiring; bearer-key middleware; binding +
transport-security policy; `/healthz`; stateless mode; read-only default;
Dockerfile + deployment docs; tool-parity and auth-rejection tests.
**Out (deferred)**: OAuth 2.1 + PKCE and per-workspace authorization
(remote-mcp-oauth's next milestone); per-tool granular permissions; rate
limiting; TLS termination (delegate to reverse proxy); install-agents HTTP
config wiring (follow-up); legacy SSE changes.

## Assumptions & risks
- Assumption: the pinned mcp SDK (1.29.0) exposes `streamable_http_app()`,
  `stateless_http`, and `TransportSecuritySettings` as the survey will
  verify; no SDK bump needed.
- Risk: a network-exposed server is a new attack surface — mitigation:
  default loopback bind, mandatory key off-loopback (per FR-004
  resolution), Host-header protections, auth rejection before store access,
  pen-test checklist in the deployment docs.
- Risk: HTTP mode weakens the local-first brand — mitigation: stdio/SSE
  byte-identical (US6), HTTP is explicit opt-in.
- Risk: container users mounting stores across machines hit SQLite-on-NFS
  corruption — mitigation: deployment docs mandate local-volume storage;
  `/healthz` reports store reachability.
