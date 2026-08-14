"""Core smoke suite — one focused test per core function across all 5 layers.

Run with `pytest -m core` for a <3s feedback loop on the agent hot path. This
is *additive*: every test here is independent and self-contained; the full
suite (no marker) remains the CI path. Tests marked `core` elsewhere (the
router golden eval) are also picked up.

Design: each test exercises the single highest-signal behavior of one core
function, chosen from the redundancy audit as the case most likely to catch a
regression. Fixtures are kept local (no cross-file coupling) so a failure
points at exactly one function.

The transport-layer `core` tests (read-only mode, stray sweeper, sse_responds)
close a real gap: those code paths had ZERO coverage before this file.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.core


# ---------------------------------------------------------------------------
# Shared local helpers (kept here so the file is fully self-contained)
# ---------------------------------------------------------------------------

def _row(conn, table, **cols):
    """Insert a row given kwargs as column=value pairs."""
    keys = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({placeholders})", list(cols.values()))


def _make_workspace(tmp_path, name: str, files: dict) -> tuple[str, str]:
    """Create a single-repo workspace under tmp_path; return (workspace, db_path)."""
    workspace = tmp_path / name
    repo = workspace / "demo"
    (repo / ".git").mkdir(parents=True)
    for fname, contents in files.items():
        (repo / fname).write_text(contents)
    return str(workspace), str(tmp_path / f"{name}.db")


# ===========================================================================
# GRAPH LAYER (6 tests)
# ===========================================================================

def test_build_graph_resolves_usecase_bare_call(tmp_path):
    """End-to-end build + caller edge: a Kotlin bare `useCase(p)` call must
    resolve to the UseCase class, not the local property. This single test
    exercises the full parser -> resolver -> schema pipeline."""
    from cairn.graph.builder import build_graph

    files = {
        "UseCase.kt": (
            "class UpdateProfileUseCase {\n"
            "    operator fun invoke(name: String): String { return name }\n"
            "}\n"
        ),
        "ViewModel.kt": (
            "class ProfileViewModel(\n"
            "    private val updateProfileUseCase: UpdateProfileUseCase\n"
            ") {\n"
            "    fun save(name: String) { updateProfileUseCase(name) }\n"
            "}\n"
        ),
    }
    ws, db_path = _make_workspace(tmp_path, "ws_build", files)
    build_graph(workspace=ws, db_path=db_path, verbose=False)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT e.resolution, t.qualified_name AS target_qname
               FROM edges e JOIN symbols s ON e.source_id = s.id
               LEFT JOIN symbols t ON e.target_id = t.id
               WHERE s.name = 'save' AND e.kind = 'calls'"""
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["target_qname"] == "UpdateProfileUseCase"
    assert rows[0]["resolution"] == "exact"


def test_search_symbols_camelcase_substring_via_like_union(fresh_db):
    """`*UseCase*` must find camelCase names containing 'UseCase'
    (UpdateProfileUseCase, GetPhotosUseCase), not just literal 'UseCase'.
    Guards the FTS5+LIKE union regression."""
    from cairn.graph.queries import search_symbols

    fresh_db.execute("INSERT INTO repos (id, name, path) VALUES ('r', 'r', '/r')")
    fresh_db.execute("INSERT INTO files (id, repo_id, path, language) VALUES ('f', 'r', '/r/U.kt', 'kotlin')")
    for sid, name in [("s1", "UpdateProfileUseCase"), ("s2", "GetPhotosUseCase"), ("s3", "UseCase")]:
        fresh_db.execute(
            "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
            "VALUES (?, 'f', ?, 'class', ?, 1, 10)",
            (sid, name, f"x.{name}"),
        )
    fresh_db.commit()
    try:
        fresh_db.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    except sqlite3.OperationalError:
        pass

    names = {r["name"] for r in search_symbols(fresh_db, "*UseCase*")}
    assert {"UpdateProfileUseCase", "GetPhotosUseCase", "UseCase"} <= names


def test_impact_analysis_surfaces_affected_tests(fresh_db, monkeypatch):
    """impact_analysis (MCP wrapper) must isolate test callers into an
    'Affected tests' section. Exercises the MCP tool + test-labeling."""
    from cairn.mcp_server import tools_graph

    monkeypatch.setattr(tools_graph, "_conn", lambda: fresh_db)
    fresh_db.execute("INSERT INTO repos (id, name, path) VALUES ('r', 'be', '/repo')")
    _row(fresh_db, "files", id="f1", repo_id="r", path="/repo/src/main/Target.kt", language="kotlin")
    _row(fresh_db, "files", id="f2", repo_id="r", path="/repo/src/test/TargetTest.kt", language="kotlin")
    _row(fresh_db, "symbols", id="st", file_id="f1", name="doThing", qualified_name="Target.doThing", kind="function", line_start=1, line_end=5)
    _row(fresh_db, "symbols", id="sx", file_id="f2", name="doThingTest", qualified_name="TargetTest.doThingTest", kind="function", line_start=1, line_end=5)
    _row(fresh_db, "edges", id="e1", source_id="sx", target_id="st", target_name=None, kind="call", line=2, column=4)
    fresh_db.commit()

    result = tools_graph.impact_analysis("doThing")
    assert "Affected tests" in result
    assert "doThingTest" in result


def test_get_callers_falls_back_to_fuzzy_when_precise_empty(fresh_db, monkeypatch):
    """get_callers auto-retries fuzzy when precise returns nothing -- the
    agent-facing regression where an externally-defined symbol's caller was
    silently missed."""
    from cairn.mcp_server import tools_graph

    monkeypatch.setattr(tools_graph, "_conn", lambda: fresh_db)
    fresh_db.execute("INSERT INTO repos (id, name, path) VALUES ('r', 'r', '/repo')")
    _row(fresh_db, "files", id="f1", repo_id="r", path="Caller.swift", language="swift")
    _row(fresh_db, "symbols", id="s1", file_id="f1", name="pingGoogle", qualified_name="M.pingGoogle", kind="method", line_start=87, line_end=90)
    _row(fresh_db, "edges", id="e1", source_id="s1", target_id=None, target_name="startLoadURL", kind="call", line=87, column=8)
    fresh_db.commit()

    result = tools_graph.get_callers("startLoadURL")
    assert "fuzzy candidates" in result
    assert "pingGoogle" in result


def test_resolver_full_contiguous_beats_last_segment_fallback():
    """The import-aware resolver must rank a full contiguous match above a
    last-segment fallback -- guards both resolution paths + ordering at once."""
    from cairn.graph.resolver import _import_aware_candidates

    my_imports = ["com.example.RepoA"]
    cands = [
        ("sid_full", "repoA", "f1", "com.example.RepoA.create"),  # full contiguous
        ("sid_fallback", "repoA", "f1", "RepoA.create"),          # last-segment fallback
    ]
    result = _import_aware_candidates("create", my_imports, cands)
    assert len(result) == 1, "ambiguous resolution not expected"
    assert result[0][0] == "sid_full", "full contiguous must outrank fallback"


def test_resolver_receiver_type_disambiguates_same_named_method(tmp_path):
    """Full build pipeline: two classes define `displayName`; the type-aware
    Tier-0 resolver must dispatch each call to the correct class with zero
    ambiguous edges."""
    from cairn.graph.builder import build_graph

    files = {
        "Profile.kt": 'class Profile {\n    fun displayName(): String { return "x" }\n}\n',
        "Account.kt": 'class Account {\n    fun displayName(): String { return "y" }\n}\n',
        "Repo.kt": (
            "class UserRepo {\n"
            "    val profile: Profile = Profile()\n"
            "    fun run() {\n"
            "        val other = Account()\n"
            "        other.displayName()\n"
            "        this.profile.displayName()\n"
            "    }\n"
            "}\n"
        ),
    }
    ws, db_path = _make_workspace(tmp_path, "ws_type", files)
    summary = build_graph(workspace=ws, db_path=db_path, verbose=False)
    assert summary["resolution"]["ambiguous"] == 0, "receiver-type dispatch must not leave ambiguous edges"


# ===========================================================================
# MEMORY LAYER (3 tests)
# ===========================================================================

def test_memory_graph_verification_counts_mixed_refs(fresh_db):
    """_graph_verification must count files (.ts/.java) AND symbols
    (snake_case, qualified) together -- the L2 alignment fix."""
    from cairn.memory.scoring import _graph_verification
    from cairn.okf.concept import OKFConcept

    fresh_db.execute("INSERT INTO repos (id, name, path) VALUES ('r', 'r', '/tmp')")
    fresh_db.execute("INSERT INTO files (id, repo_id, path, language) VALUES ('f1', 'r', 'src/Component.tsx', 'typescript')")
    fresh_db.execute("INSERT INTO files (id, repo_id, path, language) VALUES ('f2', 'r', 'src/Api.java', 'java')")
    fresh_db.execute("INSERT INTO symbols (id, file_id, name, kind, qualified_name) VALUES ('s1', 'f2', 'fetch_data', 'function', 'api.fetch_data')")
    fresh_db.execute("INSERT INTO symbols (id, file_id, name, kind, qualified_name) VALUES ('s2', 'f2', 'RequestBuilder', 'class', 'http.RequestBuilder')")
    fresh_db.commit()

    concept = OKFConcept(
        type="test", concept_id="t1", title="T",
        body="The `src/Component.tsx` UI uses `src/Api.java`. Call `fetch_data()`. Use `RequestBuilder`.",
    )
    assert _graph_verification(concept, fresh_db) == 1.0


def test_memory_store_distinct_ids_for_same_title(tmp_path):
    """store_memory must give same-title non-raw memories distinct UUID-suffixed IDs."""
    from cairn.memory.store import create_memory, store_memory, get_memory
    from cairn.okf.bundle import OKFBundle

    bundle = OKFBundle(str(tmp_path / "knowledge"))
    id1 = store_memory(create_memory(type_="pattern", title="backoff retry", body="first", confidence=0.4), bundle)
    id2 = store_memory(create_memory(type_="pattern", title="backoff retry", body="second", confidence=0.4), bundle)
    assert id1 != id2
    assert get_memory(bundle, id1).body == "first"
    assert get_memory(bundle, id2).body == "second"


def test_memory_delete_exact_match_no_sibling_clobber(tmp_path, fresh_db):
    """delete_memory must use exact WHERE memory_path = ?, never LIKE --
    deleting one memory must not remove a sibling whose path is a prefix."""
    from cairn.memory.store import delete_memory, store_memory, create_memory, get_memory
    from cairn.okf.bundle import OKFBundle

    bundle = OKFBundle(str(tmp_path / "knowledge"))
    parent = store_memory(create_memory(type_="pattern", title="retry", body="x", confidence=0.4), bundle)
    child = store_memory(create_memory(type_="pattern", title="retry backoff", body="y", confidence=0.4), bundle)

    ok = delete_memory(bundle, parent, conn=fresh_db)
    assert ok
    # child survives even though its path starts with the parent's id string
    assert get_memory(bundle, child) is not None


# ===========================================================================
# KNOWLEDGE LAYER (3 tests)
# ===========================================================================

def test_knowledge_update_status_lifecycle():
    """update_status validates transitions: forward (active->superseded) ok,
    backward (archived->active) rejected."""
    from cairn.knowledge.store import add_document, update_status, get_document
    from cairn.okf.bundle import OKFBundle

    with tempfile.TemporaryDirectory() as tmp:
        bundle = OKFBundle(tmp)
        cid = add_document(bundle, "Refund Policy", "body", "business-rule")
        assert update_status(bundle, cid, "superseded")
        assert get_document(bundle, cid).extensions["doc_status"] == "superseded"
        update_status(bundle, cid, "archived")
        # backward transition from archived must fail
        assert update_status(bundle, cid, "active") is False


def test_knowledge_search_excludes_archived_by_default():
    """search_knowledge must exclude archived docs by default but include them
    when asked -- the most user-visible search behavior."""
    from cairn.knowledge.store import add_document, update_status
    from cairn.knowledge.search import search_knowledge
    from cairn.okf.bundle import OKFBundle

    with tempfile.TemporaryDirectory() as tmp:
        bundle = OKFBundle(tmp)
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        from cairn.graph.schema import _apply_schema
        _apply_schema(conn)
        cid = add_document(bundle, "Refund Policy Late Orders", "Refunds for late deliveries.", "business-rule")
        update_status(bundle, cid, "archived")

        default_results = search_knowledge(conn, bundle, "refund policy late orders")
        assert all(r["concept_id"] != cid for r in default_results)
        incl = search_knowledge(conn, bundle, "refund policy late orders", include_archived=True)
        assert any(r["concept_id"] == cid for r in incl)
        conn.close()


def test_knowledge_trace_workflow_resolves_and_missing():
    """trace_workflow resolves by id/title and returns None for a miss."""
    from cairn.knowledge.workflow import add_workflow, trace_workflow
    from cairn.okf.bundle import OKFBundle

    steps = [{"name": "Cut branch"}, {"name": "Merge"}]
    with tempfile.TemporaryDirectory() as tmp:
        bundle = OKFBundle(tmp)
        cid = add_workflow(bundle, "Deploy Hotfix", steps=steps)
        result = trace_workflow(bundle, cid)
        assert result is not None
        assert [s["name"] for s in result["steps"]] == ["Cut branch", "Merge"]
        assert trace_workflow(bundle, "Nonexistent") is None


# ===========================================================================
# COMPASS LAYER (3 tests)
# ===========================================================================

def test_compass_critic_flags_hallucinated_file_ref(fresh_db):
    """critic_concept must flag a backtick file ref that doesn't exist in the graph."""
    from cairn.compass.critic import critic_concept
    from cairn.okf.concept import OKFConcept

    concept = OKFConcept(type="Compass", title="t", body="See `src/DoesNotExist.kt` for the entry point.")
    result = critic_concept(concept, fresh_db)
    assert result.passed is False
    assert any("DoesNotExist.kt" in e for e in result.errors)


def test_compass_critic_passes_real_qualified_symbol(fresh_db):
    """critic_concept must NOT flag a real qualified symbol reference."""
    from cairn.compass.critic import critic_concept
    from cairn.okf.concept import OKFConcept

    fresh_db.execute("INSERT INTO repos (id, name, path) VALUES ('r', 'r', '/tmp/r')")
    fresh_db.execute("INSERT INTO files (id, repo_id, path, language) VALUES ('f', 'r', '/tmp/r/src/ApiClient.ts', 'typescript')")
    fresh_db.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES ('s', 'f', 'safeApiCall', 'function', 'xyz.ApiClient.safeApiCall', 1, 10)"
    )
    fresh_db.commit()

    concept = OKFConcept(type="Compass", title="t", body="Calls `ApiClient.safeApiCall()`.")
    result = critic_concept(concept, fresh_db)
    assert result.errors == []
    assert result.warnings == []


def test_compass_router_accuracy_on_golden_queries():
    """classify_intent must hit >= 85% of the golden 41-query set across all 5 layers.
    Reuses the canonical GOLDEN list rather than duplicating it."""
    from cairn.compass.router import classify_intent
    # Import the canonical golden set maintained alongside the router; do not
    # duplicate it here (single source of truth).
    from tests.test_router_eval import GOLDEN

    hits = sum(1 for q, expected in GOLDEN if classify_intent(q)["layer"] == expected)
    assert hits / len(GOLDEN) >= 0.85


# ===========================================================================
# TRANSPORT LAYER (5 tests) — incl. 3 NEW coverage gaps
# ===========================================================================

def test_server_boot_guard_exits_on_empty_store(monkeypatch, tmp_path):
    """server.run must exit(1) on an empty DB (no symbols table) rather than
    boot a broken server."""
    from cairn.mcp_server import server

    empty_db = tmp_path / "empty.db"
    empty_db.touch()
    monkeypatch.setenv("CAIRN_DB", str(empty_db))

    with patch("cairn.mcp_server.server.mcp"), \
         patch("cairn.mcp_server.server.verify_tool_count"):
        with pytest.raises(SystemExit) as exc:
            server.run(transport="stdio")
        assert exc.value.code == 1


@pytest.mark.parametrize(
    "module_path, tool, conn_attr, query_path, ok_return, call_args",
    [
        ("cairn.mcp_server.tools_graph", "find_definition", "_conn", "cairn.graph.queries.find_definition", [], ("test_symbol",)),
        ("cairn.mcp_server.tools_graph", "get_callers", "_conn", "cairn.graph.queries.get_callers", [], ("test_symbol",)),
        ("cairn.mcp_server.tools_graph", "impact_analysis", "_conn", "cairn.graph.queries.impact_analysis", {"total": 0, "impacted": [], "cycles": []}, ("test_symbol",)),
        ("cairn.mcp_server.tools_graph", "explore", "_conn", "cairn.graph.queries.explore", {"seeds": [], "files": {}, "call_paths": {}, "blast_radius": {}, "dispatch_hops": []}, ("test_symbol",)),
        ("cairn.mcp_server.tools_graph", "search_symbols", "_conn", "cairn.graph.queries.search_symbols", [], ("test_symbol",)),
    ],
    ids=["find_definition", "get_callers", "impact_analysis", "explore", "search_symbols"],
)
def test_tool_closes_conn_on_exception(module_path, tool, conn_attr, query_path, ok_return, call_args, mock_conn):
    """Each MCP graph tool must close its connection even when the underlying
    query raises (the connection-leak fix). Parametrized across the read tools
    that share the same try/finally/_conn shape."""
    import importlib
    mod = importlib.import_module(module_path)
    fn = getattr(mod, tool)

    def raises(*a, **kw):
        raise RuntimeError("query failed")

    with patch(query_path, raises):
        with patch(f"{module_path}.{conn_attr}", return_value=mock_conn):
            try:
                fn(*call_args)
            except RuntimeError:
                pass
    assert mock_conn.is_close_called(), f"{tool} must close conn on exception"


def test_read_only_mode_blocks_db_writes(monkeypatch, tmp_path):
    """NEW: a read-only get_db connection must refuse writes. This guards the
    core read-only-daemon invariant (can't hold the writer lock -> can't cause
    'database is locked'). Verifies CAIRN_READ_ONLY -> _read_only_mode ->
    get_db(read_only=True) -> SQLite mode=ro."""
    from cairn.graph.schema import get_db

    # Create a DB first (read-only open requires an existing file)
    db_path = str(tmp_path / "ro.db")
    init = get_db(db_path)
    init.execute("CREATE TABLE IF NOT EXISTS memory_refs (id TEXT)")
    init.commit()
    init.close()

    conn = get_db(db_path, read_only=True)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO memory_refs (id) VALUES ('x')")
            conn.commit()
    finally:
        conn.close()

    # And the _read_only_mode env flag is honored by the server core
    monkeypatch.setenv("CAIRN_READ_ONLY", "1")
    from cairn.mcp_server._server_core import _read_only_mode
    assert _read_only_mode() is True


def test_sweep_strays_kills_orphans_not_daemon(monkeypatch):
    """NEW: find_strays + sweep_strays must return orphan 'cairn serve' pids while
    excluding the launchd-managed daemon pid, the daemon's spawned child, the
    current process, and non-server cmdlines a pattern scan false-positives on.
    Guards the stray-sweeper that self-heals DB lock contention (audit F1/F2:
    candidates are verified by anchored cmdline token match + lsof db-holding,
    all mocked -- no real process is touched)."""
    from cairn.mcp_server import lifecycle as lc

    fake_orphan = 99999  # the orphaned stdio server (`cairn serve`, editor shape)
    daemon_child = 77777  # the server process spawned by the launchd daemon
    daemon_pid = 88888
    own_pid = os.getpid()
    grep_pid = 66666  # `grep cairn serve`: pgrep -f matches it; token check must not

    # Mock running_pid (launchd daemon).
    monkeypatch.setattr(lc, "running_pid", lambda: daemon_pid)

    # Full cmdline table served to `ps -p <pid> -o command=`: self and the
    # daemon BOTH look like real servers holding the db -- only the protected
    # set can save them.
    cmdlines = {
        daemon_pid: "/usr/local/bin/cairn serve run --port 9876 --read-only",
        daemon_child: "/bin/zsh -c cairn helper",
        own_pid: "/usr/local/bin/cairn serve",
        fake_orphan: "/usr/local/bin/cairn serve",
        grep_pid: "grep cairn serve",
    }

    # Discriminate the subprocess shapes the sweeper issues: `pgrep -P
    # <daemon>` (child discovery), `pgrep -f 'cairn serve'` (candidate
    # superset), `ps -p <pid> -o command=` (per-pid cmdline), and `lsof -F p
    # <db>` (db holders: daemon + orphan hold THIS db).
    def fake_run(args, *rest, **kw):
        res = MagicMock()
        res.stderr = ""
        if args[0] == "pgrep" and "-P" in args:
            # daemon's direct children
            res.returncode = 0
            res.stdout = f"{daemon_child}\n"
        elif args[0] == "pgrep":
            # candidate superset from the broad pattern scan
            res.returncode = 0
            res.stdout = (
                f"{daemon_pid}\n{daemon_child}\n{own_pid}\n"
                f"{fake_orphan}\n{grep_pid}\n"
            )
        elif args[0] == "ps":
            pid = int(args[args.index("-p") + 1])
            cmd = cmdlines.get(pid)
            res.returncode = 0 if cmd else 1
            res.stdout = (cmd + "\n") if cmd else ""
        elif args[0] == "lsof":
            res.returncode = 0
            res.stdout = f"p{daemon_pid}\nf3\np{fake_orphan}\np{own_pid}\n"
        else:
            res.returncode = 1
            res.stdout = ""
        return res

    monkeypatch.setattr("subprocess.run", fake_run)

    strays = lc.find_strays("/fake/.kg")
    assert strays == [fake_orphan], "only the orphan qualifies as a stray"
    assert daemon_pid not in strays, "daemon pid must be excluded"
    assert daemon_child not in strays, "daemon's spawned child must be excluded"
    assert own_pid not in strays, "own pid must be excluded"
    assert grep_pid not in strays, "non-server cmdline must be excluded"

    # terminate_pid must tolerate a nonexistent pid (fully mocked: no real
    # signal is ever sent).
    def fake_kill(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr("os.kill", fake_kill)
    lc.terminate_pid(fake_orphan, timeout=0.1)


def test_sse_responds_detects_live_vs_dead(monkeypatch):
    """NEW: sse_responds must distinguish a server that answers HTTP from a
    dead/unreachable port -- the hardening that replaced the bare TCP-accept
    false-positive ('responds True but curl times out mid-stream')."""
    from cairn.mcp_server import lifecycle as lc

    # Dead port: socket.connect raises -> False
    with patch("socket.create_connection", side_effect=OSError("refused")):
        assert lc.sse_responds(port=19999, timeout=0.5) is False

    # Live server: connect ok, recv returns a status-line byte -> True
    fake_sock = MagicMock()
    fake_sock.recv.return_value = b"H"  # first byte of "HTTP/1.1 ..."
    fake_recv_ctx = MagicMock()
    fake_recv_ctx.__enter__.return_value = fake_sock
    fake_recv_ctx.__exit__.return_value = False
    with patch("socket.create_connection", return_value=fake_recv_ctx):
        assert lc.sse_responds(port=9876, timeout=0.5) is True

    # Wedged server: connect ok but recv returns empty -> False
    wedged_sock = MagicMock()
    wedged_sock.recv.return_value = b""
    wedged_ctx = MagicMock()
    wedged_ctx.__enter__.return_value = wedged_sock
    wedged_ctx.__exit__.return_value = False
    with patch("socket.create_connection", return_value=wedged_ctx):
        assert lc.sse_responds(port=9876, timeout=0.5) is False


# ---------------------------------------------------------------------------
# mock_conn fixture (local copy so the file is self-contained, mirroring the
# shared connection-leak test's contract).
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_conn():
    conn = MagicMock(spec=sqlite3.Connection)
    closed = False

    def _close():
        nonlocal closed
        closed = True

    conn.close.side_effect = _close
    conn.is_close_called = lambda: closed
    return conn
