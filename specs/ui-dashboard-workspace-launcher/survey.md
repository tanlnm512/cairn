# Survey: ui-dashboard-workspace-launcher

**Created**: 2026-08-20 | **Baseline**: cairn-intel 0.12.1 @ `d781383`
Phase-A output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item Q1: "Store layout: one hash-keyed dir per workspace under CAIRN_HOME"
  evidence: src/cairn/paths.py:6-8 — "CAIRN_HOME (default ~/.cairn) holds one
    store per workspace, keyed by a short hash of the workspace root. Each
    store is a directory containing a `.kg` SQLite graph DB and a
    `.knowledge/` OKF bundle."
  evidence: src/cairn/paths.py:32 — "REGISTRY_FILE = CAIRN_HOME / \"workspaces.json\""
  status: DONE
  verify: ls ~/.cairn && python3 -c "import json; print(len(json.load(open('$HOME/.cairn/workspaces.json'))))"
  gap: None — enumeration inputs exist (registry + dir listing); no cairn
    API today enumerates them together

item Q2: "The registry maps absolute workspace path to 16-hex store key"
  evidence: registry read this session — entries:
    /Users/tan.le/Projects/be -> 9521a7075f4ac248,
    /Users/tan.le/Projects/cairn -> 71e4dcfee8d29b5a (2 total)
  evidence: src/cairn/paths.py — _load_registry() used by `cairn config --list`
  status: DONE
  verify: cairn config --list
  gap: registry-vs-disk divergence handling is FR-002's new work (stale
    entries are a proven shape: 227 test-leaked registrations were cleaned
    from this machine's registry on 2026-08-20)

item Q3: "`cairn config --list` names workspaces but shows no stats"
  evidence: src/cairn/cli/core.py:171-180 — "if list_all: ... for ws_path,
    key in sorted(reg.items()): ... click.echo(f\"  {key}  {ws_path}{mark}\")"
  status: DONE
  verify: cairn config --list
  gap: no store size, last-indexed time, or call count anywhere in the CLI
    (the launcher's FR-001 is genuinely new surface)

item Q4: "The dashboard binds exactly one store at construction time"
  evidence: src/cairn/dashboard/app.py:96-98 — "def create_app(
    db_path: str | None = None, knowledge_dir: str | None = None) -> Starlette"
  evidence: src/cairn/cli/dashboard.py — "uvicorn.run(create_app(db_path=path),
    host=host, port=port)"
  status: TODO
  verify: grep -n "create_app" src/cairn/cli/dashboard.py
  gap: every handler closes over the single db_path; per-request store
    selection (FR-003) needs a resolution seam inside the handlers (they
    already call get_read_only_db(db_path) per request)

item Q5: "Per-request read-only connections are already the pattern"
  evidence: src/cairn/dashboard/data.py:452-462 — "def get_read_only_db(
    db_path: str | None = None) -> sqlite3.Connection: ... return get_db(
    db_path, read_only=True)" — called per request in every handler
  status: DONE
  verify: grep -n "get_read_only_db" src/cairn/dashboard/app.py | wc -l
  gap: None mechanically — switching means choosing which path each request
    passes, not changing connection lifecycle

item Q6: "Health/indexed-time data the overview needs per store"
  evidence: src/cairn/dashboard/data.py:183-193 — get_health reads
    build_runs newest started_at (last-indexed proxy) + db file size via
    os.stat(db_path).st_size
  evidence: src/cairn/dashboard/data.py:345-354 — tool_metrics COUNT/SUM
    aggregates (call count source)
  status: DONE
  verify: grep -n "build_runs\|st_size" src/cairn/dashboard/data.py
  gap: None per store; composing N stores under a render budget is the new
    work (FR-005)

item Q7: "Read-only guard suite to extend for listed stores"
  evidence: tests/test_dashboard_readonly.py — exists, guards the
    dashboard's no-write discipline
  status: DONE
  verify: uv run pytest tests/test_dashboard_readonly.py -q
  gap: extension must cover stores that are only listed/probed, never
    opened for views (FR-004's "visited or listed")

item Q8: "Probe cost reality on this machine"
  evidence: this session — 2 registered stores; ~/.cairn/71e4dcfee8d29b5a/.kg
    is a single 51 MB file (os stat); store dirs are tiny next to ~/.cairn/lib
  status: PARTIAL
  verify: du -sh ~/.cairn/* | sort -h
  gap: no 200-store machine exists locally — FR-005's budget is proven on
    synthesized stores (spec's headroom scenario, grounded in the observed
    227-entry test leak)
```

## Supporting evidence

```
Resolution order for the current workspace (verified this session):
- resolve_store() walks CAIRN_WORKSPACE env -> registered ancestor -> cwd
  (src/cairn/paths.py docstring); the launcher's per-request selection
  extends this with an explicit override (the clicked workspace)

WAL caution for probing (verified pattern knowledge from repo history):
- stores are WAL-mode SQLite; mode=ro reads do not write, but a probe
  strategy must still never create -wal/-shm sidecars on closed stores —
  size probing uses os.stat on the files, not connection opens, where
  possible
```

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
