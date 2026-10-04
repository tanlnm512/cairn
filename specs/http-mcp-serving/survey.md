# Survey: http-mcp-serving

**Created**: 2026-10-04 | **Baseline**: cairn-intel 0.21.2 @ bc0a359c215d08bcafd41ed22cc12bd558868130
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.
Installed MCP SDK pin: `mcp` 1.29.0 (`.venv/lib/python3.12/site-packages/mcp-1.29.0.dist-info/METADATA:3:Version: 1.29.0`); pyproject constraint `mcp>=0.9.0,<2.0.0` (pyproject.toml:46). SDK citations use full venv-relative paths.

## Items

```
item FR-001: "cairn serve --transport http (+--host/--port) exposing the same MCP
             tool registry over Streamable HTTP; stdio and --port SSE paths preserved"
  evidence:   Dispatch is binary stdio|sse today. src/cairn/mcp_server/server.py:run:178 —
              "def run(transport: str = "stdio", port: int | None = None):"; src/cairn/mcp_server/server.py:368
              "mcp.run(transport="sse")" and server.py:370 "mcp.run()" are the only two mcp.run call sites;
              src/cairn/cli/serve.py:_serve_foreground:64 dispatch line 85 — "run(transport="sse" if port else "stdio", port=port)";
              serve CLI options are only --db/--port/--read-only (src/cairn/cli/serve.py:serve:22 group, lines 13-20).
              No "streamable" token anywhere in src/ or tests/ (grep exit=1).
              SDK surface ready: .venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:run:282 —
              'transport: Literal["stdio", "sse", "streamable-http"] = "stdio"'; case "streamable-http" at
              server.py:302-303 "anyio.run(self.run_streamable_http_async)";
              .venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:streamable_http_app:953 returns the Starlette app;
              .venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:run_streamable_http_async:780 runs uvicorn with
              "host=self.settings.host, port=self.settings.port" (lines 786-789).
              Same registry: all tools decorate one singleton — src/cairn/mcp_server/_server_core.py:68
              "mcp = FastMCP("cairn", lifespan=app_lifespan, log_level="WARNING")"; all six tools_*.py
              modules import it ("from ._server_core import ... mcp" in tools_knowledge.py:8,
              tools_compass.py:6, tools_federation.py:6, tools_memory.py:8, tools_wiki.py:6, tools_graph.py:11-19).
  status:     TODO
  verify:     grep -rn "streamable" src/cairn/ tests/   (exit=1, no matches) && .venv/bin/python -m pytest tests/test_server_robustness.py -q   (14 passed in 0.79s — existing dispatch tests)
  gap:        No --transport flag, no --host flag on serve, no streamable-http branch in
              src/cairn/mcp_server/server.py:run:178, no host plumbed into mcp.settings. SDK run/streamable_http_app
              require no cairn-side registry change (shared singleton).

item FR-002: "Bearer API key on every endpoint except health; CAIRN_MCP_API_KEY env or
             --api-key flag (flag wins); refuse to start when required and absent"
  evidence:   No key surface exists: grep "healthz|CAIRN_MCP_API_KEY|api_key|bearer|Bearer" over src/cairn/
              hits only outbound embed-client auth (src/cairn/graph/embed_ladder.py:485
              "api_key = _config_value("CAIRN_EMBED_API_KEY")", line 487 "headers["Authorization"] = f"Bearer {api_key}"")
              and memory privacy scrubbing (src/cairn/memory/privacy.py:27 "r"Bearer\s+[A-Za-z0-9._\-+/=]{20,}"").
              serve CLI has no --api-key option (src/cairn/cli/serve.py:serve:22 options lines 13-20).
              SDK hooks ready: FastMCP takes "token_verifier: TokenVerifier | None = None"
              (.venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:__init__:148, param at line 155); with a token_verifier the
              streamable app wraps the endpoint — server.py:1015-1018 "Route(self.settings.streamable_http_path,
              endpoint=RequireAuthMiddleware(streamable_http_app, required_scopes, resource_metadata_url))" —
              and installs "AuthenticationMiddleware, backend=BearerAuthBackend(self._token_verifier)"
              (server.py:983-989). SDK refuses misconfig: server.py:225 "raise ValueError("Must specify either
              auth_server_provider or token_verifier when auth is enabled")".
  status:     TODO
  verify:     grep -rn "CAIRN_MCP_API_KEY" src/cairn/   (no matches) && grep -n "api-key\|api_key" src/cairn/cli/serve.py   (no matches)
  gap:         Entire bearer-key gate: env/flag sourcing with precedence, refuse-to-start check,
              a TokenVerifier implementation, and per-endpoint scope of the gate vs the health probe.

item FR-003: "Request without valid credential rejected with auth error before any store access"
  evidence:   No auth middleware runs in the cairn serving path: FastMCP constructed with neither auth nor
              token_verifier (src/cairn/mcp_server/_server_core.py:68 — only name/lifespan/log_level kwargs),
              so both SDK branches take the no-auth route ("else: # Auth is disabled, no wrapper needed",
              .venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:1021-1028). Store access happens inside tool calls via
              src/cairn/mcp_server/_server_core.py:_conn:139. SDK gate order when a verifier IS set:
              RequireAuthMiddleware is the Route endpoint (.venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:1015-1018) so it runs
              before the transport; Host/Origin validation runs inside the transport handler at
              .venv/lib/python3.12/site-packages/mcp/server/streamable_http.py:409 "error_response = await self._security.validate_request(request,
              is_post=is_post)".
  status:     TODO
  verify:     grep -n "token_verifier\|AuthenticationMiddleware\|RequireAuthMiddleware" src/cairn/mcp_server/*.py   (no matches)
  gap:        No credential check exists anywhere in the serving path; ordering must come from the
              SDK wrapper once FR-002 lands (middleware before transport/store access).

item FR-004: "Bind loopback by default; keyless only on loopback; non-loopback requires key +
             SDK transport-security (Host-header) protections"
  evidence:   Loopback default present: SDK default "host: str = "127.0.0.1"" (.venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:162);
              daemon defaults "host", default="127.0.0.1" (src/cairn/cli/serve.py:serve_start:92 line 91;
              src/cairn/cli/serve.py:serve_status:193 line 192); src/cairn/mcp_server/lifecycle.py:15
              "DEFAULT_HOST = "127.0.0.1"".
              SDK auto-applies DNS rebinding ONLY for loopback binds when transport_security is None —
              .venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:__init__:148 lines 179-185: "if transport_security is None and host in
              ("127.0.0.1", "localhost", "::1"): transport_security = TransportSecuritySettings(
              enable_dns_rebinding_protection=True, allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"], ...)".
              With settings=None the middleware disables protection for backwards compat
              (.venv/lib/python3.12/site-packages/mcp/server/transport_security.py:TransportSecurityMiddleware:37 lines 40-43). Settings shape:
              .venv/lib/python3.12/site-packages/mcp/server/transport_security.py:TransportSecuritySettings:12 — enable_dns_rebinding_protection
              (default True, line 19), allowed_hosts (line 24), allowed_origins (line 30).
              No key requirement exists anywhere (FR-002 evidence).
  status:     PARTIAL
  verify:     grep -n "127.0.0.1" src/cairn/cli/serve.py src/cairn/mcp_server/lifecycle.py && grep -rn "transport_security\|TransportSecuritySettings" src/cairn/   (no cairn-side matches — protection only via SDK defaults)
  gap:        Loopback default exists; the keyless-only-on-loopback rule and the non-loopback
              key requirement do not (depends on FR-002); cairn never passes transport_security
              explicitly, so a future non-loopback HTTP bind gets NO Host-header protection unless
              the server constructs TransportSecuritySettings itself.

item FR-005: "/healthz machine-readable payload (store reachable, read-only mode, degradation
             count), no auth required"
  evidence:   No /healthz: grep "healthz" over src/cairn/ has no matches.
              Machine-readable health data exists as an MCP resource, not HTTP:
              src/cairn/mcp_server/_server_core.py:_health_block:310 returns a dict with
              "degradations", "pending_sync", "last_build_age", "error_rate_24h", "tool_calls_24h",
              "tool_errors_24h" (lines 424-431), crash-proof ("every probe is guarded so a missing table ...
              degrades to a null/0 field rather than raising", lines 313-315);
              src/cairn/mcp_server/_server_core.py:status_resource:440 registered "@mcp.resource("cairn://status")" (line 439).
              HTTP precedent: dashboard HTML health route — src/cairn/dashboard/routes/core.py:health:78
              (get_read_only_db + get_health + render "health.html"), registered at core.py:97-98
              "if section == "health": routes.append(Route("/health", health, name="health"))".
              SDK support for an unauthenticated endpoint: .venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:custom_route:708 —
              "Routes using this decorator will not require authorization. It is intended for uses that are
              either a part of authorization flows or intended to be public such as health check endpoints."
              (lines 723-725, example at 735-737); custom routes are appended to the streamable app at
              .venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:1042 "routes.extend(self._custom_starlette_routes)".
              Read-only-mode flag readable for the payload: src/cairn/mcp_server/_server_core.py:_read_only_mode:80.
  status:     PARTIAL
  verify:     grep -rn "healthz" src/cairn/   (no matches) && grep -n "cairn://status" src/cairn/mcp_server/_server_core.py   (line 439)
  gap:        No HTTP /healthz route on the serving app; payload assembly (store reachable +
              read_only + degradation count) must be composed from _health_block:310/_read_only_mode:80.

item FR-006: "--stateless runs the Streamable HTTP session manager in stateless mode"
  evidence:   SDK wiring complete: Settings field "stateless_http: bool" (.venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:107),
              default "stateless_http: bool = False" (line 169), passed to the session manager at
              .venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:streamable_http_app:953 — "stateless=self.settings.stateless_http"
              (line 964) into ".venv/lib/python3.12/site-packages/mcp/server/streamable_http_manager.py:StreamableHTTPSessionManager:35"
              (constructor param "stateless", doc line 24 "creates a completely fresh transport for each
              request with no session tracking").
              cairn: no --stateless flag (src/cairn/cli/serve.py:serve:22 options are --db/--port/--read-only only);
              _server_core constructs FastMCP without stateless_http (src/cairn/mcp_server/_server_core.py:68).
  status:     TODO
  verify:     grep -rn "stateless" src/cairn/   (no matches) && grep -n "stateless_http" .venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py   (lines 107, 169, 197, 964)
  gap:        No --stateless flag and no pass-through to the FastMCP constructor.

item FR-007: "HTTP server defaults to read-only (consistent with the SSE daemon tri-state),
             overridable by the existing explicit read-only flag"
  evidence:   Tri-state exists and SSE defaults read-only: src/cairn/cli/serve.py:_serve_foreground:64 —
              docstring "read_only tri-state: None => auto (read-only under SSE/launchd, read-write under
              stdio), True/False => explicit override" (lines 67-68); implementation lines 80-82:
              "if read_only is None: read_only = bool(port)" then "os.environ["CAIRN_READ_ONLY"] = "1" if
              read_only else "0"". Both flags exposed: "--read-only/--read-write" default None on
              src/cairn/cli/serve.py:serve:22 (lines 15-20) and src/cairn/cli/serve.py:serve_run:59 (lines 45-58).
              launchd daemon always read-only: src/cairn/mcp_server/lifecycle.py:92 "env["CAIRN_READ_ONLY"] = "1"".
              Enforcement chain: src/cairn/mcp_server/_server_core.py:_read_only_mode:80 -> _conn opens
              "get_db(db_path, read_only=_read_only_mode())" (line 152/175); regression-tested.
              The HTTP path itself does not exist (FR-001), so its default is unproven.
  status:     PARTIAL
  verify:     .venv/bin/python -m pytest tests/test_core_smoke.py::test_read_only_mode_blocks_db_writes -q   (passed; asserts CAIRN_READ_ONLY -> _read_only_mode() is True and mode=ro writes raise)
  gap:        Only the default-for-HTTP is missing: "read_only = bool(port)" keys off --port, so an
              HTTP selection mechanism (FR-001) must be included in the read-only default condition.

item FR-008: "Dockerfile (slim base, non-root, CAIRN_HOME=/data volume, HTTP entrypoint) and
             deployment docs (local, networked, container)"
  evidence:   No Dockerfile: `ls Dockerfile` -> absent (repo root listing has no docker* entry).
              No deployment doc: docs/ holds README.md, architecture.md, benchmarks.md, cli-reference.md,
              configuration.md, indexing.md, knowledge-and-memory.md, mcp-tools.md, proposal-level-up.md,
              release-checklist.md, retrieval.md, review-checklist.md, scip.md (ls docs/) — none covers serving
              deployment; docs/cli-reference.md:128 documents serve only as
              "`cairn serve run|start|stop|status|restart` | MCP server (stdio foreground / SSE daemon `:9876`)...".
              Dockerfile inputs verified present: entry point pyproject.toml:176 "cairn = "cairn.cli:main""
              under [project.scripts] (line 175); requires-python = ">=3.10" (pyproject.toml:11);
              deps pyproject.toml:28-65 — tree-sitter grammar pins, click>=8.0 (44),
              "mcp>=0.9.0,<2.0.0" with comment "SSE/streamable-http + uvicorn/starlette are core deps in
              mcp>=0.9" (46), jinja2>=3.0 (47), pydantic>=2.0 (48), sqlite-vec>=0.1.0 (51), numpy>=1.24 (61),
              rich>=13.0 (64), questionary>=2.0 (65). [watch] extra and heavy extras (semantic) are optional —
              see pyproject [project.optional-dependencies] (present below line 65, not re-counted here).
  status:     TODO
  verify:     ls Dockerfile 2>/dev/null   (no output) && ls docs/ && grep -n "project.scripts" -A2 pyproject.toml
  gap:        Both deliverables absent. Entry point + runtime deps for a slim image are confirmed in
              pyproject; unknown — verify whether uvicorn needs explicit declaration once the image is
              built (SDK imports it lazily inside run_*_async, .venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:767/782).

item NFR-001: "Security — auth gate and Host-header protections enforced before dispatch; secrets
             never in argv-rendered logs"
  evidence:   No HTTP dispatch exists to gate (FR-001 TODO). SDK provides both halves once wired:
              per-request Host/Origin/Content-Type validation inside the transport
              (.venv/lib/python3.12/site-packages/mcp/server/streamable_http.py:409 -> .venv/lib/python3.12/site-packages/mcp/server/transport_security.py:validate_request:102,
              with "Missing Host header in request" logged at transport_security.py:48) and the auth wrapper
              as the Route endpoint (.venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:1015-1018, FR-003 evidence).
              Existing secret-handling precedent: memory ingestion scrubs bearer/API-key tokens
              (src/cairn/memory/privacy.py:27); boot error path prints the env resolution chain, not values
              (src/cairn/mcp_server/server.py:238-243 "Env resolution chain: {render_env_resolution_chain()}").
              No argv rendering exists in the serve path today (no flags beyond --db/--port/--read-only).
  status:     TODO
  verify:     grep -rn "transport_security\|BearerAuthBackend" src/cairn/   (no matches) && .venv/bin/python -m pytest tests/test_server_robustness.py -q   (14 passed — existing sanitization tests green)
  gap:        Enforcement does not exist in cairn; ordering (auth before store dispatch) rides on the
              SDK wrapper; the future --api-key flag must not be rendered into lifecycle logs — logging
              discipline for it is unbuilt (unknown — verify at implementation time).

item NFR-002: "Privacy — HTTP surface exposes only the same store data local transports expose;
             no telemetry/store metadata leaks through the health probe beyond its payload"
  evidence:   The HTTP surface does not exist (FR-001 TODO), so the invariant is not violated and not
              yet implemented. Local transports' data = the tools/resources on the shared singleton
              (FR-001 evidence: six tools_*.py modules + src/cairn/mcp_server/_server_core.py:status_resource:440).
              Health payload today is MCP-resource-only (FR-005 evidence, _health_block:310 fields);
              a bounded /healthz payload would be new.
  status:     TODO
  verify:     grep -rn "healthz" src/cairn/   (no matches) && grep -rn "streamable" src/cairn/   (no matches)
  gap:        Everything rides on FR-001/FR-005: constrain the /healthz handler to the documented fields.

item NFR-003: "Performance — no per-call store-access overhead beyond session handling (no
             per-request reopen regression vs SSE)"
  evidence:   Per-request reopen is already pooled away on the SSE path and any HTTP path reuses the
              same helpers: src/cairn/mcp_server/_server_core.py:_conn:139 — "Pooled per (thread, db path):
              the returned object's close() is a no-op release" (lines 146-148), cache keyed by
              (st.st_dev, st.st_ino) with dead-inode invalidation (lines 158-173); pooling gate
              "CAIRN_CONN_POOL" default on (lines 131-136); wrapper
              src/cairn/mcp_server/_server_core.py:109-113 (__getattr__ delegates). Tools call _conn(),
              not get_db(), directly. HTTP-specific numbers: none measured.
  status:     PARTIAL
  verify:     grep -n "CAIRN_CONN_POOL" src/cairn/mcp_server/_server_core.py   (lines 131-136) && grep -rn "get_db(" src/cairn/mcp_server/_server_core.py   (only inside _conn/_rw_conn)
  gap:        Machinery exists and is shared; no HTTP transport exists to measure a regression against
              (unknown — verify with a bench once FR-001 lands).

item NFR-004: "Reliability — store unavailable: /healthz reports unhealthy; tool calls answer with a
             clean error, not a crash"
  evidence:   Crash-proof health probes exist (MCP-resource level):
              src/cairn/mcp_server/_server_core.py:_health_block:310 "Read-only + crash-proof: every probe is
              guarded so a missing table or unresolvable backend degrades to a null/0 field rather than
              raising" (lines 313-315; per-probe try/except at 338-344, 356-387, 389-393, 396-403, 409-421);
              status_resource catches store failure and returns a string, not an exception — line 457-458
              "except Exception as e: return f"cairn status: unavailable ({e})"".
              Boot-time missing store exits cleanly: src/cairn/mcp_server/server.py:220-231 ("database is
              missing the 'symbols' table" -> sys.exit(1)), regression-tested in TestStoreExistenceCheck
              (tests/test_server_robustness.py:27, expects exit code 1 at line 297).
              Liveness probe distinguishing live/wedged/dead:
              src/cairn/mcp_server/lifecycle.py:sse_responds:412 ("A bare TCP accept is a false-positive",
              lines 416-418), tested.
              No /healthz exists to report unhealthy on; mid-run store-degradation behavior of tool calls
              over a not-yet-existing HTTP transport: unknown — verify.
  status:     PARTIAL
  verify:     .venv/bin/python -m pytest tests/test_core_smoke.py::test_sse_responds_detects_live_vs_dead -q   (passed) && .venv/bin/python -m pytest tests/test_server_robustness.py -q   (14 passed)
  gap:        /healthz unhealthy reporting does not exist (FR-005); tool-call clean-error behavior over
              HTTP unproven until the transport exists.

item NFR-005: "Observability — lifecycle milestones logged (bind, auth failures at WARN, shutdown)
             at appropriate levels, no request-level noise"
  evidence:   Existing serving logging: src/cairn/mcp_server/server.py:200 "configure_logging()" (CAIRN_LOG_LEVEL,
              stderr handler on the `cairn` logger only, lines 193-199); readiness milestone printed to stdout
              for SSE at server.py:363-367 ("MCP server listening on http://.../sse"); shutdown: watcher
              stop() in finally (server.py:371-376). Singleton pins FastMCP log level:
              src/cairn/mcp_server/_server_core.py:68 ("log_level="WARNING"" — "so constructing this singleton
              ... doesn't reconfigure the root logger", lines 55-57). SDK: uvicorn configured with
              "log_level=self.settings.log_level.lower()" (.venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:790, 786-789);
              Host-header failures already WARN ("Missing Host header in request",
              .venv/lib/python3.12/site-packages/mcp/server/transport_security.py:48). Auth-failure WARN: n/a — no auth (FR-002 TODO).
              No HTTP bind/shutdown milestone logs exist (no HTTP path); request-level noise floor of the
              streamable path: unknown — verify at implementation.
  status:     PARTIAL
  verify:     grep -n "configure_logging\|_timestamped_print" src/cairn/mcp_server/server.py | head -5 && grep -n "log_level" src/cairn/mcp_server/_server_core.py
  gap:        Bind/shutdown milestones for the HTTP transport and auth-failure WARNs are new work;
              existing logging scaffolding (levels, stderr discipline, stdout JSON-RPC rule) carries over.

item NFR-006: "Accessibility — not applicable: no UI surface is touched"
  evidence:   The serving package renders no UI: src/cairn/mcp_server/ contains server.py, _server_core.py,
              lifecycle.py, tools_*.py, metric_buffering.py, embed_buffering.py, structured.py (ls) — no
              templates/static/HTML. The only UI is the separate dashboard command/Starlette app
              (src/cairn/cli/dashboard.py -> "from ..dashboard.app import create_app" at dashboard.py:57,
              uvicorn.run at dashboard.py:61), untouched by MCP transport selection.
  status:     DONE
  verify:     ls src/cairn/mcp_server/   (no UI assets) && grep -rn "mcp_server" src/cairn/dashboard/   (no matches)
  gap:        none — spec item itself declares not-applicable; evidence confirms the serving surface
              touches no UI.
```

## Supporting evidence

Load-bearing machinery the tech-spec's code guide will cite — all pasted verbatim this session.

### SDK streamable-http surface (mcp 1.29.0, .venv/lib/python3.12/site-packages/)

- `run()` transport literal and dispatch — `.venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:run:282`:
  ```
  282	    def run(
  283	        self,
  284	        transport: Literal["stdio", "sse", "streamable-http"] = "stdio",
  285	        mount_path: str | None = None,
  286	    ) -> None:
  ...
  297	        match transport:
  298	            case "stdio":
  299	                anyio.run(self.run_stdio_async)
  300	            case "sse":  # pragma: no cover
  301	                anyio.run(lambda: self.run_sse_async(mount_path))
  302	            case "streamable-http":  # pragma: no cover
  303	                anyio.run(self.run_streamable_http_async)
  ```
- `run_streamable_http_async` — `.venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:run_streamable_http_async:780`: builds
  `starlette_app = self.streamable_http_app()` (784), `uvicorn.Config(starlette_app, host=self.settings.host,
  port=self.settings.port, log_level=self.settings.log_level.lower())` (786-790), `await server.serve()` (793).
  SSE twin: `.venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:run_sse_async:765` with `self.sse_app(mount_path)` (769).
- Settings HTTP fields — `mcp/server/fastmcp/server.py` class Settings: `host: str` (98), `port: int` (99),
  `streamable_http_path: str` (103, default "/mcp" at 167), `json_response: bool` (106, default False at 168),
  `stateless_http: bool` (107, default False at 169), `transport_security: TransportSecuritySettings | None` (130).
- DNS-rebinding auto-application — `.venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:__init__:148`, lines 179-185:
  ```
  179	        # Auto-enable DNS rebinding protection for localhost (IPv4 and IPv6)
  180	        if transport_security is None and host in ("127.0.0.1", "localhost", "::1"):
  181	            transport_security = TransportSecuritySettings(
  182	                enable_dns_rebinding_protection=True,
  183	                allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
  184	                allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
  185	            )
  ```
  Non-loopback host + transport_security=None => no protection object; middleware then runs with
  protection explicitly off (`.venv/lib/python3.12/site-packages/mcp/server/transport_security.py:TransportSecurityMiddleware:37` lines 40-43:
  "self.settings = settings or TransportSecuritySettings(enable_dns_rebinding_protection=False)").
- `streamable_http_app()` — `.venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:streamable_http_app:953`: lazily creates
  `StreamableHTTPSessionManager(app=self._mcp_server, event_store=self._event_store, retry_interval=self._retry_interval,
  json_response=self.settings.json_response, stateless=self.settings.stateless_http,
  security_settings=self.settings.transport_security, max_request_body_size=self.settings.max_request_body_size)`
  (959-967; manager class at `.venv/lib/python3.12/site-packages/mcp/server/streamable_http_manager.py:StreamableHTTPSessionManager:35`), then
  `StreamableHTTPASGIApp(self._session_manager)` (970). Auth wiring: `AuthenticationMiddleware` +
  `BearerAuthBackend(self._token_verifier)` + `AuthContextMiddleware` middleware list (983-989) when both
  `settings.auth` and `_token_verifier` are set; the endpoint branch (1006-1028) is exactly:
  ```
  1006	        if self._token_verifier:  # pragma: no cover
  ...
  1015	            routes.append(
  1016	                Route(
  1017	                    self.settings.streamable_http_path,
  1018	                    endpoint=RequireAuthMiddleware(streamable_http_app, required_scopes, resource_metadata_url),
  1019	                )
  1020	            )
  1021	        else:
  1022	            # Auth is disabled, no wrapper needed
  1023	            routes.append(
  1024	                Route(
  1025	                    self.settings.streamable_http_path,
  1026	                    endpoint=streamable_http_app,
  1027	                )
  1028	            )
  ```
  Custom routes appended last (lowest precedence) at line 1042 `routes.extend(self._custom_starlette_routes)`,
  and the app runs under `lifespan=lambda app: self.session_manager.run()` (1048) — the session manager's
  lifespan is mandatory for the streamable app (property `session_manager` raises
  "calling streamable_http_app()." if accessed before, lines 273-279).
  Per-request security validation runs inside the transport:
  `.venv/lib/python3.12/site-packages/mcp/server/streamable_http.py:409` — `error_response = await self._security.validate_request(request, is_post=is_post)`.
- Unauthenticated health endpoint support — `.venv/lib/python3.12/site-packages/mcp/server/fastmcp/server.py:custom_route:708`, docstring
  lines 723-725: "Routes using this decorator will not require authorization. It is intended for uses that
  are either a part of authorization flows or intended to be public such as health check endpoints."
  (example `@server.custom_route("/health", methods=["GET"])` at 735-737). Note: custom_route appends to
  `self._custom_starlette_routes`, consumed by BOTH `sse_app` (line 948) and `streamable_http_app` (1042).

### cairn transport dispatch (src/cairn/mcp_server/server.py)

- `run(transport: str = "stdio", port: int | None = None)` — `src/cairn/mcp_server/server.py:run:178`.
  Shared boot path for BOTH transports, in order: session-id env (191), `configure_logging()` (200),
  `verify_tool_count()` (206), stdio-only parent watchdog (212-213), DB boot guard sys.exit(1) (220-244),
  `warm_models_in_background()` (262), read-only flag read (270:
  `read_only = os.environ.get("CAIRN_READ_ONLY", "").lower() in ("1", "true", "yes")`),
  boot catch-up + memory decay skipped when read-only (276-328), live watcher when not read-only (341-346).
- SSE branch (348-370): `_install_stray_sweeper(db_path, interval_s=60.0)` (356) — sweeper only under SSE
  ("a stdio server is itself a potential stray and must not kill its siblings", 354-355); host/port read
  from `mcp.settings`, not kwargs ("FastMCP.run() in mcp>=1.0 reads host/port from mcp.settings, not
  kwargs", line 358; `mcp.settings.port = port` at 359-360); readiness print to stdout (363-367);
  `mcp.run(transport="sse")` (368). stdio branch: bare `mcp.run()` (370). Watcher stop in finally (375-376).
- Stray sweeper: `src/cairn/mcp_server/server.py:_install_stray_sweeper:399` periodic loop calling
  `src/cairn/mcp_server/server.py:_run_stray_sweep:379` -> `lc.sweep_strays(db_path, log=True)` (392).

### FastMCP singleton + read-only machinery (src/cairn/mcp_server/_server_core.py)

- Singleton: `mcp = FastMCP("cairn", lifespan=app_lifespan, log_level="WARNING")` (line 68) inside a
  warnings filter (65-67); comment lines 53-54: "The single FastMCP instance every tools_*.py module
  decorates." Lifespan yields None — tools resolve config via helpers, not lifespan context (40-50).
- `src/cairn/mcp_server/_server_core.py:_read_only_mode:80` — `CAIRN_READ_ONLY` in ("1","true","yes") (86).
- `src/cairn/mcp_server/_server_core.py:_conn:139` — read_only passthrough to `get_db` (152/175), thread/path
  pooling with inode-swap invalidation (154-189). `src/cairn/mcp_server/_server_core.py:_rw_conn:192` —
  explicit read-write escape hatch for write-purpose tools (199).
- Health data: `src/cairn/mcp_server/_server_core.py:_health_block:310` (crash-proof dict, fields at
  424-431); `src/cairn/mcp_server/_server_core.py:status_resource:440` at `cairn://status` (439).

### CLI tri-state + daemon lifecycle (src/cairn/cli/serve.py, lifecycle.py)

- `src/cairn/cli/serve.py:_serve_foreground:64`: sets CAIRN_DB/CAIRN_KNOWLEDGE/CAIRN_WORKSPACE (75-77),
  tri-state default `read_only = bool(port)` (80-81), exports `CAIRN_READ_ONLY` (82),
  `run(transport="sse" if port else "stdio", port=port)` (85).
- Flags: `serve` group (22) and `serve run` (59) both take `--db/--port/--read-only/--read-write`;
  daemon commands take `--port` (default `lc.DEFAULT_PORT`)/`--host` (default "127.0.0.1"):
  serve_start (92), serve_status (193), serve_restart (238).
- `src/cairn/mcp_server/lifecycle.py:sse_url:408` — `return f"http://{host}:{port}/sse"` (409);
  `src/cairn/mcp_server/lifecycle.py:sse_responds:412` — raw-socket HTTP probe of `/` reading the status
  line via MSG_PEEK (426-432), "Returns True only if the server both accepted AND emitted a response byte"
  (420-421). `DEFAULT_PORT = 9876` (14), `DEFAULT_HOST = "127.0.0.1"` (15); plist env pins
  `env["CAIRN_READ_ONLY"] = "1"` (92).

### CAIRN_READ_ONLY readers (grep inventory, this session)

- src/cairn/cli/serve.py:82 (writer: sets "1"/"0")
- src/cairn/mcp_server/lifecycle.py:92 (writer: launchd plist env "1")
- src/cairn/mcp_server/_server_core.py:86 (`_read_only_mode`)
- src/cairn/mcp_server/server.py:270 (run() read_only)
- src/cairn/mcp_server/metric_buffering.py:242 (inside `src/cairn/mcp_server/metric_buffering.py:_log_metric:221` — skip tool-metrics writes)
- src/cairn/telemetry/sink.py:120 (inside `src/cairn/telemetry/sink.py:is_read_only:110` — skip events writes)
- src/cairn/telemetry/events.py:143, src/cairn/telemetry/otel.py:17 (doc/comment references)
- src/cairn/graph/watcher.py:210 (inside `src/cairn/graph/watcher.py:_read_only_env:208` — watcher never writes)
- src/cairn/cli/system/doctor.py:738, src/cairn/cli/system/report.py:189 (diagnostics echo)

### Health-route precedent (dashboard)

- `src/cairn/dashboard/routes/core.py:health:78` — read-only conn + `get_health(conn, selected_db)` + HTML
  render; registered only for section=="health": core.py:97-98
  `routes.append(Route("/health", health, name="health"))`; wired in create_app at
  src/cairn/dashboard/app.py:360 `core.register(routes, context, section="health")`.

### Packaging facts for the Dockerfile (pyproject.toml)

- `[project.scripts]` line 175-176: `cairn = "cairn.cli:main"`; name `cairn-intel`, version 0.21.2 (7),
  requires-python ">=3.10" (11). Core deps (28-65): 13 pinned tree-sitter grammars (29-43), click>=8.0 (44),
  pyyaml>=6.0 (45), `mcp>=0.9.0,<2.0.0` (46, comment: "SSE/streamable-http + uvicorn/starlette are core deps
  in mcp>=0.9; the legacy [sse] extra was removed upstream"), jinja2>=3.0 (47), pydantic>=2.0 (48),
  pathspec>=0.12 (49), packaging>=21.0 (50), sqlite-vec>=0.1.0 (51), numpy>=1.24 (61), rich>=13.0 (64),
  questionary>=2.0 (65).
- Dockerfile: absent (repo root). Deployment docs: absent (docs/ listing under FR-008).

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
