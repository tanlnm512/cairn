# Spec: remote-mcp-oauth

**Status**: draft
**Created**: 2026-09-16
**Branch**: `feat/remote-mcp-oauth`

## What
Serve cairn's MCP tools over the network with authenticated access: a
`cairn serve --remote` mode speaking Streamable HTTP (the current MCP remote
transport) and authorizing clients via OAuth 2.1 with PKCE, so cloud agents
and team members reach one shared cairn store without local process access.

## Why
The MCP ecosystem's 2026 default is remote, OAuth-secured servers. Cairn
today ships stdio (default) plus an unauthenticated local SSE daemon
(`cairn serve --port`, launchd-managed, read-only by default) — no
Streamable HTTP, no authentication, no per-workspace authorization. That
blocks team deployments behind an identity provider, cloud agents that
cannot reach a user's machine, and enterprise procurement that requires
authenticated access. Local-first remains the default mode; remote is an
additive opt-in that never changes the query path or the verification
contract.

## Business value
Teams run one shared cairn store; cloud-hosted agents consume cairn's 24
tools; enterprise security review passes on standard OAuth flows. Success:
a remote deployment serving ≥2 concurrent authenticated clients with no
change to resolution-label or critic-gate behavior.

## User stories
### US1 — Remote serving (P1)
As a platform engineer, I want `cairn serve --remote` so that agents and
teammates share one cairn store over the network.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given a running remote server, When an MCP client connects via
  Streamable HTTP, Then all 24 tools answer identically to stdio mode (FR-001).
- AC2: Given an unauthenticated request, When any tool is invoked, Then the
  server rejects it before touching the store (FR-003).

### US2 — OAuth authorization (P1)
As a security reviewer, I want OAuth 2.1 + PKCE authorization so that remote
access is governed by our identity provider.

**Acceptance criteria**:
- AC1: Given a configured IdP, When a client completes the OAuth 2.1 + PKCE
  flow, Then tool calls execute under that principal's workspace scopes (FR-002, FR-004).
- AC2: Given a principal without workspace access, When they request that
  workspace's tools, Then the server denies with an authorization error (FR-004).

### US3 — Local unchanged (P1)
As an existing single-user, I want stdio mode untouched so that remote
additions carry zero migration cost.

**Acceptance criteria**:
- AC1: Given no remote flags, When `cairn serve` runs, Then behavior is
  byte-identical to today's stdio mode (FR-005).

## Requirements
- **FR-001**: The system shall provide `cairn serve --remote` exposing the
  same 24 verified MCP tools over Streamable HTTP transport, superseding the
  legacy-SSE daemon path for remote deployments while leaving existing
  stdio and `--port` SSE behavior intact.
- **FR-002**: The remote server shall authorize clients via OAuth 2.1 with
  PKCE, supporting at least one external IdP (Auth0 or WorkOS) and a
  self-hosted provider.
- **FR-003**: WHEN a request arrives without a valid token, the remote
  server shall reject it before any store access.
- **FR-004**: The system shall scope authorization per workspace: each
  authenticated principal may access only the workspaces granted to it.
- **FR-005**: WHERE remote flags are absent, `cairn serve` shall behave
  identically to the current stdio and `--port` SSE modes, with no new
  dependencies loaded on those paths.
- **FR-006**: The system shall ship deployment documentation covering
  single-binary and container deployment of the remote server.
- **FR-007**: The remote deployment model shall be [NEEDS CLARIFICATION: self-hosted only, or also a hosted/managed cairn instance? The proposal leaves this open (hosted requires infrastructure and a business model).]

## Scope
**In**: Streamable HTTP transport, OAuth 2.1 + PKCE with external IdP support,
per-workspace authorization, deployment docs, tests for auth rejection and
tool parity.
**Out (deferred)**: Managed/hosted SaaS offering (pending FR-007 ruling);
per-tool granular permissions; billing/tenancy beyond workspace scoping;
rate limiting; legacy SSE transport.

## Assumptions & risks
- Assumption: self-hosted remote is the first deliverable; managed hosting is
  out of scope until FR-007 is ruled.
- Risk: OAuth implementation complexity and new attack surface — mitigation:
  use a proven library (per constitution C-03, recorded as a D-### decision),
  loopback-only default, pen-test checklist before release.
- Risk: remote mode dilutes the local-first brand — mitigation: local remains
  the documented default; remote is explicit opt-in.
