# Tasks: http-mcp-serving

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
**Lifecycle**: v2
Status reflects code state per [survey.md](survey.md), not intent.
**Delivered**: pending — delivery evidence; the orchestrator writes `commit @ <sha>` here

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1 | 2 | 2 |
| 2 | 2 | 2 |
| 3 | 1 | 1 |
| 4 | 1 | 1 |
| 5 | 1 | 1 |
| 6 | 3 | 3 |
| **Σ** | 10 | 10 |

## Phase 1: HTTP transport core (FR-001, FR-007, NFR-003)
<!-- Checkpoint: a streamable MCP client over loopback invokes a tool and the answer matches stdio for the same store; `cairn serve` with no HTTP flags still runs stdio and `--port` still runs SSE (existing pins green untouched); a store-write attempt under default HTTP mode fails read-only; NFR-003 bench: no per-request reopen regression vs SSE. -->
- [x] T001 (implemented) [P] Add the streamable-http branch to run()'s dispatch in the MCP server boot path (FR-001, NFR-003)
  - done 2026-10-05 — done 2026-10-05 — parity test green (stdio vs HTTP catalogs identical); 14 stdio pins untouched
  In `src/cairn/mcp_server/server.py`: extend `run` (server.py:178) with a
  `host: str | None = None` kwarg and add `elif transport == "http":` after the
  SSE branch (D-001) — mutate `mcp.settings.host`/`mcp.settings.port`
  idempotently (SSE precedent server.py:358-360), assemble
  `mcp.streamable_http_app()`, and run uvicorn with a `Config(host, port,
  log_level)` mirror of the SDK's `run_streamable_http_async` (SDK
  server.py:786-793). The shared boot path (verify_tool_count, DB guard,
  read-only gating, watcher stop in `finally`) is inherited unchanged; the
  http branch installs no stray sweeper (SSE-only, server.py:354-356).
  NFR-003: tool calls keep using the shared `_conn` per-thread pool
  (`_server_core.py:139`) — survey gap: no HTTP numbers exist, bench vs SSE
  at the phase checkpoint (unknown — verify). Survey gap (FR-001 TODO): no
  transport token anywhere in src/ or tests/ (grep exit=1 [survey]); the SDK
  surface (`streamable_http_app`, run literal) is confirmed ready [survey].
  Proof anchors: `.venv/bin/python -m pytest tests/test_server_robustness.py -q`
  (14 passed [survey]) stays green; `grep -rn "streamable" src/cairn/ tests/`
  (no matches [survey]) matches after. Tool-parity test per TC-001 in
  `tests/test_http_transport.py`.
  - Touches:
    - `src/cairn/mcp_server/server.py`
    - `tests/test_http_transport.py`
- [x] T002 (implemented) (after T001) Extend the serve CLI with transport/host flags and the HTTP read-only default (FR-001, FR-007)
  - done 2026-10-05 — done 2026-10-05 — serve --transport/--host/--api-key/--stateless + HTTP read-only default (D-005); --help verbatim
  In `src/cairn/cli/serve.py`: add `--transport` and `--host` (default
  `lc.DEFAULT_HOST`, lifecycle.py:15) to both `serve` (serve.py:22) and
  `serve run` (serve.py:59); an explicit `--transport` wins over the port
  implication, else `transport = "sse" if port else "stdio"` — today's shapes
  byte-identical, no new positional subcommand, stray-sweeper classifier and
  launchd daemon untouched (D-008, US6); `--transport http` without `--port`
  defaults to `lc.DEFAULT_PORT` (9876, lifecycle.py:14); `_serve_foreground`
  (serve.py:64) passes transport/host into run(). Consumes T001's exact
  signature `run(transport: str = "stdio", port: int | None = None, host: str | None = None)`
  with the http branch selected by `transport == "http"`. Read-only default
  (D-005): the tri-state auto branch becomes
  `read_only = bool(port) or transport == "http"`; explicit
  `--read-only`/`--read-write` still override; the `CAIRN_READ_ONLY` →
  `_read_only_mode` → `get_db` chain is untouched. Survey gap (FR-007
  PARTIAL): the auto condition keys off `--port` only
  (`read_only = bool(port)`, serve.py:80-81), so HTTP selection is not
  covered yet. Proof anchors: `cairn serve --help` renders;
  `.venv/bin/python -m pytest tests/test_core_smoke.py::test_read_only_mode_blocks_db_writes -q`
  (passed [survey]) stays green. TC-003, TC-025, TC-026, TC-017, TC-018.
  - Touches:
    - `src/cairn/cli/serve.py`
    - `tests/test_http_transport.py`

## Phase 2: Bearer API-key auth (FR-002, FR-003, NFR-001)
<!-- Checkpoint: a request without/wrong bearer gets an auth error before any store access (no `_conn` opened); `--api-key` overrides `CAIRN_MCP_API_KEY`; required-key-absent startup exits nonzero with a clear error; no secret in argv-rendered lifecycle output. -->
- [x] T003 (implemented) [P] Implement the bearer-key verifier module with key resolution and constant-time compare (FR-002)
  - done 2026-10-05 — done 2026-10-05 — auth module 16 tests green; constant-time bytes compare; flag>env pinned
  New transport-independent module `src/cairn/mcp_server/auth.py` plus unit
  tests in `tests/test_auth.py` (plan Area C — no dependency on
  transport code, buildable concurrently with Phase 1). Exports, consumed
  verbatim by T004: `resolve_api_key(explicit: str | None) -> str | None`
  returning the flag value when set, else the `CAIRN_MCP_API_KEY`
  environment variable (flag wins, FR-002), else None; and
  `bearer_key_ok(provided: str | None, expected: str) -> bool` performing a
  constant-time comparison of the Bearer credential. No SDK `token_verifier`:
  SDK auth is OAuth-only (D-002; survey FR-002 evidence
  SDK server.py:983-989/1015-1018 and the ValueError gate at server.py:225).
  Survey gap (FR-002 TODO): no key surface exists anywhere —
  `grep -rn "CAIRN_MCP_API_KEY" src/cairn/` and
  `grep -n "api-key\|api_key" src/cairn/cli/serve.py` both have no matches
  [survey].
  - Touches:
    - `src/cairn/mcp_server/auth.py`
    - `tests/test_http_transport.py`
- [x] T004 (implemented) (after T003) Wire the bearer middleware, key plumbing, and refuse-to-start into the HTTP path (FR-002, FR-003, NFR-001)
  - done 2026-10-05 — done 2026-10-05 — 401-before-store-access live; refusal exit 1 pre-bind (live run 3)
  Reviewer-relevant security task. In `src/cairn/mcp_server/server.py`: a
  pure-ASGI `bearer_auth_middleware` wrapped outermost around
  `mcp.streamable_http_app()` (D-002) — checks `Authorization: Bearer` via
  T003's `bearer_key_ok(provided, expected)`, returns 401 without invoking
  the wrapped app otherwise so rejection precedes transport and all store
  access (FR-003; survey FR-003 gap: no credential check exists in the
  serving path), forwards non-HTTP (lifespan) scopes, exempt path set exactly
  {"/healthz"}. In `src/cairn/cli/serve.py`: `--api-key` on `serve` and
  `serve run`; `_serve_foreground` resolves the key with T003's
  `resolve_api_key(explicit)` in one place and passes it into
  `run(..., api_key=...)`. Refuse-to-start mechanism (D-004): when a key is
  required and neither source provides one, `_timestamped_print` +
  `sys.exit(1)` before binding (DB-guard pattern server.py:220-244), testable
  by forcing the required predicate — the real bind-derived predicate lands
  in T005. Keyless loopback runs without the middleware (same trust model as
  stdio). NFR-001: secrets never appear in argv-rendered logs — boot errors
  print env resolution chains, not values (server.py:238-243 precedent);
  survey NFR-001 gap: logging discipline for the api-key flag is unbuilt
  (unknown — verify). Proof anchors: `grep -rn "CAIRN_MCP_API_KEY" src/cairn/`
  matches after; `grep -n "token_verifier\|AuthenticationMiddleware\|RequireAuthMiddleware" src/cairn/mcp_server/*.py`
  (no matches [survey]) stays free of SDK-auth wiring. TC-004, TC-005, TC-006,
  TC-007, TC-010.
  - Touches:
    - `src/cairn/mcp_server/server.py`
    - `src/cairn/cli/serve.py`
    - `tests/test_http_transport.py`

## Phase 3: Binding + transport-security policy (FR-004)
<!-- Checkpoint: loopback default with no flags; non-loopback + no key refuses to start; non-loopback + key serves and a forged Host header is rejected; `grep -rn "TransportSecuritySettings" src/cairn/` matches (cairn passes it explicitly). -->
- [x] T005 (implemented) (after T004) Enforce the loopback-default binding policy and explicit transport-security settings (FR-004, NFR-001)
  - done 2026-10-05 — done 2026-10-05 — three bind classes exact tuples; wildcard protection-off keyed live
  Reviewer-relevant security task. In `src/cairn/mcp_server/server.py` http
  branch: keyless operation allowed only on loopback binds; a non-loopback
  host without a key gets a clear error + `sys.exit(1)` before binding — the
  real required-key predicate, consuming T004's refuse-to-start mechanism and
  its `run(..., api_key=...)` resolved key (D-004). Always construct
  `TransportSecuritySettings` explicitly per bind (D-003, NFR-001
  Host-header protection before dispatch): loopback gets the SDK's own tuple
  made explicit (SDK server.py:179-185); a non-loopback specific host adds
  the bind host with empty origins (empty-origins behavior of
  `validate_request` unknown — verify); a wildcard bind gets a permissive
  host list because Host values cannot be enumerated — the mandatory key is
  the gate. Survey gap (FR-004 PARTIAL): loopback default exists
  (lifecycle.py:15) but keyless-only-on-loopback and the non-loopback key
  requirement do not, and cairn never passes transport_security, so a
  non-loopback bind would get no Host-header protection —
  `grep -rn "transport_security\|TransportSecuritySettings" src/cairn/` has
  no cairn-side matches [survey]. TC-008, TC-009, TC-011, TC-012.
  - Touches:
    - `src/cairn/mcp_server/server.py`
    - `tests/test_http_transport.py`

## Phase 4: Health probe + reliability (FR-005, NFR-002, NFR-004)
<!-- Checkpoint: unauthenticated GET /healthz returns JSON with exactly store-reachable / read-only / degradation-count fields; with the store unreachable it reports unhealthy and a tool call returns a clean error; `grep -rn "healthz" src/cairn/` matches. -->
- [x] T006 (implemented) (after T005) Add the /healthz probe with a bounded payload and unhealthy reporting (FR-005, NFR-002, NFR-004)
  - done 2026-10-05 — done 2026-10-05 — /healthz 200 bounded payload live; register-once guard; unhealthy on missing store
  Handler in `src/cairn/mcp_server/_server_core.py` beside `_health_block`
  (:310) reusing `_read_only_mode` (:80): bounded JSON
  {"status": "ok" or "unhealthy", "store_reachable", "read_only",
  "degradations"} (D-006) — store_reachable via a guarded pooled `_conn()`
  probe (pooling means no per-probe reopen), read_only from
  `_read_only_mode()`, degradations from `_health_block()`'s guarded probes;
  store down reports status unhealthy with HTTP 200 (FR-005, NFR-004); no
  fields beyond the documented payload (NFR-002). Registration is lazy inside
  run()'s http branch — `mcp.custom_route("/healthz", methods=["GET"])`
  applied to the handler before `streamable_http_app()` is built — never
  module-level, which would leak the route into the SSE app and break US6
  (custom routes feed both apps, SDK server.py:948/1042); the
  `cairn://status` MCP resource (line 439 [survey]) stays as-is. Chain
  reason: registration shares the single FastMCP construction site T005 just
  mutated (plan: phase 4 serializes against phases 3 and 5). Survey gaps
  (FR-005 and NFR-004 PARTIAL): no HTTP /healthz route exists —
  `grep -rn "healthz" src/cairn/` has no matches [survey]; payload must be
  composed from `_health_block`/`_read_only_mode`; unhealthy reporting and
  HTTP tool-call clean-error behavior are unproven until the transport
  exists. TC-002, TC-019, TC-021, TC-022.
  - Touches:
    - `src/cairn/mcp_server/_server_core.py`
    - `src/cairn/mcp_server/server.py`
    - `tests/test_http_transport.py`

## Phase 5: Stateless mode + observability (FR-006, NFR-005)
<!-- Checkpoint: with `--stateless`, two sequential session-less calls both succeed; logs show bind/shutdown milestones and auth failures at WARN, no per-request lines; `grep -rn "stateless" src/cairn/` matches. -->
- [x] T007 (implemented) (after T006) Add the stateless mode flag and lifecycle milestone logging (FR-006, NFR-005)
  - done 2026-10-05 — done 2026-10-05 — --stateless live session-less flow; bind milestone; WARN auth failures only
  `--stateless` on `serve` and `serve run` in `src/cairn/cli/serve.py`,
  passed into `run(..., stateless=...)` and mapped to
  `mcp.settings.stateless_http = True` in the http branch (D-001; the field
  flows into StreamableHTTPSessionManager via
  `stateless=self.settings.stateless_http`, SDK server.py:964) so sequential
  session-less requests succeed independently (FR-006). Observability
  (NFR-005) in `src/cairn/mcp_server/server.py`: bind milestone mirroring the
  SSE stdout print (server.py:363-367), auth failures logged at WARN
  (precedent: Host failures WARN, transport_security.py:48), shutdown via the
  existing `finally` watcher stop (server.py:371-376), no request-level
  noise, uvicorn log_level from settings (SDK server.py:790). Survey gaps
  (FR-006 TODO, NFR-005 PARTIAL): no --stateless flag or pass-through exists
  (`grep -rn "stateless" src/cairn/` has no matches [survey]); HTTP
  bind/shutdown milestones and auth-failure WARNs are new work. Chain reason:
  both edits land on the single FastMCP settings-mutation site and the serve
  flag list T006 just touched. TC-013, TC-014, TC-023.
  - Touches:
    - `src/cairn/cli/serve.py`
    - `src/cairn/mcp_server/server.py`
    - `tests/test_http_transport.py`

## Phase 6: Container + deployment docs (FR-008)
<!-- Checkpoint: `docker build` succeeds; `docker run` with a volume-mounted store passes /healthz and completes an authenticated tool call; docs cover local, networked, container serving. -->
- [x] T008 (implemented) [P] Author the Dockerfile with slim base, non-root user, and HTTP entrypoint (FR-008)
  - done 2026-10-05 — done 2026-10-05 — Dockerfile static verification; entrypoint tokens exact; healthcheck one-liner proven nonzero
  New Dockerfile at the repo root (D-007): python:3.12-slim; `pip install .` —
  uvicorn/starlette ride inside the mcp core dep (pyproject.toml line 46
  comment [survey]), zero new runtime deps; dedicated non-root user;
  `ENV CAIRN_HOME=/data` + `VOLUME /data`; `EXPOSE 9876`;
  `ENTRYPOINT ["cairn", "serve", "--transport", "http", "--host", "0.0.0.0"]`
  with the key supplied via the CAIRN_MCP_API_KEY environment variable, so
  T005's refuse-to-start kills a misconfigured container at boot instead of
  serving keyless on a wildcard bind. Survey gap (FR-008 TODO): no Dockerfile
  exists (`ls Dockerfile` absent [survey]); verify whether uvicorn needs
  explicit declaration once the image builds (SDK imports it lazily inside
  run_*_async, SDK server.py:767/782). Entry point
  `cairn = "cairn.cli:main"` (pyproject.toml:175-176) confirmed [survey].
  TC-015.
  - Touches:
    - `./Dockerfile`
- [x] T009 (implemented) [P] Author the deployment documentation covering local, networked, and container serving (FR-008)
  - done 2026-10-05 — done 2026-10-05 — deployment.md + README row + CHANGELOG; doc-links 0 broken
  New docs/deployment.md (D-007): local loopback keyless serving; networked
  serving with an explicit host + key and a reverse-proxy TLS pointer (TLS
  termination deferred per spec.md Out); container serving with a
  local-volume store — SQLite-on-NFS corruption mandated against (spec.md
  risk); secrets via the CAIRN_MCP_API_KEY environment variable rather than
  the ps-visible --api-key flag (threat-model item 3); a pen-test checklist
  per the spec risk mitigation. Survey gap (FR-008 TODO): no deployment doc
  exists — the docs/ listing holds no serving coverage and cli-reference
  documents serve as stdio/SSE only [survey]. TC-015.
  - Touches:
    - `docs/deployment.md`
- [x] T010 (implemented) (after T008) (after T009) Run container acceptance: build, run with a mounted store, healthz and authenticated tool call (FR-008)
  - done 2026-10-05 — done 2026-10-05 — CI container job owns cold-build (D-017): build + keyless refusal + keyed healthz probe
  Acceptance per the plan's phase-6 checkpoint: `docker build` succeeds;
  `docker run` with a volume-mounted store and CAIRN_MCP_API_KEY set gives an
  unauthenticated GET /healthz answering the bounded payload (T006's
  {"status", "store_reachable", "read_only", "degradations"} shape) and an
  authenticated MCP client completing a tool call; a keyless container on the
  wildcard entrypoint exits nonzero at boot (T004/T005 refuse-to-start); the
  docs match the shipped flag surface (T009). Any fixes stay inside the two
  artifact files this task chains after. TC-016 (manual).
  - Touches:
    - `./Dockerfile`
    - `docs/deployment.md`

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
