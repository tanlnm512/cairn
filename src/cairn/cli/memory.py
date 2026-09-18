"""Memory CLI: the memory group and its subcommands."""
from __future__ import annotations

import click
import os
import sys
from pathlib import Path

from .main import DEFAULT_DB_PATH, get_db, main
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
@click.option("--recurrence-key", default=None,
              help="Failure signature from the post_tool_failure hook. The "
                   "capture happens only when this signature was already "
                   "seen once before; a first occurrence exits quietly.")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_record(mtype, title, body, resource, confidence, recurrence_key, db, knowledge):
    """Record a learning: decision|pattern|mistake|workaround.

    For decision/mistake/workaround, structure --body as the fact/rule
    itself, then a `Why:` line and a `How to apply:` line -- the reasoning
    is what makes the memory useful once the original context is forgotten.

    Skip anything cheaper to re-derive than recall: facts the graph already
    answers, plain git history, or ephemeral session-only state.
    """
    from ..graph.embeddings import embed_memory_concepts
    from ..memory.promotion import capture_memory
    from ..okf.bundle import OKFBundle

    conn = get_db(db)
    if recurrence_key:
        from ..memory.recurrence import note_failure_signature

        tool_name = (title[len("Tool failure: "):]
                     if title.startswith("Tool failure: ") else mtype)
        if note_failure_signature(conn, recurrence_key, tool_name) == 0:
            conn.commit()
            conn.close()
            return
    bundle = OKFBundle(knowledge)
    result = capture_memory(
        conn, bundle, type_=mtype, title=title, body=body or title,
        resource=resource, confidence=confidence,
    )
    embed_memory_concepts(conn, bundle, [result["path"]])
    conn.commit()
    conn.close()
    signals = result["signals"]
    click.echo(f"Recorded {mtype} '{title}' -> {result['path']} (score={signals['score']}, tier={result['tier']})")


@memory.command("evolve")
@click.argument("memory_path")
@click.option("--title", default=None, help="New title for the revised memory.")
@click.option("--body", default=None, help="New body for the revised memory.")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_evolve(memory_path, title, body, db, knowledge):
    """Revise a memory: create a new version that supersedes the old one.

    The old memory is marked superseded (hidden from search unless
    --include-superseded) and its version chain is inherited, preserving
    the full decision history.
    """
    from ..graph.embeddings import embed_memory_concepts
    from ..memory.promotion import evolve_memory
    from ..okf.bundle import OKFBundle

    conn = get_db(db)
    bundle = OKFBundle(knowledge)
    result = evolve_memory(
        conn, bundle, memory_path, new_title=title, new_body=body
    )
    if result is not None:
        embed_memory_concepts(conn, bundle, [result["path"]])
        conn.commit()
    conn.close()
    if result is None:
        click.echo(f"Memory not found: '{memory_path}'.")
        return
    signals = result["signals"]
    click.echo(
        f"Evolved '{memory_path}' -> {result['path']} "
        f"(score={signals['score']}, tier={result['tier']}, superseded {result['superseded']})"
    )


def _parse_symbol_list(symbols: str) -> list[str]:
    """Split a comma-separated --symbols value; rejects empty entries."""
    symbol_list = [s.strip() for s in symbols.split(",")]
    if not all(symbol_list):
        raise click.UsageError("--symbols requires a non-empty comma-separated list.")
    return symbol_list


def _require_agent_id(agent_id: str) -> str:
    """Validate a caller agent id for the share/check surface; UsageError on bad id."""
    from ..memory.store import validate_agent_id

    try:
        return validate_agent_id(agent_id)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc


@memory.command("search")
@click.argument("query")
@click.option("--tier", default=None)
@click.option("--as-of", default=None,
              help="Only memories valid at this ISO-8601 date/time.")
@click.option("--agent", default=None,
              help="Agent id doing the search ([A-Za-z0-9._-], <=64 chars): "
                   "merges other agents' shared memories for the queried "
                   "symbols into the results.")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_search(query, tier, as_of, agent, db, knowledge):
    """Search past memories. Shows a live refs-verified fraction per result.

    Only memories valid at --as-of are returned; the default (now) shows
    only currently-valid memories.

    With --agent, other agents' shared memories for the queried symbols are
    merged in, each carrying a "shared by" attribution line. Omitted, the
    single-agent path runs: identical output, no sharing query.
    """
    from ..memory.promotion import search_memory
    from ..memory.scoring import _graph_verification
    from ..okf.bundle import OKFBundle

    if agent is not None:
        _require_agent_id(agent)

    conn = get_db(db)
    bundle = OKFBundle(knowledge)
    try:
        results = search_memory(conn, bundle, query, tier=tier, session_id="cli", as_of=as_of)
    except ValueError as exc:
        conn.close()
        raise click.UsageError(str(exc)) from exc
    # Read-through merge, gated on --agent: the default path runs no sharing
    # query and prints the search output unchanged.
    shared_by_id: dict = {}
    if agent is not None:
        from ..memory.store import shared_recall_entries

        extra, shared_by_id = shared_recall_entries(
            conn, bundle, agent, query, tier=tier, as_of=as_of,
            result_ids={c.concept_id for c in results},
        )
        results.extend(extra)
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
        attribution = shared_by_id.get(c.concept_id)
        if attribution:
            click.echo(f"    {attribution}")
    from ..graph.embeddings import unembedded_memory_hint
    hint = unembedded_memory_hint(conn, bundle)
    if hint:
        click.echo(hint)
    conn.close()


@memory.command("timeline")
@click.argument("symbol")
@click.option("--db", default=str(DEFAULT_DB_PATH),
              help="Graph DB path (unused; validity renders from concept extensions).")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_timeline(symbol, db, knowledge):
    """Temporal history of memories citing SYMBOL.

    A memory matches when it cites SYMBOL in a backtick symbol ref (the same
    extraction the stale path uses) or mentions it in plain text. Rows are
    ordered by valid_from; each shows the validity window, recorded successor
    link, and current tier/score.
    """
    from cairn.refs import extract_symbol_refs

    from ..memory.store import list_memories
    from ..okf.bundle import OKFBundle

    bundle = OKFBundle(knowledge)
    matches = []
    for c in list_memories(bundle):
        refs = [r[:-2] if r.endswith("()") else r
                for r in extract_symbol_refs(c.body or "")]
        cited = any(r == symbol or r.endswith("." + symbol) for r in refs)
        if not cited:
            hay = f"{c.title} {c.description} {c.body}".lower()
            if symbol.lower() not in hay:
                continue
        matches.append(c)
    matches.sort(
        key=lambda c: c.extensions.get("valid_from") or c.timestamp or ""
    )

    if not matches:
        click.echo(f"No memories citing '{symbol}'.")
        return
    click.echo(f"Timeline for '{symbol}' ({len(matches)} memory(ies)):")
    for c in matches:
        ext = c.extensions
        start = (ext.get("valid_from") or c.timestamp or "unknown")[:10]
        until = ext.get("valid_until")
        window = f"{start} -> {until[:10]}" if until else f"{start} -> present"
        line = (f"  {window}  [{ext.get('memory_tier', '?')} "
                f"{ext.get('memory_score', '?')}] {c.title}  ({c.concept_id})")
        if ext.get("successor_symbol"):
            line += f"  successor: {ext['successor_symbol']}"
        click.echo(line)


@memory.command("capture")
@click.option("--session-transcript", default=None, help="JSON transcript of the session")
@click.option("--session-transcript-stdin", is_flag=True, default=False,
              help="Read the JSON transcript from stdin (avoids ARG_MAX for long sessions)")
@click.option("--session-id", default="hook", help="Origin session id")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_capture(session_transcript, session_transcript_stdin, session_id, db, knowledge):
    """Extract learnings from a session transcript and record them.

    Used by session-end hooks. Routes through the memory-extract LLM task
    (decoupled); if no agent is available, queues the task for later and exits.

    The transcript may be passed inline via ``--session-transcript <json>``
    or, for long sessions that would exceed ARG_MAX (~256KB on macOS) as an
    argv element, piped on stdin with ``--session-transcript-stdin``. When
    both are given the stdin form wins.
    """

    from ..graph.embeddings import embed_memory_concepts
    from ..llm.tasks import create_task
    from ..memory.promotion import capture_memory
    from ..okf.bundle import OKFBundle

    conn = get_db(db)
    bundle = OKFBundle(knowledge)
    if session_transcript_stdin:
        # Read the full transcript from stdin — no ARG_MAX limit.
        transcript = sys.stdin.read()
    else:
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
    captured_paths = []
    for cand in candidates:
        try:
            result = capture_memory(
                conn, bundle,
                type_=cand.get("type", "decision"),
                title=cand.get("title", "untitled"),
                body=cand.get("body", ""),
                confidence=float(cand.get("confidence", 0.6)),
                session_origin=session_id,
            )
            captured_paths.append(result["path"])
            recorded += 1
        except Exception:
            continue

    if captured_paths:
        embed_memory_concepts(conn, bundle, captured_paths)
        conn.commit()

    if recorded:
        click.echo(f"Captured {recorded} memories from session {session_id}.")
    else:
        # Decoupled fallback: queue a memory-extract task for any agent.
        # Privacy floor (audit F2): the task .md persists the facts dict
        # (body + extensions) in the bundle, so the transcript must be
        # redacted BEFORE queueing -- truncation alone keeps a secret intact.
        from ..memory.privacy import strip_private_data

        task = create_task(
            bundle,
            "memory-extract",
            f"session-{session_id}",
            facts={
                "transcript": strip_private_data(transcript)[:8000],
                "session_id": session_id,
            },
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
        if conn is not None:
            from ..graph.embeddings import unembedded_memory_hint
            hint = unembedded_memory_hint(conn, bundle)
            if hint:
                click.echo(hint)
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

    bundle = OKFBundle(knowledge)
    # Pass a writable conn so promote_memory renames the persisted embedding
    # row to the new concept_id in place (content is unchanged by a promote),
    # instead of orphaning + re-embedding identical text.
    conn = get_db(db)
    try:
        new_id = promote_memory(bundle, path, conn=conn)
        if new_id:
            conn.commit()
    finally:
        conn.close()
    if new_id:
        click.echo(f"Promoted to {new_id}")
    else:
        click.echo(f"Could not find memory at '{path}'.", err=True)
        sys.exit(1)


@memory.command("decay")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_decay(db, knowledge):
    """Expire raw memories >7d, archive tribal >90d stale."""
    from ..memory.promotion import decay
    from ..okf.bundle import OKFBundle

    bundle = OKFBundle(knowledge)
    # Pass a writable conn so decay also reaps embedding rows orphaned by the
    # tier moves (dead vectors otherwise accumulate in memory_embeddings).
    conn = get_db(db)
    try:
        result = decay(bundle, conn=conn)
    finally:
        conn.close()
    click.echo(f"Expired raw: {result['expired_raw']}, archived tribal: {result['archived_tribal']}")


@memory.command("embed")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
@click.option("--batch-size", default=64, type=int)
@click.option("--reap/--no-reap", default=True,
              help="Also delete embedding rows whose memory no longer exists "
                   "(left behind by promote/demote/decay tier moves). Default on.")
def memory_embed(db, knowledge, batch_size, reap):
    """Backfill semantic embeddings for memories captured before this existed,
    or after an embedding-model swap. Ongoing capture/evolve embed on their
    own; this is for catching up the rest."""
    from cairn.graph import embeddings as emb
    from ..okf.bundle import OKFBundle

    bundle = OKFBundle(knowledge)
    conn = get_db(db)
    try:
        if reap:
            reaped = emb.reap_orphaned_memory_embeddings(conn, bundle)
            if reaped:
                click.echo(f"Reaped {reaped} orphaned embedding row(s).")
        summary = emb.embed_memory(conn, bundle, batch_size=batch_size)
        click.echo(
            f"Embedded {summary['embedded']} memory concept(s) with {summary['model']} "
            f"({summary['skipped']} already up to date, {summary['total']} total)."
        )
    finally:
        conn.close()


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
        conn.commit()
    finally:
        conn.close()
    if ok:
        click.echo(f"Deleted memory: '{memory_path}'.")
    else:
        # False covers both "no such memory" and the store's namespace
        # refusal (delete_memory won't touch concepts outside memory/).
        click.echo(
            f"Memory not found (or outside the memory/ namespace): '{memory_path}'.",
            err=True,
        )
        sys.exit(1)


@memory.command("demote")
@click.argument("memory_path")
@click.option("--tier", "target_tier", default="raw", help="Target tier (raw or archived).")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def memory_demote(memory_path, target_tier, db, knowledge):
    """Demote a memory to a lower tier (rejects promotions)."""
    from ..memory.store import demote_memory
    from ..okf.bundle import OKFBundle

    bundle = OKFBundle(knowledge)
    # Pass a writable conn so demote_memory renames the persisted embedding
    # row to the new concept_id in place (content is unchanged by a demote),
    # instead of orphaning it.
    conn = get_db(db)
    try:
        new_path = demote_memory(bundle, memory_path, target_tier=target_tier, conn=conn)
        if new_path is not None:
            conn.commit()
    finally:
        conn.close()
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


@memory.command("share")
@click.argument("memory_id", required=False)
@click.option("--agent", "agent_id", required=True,
              help="Caller agent id: non-empty, <=64 chars, [A-Za-z0-9._-].")
@click.option("--symbols", required=True,
              help="Comma-separated symbols the share covers.")
@click.option("--db", default=str(DEFAULT_DB_PATH))
def memory_share(memory_id, agent_id, symbols, db):
    """Share MEMORY_ID with other agents for a set of symbols.

    MEMORY_ID is an optional memory concept id; a share without one records
    symbol-level sharing only. Other agents' recall for these symbols
    surfaces the share. Re-sharing an existing (agent, symbol, memory)
    row inserts nothing and exits 0.
    """
    from ..memory.store import share_memory

    memory_id = memory_id or None
    symbol_list = _parse_symbol_list(symbols)
    _require_agent_id(agent_id)

    conn = get_db(db)
    try:
        inserted = share_memory(agent_id, symbol_list, memory_id=memory_id, conn=conn)
        conn.commit()
    finally:
        conn.close()
    if inserted:
        click.echo(f"Shared for agent '{agent_id}': "
                   f"{', '.join(symbol_list)} ({inserted} new row(s)).")
    else:
        click.echo(f"Nothing new recorded: agent '{agent_id}' "
                   f"already shares these symbols.")


@memory.command("check")
@click.option("--agent", "agent_id", required=True,
              help="Caller agent id: non-empty, <=64 chars, [A-Za-z0-9._-].")
@click.option("--symbols", required=True,
              help="Comma-separated symbols about to be edited.")
@click.option("--db", default=str(DEFAULT_DB_PATH))
def memory_check(agent_id, symbols, db):
    """Record edit intent on a symbol set; warn on other agents' recent overlap.

    Records the caller's kind='intent' rows so other agents' later checks
    see the intent, then reports other agents' recent share/intent rows
    intersecting the symbol set: one warning line per other agent, naming
    the agent and the overlapping symbols. Exits 0 with or without overlap.
    """
    from datetime import datetime, timezone

    from ..memory.store import check_overlap

    symbol_list = _parse_symbol_list(symbols)
    _require_agent_id(agent_id)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_db(db)
    try:
        for symbol in symbol_list:
            # Refresh an existing intent's ts so repeat checks stay inside
            # the recency window instead of pinning the first check's ts.
            cur = conn.execute(
                "UPDATE agent_symbols SET ts = ?"
                " WHERE agent_id = ? AND symbol = ? AND kind = 'intent'"
                " AND memory_id IS NULL",
                (ts, agent_id, symbol),
            )
            if not cur.rowcount:
                conn.execute(
                    "INSERT INTO agent_symbols (agent_id, symbol, memory_id, kind, ts)"
                    " VALUES (?, ?, NULL, 'intent', ?)",
                    (agent_id, symbol, ts),
                )
        conn.commit()
    finally:
        conn.close()

    ro_conn = get_db(db, read_only=True)
    try:
        conflicts = check_overlap(agent_id, symbol_list, conn=ro_conn)
    finally:
        ro_conn.close()

    if not conflicts:
        click.echo(f"No overlap: no other agent has recent activity "
                   f"on {', '.join(symbol_list)}.")
        return
    by_agent: dict = {}
    for other_agent, symbol, _kind, _ts in conflicts:
        syms = by_agent.setdefault(other_agent, [])
        if symbol not in syms:
            syms.append(symbol)
    for other_agent, syms in by_agent.items():
        click.echo(f"Overlap warning: agent '{other_agent}' has recent "
                   f"activity on {', '.join(syms)}")

