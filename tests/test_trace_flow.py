"""Tests for trace_flow (downward call-chain tracing) and the flow compass.

trace_flow is the inverse of impact_analysis: it walks callees downward from an
entry point and records the ordered call chain, rather than walking callers
upward into a flat set. These tests cover the chain shape, cycle handling,
branch/leaf detection, and the end-to-end `cg compass flow` CLI path.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from click.testing import CliRunner

from codegraph.cli import main
from codegraph.graph.traversal import trace_flow


# ─── helpers (same pattern as test_impact_test_labeling.py) ────────────────

def _row(conn, table, **cols):
    keys = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({placeholders})", list(cols.values()))


def _seed_chain(conn: sqlite3.Connection) -> None:
    """Build: checkout -> createOrder -> chargeCard -> sendReceipt
                                       -> updateInventory
    A chain with a branch point (createOrder) and two leaves.
    """
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'shop', '/repo')")
    _row(conn, "files", id="f1", repo_id="r1", path="/repo/api/checkout.py", language="python")
    _row(conn, "files", id="f2", repo_id="r1", path="/repo/billing/order.py", language="python")
    _row(conn, "files", id="f3", repo_id="r1", path="/repo/billing/card.py", language="python")
    _row(conn, "files", id="f4", repo_id="r1", path="/repo/inventory/stock.py", language="python")
    _row(conn, "symbols", id="s1", file_id="f1", name="checkout", qualified_name="api.checkout", kind="function", line_start=1, line_end=10)
    _row(conn, "symbols", id="s2", file_id="f2", name="createOrder", qualified_name="billing.createOrder", kind="function", line_start=1, line_end=10)
    _row(conn, "symbols", id="s3", file_id="f3", name="chargeCard", qualified_name="billing.chargeCard", kind="function", line_start=1, line_end=10)
    _row(conn, "symbols", id="s4", file_id="f4", name="updateInventory", qualified_name="inv.updateInventory", kind="function", line_start=1, line_end=10)
    _row(conn, "symbols", id="s5", file_id="f3", name="sendReceipt", qualified_name="billing.sendReceipt", kind="function", line_start=1, line_end=10)
    _row(conn, "edges", id="e1", source_id="s1", target_id="s2", target_name="createOrder", kind="call", line=5, column=0, resolution="exact")
    _row(conn, "edges", id="e2", source_id="s2", target_id="s3", target_name="chargeCard", kind="call", line=5, column=0, resolution="exact")
    _row(conn, "edges", id="e3", source_id="s2", target_id="s4", target_name="updateInventory", kind="call", line=6, column=0, resolution="exact")
    _row(conn, "edges", id="e4", source_id="s3", target_id="s5", target_name="sendReceipt", kind="call", line=5, column=0, resolution="exact")
    conn.commit()


def _seed_cycle(conn: sqlite3.Connection) -> None:
    """Build: a -> b -> a (mutual recursion cycle)."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'app', '/repo')")
    _row(conn, "files", id="f1", repo_id="r1", path="/repo/a.py", language="python")
    _row(conn, "files", id="f2", repo_id="r1", path="/repo/b.py", language="python")
    _row(conn, "symbols", id="s1", file_id="f1", name="funcA", qualified_name="a.funcA", kind="function", line_start=1, line_end=10)
    _row(conn, "symbols", id="s2", file_id="f2", name="funcB", qualified_name="b.funcB", kind="function", line_start=1, line_end=10)
    _row(conn, "edges", id="e1", source_id="s1", target_id="s2", target_name="funcB", kind="call", line=5, column=0, resolution="exact")
    _row(conn, "edges", id="e2", source_id="s2", target_id="s1", target_name="funcA", kind="call", line=5, column=0, resolution="exact")
    conn.commit()


# ─── trace_flow tests ──────────────────────────────────────────────────────

class TestTraceFlow:
    def test_traces_full_chain(self, fresh_db):
        _seed_chain(fresh_db)
        result = trace_flow(fresh_db, "checkout")
        names = [n["symbol"] for n in result["chain"]]
        assert "checkout" in names
        assert "createOrder" in names
        assert "chargeCard" in names
        assert "updateInventory" in names
        assert "sendReceipt" in names
        assert result["total"] == 5

    def test_entry_at_depth_zero(self, fresh_db):
        _seed_chain(fresh_db)
        result = trace_flow(fresh_db, "checkout")
        entry_node = [n for n in result["chain"] if n["symbol"] == "checkout"][0]
        assert entry_node["depth"] == 0
        assert entry_node["parent"] is None

    def test_depth_increases_downward(self, fresh_db):
        _seed_chain(fresh_db)
        result = trace_flow(fresh_db, "checkout")
        depths = {n["symbol"]: n["depth"] for n in result["chain"]}
        assert depths["createOrder"] == 1
        assert depths["chargeCard"] == 2
        assert depths["updateInventory"] == 2
        assert depths["sendReceipt"] == 3

    def test_detects_branch_points(self, fresh_db):
        _seed_chain(fresh_db)
        result = trace_flow(fresh_db, "checkout")
        branch_syms = [b["symbol"] for b in result["branches"]]
        assert "createOrder" in branch_syms
        # The branch should list both callees.
        co_branch = [b for b in result["branches"] if b["symbol"] == "createOrder"][0]
        assert "chargeCard" in co_branch["callees"]
        assert "updateInventory" in co_branch["callees"]

    def test_detects_leaves(self, fresh_db):
        _seed_chain(fresh_db)
        result = trace_flow(fresh_db, "checkout")
        # sendReceipt and updateInventory have no outgoing resolved edges.
        assert "sendReceipt" in result["leaves"]
        assert "updateInventory" in result["leaves"]

    def test_definition_file_not_call_site(self, fresh_db):
        """Each node's file is where the symbol is DEFINED, not called from."""
        _seed_chain(fresh_db)
        result = trace_flow(fresh_db, "checkout")
        files = {n["symbol"]: n["file"] for n in result["chain"]}
        # createOrder is defined in billing/order.py, even though the call
        # edge originates from checkout.py.
        assert files["createOrder"] == "/repo/billing/order.py"
        assert files["chargeCard"] == "/repo/billing/card.py"

    def test_cycle_detected(self, fresh_db):
        _seed_cycle(fresh_db)
        result = trace_flow(fresh_db, "funcA")
        assert len(result["cycles"]) >= 1
        cycle_syms = [c["symbol"] for c in result["cycles"]]
        # The back-edge target is funcA (already on path when funcB calls it).
        assert "funcA" in cycle_syms

    def test_cycle_does_not_explode(self, fresh_db):
        """A mutual-recursion cycle must terminate, not loop forever."""
        _seed_cycle(fresh_db)
        result = trace_flow(fresh_db, "funcA")
        assert result["total"] <= 3  # funcA + funcB, no infinite expansion
        assert result["truncated"] is False

    def test_no_outgoing_calls(self, fresh_db):
        """An entry with no callees returns just itself."""
        conn = fresh_db
        conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'app', '/repo')")
        _row(conn, "files", id="f1", repo_id="r1", path="/repo/leaf.py", language="python")
        _row(conn, "symbols", id="s1", file_id="f1", name="lonely", qualified_name="x.lonely", kind="function", line_start=1, line_end=10)
        conn.commit()
        result = trace_flow(conn, "lonely")
        assert result["total"] == 1
        assert result["chain"][0]["symbol"] == "lonely"
        assert result["leaves"] == []

    def test_limit_truncates(self, fresh_db):
        """A wide fan-out hits the node cap and sets truncated=True."""
        conn = fresh_db
        conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'wide', '/repo')")
        _row(conn, "files", id="f0", repo_id="r1", path="/repo/entry.py", language="python")
        _row(conn, "symbols", id="s0", file_id="f0", name="entry", qualified_name="e.entry", kind="function", line_start=1, line_end=10)
        for i in range(1, 20):
            fid = f"f{i}"
            sid = f"s{i}"
            _row(conn, "files", id=fid, repo_id="r1", path=f"/repo/m{i}.py", language="python")
            _row(conn, "symbols", id=sid, file_id=fid, name=f"m{i}", qualified_name=f"m.m{i}", kind="function", line_start=1, line_end=10)
            _row(conn, "edges", id=f"e{i}", source_id="s0", target_id=sid, target_name=f"m{i}", kind="call", line=5, column=0, resolution="exact")
        conn.commit()
        result = trace_flow(conn, "entry", limit=5)
        assert result["truncated"] is True


# ─── compass flow CLI tests ────────────────────────────────────────────────

class TestCompassFlowCLI:
    def _setup(self, tmpdir: str):
        db = str(Path(tmpdir) / "test.db")
        know = str(Path(tmpdir) / ".knowledge")
        Path(know).mkdir(parents=True, exist_ok=True)
        from codegraph.graph.schema import get_db
        conn = get_db(db)
        _seed_chain(conn)
        conn.close()
        return db, know

    def test_dry_run_traces_without_writing(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            db, know = self._setup(tmp)
            result = runner.invoke(main, ["compass", "flow", "checkout", "--db", db, "--knowledge", know, "--dry-run"])
            assert result.exit_code == 0
            assert "5 step(s)" in result.output
            assert "createOrder" in result.output
            # Nothing written in dry-run.
            assert not list(Path(know).rglob("compass/*.md"))

    def test_write_produces_critic_passed_file(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            db, know = self._setup(tmp)
            result = runner.invoke(main, ["compass", "flow", "checkout", "--db", db, "--knowledge", know])
            assert result.exit_code == 0
            assert "quality: 1.00" in result.output
            files = list(Path(know).rglob("compass/*.md"))
            assert len(files) == 1
            assert "flow-checkout" in str(files[0])

    def test_no_callees_exits_nonzero(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "test.db")
            know = str(Path(tmp) / ".knowledge")
            Path(know).mkdir(parents=True, exist_ok=True)
            from codegraph.graph.schema import get_db
            conn = get_db(db)
            conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'app', '/repo')")
            _row(conn, "files", id="f1", repo_id="r1", path="/repo/leaf.py", language="python")
            _row(conn, "symbols", id="s1", file_id="f1", name="lonely", qualified_name="x.lonely", kind="function", line_start=1, line_end=10)
            conn.commit()
            conn.close()
            result = runner.invoke(main, ["compass", "flow", "lonely", "--db", db, "--knowledge", know])
            assert result.exit_code == 1
            assert "nothing to document" in result.output

    def test_flow_compass_appears_in_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["compass", "--help"])
        assert result.exit_code == 0
        assert "flow" in result.output


# ─── flow gap detection tests ──────────────────────────────────────────────

def _seed_flows(conn: sqlite3.Connection) -> None:
    """Build a graph with 3 candidate flows of varying richness + 1 trivial.

    richFlow  -> a, b, c, d, e   (5 edges — qualifies at default threshold)
    midFlow   -> x, y, z         (3 edges — qualifies only at --min-edges 3)
    trivial   -> one             (1 edge  — never qualifies)
    """
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'app', '/repo')")
    _row(conn, "files", id="f1", repo_id="r1", path="/repo/rich.py", language="python")
    _row(conn, "files", id="f2", repo_id="r1", path="/repo/mid.py", language="python")
    _row(conn, "files", id="f3", repo_id="r1", path="/repo/trivial.py", language="python")
    # Targets (shared leaf symbols)
    for i in range(5):
        _row(conn, "files", id=f"ft{i}", repo_id="r1", path=f"/repo/leaf{i}.py", language="python")
        _row(conn, "symbols", id=f"st{i}", file_id=f"ft{i}", name=f"leaf{i}", qualified_name=f"l.leaf{i}", kind="function", line_start=1, line_end=5)
    # richFlow: 5 outgoing edges
    _row(conn, "symbols", id="sr", file_id="f1", name="richFlow", qualified_name="r.rich", kind="function", line_start=1, line_end=20)
    for i in range(5):
        _row(conn, "edges", id=f"er{i}", source_id="sr", target_id=f"st{i}", target_name=f"leaf{i}", kind="call", line=5+i, column=0, resolution="exact")
    # midFlow: 3 outgoing edges
    _row(conn, "symbols", id="sm", file_id="f2", name="midFlow", qualified_name="m.mid", kind="function", line_start=1, line_end=20)
    for i in range(3):
        _row(conn, "edges", id=f"em{i}", source_id="sm", target_id=f"st{i}", target_name=f"leaf{i}", kind="call", line=5+i, column=0, resolution="exact")
    # trivial: 1 outgoing edge
    _row(conn, "symbols", id="st", file_id="f3", name="trivial", qualified_name="t.trivial", kind="function", line_start=1, line_end=5)
    _row(conn, "edges", id="et0", source_id="st", target_id="st0", target_name="leaf0", kind="call", line=5, column=0, resolution="exact")
    conn.commit()


def _seed_name_collision(conn: sqlite3.Connection) -> None:
    """Two handleCommand methods in different files — both should be candidates."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'app', '/repo')")
    _row(conn, "files", id="f1", repo_id="r1", path="/repo/ChatVM.py", language="python")
    _row(conn, "files", id="f2", repo_id="r1", path="/repo/HomeVM.py", language="python")
    for i in range(5):
        _row(conn, "files", id=f"ft{i}", repo_id="r1", path=f"/repo/leaf{i}.py", language="python")
        _row(conn, "symbols", id=f"st{i}", file_id=f"ft{i}", name=f"leaf{i}", qualified_name=f"l.leaf{i}", kind="function", line_start=1, line_end=5)
    _row(conn, "symbols", id="s1", file_id="f1", name="handleCommand", qualified_name="Chat.handleCommand", kind="method", line_start=1, line_end=20)
    _row(conn, "symbols", id="s2", file_id="f2", name="handleCommand", qualified_name="Home.handleCommand", kind="method", line_start=1, line_end=20)
    for i in range(5):
        _row(conn, "edges", id=f"e1{i}", source_id="s1", target_id=f"st{i}", target_name=f"leaf{i}", kind="call", line=5+i, column=0, resolution="exact")
        _row(conn, "edges", id=f"e2{i}", source_id="s2", target_id=f"st{i}", target_name=f"leaf{i}", kind="call", line=5+i, column=0, resolution="exact")
    conn.commit()


class TestDetectFlowGaps:
    def test_finds_rich_flows(self, fresh_db):
        from codegraph.compass.flow_gaps import detect_flow_gaps
        from codegraph.okf.bundle import OKFBundle
        import tempfile
        know = tempfile.mkdtemp()
        bundle = OKFBundle(know)
        _seed_flows(fresh_db)
        result = detect_flow_gaps(fresh_db, bundle, min_edges=5)
        names = [e["name"] for e in result["uncovered"]]
        assert "richFlow" in names
        assert "midFlow" not in names   # only 3 edges, below threshold
        assert "trivial" not in names   # only 1 edge

    def test_min_edges_threshold_filters(self, fresh_db):
        from codegraph.compass.flow_gaps import detect_flow_gaps
        from codegraph.okf.bundle import OKFBundle
        import tempfile
        know = tempfile.mkdtemp()
        bundle = OKFBundle(know)
        _seed_flows(fresh_db)
        # At threshold 3, midFlow qualifies too.
        result = detect_flow_gaps(fresh_db, bundle, min_edges=3)
        names = [e["name"] for e in result["uncovered"]]
        assert "richFlow" in names
        assert "midFlow" in names
        assert "trivial" not in names  # still below

    def test_sorted_by_richest_first(self, fresh_db):
        from codegraph.compass.flow_gaps import detect_flow_gaps
        from codegraph.okf.bundle import OKFBundle
        import tempfile
        know = tempfile.mkdtemp()
        bundle = OKFBundle(know)
        _seed_flows(fresh_db)
        result = detect_flow_gaps(fresh_db, bundle, min_edges=3)
        edges = [e["out_edges"] for e in result["uncovered"]]
        assert edges == sorted(edges, reverse=True)
        assert edges[0] == 5  # richFlow first

    def test_coverage_marking(self, fresh_db, tmp_path):
        """A flow with an existing compass/flow-* file is marked covered."""
        from codegraph.compass.flow_gaps import detect_flow_gaps
        from codegraph.okf.bundle import OKFBundle
        from codegraph.okf.concept import OKFConcept
        know = str(tmp_path / ".knowledge")
        bundle = OKFBundle(know)
        # Write a flow compass for richFlow.
        concept = OKFConcept(
            type="Compass", title="Flow: richFlow", resource="richFlow",
            concept_id="compass/flow-richFlow", body="# test\n",
        )
        bundle.write_concept(concept)
        _seed_flows(fresh_db)
        result = detect_flow_gaps(fresh_db, bundle, min_edges=3)
        uncovered_names = [e["name"] for e in result["uncovered"]]
        covered_names = [e["name"] for e in result["covered"]]
        assert "richFlow" not in uncovered_names
        assert "richFlow" in covered_names
        assert "midFlow" in uncovered_names

    def test_name_collision_distinct_entries(self, fresh_db):
        """Two handleCommand methods in different files are distinct candidates."""
        from codegraph.compass.flow_gaps import detect_flow_gaps
        from codegraph.okf.bundle import OKFBundle
        import tempfile
        know = tempfile.mkdtemp()
        bundle = OKFBundle(know)
        _seed_name_collision(fresh_db)
        result = detect_flow_gaps(fresh_db, bundle, min_edges=5)
        handle_cmds = [e for e in result["uncovered"] if e["name"] == "handleCommand"]
        assert len(handle_cmds) == 2
        # They come from different files.
        files = {e["file"] for e in handle_cmds}
        assert "/repo/ChatVM.py" in files
        assert "/repo/HomeVM.py" in files
        # Collision-safe resources are distinct.
        resources = {e["resource"] for e in handle_cmds}
        assert len(resources) == 2
        assert all(e["colliding"] for e in handle_cmds)

    def test_collision_coverage_independent(self, fresh_db, tmp_path):
        """Documenting one handleCommand doesn't mark the other as covered."""
        from codegraph.compass.flow_gaps import detect_flow_gaps
        from codegraph.okf.bundle import OKFBundle
        from codegraph.okf.concept import OKFConcept
        know = str(tmp_path / ".knowledge")
        bundle = OKFBundle(know)
        # Document only the ChatVM handleCommand.
        bundle.write_concept(OKFConcept(
            type="Compass", title="Flow: handleCommand (ChatVM)",
            resource="handleCommand#ChatVM.py",
            concept_id="compass/flow-handleCommand-ChatVM-py", body="# test\n",
        ))
        _seed_name_collision(fresh_db)
        result = detect_flow_gaps(fresh_db, bundle, min_edges=5)
        handle_cmds = [e for e in result["uncovered"] if e["name"] == "handleCommand"]
        covered_hc = [e for e in result["covered"] if e["name"] == "handleCommand"]
        # Only the documented one is covered; the other is still uncovered.
        assert len(covered_hc) == 1
        assert len(handle_cmds) == 1

    def test_empty_graph(self, fresh_db):
        from codegraph.compass.flow_gaps import detect_flow_gaps
        from codegraph.okf.bundle import OKFBundle
        import tempfile
        know = tempfile.mkdtemp()
        bundle = OKFBundle(know)
        result = detect_flow_gaps(fresh_db, bundle, min_edges=5)
        assert result["uncovered"] == []
        assert result["covered"] == []


class TestFlowGapsCLI:
    def test_flow_gaps_lists_uncovered(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "test.db")
            know = str(Path(tmp) / ".knowledge")
            Path(know).mkdir(parents=True, exist_ok=True)
            from codegraph.graph.schema import get_db
            conn = get_db(db)
            _seed_flows(conn)
            conn.close()
            result = runner.invoke(main, ["compass", "flow-gaps", "--min-edges", "3", "--db", db, "--knowledge", know])
            assert result.exit_code == 0
            assert "richFlow" in result.output
            assert "midFlow" in result.output
            assert "undocumented" in result.output

    def test_flow_gaps_all_documented(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "test.db")
            know = str(Path(tmp) / ".knowledge")
            Path(know).mkdir(parents=True, exist_ok=True)
            from codegraph.graph.schema import get_db
            from codegraph.okf.bundle import OKFBundle
            from codegraph.okf.concept import OKFConcept
            conn = get_db(db)
            _seed_flows(conn)
            conn.close()
            # Document both qualifying flows.
            bundle = OKFBundle(know)
            for name in ("richFlow", "midFlow"):
                bundle.write_concept(OKFConcept(
                    type="Compass", title=f"Flow: {name}", resource=name,
                    concept_id=f"compass/flow-{name}", body="# test\n",
                ))
            result = runner.invoke(main, ["compass", "flow-gaps", "--min-edges", "3", "--db", db, "--knowledge", know])
            assert result.exit_code == 0
            assert "All candidate flows are documented" in result.output

    def test_flow_gaps_appears_in_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["compass", "--help"])
        assert result.exit_code == 0
        assert "flow-gaps" in result.output


# ─── ID-based tracing tests ────────────────────────────────────────────────

class TestTraceFlowByID:
    def test_entry_id_traces_correct_symbol(self, fresh_db):
        """entry_id disambiguates two symbols with the same name."""
        from codegraph.graph.traversal import find_definition_by_id, trace_flow
        _seed_name_collision(fresh_db)
        # Both symbols are named handleCommand; find both IDs.
        rows = fresh_db.execute(
            "SELECT id, file_id FROM symbols WHERE name = 'handleCommand'"
        ).fetchall()
        assert len(rows) == 2
        # Trace from the ChatVM one specifically.
        chatvm_id = [r["id"] for r in rows if r["file_id"] == "f1"][0]
        result = trace_flow(fresh_db, "handleCommand", entry_id=chatvm_id)
        # The entry node should be from ChatVM.py.
        entry_node = result["chain"][0]
        assert entry_node["symbol"] == "handleCommand"
        assert "ChatVM.py" in entry_node["file"]

    def test_find_definition_by_id_returns_correct_row(self, fresh_db):
        from codegraph.graph.traversal import find_definition_by_id
        _seed_name_collision(fresh_db)
        rows = fresh_db.execute("SELECT id FROM symbols WHERE name = 'handleCommand'").fetchall()
        for r in rows:
            found = find_definition_by_id(fresh_db, r["id"])
            assert len(found) == 1
            assert found[0]["id"] == r["id"]
            assert found[0]["name"] == "handleCommand"

    def test_entry_id_nonexistent_returns_empty(self, fresh_db):
        from codegraph.graph.traversal import find_definition_by_id
        assert find_definition_by_id(fresh_db, "nonexistent-id") == []


# ─── batch generation (--generate) tests ──────────────────────────────────

class TestFlowGapsGenerate:
    def test_generate_writes_compass_files(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "test.db")
            know = str(Path(tmp) / ".knowledge")
            Path(know).mkdir(parents=True, exist_ok=True)
            from codegraph.graph.schema import get_db
            conn = get_db(db)
            _seed_flows(conn)
            conn.close()
            result = runner.invoke(main, [
                "compass", "flow-gaps", "--generate", "--min-edges", "3",
                "--db", db, "--knowledge", know,
            ])
            assert result.exit_code == 0
            assert "Batch complete" in result.output
            assert "generated" in result.output
            # Files should be written.
            files = list(Path(know).rglob("compass/flow-*.md"))
            assert len(files) >= 2  # richFlow + midFlow

    def test_generate_limit_caps_count(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "test.db")
            know = str(Path(tmp) / ".knowledge")
            Path(know).mkdir(parents=True, exist_ok=True)
            from codegraph.graph.schema import get_db
            conn = get_db(db)
            _seed_flows(conn)
            conn.close()
            result = runner.invoke(main, [
                "compass", "flow-gaps", "--generate", "--min-edges", "3",
                "--limit", "1", "--db", db, "--knowledge", know,
            ])
            assert result.exit_code == 0
            assert "Generating 1 flow compass(es)" in result.output
            files = list(Path(know).rglob("compass/flow-*.md"))
            assert len(files) == 1

    def test_generate_dry_run_writes_nothing(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "test.db")
            know = str(Path(tmp) / ".knowledge")
            Path(know).mkdir(parents=True, exist_ok=True)
            from codegraph.graph.schema import get_db
            conn = get_db(db)
            _seed_flows(conn)
            conn.close()
            result = runner.invoke(main, [
                "compass", "flow-gaps", "--generate", "--dry-run",
                "--min-edges", "3", "--db", db, "--knowledge", know,
            ])
            assert result.exit_code == 0
            assert "nothing will be written" in result.output
            files = list(Path(know).rglob("compass/flow-*.md"))
            assert len(files) == 0


# ─── flow-to-workflow bridge tests ─────────────────────────────────────────

class TestFlowToWorkflow:
    def _facts(self):
        """Build a minimal facts dict mimicking _gather_flow_facts output."""
        return {
            "entry": "checkout",
            "total_steps": 4,
            "truncated": False,
            "chain": [],
            "chain_raw": [
                {"symbol": "checkout", "kind": "function", "file": "api/checkout.py",
                 "repo": "r1", "depth": 0, "parent": None},
                {"symbol": "createOrder", "kind": "function", "file": "billing/order.py",
                 "repo": "r1", "depth": 1, "parent": "checkout"},
                {"symbol": "chargeCard", "kind": "function", "file": "billing/card.py",
                 "repo": "r1", "depth": 2, "parent": "createOrder"},
                {"symbol": "sendReceipt", "kind": "function", "file": "billing/card.py",
                 "repo": "r1", "depth": 3, "parent": "chargeCard"},
            ],
            "branches": [{"symbol": "createOrder", "callees": ["chargeCard", "updateInventory"]}],
            "leaves": ["sendReceipt"],
            "modules": ["r1/api", "r1/billing"],
            "cycles": [],
        }

    def test_converts_chain_to_steps(self):
        from codegraph.knowledge.workflow import flow_to_workflow
        steps = flow_to_workflow(self._facts())
        assert len(steps) == 4
        assert steps[0]["name"] == "checkout"
        assert steps[1]["name"] == "createOrder"

    def test_step_has_symbol_and_file(self):
        from codegraph.knowledge.workflow import flow_to_workflow
        steps = flow_to_workflow(self._facts())
        for step in steps:
            assert "symbol" in step
            assert "file" in step
        assert steps[1]["symbol"] == "createOrder"
        assert steps[1]["file"] == "billing/order.py"

    def test_entry_step_labeled(self):
        from codegraph.knowledge.workflow import flow_to_workflow
        steps = flow_to_workflow(self._facts())
        assert "Entry point" in steps[0]["description"]

    def test_branch_annotated(self):
        from codegraph.knowledge.workflow import flow_to_workflow
        steps = flow_to_workflow(self._facts())
        create_order = [s for s in steps if s["name"] == "createOrder"][0]
        assert "branches to" in create_order["description"]
        assert "chargeCard" in create_order["description"]

    def test_leaf_annotated(self):
        from codegraph.knowledge.workflow import flow_to_workflow
        steps = flow_to_workflow(self._facts())
        receipt = [s for s in steps if s["name"] == "sendReceipt"][0]
        assert "terminal" in receipt["description"].lower()

    def test_parent_in_description(self):
        from codegraph.knowledge.workflow import flow_to_workflow
        steps = flow_to_workflow(self._facts())
        charge = [s for s in steps if s["name"] == "chargeCard"][0]
        assert "called by" in charge["description"]
        assert "createOrder" in charge["description"]

    def test_max_steps_caps_output(self):
        from codegraph.knowledge.workflow import flow_to_workflow
        steps = flow_to_workflow(self._facts(), max_steps=2)
        assert len(steps) == 3  # 2 steps + 1 truncation notice
        assert "truncated" in steps[-1]["name"].lower()

    def test_empty_chain_returns_empty(self):
        from codegraph.knowledge.workflow import flow_to_workflow
        steps = flow_to_workflow({"chain_raw": [], "entry": "x"})
        assert steps == []


class TestFlowWorkflowEndToEnd:
    def test_generate_flow_workflow_writes_searchable_doc(self, tmp_path):
        """generate_flow_workflow writes a workflow that trace_workflow can read back."""
        from codegraph.compass.generator import generate_flow_workflow
        from codegraph.knowledge.workflow import trace_workflow
        from codegraph.okf.bundle import OKFBundle
        from codegraph.graph.schema import get_db

        db = str(tmp_path / "test.db")
        know = str(tmp_path / ".knowledge")
        conn = get_db(db)
        _seed_chain(conn)  # checkout -> createOrder -> chargeCard -> ...
        conn.close()

        bundle = OKFBundle(know)
        conn = get_db(db)
        cid = generate_flow_workflow("checkout", conn, bundle, max_steps=10)
        conn.close()

        assert "knowledge/workflow/" in cid
        # Trace it back — the workflow is searchable and has steps.
        result = trace_workflow(bundle, "Flow: checkout")
        assert result is not None
        assert len(result["steps"]) >= 3
        assert result["steps"][0]["name"] == "checkout"

    def test_cli_as_workflow_dry_run(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "test.db")
            know = str(Path(tmp) / ".knowledge")
            Path(know).mkdir(parents=True, exist_ok=True)
            from codegraph.graph.schema import get_db
            conn = get_db(db)
            _seed_chain(conn)
            conn.close()
            result = runner.invoke(main, [
                "compass", "flow", "checkout", "--as-workflow", "--dry-run",
                "--db", db, "--knowledge", know,
            ])
            assert result.exit_code == 0
            assert "workflow steps" in result.output
            assert "checkout" in result.output
            # Nothing written in dry-run.
            assert not list(Path(know).rglob("knowledge/workflow/*.md"))

    def test_cli_as_workflow_writes_both_docs(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "test.db")
            know = str(Path(tmp) / ".knowledge")
            Path(know).mkdir(parents=True, exist_ok=True)
            from codegraph.graph.schema import get_db
            conn = get_db(db)
            _seed_chain(conn)
            conn.close()
            result = runner.invoke(main, [
                "compass", "flow", "checkout", "--as-workflow",
                "--db", db, "--knowledge", know,
            ])
            assert result.exit_code == 0
            # Both compass and workflow files written.
            compass_files = list(Path(know).rglob("compass/flow-*.md"))
            workflow_files = list(Path(know).rglob("knowledge/workflow/*.md"))
            assert len(compass_files) >= 1
            assert len(workflow_files) >= 1


# ─── flow-synthesize task queue tests ──────────────────────────────────────

class TestFlowSynthesizeTask:
    def test_flow_task_promoted_on_critic_pass(self, fresh_db, tmp_path):
        """A passing flow-synthesize task auto-promotes to compass/flow-<entry>."""
        from codegraph.llm.tasks import create_task, claim_task, complete_task
        from codegraph.okf.bundle import OKFBundle

        conn = fresh_db
        _seed_chain(conn)  # checkout -> createOrder -> chargeCard -> ...
        know = tmp_path / ".knowledge"
        (know / "_tasks").mkdir(parents=True)
        bundle = OKFBundle(str(know))

        task = create_task(
            bundle,
            task_kind="flow-synthesize",
            resource="checkout",
            facts={"entry": "checkout", "chain_raw": [
                {"symbol": "checkout", "kind": "function", "file": "api/checkout.py",
                 "repo": "r1", "depth": 0, "parent": None},
            ]},
        )
        claim_task(bundle, task.id, "test-agent")

        # A passing result (5 flow sections, references real files)
        passing = (
            "# What Does This Flow Do?\nEntry `checkout` in `api/checkout.py`.\n"
            "# Call Sequence\n`checkout` calls `createOrder` in `billing/order.py`.\n"
            "# Failure-Prone Steps\n`createOrder` branches.\n"
            "# Modules Spanned\n`api/checkout.py`, `billing/order.py`.\n"
            "# Tribal Knowledge\nNone yet.\n"
        )
        outcome = complete_task(bundle, task.id, passing, conn=conn)

        assert outcome["promoted"] is True
        assert outcome["revised"] is False
        # The promoted concept should be at compass/flow-checkout
        flow_files = list(know.rglob("compass/flow-checkout.md"))
        assert len(flow_files) == 1

    def test_flow_revise_spawned_on_critic_fail(self, fresh_db, tmp_path):
        """A failing flow-synthesize task spawns a flow-revise task."""
        from codegraph.llm.tasks import create_task, claim_task, complete_task, list_tasks
        from codegraph.okf.bundle import OKFBundle

        conn = fresh_db
        _seed_chain(conn)
        know = tmp_path / ".knowledge"
        (know / "_tasks").mkdir(parents=True)
        bundle = OKFBundle(str(know))

        task = create_task(
            bundle,
            task_kind="flow-synthesize",
            resource="checkout",
            facts={"entry": "checkout"},
        )
        claim_task(bundle, task.id, "test-agent")

        # A failing result (hallucinated file)
        failing = "# What Does This Flow Do?\nSee `nonexistent/file.py`.\n"
        outcome = complete_task(bundle, task.id, failing, conn=conn)

        assert outcome["promoted"] is False
        assert outcome["revised"] is True

        # A flow-revise task should be queued
        pending = list_tasks(bundle, status="pending")
        revise_kinds = [t.task_kind for t in pending]
        assert "flow-revise" in revise_kinds

    def test_cli_flow_use_llm_queues_task(self):
        """cg compass flow <entry> --use-llm creates a flow-synthesize task."""
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "test.db")
            know = str(Path(tmp) / ".knowledge")
            Path(know).mkdir(parents=True, exist_ok=True)
            from codegraph.graph.schema import get_db
            conn = get_db(db)
            _seed_chain(conn)
            conn.close()
            result = runner.invoke(main, [
                "compass", "flow", "checkout", "--use-llm",
                "--db", db, "--knowledge", know,
            ])
            assert result.exit_code == 0
            assert "Queued flow task" in result.output
            assert "cg task show" in result.output
            # Task file should exist
            task_files = list(Path(know).rglob("_tasks/*.md"))
            assert len(task_files) >= 1


# ─── MCP tool tests ────────────────────────────────────────────────────────

class TestMCPFlowTools:
    def test_trace_flow_mcp_tool(self, fresh_db):
        """The MCP trace_flow tool returns formatted call chain text."""
        _seed_chain(fresh_db)
        # Import the tool function directly (it's registered on the mcp instance
        # but the function itself is callable).
        from codegraph.mcp_server.tools_compass import trace_flow
        # Monkeypatch _conn to return our test conn (the tool uses _conn()).
        import codegraph.mcp_server.tools_compass as tc
        orig_conn = tc._conn
        tc._conn = lambda: fresh_db
        try:
            result = trace_flow("checkout")
        finally:
            tc._conn = orig_conn
        assert "checkout" in result
        assert "createOrder" in result
        assert "step" in result.lower() or "traced" in result.lower()

    def test_generate_flow_mcp_tool(self, fresh_db, tmp_path):
        """The MCP generate_flow tool writes a compass file."""
        _seed_chain(fresh_db)
        from codegraph.mcp_server import tools_compass as tc
        from codegraph.okf.bundle import OKFBundle
        know = str(tmp_path / ".knowledge")
        Path(know).mkdir(parents=True, exist_ok=True)
        bundle = OKFBundle(know)
        orig_conn = tc._rw_conn
        orig_bundle = tc._bundle
        tc._rw_conn = lambda: fresh_db
        tc._bundle = lambda: bundle
        try:
            result = tc.generate_flow("checkout")
        finally:
            tc._rw_conn = orig_conn
            tc._bundle = orig_bundle
        assert "Compass" in result
        assert "compass/flow-checkout" in result
        # File should be written
        files = list(Path(know).rglob("compass/flow-checkout.md"))
        assert len(files) == 1

    def test_generate_flow_mcp_with_workflow(self, fresh_db, tmp_path):
        """The MCP generate_flow tool with as_workflow writes both docs."""
        _seed_chain(fresh_db)
        from codegraph.mcp_server import tools_compass as tc
        from codegraph.okf.bundle import OKFBundle
        know = str(tmp_path / ".knowledge")
        Path(know).mkdir(parents=True, exist_ok=True)
        bundle = OKFBundle(know)
        orig_conn = tc._rw_conn
        orig_bundle = tc._bundle
        tc._rw_conn = lambda: fresh_db
        tc._bundle = lambda: bundle
        try:
            result = tc.generate_flow("checkout", as_workflow=True)
        finally:
            tc._rw_conn = orig_conn
            tc._bundle = orig_bundle
        assert "Compass" in result
        assert "Workflow" in result
        compass_files = list(Path(know).rglob("compass/flow-checkout.md"))
        workflow_files = list(Path(know).rglob("knowledge/workflow/*.md"))
        assert len(compass_files) == 1
        assert len(workflow_files) == 1


# ─── workflow staleness + sync tests ───────────────────────────────────────

class TestWorkflowStaleness:
    def test_fresh_workflow_not_stale(self, fresh_db, tmp_path):
        """A workflow whose steps all resolve is not stale."""
        from codegraph.knowledge.workflow import check_workflow_staleness, add_workflow
        from codegraph.okf.bundle import OKFBundle
        _seed_chain(fresh_db)  # checkout -> createOrder -> chargeCard -> ...
        bundle = OKFBundle(str(tmp_path / ".knowledge"))
        add_workflow(bundle, "Test Flow", steps=[
            {"name": "checkout", "symbol": "checkout", "file": "/repo/api/checkout.py"},
            {"name": "createOrder", "symbol": "createOrder", "file": "/repo/billing/order.py"},
        ])
        report = check_workflow_staleness(fresh_db, bundle, "Test Flow")
        assert report is not None
        assert report["stale_count"] == 0
        assert report["total_steps"] == 2

    def test_stale_symbol_detected(self, fresh_db, tmp_path):
        """A step referencing a non-existent symbol is flagged."""
        from codegraph.knowledge.workflow import check_workflow_staleness, add_workflow
        from codegraph.okf.bundle import OKFBundle
        _seed_chain(fresh_db)
        bundle = OKFBundle(str(tmp_path / ".knowledge"))
        add_workflow(bundle, "Stale Flow", steps=[
            {"name": "checkout", "symbol": "checkout", "file": "/repo/api/checkout.py"},
            {"name": "renamedMethod", "symbol": "renamedMethod", "file": "/repo/billing/order.py"},
        ])
        report = check_workflow_staleness(fresh_db, bundle, "Stale Flow")
        assert report["stale_count"] == 1
        assert report["stale_details"][0]["step"] == "renamedMethod"
        assert report["stale_details"][0]["symbol_ok"] is False

    def test_stale_file_detected(self, fresh_db, tmp_path):
        """A step referencing a non-existent file is flagged."""
        from codegraph.knowledge.workflow import check_workflow_staleness, add_workflow
        from codegraph.okf.bundle import OKFBundle
        _seed_chain(fresh_db)
        bundle = OKFBundle(str(tmp_path / ".knowledge"))
        add_workflow(bundle, "Stale File Flow", steps=[
            {"name": "checkout", "symbol": "checkout", "file": "/repo/moved/elsewhere.py"},
        ])
        report = check_workflow_staleness(fresh_db, bundle, "Stale File Flow")
        assert report["stale_count"] == 1
        assert report["stale_details"][0]["file_ok"] is False

    def test_check_all_workflows(self, fresh_db, tmp_path):
        """Batch check returns only stale workflows, sorted by stale count."""
        from codegraph.knowledge.workflow import check_all_workflows, add_workflow
        from codegraph.okf.bundle import OKFBundle
        _seed_chain(fresh_db)
        bundle = OKFBundle(str(tmp_path / ".knowledge"))
        # Fresh workflow
        add_workflow(bundle, "Fresh", steps=[
            {"name": "checkout", "symbol": "checkout", "file": "/repo/api/checkout.py"},
        ])
        # Stale workflow
        add_workflow(bundle, "Stale", steps=[
            {"name": "gone1", "symbol": "gone1", "file": "/repo/x.py"},
            {"name": "gone2", "symbol": "gone2", "file": "/repo/y.py"},
        ])
        reports = check_all_workflows(fresh_db, bundle)
        assert len(reports) == 1  # only the stale one
        assert reports[0]["title"] == "Stale"
        assert reports[0]["stale_count"] == 2


class TestWorkflowSync:
    def test_sync_updates_steps(self, fresh_db, tmp_path):
        """sync_workflow re-traces and rebuilds steps from current graph."""
        from codegraph.knowledge.workflow import sync_workflow, trace_workflow, add_workflow
        from codegraph.okf.bundle import OKFBundle
        _seed_chain(fresh_db)
        bundle = OKFBundle(str(tmp_path / ".knowledge"))
        # Create a workflow with a stale step
        add_workflow(bundle, "Flow: checkout", steps=[
            {"name": "checkout", "symbol": "checkout", "file": "/repo/api/checkout.py"},
            {"name": "oldStep", "symbol": "oldStep", "file": "/repo/old.py"},
        ], resource="checkout")
        # Sync it
        result = sync_workflow(fresh_db, bundle, "Flow: checkout")
        assert result is not None
        assert result["error"] is None
        assert result["old_step_count"] == 2
        # New steps should come from the real trace (checkout -> createOrder -> ...)
        assert result["new_step_count"] >= 3
        assert "createOrder" in result["added"] or result["new_step_count"] > 2
        # Verify the stored workflow now has fresh steps
        traced = trace_workflow(bundle, "Flow: checkout")
        names = [s["name"] for s in traced["steps"]]
        assert "checkout" in names
        assert "createOrder" in names
        assert "oldStep" not in names

    def test_sync_handles_missing_entry(self, fresh_db, tmp_path):
        """If the entry symbol was removed, sync reports error without writing."""
        from codegraph.knowledge.workflow import sync_workflow, add_workflow
        from codegraph.okf.bundle import OKFBundle
        _seed_chain(fresh_db)
        bundle = OKFBundle(str(tmp_path / ".knowledge"))
        add_workflow(bundle, "Flow: ghost", steps=[
            {"name": "ghost", "symbol": "ghost", "file": "/repo/ghost.py"},
        ], resource="nonexistentEntry")
        result = sync_workflow(fresh_db, bundle, "Flow: ghost")
        assert result is not None
        assert result["error"] is not None
        assert "no longer traces" in result["error"]

    def test_cli_sync_dry_run(self):
        """cg knowledge workflow sync --dry-run reports staleness without writing."""
        from codegraph.knowledge.workflow import add_workflow
        from codegraph.okf.bundle import OKFBundle
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "test.db")
            know = str(Path(tmp) / ".knowledge")
            Path(know).mkdir(parents=True, exist_ok=True)
            from codegraph.graph.schema import get_db
            conn = get_db(db)
            _seed_chain(conn)
            conn.close()
            # Create a stale workflow directly (the CLI add doesn't take --knowledge)
            bundle = OKFBundle(know)
            add_workflow(bundle, "Test Flow", steps=[
                {"name": "checkout", "symbol": "checkout", "file": "/repo/api/checkout.py"},
                {"name": "gone", "symbol": "goneSymbol", "file": "/repo/gone.py"},
            ])
            # Dry-run sync
            result = runner.invoke(main, [
                "knowledge", "workflow", "sync", "Test Flow", "--dry-run",
                "--db", db, "--knowledge", know,
            ])
            assert result.exit_code == 0
            assert "stale" in result.output.lower()
            assert "gone" in result.output

    def test_cli_sync_all(self):
        """cg knowledge workflow sync --all syncs every workflow."""
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "test.db")
            know = str(Path(tmp) / ".knowledge")
            Path(know).mkdir(parents=True, exist_ok=True)
            from codegraph.graph.schema import get_db
            conn = get_db(db)
            _seed_chain(conn)
            conn.close()
            # Create a workflow with a real entry point
            runner.invoke(main, [
                "compass", "flow", "checkout", "--as-workflow",
                "--db", db, "--knowledge", know,
            ])
            # Sync all
            result = runner.invoke(main, [
                "knowledge", "workflow", "sync", "--all",
                "--db", db, "--knowledge", know,
            ])
            assert result.exit_code == 0
            assert "Sync complete" in result.output
