"""WI-3: test-labeling in impact analysis.

Tests are in the graph as ordinary symbols (no `test` kind; verified
2026-07-30). ``impact_analysis`` already visits them; WI-3 labels and isolates
them into an ``Affected tests`` section so the caller sees "what to run".
"""
from __future__ import annotations

import pytest

from codegraph.graph.tests import is_test_symbol, filter_tests
from codegraph.mcp_server import tools_graph


def _row(conn, table, **cols):
    keys = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({placeholders})", list(cols.values()))


# --- unit tests for the detection heuristic ---

def test_path_signal_kotlin_test_root():
    r = is_test_symbol("/repo/be-sdk/lib/src/test/java/RepoTest.kt", "setUp", "RepoTest.setUp")
    assert r["is_test"] and "path" in r["detection_method"]


def test_path_signal_pytest_file():
    r = is_test_symbol("/repo/svc/tests/test_api.py", "test_create", "test_api.test_create")
    assert r["is_test"]


def test_name_signal_suffixed_class():
    # No test path (and NOT under src/main), symbol name ends in Test ->
    # detected. (detection_method may be 'path+name' since the filename also
    # contains 'Test.kt'; the point is it IS flagged outside src/main.)
    r = is_test_symbol("/repo/lib/Thing.kt", "ThingTest", "")
    assert r["is_test"]


def test_not_a_test_production_symbol():
    r = is_test_symbol("/repo/be-sdk/lib/src/main/java/Repo.kt", "create", "Repo.create")
    assert not r["is_test"] and r["detection_method"] == ""


def test_name_signal_does_not_false_match_update():
    # "LatestUpdate" must NOT match (suffix is "Update", not "Test"/"Spec").
    r = is_test_symbol("/repo/src/main/Update.kt", "LatestUpdate", "")
    assert not r["is_test"]


def test_production_file_named_test_is_not_a_test():
    """Audit fix (2026-07-30): a file named `Test.kt` under `src/main/` is a
    production class, not a test. Pre-fix this was a false positive -- 7 real
    customer-android files like `xyz.be.delivery.util.Test` were mislabeled."""
    r = is_test_symbol(
        "/repo/beCustomer/src/main/java/xyz/be/delivery/util/Test.kt", "doThing", "Test.doThing"
    )
    assert not r["is_test"], "src/main/.../Test.kt is production, not a test"


def test_abtest_class_in_main_is_not_a_test():
    """An A/B-test class in src/main is production infra, not a unit test.
    Borderline, but it must not pollute the 'run these to verify' section."""
    r = is_test_symbol(
        "/repo/beCustomer/src/main/java/xyz/be/delivery/PadBookingABTest.kt",
        "PadBookingABTest", "",
    )
    assert not r["is_test"]


def test_real_test_file_in_main_with_testpath_dir_still_detected():
    """If a file is BOTH in src/main AND under a real test dir pattern (unusual
    but possible), the strong signal wins and it IS a test."""
    r = is_test_symbol(
        "/repo/src/main/tests/ThingTest.kt", "ThingTest", "ThingTest"
    )
    assert r["is_test"]


def test_filter_tests_isolates_and_annotates():
    impacted = [
        {"symbol": "Repo.create", "file": "/src/main/Repo.kt", "repo": "r", "depth": 1},
        {"symbol": "setUp", "file": "/src/test/RepoTest.kt", "repo": "r", "depth": 2},
        {"symbol": "test_create", "file": "/tests/test_api.py", "repo": "r", "depth": 3},
    ]
    tests = filter_tests(impacted)
    assert len(tests) == 2
    assert all("detection_method" in t for t in tests)
    assert {t["symbol"] for t in tests} == {"setUp", "test_create"}


# --- integration: impact_analysis surfaces the section ---

@pytest.fixture
def _patched_conn(fresh_db, monkeypatch):
    monkeypatch.setattr(tools_graph, "_conn", lambda: fresh_db)
    return fresh_db


def _seed_test_caller_graph(conn):
    """A production symbol `doThing` called by a test `doThingTest` in a test path."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'be-sdk', '/repo')")
    _row(conn, "files", id="f1", repo_id="r1", path="/repo/lib/src/main/kt/Target.kt", language="kotlin")
    _row(conn, "files", id="f2", repo_id="r1", path="/repo/lib/src/test/kt/TargetTest.kt", language="kotlin")
    _row(conn, "files", id="f3", repo_id="r1", path="/repo/lib/src/main/kt/Caller.kt", language="kotlin")
    _row(conn, "symbols", id="s_target", file_id="f1", name="doThing", qualified_name="Target.doThing",
         kind="function", line_start=5, line_end=8)
    _row(conn, "symbols", id="s_test", file_id="f2", name="doThingTest", qualified_name="TargetTest.doThingTest",
         kind="function", line_start=12, line_end=20)
    _row(conn, "symbols", id="s_caller", file_id="f3", name="callIt", qualified_name="Caller.callIt",
         kind="function", line_start=3, line_end=6)
    # test calls target; production caller also calls target.
    _row(conn, "edges", id="e1", source_id="s_test", target_id="s_target", target_name=None,
         kind="call", line=14, column=4)
    _row(conn, "edges", id="e2", source_id="s_caller", target_id="s_target", target_name=None,
         kind="call", line=4, column=4)
    conn.commit()


def test_impact_analysis_surfaces_affected_tests(_patched_conn):
    _seed_test_caller_graph(_patched_conn)

    result = tools_graph.impact_analysis("doThing")

    # The MCP tool reports counts per depth (not every symbol) to stay compact;
    # WI-3 adds the Affected tests section which DOES name tests (that's its value).
    assert "Affected tests" in result
    assert "doThingTest" in result
    assert "TargetTest.kt" in result
    # Total counts both the test and the production caller.
    assert "2 total impacted" in result


def test_impact_analysis_no_test_section_when_no_tests(_patched_conn):
    """A graph with no test callers must not emit an empty Affected tests section."""
    conn = _patched_conn
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'be-sdk', '/repo')")
    _row(conn, "files", id="f1", repo_id="r1", path="/repo/lib/src/main/kt/Target.kt", language="kotlin")
    _row(conn, "files", id="f2", repo_id="r1", path="/repo/lib/src/main/kt/Caller.kt", language="kotlin")
    _row(conn, "symbols", id="s_target", file_id="f1", name="doThing", qualified_name="Target.doThing",
         kind="function", line_start=5, line_end=8)
    _row(conn, "symbols", id="s_caller", file_id="f2", name="callIt", qualified_name="Caller.callIt",
         kind="function", line_start=3, line_end=6)
    _row(conn, "edges", id="e1", source_id="s_caller", target_id="s_target", target_name=None,
         kind="call", line=4, column=4)
    conn.commit()

    result = tools_graph.impact_analysis("doThing")
    assert "Affected tests" not in result
    assert "1 total impacted" in result
