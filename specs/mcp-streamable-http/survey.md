# Survey: mcp-streamable-http

**Created**: 2026-10-11 | **Baseline**: 0.22.0 @ 31a66df
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item FR-001: "MCP tools over Streamable HTTP at a single MCP path; no stdio/SSE in the serve path"
  evidence:   run() is HTTP-only with no transport parameter: src/cairn/mcp_server/server.py:run:222 signature `def run(port: int | None = None, host: str | None = None, api_key: str | None = None, stateless: bool = False)`; the sole app builder is the SDK's — server.py:216 `app: Any = mcp.streamable_http_app()` inside src/cairn/mcp_server/server.py:_build_http_app:200; readiness names the single path, server.py:351-353 `f"cairn: MCP server listening on " f"http://{mcp.settings.host}:{mcp.settings.port}/mcp"`; `grep -n "transport" src/cairn/mcp_server/server.py` → only the TransportSecuritySettings import (:24), a section comment (:111), `def _transport_security_for` (:158), and its call (:212) — no stdio/sse branches. The CLI offers no transport choice: `grep -n 'Choice(\["stdio"' src/cairn/cli/serve.py` → no matches; --port help is "HTTP daemon port (default 9876)." (serve.py:14, :60).
  status:     DONE
  verify:     grep -n "transport" src/cairn/mcp_server/server.py (no transport branches; only _transport_security_for at :158/:212); CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_http_transport.py -q (32 passed incl. tests/test_http_transport.py:168 test_http_tool_catalog_pins_26_tools)
  gap:        none (the stdio/SSE branches and the stdio default are gone)

item FR-002: "Daemon read-write by default (watcher, catch-up, memory writes); --read-only disables"
  evidence:   Foreground serve defaults read-write: src/cairn/cli/serve.py:_serve_foreground:114 — serve.py:123 `os.environ["CAIRN_READ_ONLY"] = "1" if read_only else "0"` with `--read-only/--read-write` defaulting None (serve.py:16-18, :62-64); no `bool(port)`-style auto-read-only remains. The managed daemon plist is read-write by default: src/cairn/mcp_server/lifecycle.py:render_plist:96 — lifecycle.py:102 `read_only: bool = False`; args lifecycle.py:120-123 `args = [bin_, "serve", "run", "--port", str(port)]` / `if read_only:` / `args.append("--read-only")` / `env["CAIRN_READ_ONLY"] = "1"`. Read-only gates live in src/cairn/mcp_server/server.py:run:222: read_only from env :281, catch-up and decay skipped :284-288, watcher started only `if not read_only` :337.
  status:     DONE
  verify:     grep -n "read_only" src/cairn/cli/serve.py → `123:    os.environ["CAIRN_READ_ONLY"] = "1" if read_only else "0"`; CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_server_robustness.py -q → 16 passed (test_default_boot_serves_read_write_across_clients :607, test_read_only_opt_out_blocks_write_tools :637)
  gap:        none

item FR-003: "Refuse stdio/SSE-specific serve options non-zero, naming the HTTP replacement"
  evidence:   --transport is a hidden tombstone refused on both serve and serve run: src/cairn/cli/serve.py:serve:47 — serve.py:49-50 `if transport is not None:` / `_refuse_removed_transport(port, host)`; same gate on src/cairn/cli/serve.py:serve_run:96 (:98-99); src/cairn/cli/serve.py:_refuse_removed_transport:106 — serve.py:109 `click.echo(f"cairn: error: --transport is removed; cairn serves streamable HTTP at {url}.")`, connect hint :110, `sys.exit(1)` :111. No stdio/SSE-specific options remain first-class: `grep -n 'Choice(\["stdio"' src/cairn/cli/serve.py` → no matches; --port help is "HTTP daemon port (default 9876)." (serve.py:14, :60).
  status:     DONE
  verify:     grep -n 'Choice(\["stdio"' src/cairn/cli/serve.py || true → no matches; CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_http_transport.py -q → 32 passed (test_serve_refuses_removed_transport_values :496 pins non-zero exit + "http://" + "cairn connect" for --transport stdio|sse|http on serve and serve run)
  gap:        none

item FR-004: "cairn connect starts exactly one detached daemon (race-guarded) or reuses a healthy one; emits URL"
  evidence:   The command exists: src/cairn/cli/connect.py:connect:57 — healthz fast path connect.py:59-61 (`if lc.healthz_responds(port, host):` → `_report(..., started=False)`); otherwise a bounded exclusive flock via src/cairn/cli/connect.py:_acquire_start_lock:25 (flock LOCK_EX|LOCK_NB :33, 30s deadline :15), a re-probe under the lock :64-66, stray sweep :69, then `pid, url, started = lc.ensure_daemon_running(port, host)` :71 — the single-start core src/cairn/mcp_server/lifecycle.py:ensure_daemon_running:504, itself under lifecycle's own start lock src/cairn/mcp_server/lifecycle.py:_start_lock:417. Started-vs-reused report: src/cairn/cli/connect.py:_report:45 — "cairn daemon started (pid {pid}) at {url}" :49 / "cairn daemon already running at {url}" :51.
  status:     DONE
  verify:     grep -rn "def connect" src/cairn/cli/ → `src/cairn/cli/connect.py:57:def connect(port, host):`; CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_connect.py -q → 4 passed (cold-starts-one :92, warm-reuses :104, racing-converges :116, killed-daemon-replaced :162)
  gap:        none

item FR-005: "Loopback:9876 default with existing bind policy; init generates+persists CAIRN_MCP_API_KEY; configs embed it; unkeyed loopback OK"
  evidence:   Bind policy unchanged and passing: src/cairn/mcp_server/lifecycle.py:16 `DEFAULT_PORT = 9876`, :17 `DEFAULT_HOST = "127.0.0.1"`; HTTP without --port lands on it (serve.py:124 `port = port if port is not None else lc.DEFAULT_PORT`); src/cairn/mcp_server/server.py:_transport_security_for:158 (DNS-rebinding on loopback, off on wildcard), src/cairn/mcp_server/server.py:_require_http_api_key:177 — :179 `if api_key or host in _HTTP_LOOPBACK_HOSTS: return` (key mandatory only off-loopback; unkeyed loopback permitted since the middleware wraps only `if api_key:` server.py:217-218). Init now generates and persists the key: src/cairn/cli/core.py:init:54 — core.py:66 `key_file = CAIRN_HOME / "mcp-api-key"`, :70 `fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)`, :75 `fh.write(secrets.token_urlsafe(32))`; reader src/cairn/mcp_server/auth.py:persisted_api_key:15. Configs embed it: src/cairn/agent_install/_common.py:_url_entry:87 — :89-92 `entry: dict = {"type": "http", url_key: daemon_mcp_url()}` + `entry["headers"] = headers`; `cairn config --mcp-config` prints the same shape (src/cairn/cli/core.py:config:196 — core.py:225-228 `entry: dict = {"type": "http", "url": mcp_url()}` / `entry["headers"] = {"Authorization": f"Bearer {key}"}`). resolve_api_key still reads flag/env only (src/cairn/mcp_server/auth.py:resolve_api_key:8), so the serve path never auto-loads the file.
  status:     DONE
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_http_transport.py tests/test_auth.py -q → 51 passed (incl. test_unkeyed_non_loopback_refuses_to_start_before_binding :588); CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_cli_init_rail.py tests/test_auth.py -q → 25 passed (test_fresh_init_persists_user_only_api_key_file tests/test_cli_init_rail.py:107, test_resolve_api_key_ignores_persisted_key_file tests/test_auth.py:52); grep -rn "CAIRN_MCP_API_KEY" src/cairn/cli/ → help-text matches only (serve.py:37, :87)
  gap:        none

item FR-006: "Every supported client registered in URL + key form; verification probes healthz, not a process"
  evidence:   Generators are URL+key: src/cairn/agent_install/_common.py:mcp_config_json:103 — :105 `return {"mcpServers": {"cairn": _url_entry()}}`; _url_entry (:87) builds `{"type": "http", url_key: daemon_mcp_url()}` plus the Authorization header (:89-92) from src/cairn/agent_install/_common.py:auth_headers:79. The old spawn-probe and TCP-check symbols are deleted: `grep -n "def verify_registration\|def sse_daemon_reachable\|def mcp_config_json_desktop" src/cairn/agent_install/__init__.py src/cairn/agent_install/clients/claude_desktop.py` → no matches; sse_url/sse_responds likewise gone from lifecycle (`rg -n "sse_url|sse_responds" src/cairn/` → no matches). Verification now probes healthz: src/cairn/agent_install/__init__.py:_verify_results:224 — :245 `if lifecycle.healthz_responds(port=port, host=host, timeout=_PROBE_TIMEOUT_S):` → pass/fail verdicts :246-251, URL derived from each written registration (_registration_entry :178, _registration_url :205, _url_host_port :214).
  status:     DONE
  verify:     grep -n "def verify_registration\|def sse_daemon_reachable" src/cairn/agent_install/__init__.py || true → no matches; CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_clients.py -q → 13 passed (test_shared_mcp_servers_generator_is_url_plus_key :69, test_install_all_writes_url_key_for_every_supported_client :136)
  gap:        none

item FR-007: "Claude Desktop reported unsupported, no config written (no URL registration support)"
  evidence:   The desktop installer is now an unsupported-report: src/cairn/agent_install/clients/claude_desktop.py:install_claude_desktop:11 — :15-18 note "Claude Desktop supports no remote/URL MCP registration — unsupported, skipped (no config written)."; it writes no configuration (the result carries the note only) and mcp_config_json_desktop is deleted (`grep -n "def mcp_config_json_desktop" src/cairn/agent_install/clients/claude_desktop.py` → no matches); uninstall still strips legacy entries (:22-24). Whether current Claude Desktop builds accept remote/URL MCP registrations: unknown — verify (client capability, outside this repo).
  status:     DONE
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_clients.py -q → 13 passed (test_claude_desktop_is_reported_unsupported_and_writes_no_config :186, test_claude_desktop_uninstall_still_strips_legacy_entries :205)
  gap:        none

item FR-008: "Stray sweeper evicts orphaned serve processes holding the DB, extended to the HTTP daemon"
  evidence:   The sweeper now installs on the sole serving path: src/cairn/mcp_server/server.py:run:222 — :344 `_install_stray_sweeper(db_path)` inside the pre-serving try block (no transport branches remain); src/cairn/mcp_server/server.py:_install_stray_sweeper:383 runs the periodic pass. Sweep machinery is transport-agnostic by cmdline: src/cairn/mcp_server/lifecycle.py:sweep_strays:335 → src/cairn/mcp_server/lifecycle.py:find_strays:250 (pgrep candidates + lsof DB-holder verification :272-281, pidfile pid protected only after cmdline re-verification :256-260) → src/cairn/mcp_server/lifecycle.py:terminate_pid:303 ("SIGTERM, wait, then SIGKILL").
  status:     DONE
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_stray_sweeper_safety.py -q → 34 passed
  gap:        none

item FR-009: "doctor flags cairn-owned stdio/SSE registrations; --fix repoints to URL+key"
  evidence:   The audit flags by transport class: src/cairn/cli/system/doctor.py:_registration_findings:728 — stdio spawn-shaped entries WARN (:754-768), SSE-era endpoints FAIL (:770-778 `f"{client}: SSE registration in {disp} points at {url} but the " f"daemon serves {daemon_url} (pre-HTTP-era endpoint)"`), Claude Desktop flagged unsupported and never repointed (:742-753); audit tables doctor.py:655 `_REG_WS_FILES` / :664 `_REG_HOME_FILES` plus the desktop config path via _client_config_paths :676. --fix repoints to the URL: src/cairn/cli/system/doctor.py:doctor:941 takes `--fix` with help (doctor.py:939) "Repoint stale cairn-owned stdio/SSE registrations to the daemon URL."; src/cairn/cli/system/doctor_fix.py:apply_doctor_fixes:69 — _REPOINTED = "repointed-to-url" (doctor_fix.py:9), replacement built by the same generators the installers use (src/cairn/cli/system/doctor_fix.py:_url_config:15, src/cairn/cli/system/doctor_fix.py:_replace_registration:39), backup-before-write + freshness refusal + workspace-owned skip (:106-124); summary src/cairn/cli/system/doctor.py:_fix_summary_lines:883 — :899-901 `f"repointed {client} in {config} to the daemon URL ({url})"`.
  status:     DONE
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_doctor_fix.py -q → 15 passed (test_repoints_stdio_registrations_to_url :127, test_repoints_sse_registration_to_url :133, test_desktop_registration_gets_removal_hint_and_is_never_repointed :142)
  gap:        none

item FR-010: "serve start|stop|status|restart manage the HTTP daemon; launchd on macOS, daemonize fallback elsewhere"
  evidence:   All four subcommands drive one ensure-running core: `grep -n "@serve.command" src/cairn/cli/serve.py` → run/start/stop/status/restart at :58/:135/:156/:187/:235; src/cairn/cli/serve.py:serve_start:138 — docstring :139 "Start the persistent shared HTTP daemon (launchd on macOS, daemonized otherwise); idempotent.", serve.py:141 `pid, url, started = lc.ensure_daemon_running(port, host)`; the old `if not lc.is_macos(): … sys.exit(1)` gate is gone. The core: src/cairn/mcp_server/lifecycle.py:ensure_daemon_running:504 — :509-511 `if is_macos():` → `_ensure_running_launchd(port, host)` else `_ensure_running_daemonize(port, host)`; daemonize fallback src/cairn/mcp_server/lifecycle.py:_ensure_running_daemonize:481 (fork :494, detach + exec serve run via src/cairn/mcp_server/lifecycle.py:_daemonize_exec:462 — `os.setsid()` :464, pidfile :498); launchd path writes the read-write plist (src/cairn/mcp_server/lifecycle.py:render_plist:96, args :120-123) and waits for healthz (:457). serve_status probes healthz (serve.py:198 `responds = lc.healthz_responds(port, host)`); restart keeps its stop+start composition (serve.py:241-242).
  status:     DONE
  verify:     grep -n "@serve.command" src/cairn/cli/serve.py → run/start/stop/status/restart at :58/:135/:156/:187/:235; CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_serve_manager.py tests/test_port_dry_and_unload_fixes.py -q → 19 passed
  gap:        none

item FR-011: "MCP protocol surface stays SDK-generated; no hand-rolled transport (2026-07-28 revision via SDK bump only)"
  evidence:   pyproject.toml:46 `"mcp>=0.9.0,<2.0.0"` (uv.lock:1796-1797 resolves `name = "mcp"` / `version = "1.29.0"`); the only app builder is the SDK's — server.py:216 `app: Any = mcp.streamable_http_app()`; no hand-rolled JSON-RPC: `grep -rn "jsonrpc" src/cairn --include='*.py'` → only src/cairn/graph/lsp.py:105, :119 (LSP client, not MCP transport). Pinned SDK's latest protocol revision is 2025-11-25 (`CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test python -c "from mcp import types; print(types.LATEST_PROTOCOL_VERSION)"` → `2025-11-25`), so the 2026-07-28 revision is not in the pinned SDK.
  status:     DONE
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_http_transport.py -q → 32 passed (catalog pin test_http_tool_catalog_pins_26_tools exercises the SDK app builder)
  gap:        none for the current surface (constraint item: keep it this way)

item NFR-001: "Security — preserve existing HTTP auth/bind policy (bearer middleware, healthz exemption, loopback-vs-wildcard, pre-bind refusal)"
  evidence:   Policy preserved and test-pinned through the collapse: src/cairn/mcp_server/server.py:_BearerAuthMiddleware:127 (401 JSON body; :135 `scope.get("path") == _HEALTHZ_PATH` exemption); src/cairn/mcp_server/server.py:_transport_security_for:158; src/cairn/mcp_server/server.py:_require_http_api_key:177 (pre-bind sys.exit(1) :185); constant-time check src/cairn/mcp_server/auth.py:bearer_key_ok:27 (compare_digest :38).
  status:     DONE
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_http_transport.py tests/test_auth.py -q → 51 passed (test_bearer_middleware_healthz_is_exempt_and_bare_token_accepted :318, test_refuse_predicate_exits_unkeyed_non_loopback :358, test_transport_security_settings_match_bind_class :335)
  gap:        none (preservation constraint held through the refactor)

item NFR-002: "Privacy — API key stored user-only, never logged; credential-echo prohibitions hold"
  evidence:   The key file is created user-only: src/cairn/cli/core.py:init:54 — core.py:70 `fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)`; read back by src/cairn/mcp_server/auth.py:persisted_api_key:15. Never-logged holds: server.py:141 comment "Never log the presented credential or the configured key." — the 401 warning logs only the path (:142-144); doctor echoes key presence only, src/cairn/cli/system/doctor.py:641-644 `key_echo = "<unset>" if key_source == "default" else f"<set via {key_source}>"`.
  status:     DONE
  verify:     rg -n 'chmod|0o600' src/cairn/mcp_server/ src/cairn/cli/core.py → `src/cairn/cli/core.py:70:            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)` (the init key-file create); CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_cli_init_rail.py tests/test_auth.py -q → 25 passed (test_fresh_init_persists_user_only_api_key_file tests/test_cli_init_rail.py:107, test_init_preserves_existing_api_key tests/test_cli_init_rail.py:136)
  gap:        none

item NFR-003: "Performance — warm daemon serves a second client's first call without re-running boot work"
  evidence:   Boot work is per-process and runs once, before serving: src/cairn/mcp_server/server.py:run:222 — model warmup :276-278 (`warm_models_in_background()`), catch-up :292-295 (`ensure_fresh_force`), watcher :338-341 — all ahead of the serving block (:343-364); every registration is URL-form sharing the daemon (src/cairn/agent_install/_common.py:mcp_config_json:103; `grep -rn "stdio" src/cairn/agent_install/` → no matches — no per-client stdio spawns remain). The amortization is now test-pinned: tests/test_server_robustness.py:661 test_second_client_first_call_rides_warm_process asserts a second session's first call succeeds on the same pid with exactly one readiness line ("boot work must run once per process").
  status:     DONE
  verify:     grep -n "warm_models_in_background\|ensure_fresh_force\|FileWatcherService" src/cairn/mcp_server/server.py → :276/:278, :292/:295, :338/:340 (all inside run(), before serving); CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_server_robustness.py -q → 16 passed
  gap:        none

item NFR-004: "Reliability — concurrent connects converge to one daemon; crashed daemon replaceable by next connect"
  evidence:   Lock machinery now exists: `grep -rn "fcntl\|flock\|lockfile\|FileLock" src/cairn/cli/ src/cairn/mcp_server/` → src/cairn/mcp_server/lifecycle.py:_start_lock:417 (exclusive flock over the start sequence, kernel-released when the holder dies) and src/cairn/cli/connect.py:_acquire_start_lock:25 (bounded LOCK_EX|LOCK_NB, 30s deadline). connect.py:62-66 takes the lock, then re-probes healthz before starting — the concurrent loser converges to reused; a crashed daemon is swept before replacement (connect.py:69 `lc.sweep_strays(str(resolve_store().db), log=True)` then :71 ensure_daemon_running). Tests pin both: tests/test_connect.py:116 test_racing_connects_converge_to_one_daemon, :162 test_killed_daemon_replaced_with_orphan_swept_first.
  status:     DONE
  verify:     grep -rn "fcntl\|flock" src/cairn/cli/ src/cairn/mcp_server/ → lifecycle.py:421/:427, connect.py:5/:33; CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_connect.py -q → 4 passed
  gap:        none

item NFR-005: "Observability — /healthz + readiness line; connect reports started vs reused"
  evidence:   healthz exists: src/cairn/mcp_server/server.py:_register_healthz_route:188 (custom_route :197); handler src/cairn/mcp_server/_server_core.py:healthz_response:360 over src/cairn/mcp_server/_server_core.py:healthz_payload:338 ("Bounded /healthz payload; an unreachable store degrades to status \"unhealthy\"."). The HTTP readiness line is the daemon's only path — server.py:351-355 `cairn: MCP server listening on http://…/mcp` to stdout (`file=sys.stdout` :354). connect reports started-vs-reused: src/cairn/cli/connect.py:_report:45 — :49 `f"cairn daemon started{pid_part} at {url}"` / :51 `f"cairn daemon already running at {url}"`.
  status:     DONE
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_http_transport.py -q → 32 passed (test_healthz_payload_shape_on_reachable_store :404, test_healthz_route_registers_exactly_once :379); CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_connect.py -q → 4 passed (started report :92, reused report :104)
  gap:        none

item NFR-006: "Accessibility — not applicable (no new user-facing UI surface)"
  evidence:   spec.md:172 `- **NFR-006**: Accessibility — not applicable: no new user-facing` (declared N/A at spec time); the surfaces this spec touches are CLI + config files (serve/connect/install-agents/doctor commands and JSON configs, evidence above).
  status:     DONE
  verify:     grep -n "not applicable" specs/mcp-streamable-http/spec.md → `172:- **NFR-006**: Accessibility — not applicable: no new user-facing`
  gap:        none (declared not applicable)
```

## Supporting evidence
- Tool registry: shared FastMCP singleton; `_EXPECTED_TOOL_COUNT = 26` (server.py:51).
  Re-counted this session: `grep -c "^@mcp.tool" src/cairn/mcp_server/tools_*.py` →
  tools_graph.py:12, tools_compass.py:5, tools_knowledge.py:5, tools_memory.py:2,
  tools_federation.py:1, tools_wiki.py:1 — sum 26 (matches spec.md's "26 MCP tools";
  the 24 in specs/remote-mcp-oauth/survey.md was that spec's older baseline).
- Serving is HTTP-only: src/cairn/mcp_server/server.py:run:222 is the sole serving
  path — tool modules register via import side effects (server.py:43-48); tool
  bodies are transport-agnostic.
- Much of the HTTP substrate landed days before this survey via the archived spec
  `specs/archive/2026-10-07-http-mcp-serving/` (its test.md carries the
  parity/healthz/auth probe conditions still cited by tests/test_http_transport.py).
- Installer surface: CLIENTS = 9 entries — src/cairn/agent_install/_common.py:16
  `CLIENTS = ["claude", "claude-desktop", "cursor", "droid", "zcode", "agy", "opencode", "kilo", "omp"]`
  (spec's "8 remaining clients" = this list minus claude-desktop). install-agents has
  no transport flags left (`grep -n '"--sse"' src/cairn/cli/agents.py` → no matches);
  install() takes no transport parameter (src/cairn/agent_install/__init__.py:install:254)
  and ends with a healthz daemon gate (src/cairn/cli/agents.py:install_agents:26 —
  agents.py:146-149 `url = mcp_url()` … `if not healthz_responds():` → connect hint).
- `cairn config --mcp-config` prints the URL+key form: src/cairn/cli/core.py:config:196 —
  core.py:225-229 `entry: dict = {"type": "http", "url": mcp_url()}` plus Bearer
  headers when a persisted key exists (keyless URL form otherwise, since loopback
  serves unkeyed).
- Doctor is a package at 0.22.0 (src/cairn/cli/system/doctor.py), not the 0.21.0-era
  cli/system.py named in specs/context/structure.md — context line refs for doctor are stale.
- MCP SDK: pin pyproject.toml:46 `mcp>=0.9.0,<2.0.0`; resolved uv.lock mcp 1.29.0;
  LATEST_PROTOCOL_VERSION 2025-11-25 (session probe) — the 2026-07-28 revision
  arrives only via a future SDK bump inside the pin range.
- Test runs this session (canonical form, CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test):
  pytest tests/test_http_transport.py -q → `32 passed in 3.24s`;
  pytest tests/test_http_transport.py tests/test_auth.py -q → `51 passed in 3.40s`;
  pytest tests/test_connect.py -q → `4 passed in 5.61s`;
  pytest tests/test_clients.py -q → `13 passed in 0.69s`;
  pytest tests/test_stray_sweeper_safety.py -q → `34 passed in 0.39s`;
  pytest tests/test_doctor_fix.py -q → `15 passed in 0.37s`;
  pytest tests/test_serve_manager.py tests/test_port_dry_and_unload_fixes.py -q →
  `19 passed in 2.50s`; pytest tests/test_cli_init_rail.py tests/test_auth.py -q →
  `25 passed in 0.43s`; pytest tests/test_server_robustness.py -q → `16 passed in 3.27s`;
  pytest tests/test_install_uninstall_fidelity.py -q → `69 passed, 1 skipped in 1.47s`.
- Delta note (this re-survey): HEAD is still the baseline commit 31a66df — the
  implemented state cited above is uncommitted working-tree change on top of it
  (git diff --name-only 31a66df -- src/ tests/ uv.lock lists the mcp_server/, cli/,
  agent_install/ files plus their tests; src/cairn/mcp_server/_server_core.py and
  pyproject.toml are untouched).

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
