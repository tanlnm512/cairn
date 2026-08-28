# Survey: env-propagation

**Created**: 2026-08-28 | **Baseline**: 0.16.0 @ fe7a7f09edb015d6a8fb12cd5d0f1b06ed07f5c3
Phase-A output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item FR-001: "stdio MCP registrations embed env.CAIRN_HOME when non-default; no env block when default"
  evidence:   src/cairn/agent_install/_common.py:111-117 (mcp_config_json stdio branch — command+args only, no env key):
                 cmd = resolve_cg_command()
                 if len(cmd) == 1:
                     # cairn binary: args = ["serve"]
                     return {"mcpServers": {"cairn": {"command": cmd[0], "args": ["serve"]}}}
                 # module fallback (e.g. [python, "-m", "cairn.cli.main"]): append "serve" to args
                 command, *prefix = cmd
                 return {"mcpServers": {"cairn": {"command": command, "args": [*prefix, "serve"]}}}
             src/cairn/agent_install/clients/claude_desktop.py:32-35 (the ONLY client writing any env today, and it is CAIRN_WORKSPACE, not CAIRN_HOME):
                 cfg = mcp_config_json(transport="stdio")
                 cfg["mcpServers"]["cairn"]["env"] = {
                     "CAIRN_WORKSPACE": str(Path(workspace).resolve())
                 }
             src/cairn/agent_install/merge.py:256-258 (idempotence already compares env dicts — machinery an env block would flow through):
                 # Also compare env (Claude Desktop pins CAIRN_WORKSPACE here). Absent
                 # env == {}, so this is a no-op for clients that don't set one.
                 return (cur.get("env") or {}) == (new.get("env") or {})
             Consumers of mcp_config_json (stdio shape): src/cairn/agent_install/clients/claude.py:89 (.mcp.json),
             cursor.py:52, droid.py:67 (file fallback), omp.py:58. Custom shapes: zcode.py:48-52
             (mcp.servers.cairn, command+args), opencode.py:53-54 (mcp.cairn command ARRAY),
             kilo.py:28 (mcp.cairn command ARRAY), agy.py:50-54 (command+args). Global-scope claude/droid
             register via subprocess CLIs, not files: claude.py:99-100 (`claude mcp add cairn --scope user *cmd serve`),
             droid.py:58-59 (`droid mcp add cairn *cmd serve`).
             Store-home resolution the env would carry: src/cairn/paths.py:31-33:
                 CAIRN_HOME = Path(
                     os.environ.get("CAIRN_HOME", str(Path.home() / ".cairn"))
                 )
  status:     TODO (the "SHALL NOT add on default" half holds trivially today — no generator writes env;
              the "SHALL embed when non-default" half is entirely absent)
  verify:     uv run pytest tests/test_install_uninstall_fidelity.py tests/test_clients.py -q
              (existing fidelity tests assert today's env-less shapes; recorded, not executed this session — see Rules)
  gap:        No code path reads CAIRN_HOME at install time or embeds env.CAIRN_HOME in any of the
              9 stdio config generators (mcp_config_json + 4 per-shape variants + claude-desktop override);
              no notion of "non-default home" exists in agent_install/ (zero CAIRN_HOME references —
              grep of src/cairn/agent_install/ this session returned none).
```

```
item FR-002: "generated hook command strings embed the cairn-home assignment when non-default"
  evidence:   src/cairn/agent_install/_common.py:125-127 (the single hook-command builder — python + module, no env assignment):
                 def _claude_hook_command(entrypoint: str) -> str:
                     """Build a hook command string: `<python> -m cairn.hooks.claude_hooks <entry>`."""
                     return f"{_python_for_hooks()} -m cairn.hooks.claude_hooks {entrypoint}"
             Hook writers that use it: src/cairn/agent_install/clients/claude.py:45-63 (claude_hooks_block →
             .claude/settings.json, merged at claude.py:133) and cursor.py:26-37 (cursor_hooks_json →
             .cursor/hooks.json, merged at cursor.py:59). Git post-commit hook is a second generator,
             src/cairn/hooks/git_hooks.py:32-38 (POST_COMMIT_TEMPLATE — bare PATH-resolved `cairn`):
                 cairn update --repo "{repo}" > /dev/null 2>&1 &
                 cairn validate-paths --mark > /dev/null 2>&1 &
             Hook runtime resolves cairn via PATH and inherits parent env only:
             src/cairn/hooks/claude_hooks.py:26-33:
                 def _cg_command() -> list[str]:
                     cairn_bin = shutil.which("cairn")
                     if cairn_bin:
                         return [cairn_bin]
                     return [sys.executable, "-m", "cairn.cli.main"]
             and claude_hooks.py:44-51 — subprocess.run(_cg_command() + args, ...) with NO env kwarg
             (inherits the hook-spawner's env verbatim).
  status:     TODO
  verify:     uv run pytest tests/test_install_uninstall_fidelity.py::TestHookIdempotency -q
              (existing hook-shape tests; recorded, not executed this session — see Rules)
  gap:        No generator embeds any env assignment in a hook command string; the hook process relies
              entirely on inherited env, so a non-default CAIRN_HOME set only in the installer's shell
              does not reach hook-spawned `cairn update` / `memory capture`.
```

```
item FR-003: "LaunchAgent plist EnvironmentVariables includes CAIRN_HOME when non-default"
  evidence:   src/cairn/mcp_server/lifecycle.py:84-97 (render_plist env dict — PATH + the four cairn vars,
              NO CAIRN_HOME):
                 env = {
                     # Inherit the user PATH so `cairn` can find python etc.
                     "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                 }
                 if workspace:
                     env["CAIRN_WORKSPACE"] = str(workspace)
                 if db_path:
                     env["CAIRN_DB"] = str(db_path)
                 if knowledge_path:
                     env["CAIRN_KNOWLEDGE"] = str(knowledge_path)
                 args = [bin_, "serve", "run", "--port", str(port)]
                 if read_only:
                     args.append("--read-only")
                     env["CAIRN_READ_ONLY"] = "1"
             src/cairn/cli/serve.py:130-135 (serve_start renders + writes the plist with workspace/db/knowledge):
                 lc.write_plist(lc.render_plist(
                     port=port, host=host,
                     workspace=str(store.workspace),
                     db_path=str(store.db),
                     knowledge_path=str(store.knowledge),
                 ))
             macOS gate: src/cairn/cli/serve.py:105-108 (serve_start exits 1 on non-darwin) and
             lifecycle.py:383-386 (load() raises "LaunchAgent daemon management is macOS-only.").
  status:     TODO
  verify:     uv run pytest tests/test_port_dry_and_unload_fixes.py -q
              (the only lifecycle tests found this session — they cover port constants and unload status,
              NOT plist env content; recorded, not executed — see Rules)
  gap:        render_plist() has no CAIRN_HOME parameter or env entry; no test asserts plist
              EnvironmentVariables content at all.
```

```
item FR-004: "store-open failure names resolved DB path + env resolution chain + remediation, not a bare OperationalError"
  evidence:   src/cairn/mcp_server/server.py:213-234 (the MCP-server boot guard — remediation present,
              resolved path and env chain ABSENT; the raw exception text is interpolated verbatim):
                 try:
                     check_conn = sqlite3.connect(db_path)
                     ...
                 except Exception as e:
                     # If we can't even check the DB, exit with a helpful message
                     from datetime import datetime
                     ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                     print(f"[{ts}] cairn: error: failed to check database: {e}. "
                           f"Run 'cairn init && cairn build' first.", file=sys.stderr, flush=True)
                     sys.exit(1)
             The DB open underneath: src/cairn/graph/schema.py:751-754 (a missing parent directory surfaces
             as sqlite3.OperationalError("unable to open database file") — no path in the message):
                 uri = f"file:{quote(str(path.resolve()))}?mode=ro"
                 conn = sqlite3.connect(uri, uri=True)
             else:
                 conn = sqlite3.connect(str(path))
             Resolution chain that would need naming: src/cairn/paths.py:285-302 (resolve_workspace:
             explicit > CAIRN_WORKSPACE env > registered ancestor walk > cwd) and paths.py:305-318
             (resolve_store: CAIRN_DB/CAIRN_KNOWLEDGE hard overrides over CAIRN_HOME/key).
             Adjacent, already-better message on the doctor surface: src/cairn/cli/system.py:1450-1454:
                 if not Path(db).exists():
                     db_error = FileNotFoundError(
                         f"store not found at {db} -- run `cairn init` + `cairn build` first"
                     )
             Existing tests read this session: tests/test_server_robustness.py:30-45
             (TestStoreExistenceCheck::test_missing_store_exits_cleanly — asserts exit code 1 only) and
             tests/test_doctor.py:132-143 (test_schema_fail_unopenable_db — asserts "cannot open database"
             in the doctor detail).
  status:     PARTIAL (a guarded exit-with-remediation exists on the server boot path; it names neither the
              resolved DB path nor the env chain, and the remediation never mentions CAIRN_HOME)
  verify:     uv run pytest tests/test_server_robustness.py::TestStoreExistenceCheck tests/test_doctor.py::test_schema_fail_unopenable_db -q
              (recorded, not executed this session — see Rules)
  gap:        The catch-all message interpolates {e} (bare OperationalError text) and does not print
              db_path (already in scope at server.py:210), the CAIRN_HOME/CAIRN_WORKSPACE/CAIRN_DB values
              in effect, or a CAIRN_HOME remediation; no equivalent guard exists on the CLI get_db path
              (schema.py:729-754 raises raw).
```

```
item FR-005: "machine-readable resolution probe reporting cairn_home, workspace, db, knowledge"
  evidence:   src/cairn/cli/core.py:186-191 (`cairn config` prints all four values — TEXT only):
                 store = resolve_store()
                 click.echo(f"workspace:  {store.workspace}")
                 click.echo(f"store:      {store.home}")
                 click.echo(f"  .kg:         {store.db}{' (exists)' if store.db.exists() else ' (missing)'}")
                 click.echo(f"  .knowledge:  {store.knowledge}")
                 click.echo(f"home:       {store.home.parent}  (override with CAIRN_HOME)")
             src/cairn/cli/core.py:150-170 (`--db` is machine-readable but db-path-only;
             `--mcp-config` prints a canned snippet; no --json flag exists on the command):
                 @click.option("--db", "db_only", is_flag=True,
                               help="Print only the resolved graph DB path (machine-readable; for scripting).")
                 ...
                 if db_only:
                     click.echo(str(resolve_store().db))
                     return
             `cairn status` (the other candidate surface) has no --json either:
             src/cairn/cli/system.py:422-423 (options are only --db and --knowledge), rendering via
             display.kv at system.py:463-485.
             The data source for any probe: src/cairn/paths.py:305-318 (resolve_store returns
             StorePaths(workspace, home, db, knowledge); StorePaths dataclass at paths.py:101-114).
  status:     PARTIAL (all four values assembled by one existing command in text form + a db-only
              machine-readable flag; no JSON/structured probe emitting all four)
  verify:     uv run cairn config --db   (prints exactly the db path; recorded, not executed — see Rules)
  gap:        No machine-readable (JSON) probe reporting cairn_home + workspace + db + knowledge exists;
              `cairn config --db` covers one field, `cairn config` text covers all four unparseably.
```

```
item FR-006: "install-agents verifies each stdio registration by spawning its exact command+env and comparing resolved stores"
  evidence:   src/cairn/agent_install/__init__.py:299-304 (install() loops installers, collects results —
              nothing spawns a verification process):
                 results: list[InstallResult] = []
                 for client in [c for c in CLIENTS if c in target]:
                     results.append(_INSTALLERS[client](
                         workspace, force, dry_run, transport=transport, sse_url=sse_url,
                         scope=scope,
                     ))
             src/cairn/cli/agents.py:157-174 (the CLI prints the report; the only post-install probe is
              an SSE-daemon TCP reachability note, not a per-registration resolution check):
                 report = install(ws, clients=target_clients, force=force, dry_run=dry_run, ...)
                 ...
                 if report.transport == "sse":
                     from ..agent_install import sse_daemon_reachable
                     if not sse_daemon_reachable(sse_url):
                         click.echo("  note: SSE daemon not reachable — run `cairn serve start` ...")
             sse_daemon_reachable (socket connect only): src/cairn/agent_install/__init__.py:184-210.
             Reusable installed-state reader: src/cairn/agent_install/detect.py:150-211 (check_installed
             probes each client's config file for a cairn entry via _json_has_cairn, detect.py:122-147).
  status:     TODO
  verify:     uv run pytest tests/test_install_uninstall_fidelity.py -q
              (existing install tests assert file shapes only — no spawn-verify test exists; recorded,
              not executed this session — see Rules)
  gap:        No code spawns a registration's command with its env (cwd = target workspace) after writing,
              no probe output is compared against the install-time store, and InstallResult carries no
              PASS/FAIL verification field (dataclass at _common.py:36-44: client/written/skipped/notes).
```

```
item FR-007: "cairn doctor environment check: store existence, client-registration consistency, platform/transport, binary coherence"
  evidence:   src/cairn/cli/system.py:1466-1476 (the complete check list executed by _run_doctor — 9 checks,
              none auditing environment wiring):
                 return [
                     _check_schema(conn),
                     _check_embeddings(conn),
                     _check_ann(conn),
                     *_check_embed_server(conn),
                     _check_freshness(conn),
                     _check_parse_errors(conn),
                     _check_concurrency(conn),
                     _check_tool_health(conn),
                     _check_config(),
                 ]
             Doctor plumbing an environment check would slot into: _result shape at system.py:742-744
             ({"name", "status", "detail", "hint"}), text rendering _render_doctor system.py:1488-1503,
             --json at system.py:1523-1524 (click.echo(json.dumps(results, indent=2))), exit contract
             system.py:1527-1528 (1 iff any FAIL). Pinned by tests/test_doctor.py:96-124
             (test_eight_checks_always_emitted asserts the exact 9-name sequence via --json):
                 expected = [
                     "schema", "embeddings", "ann", "embed_server", "freshness",
                     "parse_errors", "concurrency", "tool_health", "config",
                 ]
                 assert [d["name"] for d in data] == expected
             Machinery reusable for the four sub-audits, all read this session:
              (a) store existence: system.py:1451-1454 (Path(db).exists() branch) + paths.resolve_store;
              (b) registration consistency: detect.py:150-211 check_installed + per-client config paths
                  (detect.py:162-209); SSE reachability: lifecycle.py:418-440 sse_responds (socket probe of
                  GET / reading the first status byte);
              (c) platform gate: lifecycle.py:27-28 is_macos (sys.platform == "darwin"); the SSE-on-Linux
                  state exists today (mcp_config_json sse branch _common.py:105-110 is written on any OS
                  while serve start exits 1 off-darwin, serve.py:105-108);
              (d) binary coherence: resolve_cg_command _common.py:67-76 (shutil.which("cairn") preferred
                  over sys.executable fallback) vs lifecycle.cg_bin lifecycle.py:43-53 (CAIRN_BIN env >
                  which > ~/.local/bin/cairn).
  status:     TODO
  verify:     uv run pytest tests/test_doctor.py -q
              (recorded, not executed this session — see Rules)
  gap:        No check named for environment/client-registrations/platform/binary exists; all 9 current
              checks audit the store's internals or the process's embed config, and the check-name
              sequence is frozen by test_doctor.py:96-124 (adding a check requires updating that test).
```

## Supporting evidence

**Resolution chain (the env semantics this spec propagates).**
- `src/cairn/paths.py:31-33` — `CAIRN_HOME = Path(os.environ.get("CAIRN_HOME", str(Path.home() / ".cairn")))` — import-time binding; a value of literally `~/.cairn` is NOT canonicalized against the default (any set value is used verbatim). Derived module constants: `REGISTRY_FILE = CAIRN_HOME / "workspaces.json"` (paths.py:35), `CONFIG_FILE = CAIRN_HOME / "config.json"` (paths.py:40), `SHARED_LIB = CAIRN_HOME / "lib"` (paths.py:52) — all bound at import, so plist `CAIRN_HOME` propagation affects `config.json` + shared libs exactly as spec AC3 assumes.
- `paths.py:285-302` resolve_workspace order: explicit arg > `CAIRN_WORKSPACE` env > registered ancestor walk > cwd. `paths.py:305-318` resolve_store: `CAIRN_DB`/`CAIRN_KNOWLEDGE` env overrides, else `<CAIRN_HOME>/<store_key(ws)>/{.kg,.knowledge}`. `store_key` = `hashlib.sha256(str(workspace)).hexdigest()[:16]` (paths.py:117-119).
- Lazy CAIRN_HOME re-read exists in exactly one place: `src/cairn/cli/uninstall.py:30-32`:
  ```
  def _cairn_home() -> Path:
      """CAIRN_HOME read lazily so the env var set after import is honored"""
      return Path(os.environ.get("CAIRN_HOME", str(Path.home() / ".cairn")))
  ```

**Client matrix (from the per-client modules, all read this session).**
CLIENTS order: `src/cairn/agent_install/_common.py:16` — `["claude", "claude-desktop", "cursor", "droid", "zcode", "agy", "opencode", "kilo", "omp"]`.

| client | stdio MCP writer | SSE shape | hooks written | env in config today |
|---|---|---|---|---|
| claude | `.mcp.json` via mcp_config_json (claude.py:89); global scope via `claude mcp add` subprocess (claude.py:99-100) | url (via mcp_config_json sse) / `claude mcp add --transport sse` (claude.py:96-97) | YES — `.claude/settings.json` (claude.py:133) | none |
| claude-desktop | global `claude_desktop_config.json`, stdio ALWAYS (claude_desktop.py:24-32) | not supported (docstring claude_desktop.py:22-27) | no | `env.CAIRN_WORKSPACE` (claude_desktop.py:32-35) |
| cursor | `.cursor/mcp.json` via mcp_config_json (cursor.py:52) | type+url (_common.py:110) | YES — `.cursor/hooks.json` (cursor.py:59) | none |
| droid | `.factory/mcp.json` file fallback (droid.py:67); CLI `droid mcp add` when present (droid.py:58-59) | `--type sse` (droid.py:56) | no (git hooks via `cairn hooks install`) | none |
| zcode | `.zcode/config.json` nested mcp.servers (zcode.py:48-52, merged zcode.py:82); global `~/.zcode/cli/config.json` | type:sse+url (zcode.py:47) | no — note at zcode.py:102 | none |
| agy | global `~/.gemini/config/mcp_config.json`, mcpServers shape (agy.py:50-54, merged agy.py:63) | `serverUrl` (agy.py:49) | no | none |
| opencode | `opencode.json` `mcp.cairn` command ARRAY (opencode.py:53-54, merged opencode.py:80) | type:remote+url (opencode.py:52) | no | none |
| kilo | `kilo.json` `mcp.cairn` command ARRAY (kilo.py:28, merged kilo.py:57) | type:remote+url (kilo.py:27) | no | none |
| omp | `.omp/mcp.json` mcpServers (omp.py:58) | type+url (via mcp_config_json) | no | none |

Transport default: `install()` with `transport=None` resolves to `"sse"` (agent_install/__init__.py:287-288); the CLI default is likewise sse unless `--stdio` (agents.py:154). Hook command marker/uninstall matching is entrypoint-based, path-independent (merge.py:274-298, `_hook_markers` _common.py:130-139) — an embedded env assignment inside the command string would not break matching (it matches on `cairn.hooks.claude_hooks <ep>` substring).

**Where `cairn status` output is produced (FR-005 extension surface).**
`src/cairn/cli/system.py:421-485`: options `--db`/`--knowledge` only (422-423); body renders graph/compass/wiki/memory via `display.kv` (463-468), pending sync (470-475), parse errors (477-485). No `--json` branch exists anywhere in the command.

**Doctor result rendering path (FR-007 integration surface).**
Text: `_render_doctor` (system.py:1488-1503) — one line per check `✓/!/✗ STATUS name: detail` + optional `hint`, markup-escaped. JSON: `doctor` command (system.py:1506-1528) — `click.echo(json.dumps(results, indent=2))` at 1523-1524, exit code `1 if any(r["status"] == _FAIL ...)` at 1527-1528. The `report` command (system.py:1804-1843) reuses `_run_doctor` through a privacy scrubber (`_scrub_doctor` 1593-1607) — a new environment check's detail strings will flow through that redaction too.

**MCP server env contract (what a spawned `cairn serve` expects).**
`src/cairn/cli/serve.py:65-85` `_serve_foreground`: sets `CAIRN_DB`/`CAIRN_KNOWLEDGE` (76-77) and `CAIRN_READ_ONLY` (82) into os.environ before `run()`; run() re-reads `CAIRN_DB or resolve_store().db` (server.py:210). `_server_core._conn` (159-209) opens `os.environ.get("CAIRN_DB") or str(_store().db)`; `_store()` = `resolve_store()` (_server_core.py:81-87). So a client-spawned stdio server with NO env resolves via cwd ancestor-walk under the DEFAULT home — the silent-wrong-store mechanism of issue #70.

**Installer binary resolution (FR-007d surface).**
`resolve_cg_command` (_common.py:67-76): `shutil.which("cairn")` else `[sys.executable, "-m", "cairn.cli.main"]` — configs pin whichever binary is first on PATH at install time. `lifecycle.cg_bin` (lifecycle.py:43-53): `CAIRN_BIN` env > `which` > `~/.local/bin/cairn` > bare `"cairn"`.

**Existing test files the brief named (all present on disk, headers read this session).**
tests/test_install_uninstall_fidelity.py (classes at 86, 196, 319, 347, 366, 412, 485, 526, 561, 644, 779, 852; subprocess cases pin `CAIRN_HOME` env at 513, 577, 590, 631), tests/test_clients.py (opencode-focused, 8 tests), tests/test_atomic_config_writes.py (TestAtomicWrite/TestSafeJsonLoad/TestMergeMalformedBackup/TestNonObjectKeyBackup at 16/70/88/143), tests/test_doctor.py (37 tests incl. the 9-name sequence at 96-124), tests/test_server_robustness.py (TestStoreExistenceCheck at 27).

## Unknowns
1. Test-suite pass state at the baseline commit — unknown — verify (verify commands were
   NOT executed; this surveyor session had no shell tool — see Rules).
2. Whether MCP clients inject any env of their own into spawned stdio servers beyond the
   config's `env` block (out-of-repo client behavior) — unknown — verify.
3. Env-propagation contract of the external registration CLIs (`claude mcp add --scope user`,
   `droid mcp add`) — whether they accept/persist an env block at all — unknown — verify
   (blocked FR-001 for global-scope claude/droid registrations).
4. FR-001's "default (unset or `~/.cairn`)" semantics vs paths.py:31-33: the code binds any
   SET value verbatim with no canonicalization, so `CAIRN_HOME=~/.cairn` (explicitly set to
   the default path) currently behaves as non-default for resolution purposes — whether the
   spec intends set-but-default to count as default — unknown — verify at approval gate.

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
- Session caveat: this surveyor had no shell tool; pytest/CLI verify commands above are
  recorded as the exact commands that prove each status but were NOT executed. All
  statuses derive from file reads in this session; no status is DONE, consistent with
  the "cited test must run and pass" rule.
- Re-count correction vs the brief: the brief says "the 8 checks"; `_run_doctor`'s return
  list (system.py:1466-1476) and test_doctor.py:109-119 both carry **9** check names
  (embed_server slots after ann).

## Orchestrator verification addendum (2026-08-28, post-survey)

The surveyor session had no shell tool; the orchestrator executed every verify
command above at baseline fe7a7f0. Canonical invocation is
`uv run --extra test pytest ...` (plain `uv run pytest` lacks the [test] extra
locally; CI installs it explicitly).

| verify command | result |
|---|---|
| `uv run --extra test pytest tests/test_install_uninstall_fidelity.py tests/test_clients.py -q` | 61 passed (includes TestHookIdempotency — FR-002's verify) |
| `uv run --extra test pytest tests/test_port_dry_and_unload_fixes.py -q` | 6 passed |
| `uv run --extra test pytest tests/test_server_robustness.py::TestStoreExistenceCheck tests/test_doctor.py::test_schema_fail_unopenable_db -q` | 3 passed |
| `uv run --extra test pytest tests/test_doctor.py -q` | 40 passed |
| `uv run cairn config --db` | prints the resolved db path (exit 0) |

Unknown #1 resolved: baseline suite green for every cited file. Unknowns #2-#4
remain open for tech/planner; #4 resolved by spec ruling below (FR-001).
