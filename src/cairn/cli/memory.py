"""Memory CLI: the memory group and 12 subcommands."""
from __future__ import annotations

import click
import json
import os
import subprocess
import sys
from pathlib import Path

from .main import DEFAULT_DB_PATH, DEFAULT_KNOWLEDGE_PATH, builder, get_db, main, queries, scanner_mod
from ._helpers import _human_bytes, _mods, _shorten  # noqa: F401

@main.group()
def memory():
    """Agent memory: decisions, patterns, mistakes, workarounds."""


@memory.command("record")
@click.argument("mtype", type=click.Choice(["decision", "pattern", "mistake", "workaround"]))
@click.argument("title")
@click.option("--body", default="", help="Memory body text.")
@click.option("--resource", default=None, help="Related file path.")
@click.option("--confidence", default=0.7, type=float, help="Agent confidence 0.0-1.0.")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_record(mtype, title, body, resource, confidence, db, knowledge):
    """Record a learning: decision|pattern|mistake|workaround.

    For decision/mistake/workaround, structure --body as the fact/rule
    itself, then a `Why:` line and a `How to apply:` line -- the reasoning
    is what makes the memory useful once the original context is forgotten.

    Skip anything cheaper to re-derive than recall: facts the graph already
    answers, plain git history, or ephemeral session-only state.
    """
    from ..memory.promotion import capture_memory
    from ..okf.bundle import OKFBundle

    conn = get_db(db)
    bundle = OKFBundle(knowledge)
    result = capture_memory(
        conn, bundle, type_=mtype, title=title, body=body or title,
        resource=resource, confidence=confidence,
    )
    conn.close()
    signals = result["signals"]
    click.echo(f"Recorded {mtype} '{title}' -> {result['path']} (score={signals['score']}, tier={result['tier']})")


@memory.command("search")
@click.argument("query")
@click.option("--tier", default=None)
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_search(query, tier, db, knowledge):
    """Search past memories. Shows a live refs-verified fraction per result."""
    from ..memory.promotion import search_memory
    from ..memory.scoring import _graph_verification
    from ..okf.bundle import OKFBundle

    conn = get_db(db)
    bundle = OKFBundle(knowledge)
    results = search_memory(conn, bundle, query, tier=tier, session_id="cli")
    if not results:
        conn.close()
        click.echo(f"No memories matching '{query}'.")
        return
    for c in results:
        score = c.extensions.get("memory_score", "?")
        t = c.extensions.get("memory_tier", "?")
        try:
            refs = round(_graph_verification(c, conn), 3)
        except Exception:
            refs = "?"
        click.echo(f"  [{t} {score}, refs-verified={refs}] {c.title}  ({c.concept_id})")
    conn.close()


@memory.command("capture")
@click.option("--session-transcript", default=None, help="JSON transcript of the session")
@click.option("--session-id", default="hook", help="Origin session id")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_capture(session_transcript, session_id, db, knowledge):
    """Extract learnings from a session transcript and record them.

    Used by session-end hooks. Routes through the memory-extract LLM task
    (decoupled); if no agent is available, queues the task for later and exits.
    """
    import json
    import os

    from ..llm.tasks import create_task
    from ..memory.promotion import capture_memory
    from ..okf.bundle import OKFBundle

    conn = get_db(db)
    bundle = OKFBundle(knowledge)
    transcript = session_transcript or "[]"

    # Try synchronous extraction via a configured subprocess backend.
    backend = os.environ.get("CAIRN_LLM_BACKEND", "").lower()
    candidates = []
    if backend in ("droid", "opencode", "claude"):
        from ..llm.client import SubprocessBackend

        client = SubprocessBackend(bundle, cli=backend)
        try:
            candidates = client.extract(transcript)
        except Exception:
            candidates = []

    # If we got candidates, record them now.
    recorded = 0
    for cand in candidates:
        try:
            capture_memory(
                conn, bundle,
                type_=cand.get("type", "decision"),
                title=cand.get("title", "untitled"),
                body=cand.get("body", ""),
                confidence=float(cand.get("confidence", 0.6)),
                session_origin=session_id,
            )
            recorded += 1
        except Exception:
            continue

    if recorded:
        click.echo(f"Captured {recorded} memories from session {session_id}.")
    else:
        # Decoupled fallback: queue a memory-extract task for any agent.
        task = create_task(
            bundle,
            "memory-extract",
            f"session-{session_id}",
            facts={"transcript": transcript[:8000], "session_id": session_id},
        )
        click.echo(f"No agent available; queued memory-extract task {task.id}.")
    conn.close()


@memory.command("list")
@click.option("--tier", default=None)
@click.option("--tag", default=None)
@click.option("--db", default=str(DEFAULT_DB_PATH),
              help="Graph DB path (enables refs-verified fractions).")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_list(tier, tag, db, knowledge):
    """List memories, optionally filtered. Shows refs-verified when --db resolves."""
    from ..memory import store as store_mod
    from ..memory.scoring import _graph_verification
    from ..okf.bundle import OKFBundle

    bundle = OKFBundle(knowledge)
    mems = store_mod.list_memories(bundle, tier=tier, tag=tag)
    if not mems:
        click.echo("No memories found.")
        return
    # Open the graph conn only if the DB exists; otherwise skip verification.
    from pathlib import Path

    conn = None
    if Path(db).exists():
        conn = get_db(db)
    try:
        for c in mems:
            score = c.extensions.get("memory_score", "?")
            refs = "?"
            if conn is not None:
                try:
                    refs = round(_graph_verification(c, conn), 3)
                except Exception:
                    pass
            click.echo(f"  [{c.extensions.get('memory_tier','?')} {score}, "
                       f"refs-verified={refs}] {c.title}")
    finally:
        if conn is not None:
            conn.close()


@memory.command("stats")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_stats(knowledge):
    """Memory statistics by tier."""
    from ..memory.promotion import memory_stats as mstats
    from ..okf.bundle import OKFBundle

    bundle = OKFBundle(knowledge)
    stats = mstats(bundle)
    for tier, info in stats.items():
        click.echo(f"  {tier:10} {info['count']:>4} memories (avg score {info['avg_score']})")


@memory.command("digest")
@click.option("--limit", default=10, type=int, help="Max memories to show.")
@click.option("--db", default=str(DEFAULT_DB_PATH),
              help="Graph DB path (enables refs-verified fractions).")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_digest(limit, db, knowledge):
    """Top tribal memories by score -- quick session-orientation digest.

    Shows a live refs-verified fraction per memory (backtick file/symbol refs
    that still exist in the graph). A low value flags a memory citing a renamed
    or removed symbol; verify before relying on it.
    """
    from ..memory.promotion import tribal_digest
    from ..memory.scoring import _graph_verification
    from ..okf.bundle import OKFBundle

    bundle = OKFBundle(knowledge)
    mems = tribal_digest(bundle, limit=limit)
    if not mems:
        click.echo("No tribal memories yet.")
        return
    from pathlib import Path

    conn = None
    if Path(db).exists():
        conn = get_db(db)
    try:
        for c in mems:
            score = c.extensions.get("memory_score", "?")
            refs = "?"
            if conn is not None:
                try:
                    refs = round(_graph_verification(c, conn), 3)
                except Exception:
                    pass
            click.echo(f"  [{score}, refs-verified={refs}] {c.title}")
    finally:
        if conn is not None:
            conn.close()


@memory.command("promote")
@click.argument("path")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_promote(path, db, knowledge):
    """Force-promote a memory to canonical (compass/wiki)."""
    from ..memory.promotion import promote_memory
    from ..okf.bundle import OKFBundle

    conn = get_db(db)
    bundle = OKFBundle(knowledge)
    new_id = promote_memory(bundle, path, conn=conn)
    conn.close()
    if new_id:
        click.echo(f"Promoted to {new_id}")
    else:
        click.echo(f"Could not find memory at '{path}'.", err=True)
        sys.exit(1)


@memory.command("decay")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_decay(knowledge):
    """Expire raw memories >7d, archive tribal >90d stale."""
    from ..memory.promotion import decay
    from ..okf.bundle import OKFBundle

    bundle = OKFBundle(knowledge)
    result = decay(bundle)
    click.echo(f"Expired raw: {result['expired_raw']}, archived tribal: {result['archived_tribal']}")


@memory.command("batch-critic")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_batch_critic(db, knowledge):
    """Run critic pass on queued draft memories."""
    from ..memory.promotion import batch_critic
    from ..okf.bundle import OKFBundle

    conn = get_db(db)
    bundle = OKFBundle(knowledge)
    result = batch_critic(conn, bundle)
    conn.close()
    click.echo(f"Processed {result['processed']} drafts: "
               f"{result['tribal']} -> tribal, {result['dropped']} dropped, "
               f"{result['remaining_drafts']} remain drafts.")


@memory.command("forget")
@click.argument("memory_path")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_forget(memory_path, db, knowledge):
    """Permanently delete a memory and its cross-session refs."""
    from ..memory.store import delete_memory
    from ..okf.bundle import OKFBundle

    bundle = OKFBundle(knowledge)
    conn = get_db(db)
    try:
        ok = delete_memory(bundle, memory_path, conn=conn)
    finally:
        conn.close()
    if ok:
        click.echo(f"Deleted memory: '{memory_path}'.")
    else:
        click.echo(f"Memory not found: '{memory_path}'.", err=True)
        sys.exit(1)


@memory.command("demote")
@click.argument("memory_path")
@click.option("--tier", "target_tier", default="raw", help="Target tier (raw or archived).")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_demote(memory_path, target_tier, knowledge):
    """Demote a memory to a lower tier (rejects promotions)."""
    from ..memory.store import demote_memory
    from ..okf.bundle import OKFBundle

    bundle = OKFBundle(knowledge)
    new_path = demote_memory(bundle, memory_path, target_tier=target_tier)
    if new_path is None:
        click.echo(
            f"Cannot demote '{memory_path}' to '{target_tier}'. "
            "Target must be strictly lower than current tier, or memory not found.",
            err=True,
        )
        sys.exit(1)
    click.echo(f"Demoted '{memory_path}' -> {new_path} (tier → {target_tier})")


@memory.command("purge")
@click.option("--max-days", default=90, type=int, help="Delete archived memories older than this.")
@click.option("--dry-run", is_flag=True, help="List candidates without deleting.")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_purge(max_days, dry_run, knowledge):
    """Purge old archived memories (CLI-only — not exposed as MCP)."""
    from ..memory.store import list_memories, purge_archived
    from ..okf.bundle import OKFBundle
    from datetime import datetime, timezone

    bundle = OKFBundle(knowledge)
    if dry_run:
        now = datetime.now(timezone.utc)
        candidates = []
        for c in list_memories(bundle, tier="archived"):
            ts = c.timestamp
            if ts:
                try:
                    age = (now - datetime.fromisoformat(ts)).days
                except (ValueError, TypeError):
                    age = 0
                if age > max_days:
                    candidates.append((c.concept_id, age))
        if not candidates:
            click.echo(f"No archived memories older than {max_days} days.")
            return
        click.echo(f"{len(candidates)} archived memory(ies) older than {max_days} days:")
        for cid, age in candidates:
            click.echo(f"  {cid}  ({age}d)")
        return

    purged = purge_archived(bundle, max_days=max_days)
    click.echo(f"Purged {purged} archived memory(ies) older than {max_days} days.")


@memory.command("consolidate")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_consolidate(knowledge):
    """Consolidate redundant raw memories into unified tribal knowledge."""
    from ..memory.store import consolidate_memories
    from ..okf.bundle import OKFBundle

    bundle = OKFBundle(knowledge)
    count = consolidate_memories(bundle)
    click.echo(f"Consolidated {count} raw memories into tribal knowledge.")

