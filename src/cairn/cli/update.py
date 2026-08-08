"""Update CLI: incremental graph reindex."""
from __future__ import annotations

import click
import sys
from pathlib import Path

from .main import DEFAULT_DB_PATH, DEFAULT_KNOWLEDGE_PATH, main, scanner_mod

@main.command()
@click.option("--repo", default=None, help="Specific repo to update.")
@click.option("--file", "file_path", default=None, help="Specific file changed (for PostToolUse hooks)")
@click.option("--workspace", default=scanner_mod.DEFAULT_WORKSPACE)
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_KNOWLEDGE_PATH),
              help="Knowledge bundle path (for the post-update memory staleness scan).")
def update(repo, file_path, workspace, db, knowledge):
    """Incremental graph update from git diff (or a single changed file)."""
    from . import display
    from ..graph.schema import get_db
    from ..memory.promotion import decay
    from ..okf.bundle import OKFBundle

    # If a specific file is given, run true single-file incremental re-indexing
    if file_path:
        from ..graph.incremental import _reindex_file
        if not repo:
            repo = scanner_mod.infer_repo_for_path(file_path, workspace)
        if not repo:
            display.error(f"Could not infer repository for path: {file_path}")
            sys.exit(1)

        conn = get_db(db, busy_timeout_ms=20000)
        abs_path = Path(file_path).resolve()
        repo_path = scanner_mod.resolve_repo_path(workspace, repo)
        try:
            rel_path = str(abs_path.relative_to(repo_path))
        except ValueError:
            conn.close()
            display.error(f"File {file_path} is not under repository path {repo_path}")
            sys.exit(1)

        try:
            _reindex_file(conn, repo, repo_path, rel_path, workspace=str(workspace))
        finally:
            conn.close()
        display.success(f"Reindexed {rel_path} in repo {repo}")
        return

    from ..graph.incremental import incremental_update
    result = incremental_update(repo=repo, workspace=workspace, db_path=db)
    errors = result.get("errors") or []
    deleted = result.get("files_deleted", 0)

    if errors:
        # Surface failures rather than reporting a clean success when reindex
        # actually failed. Show the count and the first few messages so the
        # user can act on them.
        display.warning(
            f"Updated: reindexed {result['files_reindexed']} files "
            f"({deleted} deleted) across {result['repos_scanned']} repos "
            f"with {len(errors)} error(s)"
        )
        for msg in errors[:5]:
            display.warning(f"  {msg}")
        if len(errors) > 5:
            display.warning(f"  ... and {len(errors) - 5} more")
    else:
        deleted_part = f", {deleted} deleted" if deleted else ""
        display.success(
            f"Updated: reindexed {result['files_reindexed']} files{deleted_part} "
            f"across {result['repos_scanned']} repos"
        )
    
    # Run memory decay after update to archive stale raw memories automatically.
    # This ensures raw memories don't grow unbounded over time.
    try:
        bundle = OKFBundle(knowledge)
        decay_result = decay(bundle)
        if decay_result.get("expired_raw", 0) > 0 or decay_result.get("archived_tribal", 0) > 0:
            display.success(
                f"Memory decay: archived {decay_result['expired_raw']} stale raw memories, "
                f"{decay_result['archived_tribal']} stale tribal memories"
            )
    except Exception as e:
        # Don't fail the whole update if decay has an issue
        display.warning(f"Memory decay failed (non-critical): {e}")

    # Memory-anchored-file hints (Phase 3.2): if a reindex changed symbols that
    # a memory cites, the memory may now be stale. Scan all memory tiers
    # (raw/drafts/tribal/archived) for any whose backtick refs no longer fully
    # resolve (refs_verified < 1.0) and warn -- the graph just changed, so
    # surface memories that may have drifted. Warning, not a block: `cairn
    # update` must not fail on memory state. Only considers explicit backtick
    # refs (never loose mentions), so a file named in prose alone is not an
    # anchor.
    if result.get("files_reindexed", 0) > 0 or result.get("files_deleted", 0) > 0:
        try:
            from ..memory.scoring import _graph_verification
            from ..graph.schema import get_db as _get_db
            conn = _get_db(db, busy_timeout_ms=20000)
            try:
                stale_mems = []
                for cid in bundle.list_concepts(prefix="memory/"):
                    try:
                        c = bundle.read_concept(cid)
                    except Exception:
                        continue
                    try:
                        if _graph_verification(c, conn) < 1.0:
                            stale_mems.append(cid)
                    except Exception:
                        continue
            finally:
                conn.close()
            if stale_mems:
                display.warning(
                    f"{len(stale_mems)} memor(s) reference file/symbol(s) that "
                    f"no longer fully resolve after this update -- verify before "
                    f"relying on them:"
                )
                for cid in stale_mems[:5]:
                    display.warning(f"  {cid}")
                if len(stale_mems) > 5:
                    display.warning(f"  ... and {len(stale_mems) - 5} more")
        except Exception as e:
            # Memory hints are advisory; never fail the update over them.
            display.warning(f"Memory staleness scan failed (non-critical): {e}")



