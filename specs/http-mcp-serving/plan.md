# Plan: http-mcp-serving

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-04
**Team context**: none recorded — solo assumed, PR-per-milestone. Sources: [spec.md](spec.md) + [survey.md](survey.md) (code state derives only from survey evidence).

## Milestones

| Phase | Milestone | Delivers (demoable) | Requirements | Depends on |
|-------|-----------|---------------------|--------------|------------|
| 1 | HTTP transport core | `cairn serve --transport http [--host H] [--port N]` serves the existing tool registry over Streamable HTTP on loopback; a tool call answers identically to stdio; HTTP defaults to read-only (tri-state, `--read-only/--read-write` override); stdio/SSE behavior unchanged | FR-001, FR-007, NFR-003 | — |
| 2 | Bearer API-key auth | A request with a missing/wrong credential is rejected with an auth error before any store access; key resolves from `CAIRN_MCP_API_KEY` or `--api-key` (flag wins); when a key is required and absent, startup refuses with a clear error; secrets never render into argv-style logs | FR-002, FR-003, NFR-001 | Phase 1 |
| 3 | Binding + transport-security policy | No host override → loopback bind; keyless operation only on loopback; a non-loopback bind without a key refuses to start; a non-loopback bind runs with cairn-applied SDK Host-header (DNS-rebinding) protections | FR-004 | Phase 2 (+ TransportSecuritySettings decision, see Risks) |
| 4 | Health probe + reliability | `/healthz` answers unauthenticated with a machine-readable payload (store reachable, read-only mode, degradation count) bounded to the documented fields; store unavailable → `/healthz` unhealthy and tool calls return a clean error, not a crash | FR-005, NFR-002, NFR-004 | Phase 1 |
| 5 | Stateless mode + observability | `--stateless` serves sequential session-less requests independently; lifecycle milestones (bind, auth failures at WARN, shutdown) log at appropriate levels with no request-level noise | FR-006, NFR-005 | Phase 1 (order vs Phase 4 swappable) |
| 6 | Container + deployment docs | Image builds (slim, non-root, `CAIRN_HOME=/data` volume, HTTP entrypoint); a container run with a volume-mounted store passes `/healthz` and completes an authenticated tool call; docs cover local, networked, and container serving | FR-008 | Phases 2 + 4 (acceptance); sequencing last per orchestrator addendum |

Coverage check: FR-001..008 each in exactly one phase; applicable NFR-002..005 each in exactly one phase, NFR-001 spanning Phases 2-3 (auth gate, transport security); NFR-006 not applicable (survey item NFR-006, gap: none). US6 (local transports byte-identical) is a standing invariant re-verified at every checkpoint, not a separate phase.

## Dependencies

Spine: **1 → 2 → 3**, with **4** and **5** hanging off 1, and **6** last.

- 1 → 2: the auth wrapper exists only on the streamable app's route (SDK installs `RequireAuthMiddleware`/`BearerAuthBackend` when a token verifier is set — survey FR-003 evidence); the verifier and flags cannot wire into anything until Phase 1 builds the transport.
- 2 → 3: FR-004's "non-loopback requires a key" and the refuse-to-start rule consume Phase 2's key-resolution mechanism. Phase 2 owns the mechanism (resolution, precedence, refusal behavior, testable by forcing the required predicate); Phase 3 supplies the real predicate (bind address) and the explicit `TransportSecuritySettings`.
- 1 → 4: `/healthz` rides the SDK `custom_route` escape hatch, appended to the streamable app built in Phase 1 (survey FR-005 evidence); the payload composes `_health_block` + `_read_only_mode` in `_server_core.py`.
- 1 → 5: `--stateless` is a pass-through to the FastMCP session manager (`stateless_http` setting — survey FR-006 evidence); nothing to configure before the transport exists.
- 6 last: the container acceptance test exercises auth (Phase 2) and `/healthz` (Phase 4); deployment docs describe the final flag/behavior surface. Orchestrator addendum: container/docs milestone sequenced last.

## Parallelization map

Parallel is the default; the serial exceptions below carry the proof.

Independent (concurrent by default):
- **Area A — Phase 1 transport core**: `src/cairn/cli/serve.py` (flags, dispatch, read-only default), `src/cairn/mcp_server/server.py` (streamable-http branch, host plumbing), `src/cairn/mcp_server/_server_core.py` (settings), tests. Nothing else in the plan can start wiring until its app exists, so it is the spine starter, not a blocker on Area B.
- **Area B — Phase 6 authoring** (Dockerfile at repo root + deployment doc under `docs/`): new files, zero overlap with any `src/cairn/` file — can be authored concurrently from day one. Only its *acceptance* is serial (needs Phases 2 + 4 running).
- **Area C — Phase 2 auth module**: the key-resolution/compare verifier is a new module + unit tests with no dependency on transport code; it can be built concurrently with Phase 1. Its *wiring* (singleton constructor + CLI flag) serializes after Phase 1.

Strictly ordered (exceptions — justification):
- **2 → 3**: Phase 3 consumes Phase 2's refuse-to-start mechanism; both edit `src/cairn/cli/serve.py` option surfaces and the boot path in `src/cairn/mcp_server/server.py`.
- **Phases 2, 3, 5 pairwise serial**: every new server behavior funnels through exactly one FastMCP construction — `src/cairn/mcp_server/_server_core.py:68` (`mcp = FastMCP(...)`, module-level, built at import; grep confirms it is the only construction) — plus the `serve` CLI flag list. token_verifier (2), transport_security (3), and stateless_http (5) all mutate that single line/site; concurrent branches cannot merge cleanly. Phase 4's route registration shares `_server_core.py` payload helpers, so it serializes against 3 and 5 for the same reason, though its helper assembly can overlap.
- **4 vs 5 order-swappable**; both before 6 (docs document stateless load-balancing and the health probe).
- **6 acceptance last**: see Dependencies.

## Checkpoints

- **After Phase 1**: an MCP streamable client over loopback invokes a tool and the answer matches stdio for the same store; `cairn serve` with no HTTP flags still runs stdio and `--port` still runs SSE (existing pins untouched). Verify: `.venv/bin/python -m pytest tests/test_server_robustness.py -q` and `grep -rn "streamable" src/cairn/` (matches now; exit=1 before, per survey FR-001) and a store-write attempt under default HTTP mode fails read-only (extends `tests/test_core_smoke.py::test_read_only_mode_blocks_db_writes` shape). NFR-003: bench tool-call latency HTTP vs SSE — no per-request reopen regression (pooling machinery shared via `_conn`; HTTP numbers unknown — verify at this checkpoint).
- **After Phase 2**: request without/wrong bearer → auth error before store access (no `_conn` opened); `--api-key` overrides `CAIRN_MCP_API_KEY`; required-key-absent startup exits nonzero with a clear error; no secret in argv-rendered lifecycle output. Verify: `grep -rn "CAIRN_MCP_API_KEY" src/cairn/` (matches) plus new auth-rejection tests.
- **After Phase 3**: loopback default with no flags; non-loopback + no key → refuse to start; non-loopback + key → served and a forged Host header is rejected. Verify: `grep -rn "TransportSecuritySettings" src/cairn/` (matches — cairn now passes it explicitly; before, only SDK loopback auto-protection existed, survey FR-004).
- **After Phase 4**: unauthenticated `GET /healthz` returns JSON with exactly store-reachable / read-only / degradation-count fields (nothing more — NFR-002); with the store unreachable it reports unhealthy and a tool call returns a clean error. Verify: `grep -rn "healthz" src/cairn/` (matches) + new endpoint tests.
- **After Phase 5**: with `--stateless`, two sequential session-less calls both succeed; logs show bind/shutdown milestones and auth failures at WARN, no per-request lines. Verify: `grep -rn "stateless" src/cairn/` (matches) + new tests.
- **After Phase 6**: `docker build` succeeds; `docker run` with a volume-mounted store → `/healthz` passes and an authenticated client completes a tool call; docs cover local, networked, container. Verify: `ls Dockerfile` (present) + the container run itself.

## Risks & mitigations

- **TransportSecuritySettings decision (gating, per orchestrator addendum)**: the SDK auto-applies DNS-rebinding protection ONLY for loopback binds when `transport_security` is None; a non-loopback bind without an explicit cairn-constructed `TransportSecuritySettings` silently gets no Host-header protection (survey FR-004 + supporting evidence, fastmcp `__init__`/`TransportSecurityMiddleware`). → tech.md must resolve and sequence this decision early: it gates Phase 3, and no non-loopback task starts before it lands. Phase 3 pulled immediately after Phase 2 for de-risking.
- **Singleton mutation ordering**: settings must be applied before the streamable app is built (SSE precedent: `mcp.settings.port` mutation in `server.py`'s SSE branch). → Phase 1 owns the settings-plumbing pattern all later phases reuse.
- **Container store integrity**: SQLite over network volumes corrupts (spec risk). → deployment docs mandate local-volume mounts; `/healthz` reports store reachability (Phase 4).
- **uvicorn in the slim image**: SDK imports uvicorn lazily; whether it needs explicit declaration in image deps is unknown — verify when building (survey FR-008 gap).
- **Regression on stdio/SSE (US6)**: standing invariant — existing dispatch/robustness pins stay green untouched at every checkpoint; HTTP is explicit opt-in via `--transport http`.
- **Spec assumption now evidenced**: SDK 1.29.0 exposes `streamable_http_app`, `stateless_http`, and `TransportSecuritySettings` — confirmed by survey (items FR-001, FR-006, FR-004). No SDK bump planned.

## Delivery

Solo, one branch `feat/http-mcp-serving`, one PR per milestone (phases 1-6 in order), conventional-commit titles. Each PR must pass the pre-commit/CI gates and leave the stdio/SSE pins green before merge; `cairn update` + `record_memory` after each merge per workspace workflow.
