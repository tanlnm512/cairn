"""Share-to-recall integration: a share made through ``cairn memory share``
surfaces in another agent's next relevant recall on both shipped read
surfaces — ``cairn memory search --agent`` and the ``recall_memory`` MCP
tool with ``agent`` set — with a ``shared by`` attribution line and no
intermediate step (no subscribe/refresh/registration/polling).

The no-``agent`` legs pin the parameter gate: the single-agent path runs no
read-through, so a share on disk never leaks into a bare search or a bare
recall.
"""
from __future__ import annotations

import re

import pytest
from click.testing import CliRunner

from cairn.graph.schema import get_db
from cairn.memory.promotion import capture_memory
from cairn.okf.bundle import OKFBundle

AGENT_A = "agentA"
AGENT_B = "agentB"


@pytest.fixture(autouse=True)
def _hash_backend(hash_backend):
    """The CLI record/search paths embed; keep them on the dep-free backend."""


@pytest.fixture
def runner():
    return CliRunner()


def _store(tmp_path):
    db_path = str(tmp_path / "g.db")
    knowledge = str(tmp_path / "k")
    conn = get_db(db_path)
    conn.close()
    return db_path, knowledge


def _record(runner, db_path, knowledge, title, body):
    """Record via the shipped CLI and return the memory concept id."""
    from cairn.cli import main

    result = runner.invoke(
        main,
        ["memory", "record", "decision", title, "--body", body,
         "--db", db_path, "--knowledge", knowledge],
    )
    assert result.exit_code == 0, result.output
    m = re.search(r" -> (\S+) \(score=", result.output)
    assert m, result.output
    return m.group(1)


def _share(runner, db_path, memory_id, symbols):
    from cairn.cli import main

    return runner.invoke(
        main,
        ["memory", "share", memory_id, "--agent", AGENT_A,
         "--symbols", symbols, "--db", db_path],
    )


def _search(runner, db_path, knowledge, query, *extra):
    from cairn.cli import main

    return runner.invoke(
        main,
        ["memory", "search", query, "--db", db_path, "--knowledge", knowledge,
         *extra],
    )


def _recall(db_path, knowledge, query, monkeypatch, **kw):
    """Invoke the recall_memory MCP tool against the test store/bundle."""
    from cairn.mcp_server import tools_memory

    monkeypatch.setattr(tools_memory, "_conn", lambda: get_db(db_path))
    monkeypatch.setattr(tools_memory, "_bundle", lambda: OKFBundle(knowledge))
    return tools_memory.recall_memory(query, **kw)


def _result_rows(output):
    """Result lines only — a bare ``No memories matching '<q>'.`` echo quotes
    the query and must not count as a hit."""
    return [l for l in output.splitlines() if l.startswith("  [")]


def _rows_with(output, title):
    return [l for l in _result_rows(output) if title in l]


def _attribution_lines(output):
    return [l for l in output.splitlines() if l.strip().startswith("shared by ")]


def test_share_surfaces_in_next_agent_search(runner, tmp_path):
    """agentA records, shares to auth,session, and agentB's immediate
    ``search --agent auth`` recall surfaces the entry with attribution."""
    db_path, knowledge = _store(tmp_path)
    memory_id = _record(
        runner, db_path, knowledge, "Credential rotation cadence",
        "Rotate every 90 days. Why: blast-radius cap.",
    )

    bare = _search(runner, db_path, knowledge, "auth")
    assert bare.exit_code == 0, bare.output
    assert _result_rows(bare.output) == []

    share = _share(runner, db_path, memory_id, "auth,session")
    assert share.exit_code == 0, share.output
    assert "2 new row(s)" in share.output

    recalled = _search(runner, db_path, knowledge, "auth", "--agent", AGENT_B)
    assert recalled.exit_code == 0, recalled.output
    assert len(_rows_with(recalled.output, "Credential rotation cadence")) == 1
    # Attribution names the symbols that matched the query, not the share list.
    attrib = _attribution_lines(recalled.output)
    assert attrib == ["    shared by agentA on 'auth'"]


def test_reshare_same_symbols_no_duplicate_in_recall(runner, tmp_path):
    """Re-sharing the same (agent, symbols, memory) is a no-op and the next
    recall still shows exactly one copy."""
    db_path, knowledge = _store(tmp_path)
    memory_id = _record(
        runner, db_path, knowledge, "Sticky routing rule",
        "Pin a tenant to one node. Why: cache affinity.",
    )
    first = _share(runner, db_path, memory_id, "session,auth")
    assert first.exit_code == 0, first.output
    again = _share(runner, db_path, memory_id, "session,auth")
    assert again.exit_code == 0, again.output
    assert "Nothing new recorded" in again.output

    recalled = _search(runner, db_path, knowledge, "session auth",
                       "--agent", AGENT_B)
    assert recalled.exit_code == 0, recalled.output
    assert len(_rows_with(recalled.output, "Sticky routing rule")) == 1
    assert len(_attribution_lines(recalled.output)) == 1


def test_shared_entry_already_surfaced_carries_attribution(runner, tmp_path):
    """A shared memory the base search returns on its own still shows the
    attribution line and is never duplicated by the read-through merge."""
    db_path, knowledge = _store(tmp_path)
    memory_id = _record(
        runner, db_path, knowledge, "AuthGate retry ladder",
        "Three attempts then backoff. Why: lockout safety.",
    )
    share = _share(runner, db_path, memory_id, "AuthGate")
    assert share.exit_code == 0, share.output

    recalled = _search(runner, db_path, knowledge, "AuthGate",
                       "--agent", AGENT_B)
    assert recalled.exit_code == 0, recalled.output
    assert len(_rows_with(recalled.output, "AuthGate retry ladder")) == 1
    attrib = _attribution_lines(recalled.output)
    assert len(attrib) == 1
    assert "agentA" in attrib[0] and "AuthGate" in attrib[0]


def test_wide_symbol_list_surfaces_on_any_member(runner, tmp_path):
    """Sharing to 50 symbols carries the share on every one of them: a recall
    for the 38th symbol surfaces the entry."""
    db_path, knowledge = _store(tmp_path)
    memory_id = _record(
        runner, db_path, knowledge, "Wide net rule",
        "One entry covers the whole set. Why: symmetry.",
    )
    symbols = ",".join("sym%02d" % i for i in range(50))
    share = _share(runner, db_path, memory_id, symbols)
    assert share.exit_code == 0, share.output
    assert "50 new row(s)" in share.output

    recalled = _search(runner, db_path, knowledge, "sym37", "--agent", AGENT_B)
    assert recalled.exit_code == 0, recalled.output
    assert len(_rows_with(recalled.output, "Wide net rule")) == 1


def test_mcp_recall_with_agent_surfaces_shared_entry(runner, tmp_path,
                                                     monkeypatch):
    """agentB's recall_memory call with agent set surfaces agentA's share
    with the exact attribution line."""
    db_path, knowledge = _store(tmp_path)
    memory_id = _record(
        runner, db_path, knowledge, "Token mirror rule",
        "Mirror tokens across replicas. Why: failover speed.",
    )
    share = _share(runner, db_path, memory_id, "auth")
    assert share.exit_code == 0, share.output

    out = _recall(db_path, knowledge, "auth", monkeypatch, agent=AGENT_B)
    assert len(_rows_with(out, "Token mirror rule")) == 1
    assert "shared by agentA on 'auth'" in out


def test_mcp_recall_without_agent_hides_shared_entry(runner, tmp_path,
                                                     monkeypatch):
    """The same share stays invisible to a recall_memory call with no agent:
    the single-agent path runs no read-through."""
    db_path, knowledge = _store(tmp_path)
    memory_id = _record(
        runner, db_path, knowledge, "Quiet vault rule",
        "Unshared entries stay private. Why: namespace integrity.",
    )
    share = _share(runner, db_path, memory_id, "auth")
    assert share.exit_code == 0, share.output

    out = _recall(db_path, knowledge, "auth", monkeypatch)
    assert "Quiet vault rule" not in out
    assert "shared by" not in out


def test_capture_and_share_roundtrip_surfaces_via_both_surfaces(
    runner, tmp_path, monkeypatch,
):
    """End-to-end: capture through the store API, share through the CLI, then
    the same fixture surfaces through search --agent and recall_memory."""
    db_path, knowledge = _store(tmp_path)
    conn = get_db(db_path)
    bundle = OKFBundle(knowledge)
    result = capture_memory(
        conn, bundle, type_="workaround",
        title="Hot partition workaround",
        body="Shard before the queue fills. Why: head-of-line stall.",
        confidence=0.7,
    )
    conn.commit()
    conn.close()

    share = _share(runner, db_path, result["path"], "queue,partitions")
    assert share.exit_code == 0, share.output

    cli_out = _search(runner, db_path, knowledge, "queue", "--agent", AGENT_B)
    assert cli_out.exit_code == 0, cli_out.output
    assert len(_rows_with(cli_out.output, "Hot partition workaround")) == 1

    mcp_out = _recall(db_path, knowledge, "partitions", monkeypatch,
                      agent=AGENT_B)
    assert len(_rows_with(mcp_out, "Hot partition workaround")) == 1
    assert "shared by agentA on 'partitions'" in mcp_out
