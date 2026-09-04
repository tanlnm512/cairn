"""TC-024/D-012: memory lifecycle verbs left the MCP surface but stay reachable.

The six lifecycle tools (digest, evolve, promote, demote, delete, decay) are
unregistered from the MCP server; each operation remains available as a
``cairn memory <verb>`` CLI command (delete's verb is ``forget``). The CLI
``demote`` verb opens the graph DB via ``--db`` and passes a writable conn
into ``demote_memory`` so the persisted embedding row follows the tier-move
rename instead of orphaning (D-012).
"""
from __future__ import annotations

from pathlib import Path


REMOVED_MCP_TOOLS = (
    "memory_digest",
    "memory_evolve",
    "memory_promote",
    "memory_demote",
    "memory_delete",
    "memory_decay",
)


def _invoke_cli(args: list):
    """Run the cairn CLI in-process with the module-level import deferred."""
    from click.testing import CliRunner

    from cairn.cli.main import main

    return CliRunner().invoke(main, args, catch_exceptions=False)


def _registered_mcp_tools() -> set[str]:
    """Names on the live FastMCP registry (server import registers all tools)."""
    import cairn.mcp_server.server  # noqa: F401  (registration side effects)
    from cairn.mcp_server._server_core import mcp

    return {t.name for t in mcp._tool_manager.list_tools()}


def _relative_id(bundle, concept_id: str) -> str:
    """Normalize a concept_id (may be bundle-relative or resolved absolute)."""
    path = Path(concept_id)
    try:
        return str(path.resolve().relative_to(Path(bundle.root).resolve()))
    except ValueError:
        return concept_id


# --------------------------------------------------------------------------
# TC-024: gone from MCP, alive via CLI
# --------------------------------------------------------------------------

def test_six_memory_lifecycle_tools_absent_from_mcp_registry():
    """None of the six lifecycle tools is registered; the two survivors stay."""
    tools = _registered_mcp_tools()
    assert not (set(REMOVED_MCP_TOOLS) & tools), (
        f"removed memory tools still registered: {sorted(set(REMOVED_MCP_TOOLS) & tools)}"
    )
    assert {"recall_memory", "record_memory"} <= tools


def test_memory_demote_cli_accepts_db_and_carries_embedding_row(tmp_path):
    """D-012: `cairn memory demote --db` threads a writable conn into
    demote_memory, so the memory's embedding row is renamed to the new
    concept_id instead of orphaning under the old one."""
    from cairn.graph.schema import get_db
    from cairn.memory.promotion import capture_memory
    from cairn.memory.store import list_memories
    from cairn.okf.bundle import OKFBundle

    db = tmp_path / "graph.db"
    knowledge = tmp_path / "knowledge"
    bundle = OKFBundle(str(knowledge))

    conn = get_db(str(db))
    try:
        result = capture_memory(
            conn, bundle, type_="decision", title="Demote target",
            body="body", confidence=0.8,
        )
        conn.commit()
    finally:
        conn.close()
    old_id = result["path"]

    # Pin one embedding row on the memory (its content is irrelevant to the
    # rename; an unknown model keeps read paths out of the comparison).
    conn = get_db(str(db))
    try:
        conn.execute(
            "INSERT INTO memory_embeddings (doc_id, chunk_index, model, dim, vec, chunk) "
            "VALUES (?, 0, 'test-model', 4, ?, 'body')",
            (old_id, b"\x00" * 16),
        )
        conn.commit()
    finally:
        conn.close()

    demoted = _invoke_cli([
        "memory", "demote", old_id, "--tier", "archived",
        "--db", str(db), "--knowledge", str(knowledge),
    ])
    assert demoted.exit_code == 0, demoted.output
    assert "Demoted" in demoted.output

    (archived,) = list_memories(bundle, tier="archived")
    new_id = _relative_id(bundle, archived.concept_id)
    assert new_id != old_id

    conn = get_db(str(db))
    try:
        doc_ids = {row[0] for row in conn.execute("SELECT doc_id FROM memory_embeddings")}
    finally:
        conn.close()
    assert doc_ids == {new_id}, (
        f"embedding row must follow the demote rename: expected only {new_id!r}, got {sorted(doc_ids)}"
    )


def test_six_memory_cli_verbs_remain_reachable(tmp_path):
    """Each lifecycle verb still performs its operation via `cairn memory`."""
    from cairn.graph.schema import get_db
    from cairn.memory.promotion import capture_memory
    from cairn.okf.bundle import OKFBundle

    db = tmp_path / "graph.db"
    knowledge = tmp_path / "knowledge"
    bundle = OKFBundle(str(knowledge))

    conn = get_db(str(db))
    try:
        # One memory per mutating verb: evolve/forget target `evolved`,
        # promote consumes `promoted`, demote consumes `demoted` (tier moves
        # rename the concept, so each path must still resolve when used).
        evolved = capture_memory(
            conn, bundle, type_="decision", title="Walkthrough evolved",
            body="v1", confidence=0.8,
        )
        promoted = capture_memory(
            conn, bundle, type_="pattern", title="Walkthrough promoted",
            body="seed", confidence=0.8,
        )
        demoted = capture_memory(
            conn, bundle, type_="decision", title="Walkthrough demoted",
            body="seed", confidence=0.8,
        )
        conn.commit()
    finally:
        conn.close()
    evolved_path, promoted_path, demoted_path = (
        evolved["path"], promoted["path"], demoted["path"])

    def run(verb: str, *args: str):
        result = _invoke_cli(
            ["memory", verb, *args, "--db", str(db), "--knowledge", str(knowledge)])
        assert result.exit_code == 0, f"{verb}: {result.output}"
        return result.output

    assert "Evolved" in run("evolve", evolved_path, "--body", "v2")
    run("digest", "--limit", "5")
    assert "Promoted" in run("promote", promoted_path)
    assert "Demoted" in run("demote", demoted_path, "--tier", "archived")
    assert "Expired raw" in run("decay")
    # The superseded original still exists on disk, so forget removes it.
    assert "Deleted memory" in run("forget", evolved_path)
