"""Boot-time catch-up + live file watching for a running `cairn serve`."""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

from . import scanner as scanner_mod


# ---------------------------------------------------------------------------
# Gitignore cache invalidation
# ---------------------------------------------------------------------------

def invalidate_gitignore_cache(path: str):
    """Clear the gitignore cache for the repo containing `path`.

    Called when a .gitignore file itself changes, so subsequent scans
    pick up the new ignore rules. The cache is keyed by repo root.
    """
    p = Path(path)
    # Walk up to find the repo root (dir containing .git).
    for parent in p.parents:
        if (parent / ".git").exists():
            key = str(parent)
            scanner_mod._gitignore_cache.pop(key, None)
            return


# ---------------------------------------------------------------------------
# Core freshness check
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FreshnessReport:
    """Observed index drift and whether the query repaired it."""

    drifted_paths: tuple[str, ...]
    repaired: bool

    def banner(self) -> str:
        if not self.drifted_paths:
            return ""
        paths = list(self.drifted_paths)
        shown = ", ".join(paths[:3])
        if len(paths) > 3:
            shown += f", +{len(paths) - 3} more"
        return f"Stale graph: {len(paths)} file(s) not refreshed: {shown}"


def refresh_for_query(
    conn: sqlite3.Connection,
    workspace: str | None = None,
    *,
    repair: bool | None = None,
) -> FreshnessReport:
    """Probe indexed files and optionally repair drift before a graph read."""
    if repair is None:
        disabled = os.environ.get("CAIRN_NO_REFRESH", "").lower() in (
            "1",
            "true",
            "yes",
        )
        repair = not disabled
    if workspace is None:
        from ..paths import resolve_workspace

        workspace = str(resolve_workspace())

    drifted = tuple(sorted(set(_detect_changed(conn, workspace))))
    if not drifted or not repair:
        return FreshnessReport(drifted, repaired=False)

    _repair_changed(conn, workspace, drifted, strict=True)
    return FreshnessReport(drifted, repaired=True)


def _repair_changed(
    conn: sqlite3.Connection,
    workspace: str,
    drifted: tuple[str, ...],
    *,
    strict: bool,
) -> int:
    for path in drifted:
        if path.endswith(".gitignore"):
            invalidate_gitignore_cache(path)

    from .incremental import reindex_paths

    result = reindex_paths(conn, workspace, list(drifted))
    errors = result.get("errors", [])
    if errors:
        raise RuntimeError(f"graph refresh failed: {'; '.join(errors)}")
    repaired = result.get("reindexed", 0) + result.get("deleted", 0)
    if strict and repaired != len(drifted):
        raise RuntimeError(
            f"graph refresh incomplete: {repaired}/{len(drifted)} file(s)"
        )
    return repaired


def _detect_changed(conn, workspace: str) -> list[str]:
    """Compare files table (size, mtime) against disk. Return changed paths.

    The scan loop behind `ensure_fresh_force` (and `_do_catch_up` generally).
    """
    changed: list[str] = []

    for repo_path in scanner_mod.discover_repos(workspace):
        repo_name = scanner_mod.repository_id(repo_path)
        try:
            file_rows = conn.execute(
                "SELECT path, size, mtime FROM files WHERE repo_id = ?",
                (repo_name,),
            ).fetchall()
        except Exception:
            continue

        # If this repo has no rows, it likely hasn't been indexed yet (or its
        # repo_id key doesn't match after a path/portability migration). Skip it
        # rather than fall back to ALL rows: a broad fallback would mis-classify
        # every other repo's file as "new" for this repo and trigger a full
        # workspace reindex on every boot. The repo will be picked up by a
        # later `cairn build`/`cairn update`.
        if not file_rows:
            continue

        for row in file_rows:
            # files.path is repo-relative; resolve to absolute via the chokepoint.
            p = Path(scanner_mod.resolve_file_path(workspace, repo_name, row["path"]))
            try:
                st = p.stat()
            except OSError:
                # File deleted/moved since index.
                changed.append(str(p))
                continue
            if st.st_size != (row["size"] or 0):
                # Size changed — definitely different content.
                changed.append(str(p))
            elif abs(st.st_mtime - (row["mtime"] or 0.0)) > 0.5:
                # mtime changed but size same — could be touch-only or
                # real edit with same byte count. Re-index to be safe.
                changed.append(str(p))

        # Detect NEW source files not yet in the DB. Storage is repo-relative;
        # the scanner yields absolute, so compare on both forms.
        existing = {row["path"] for row in file_rows}
        for src in scanner_mod.iter_source_files(repo_path):
            rel = str(src.relative_to(repo_path)) if str(src).startswith(str(repo_path)) else str(src)
            if rel not in existing and str(src) not in existing:
                changed.append(str(src))

    return changed


def ensure_fresh_force(conn, workspace: str) -> int:
    """Detect and re-index disk changes. Called once at `cairn serve` boot."""
    return _do_catch_up(conn, workspace)


def _do_catch_up(conn, workspace: str) -> int:
    """Detect changed files and re-index them. Returns count."""
    changed = _detect_changed(conn, workspace)
    if not changed:
        return 0
    return _repair_changed(
        conn, workspace, tuple(sorted(set(changed))), strict=False
    )


# ---------------------------------------------------------------------------
# Live file watching (FRESH-1)
# -----------------------------------------------------------------------------

# `cairn` namespace logger: the server configures it with a stderr handler;
# stdout is the JSON-RPC channel under stdio, so this module must never print.
_LOGGER = logging.getLogger("cairn")

# Events within one quiet window coalesce into a single update pass. The
# window opens on the FIRST event and is never extended, so worst-case
# latency from edit to reindex is bounded at DEBOUNCE_S.
DEBOUNCE_S = 2.0

# How long stop() waits for the observer thread to join. The observer's own
# dispatch is non-blocking (handlers only append to a set + arm a timer), so
# this is generous headroom rather than an expected wait.
_JOIN_TIMEOUT_S = 5.0


def _watch_env_enabled() -> bool:
    """CAIRN_WATCH hard kill switch (default on)."""
    return os.environ.get("CAIRN_WATCH", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _read_only_env() -> bool:
    """True under CAIRN_READ_ONLY — the watcher must never write."""
    return os.environ.get("CAIRN_READ_ONLY", "").lower() in ("1", "true", "yes")


class _DebouncingHandler:
    """Filesystem event handler that queues changes for debounced processing."""

    def __init__(self, service: "FileWatcherService"):
        self._service = service

    def dispatch(self, event) -> None:  # watchdog protocol
        try:
            self._service._on_event(event)
        except Exception:
            # A broken handler must never kill watchdog's dispatch thread.
            _LOGGER.debug("watcher: event dispatch failed", exc_info=True)


class FileWatcherService:
    """Monitors repository files and schedules incremental graph reindexing.

    Debounces filesystem modification events and runs incremental updates on a background timer.
    """

    def __init__(
        self,
        workspace: str,
        db_path: str,
        debounce_s: float = DEBOUNCE_S,
    ):
        self.workspace = str(workspace)
        self.db_path = str(db_path)
        self._debounce_s = float(debounce_s)

        # The Observer (a Thread subclass) -- typed as Thread so this module
        # type-checks without watchdog installed; only Thread-level API
        # (daemon/start/stop/join) is used through this attribute.
        self._observer: threading.Thread | None = None
        self._started = False
        self._running = False
        # Pending event paths + the armed debounce timer. Guarded by _lock:
        # watchdog's dispatch thread offers, the timer thread drains.
        self._pending: set[str] = set()
        self._timer: threading.Timer | None = None
        self._busy = False  # an update pass is in flight (serialize passes)
        self._lock = threading.Lock()
        # One-shot latch for absorbed update failures (lock contention and
        # friends): warn once per contiguous run of failures, reset on
        # success. Keeps a long CLI build from spamming one warning per batch.
        self._failure_latched = False

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> bool:
        """Start watching. Idempotent. Returns True iff actually watching.

        False (a no-op, never an exception) when: CAIRN_WATCH=0,
        CAIRN_READ_ONLY is set, watchdog is not installed, or the workspace
        has no repos to watch. The unavailable-watchdog case logs a single
        info line telling the user about the ``[watch]`` extra.
        """
        if self._started:
            return True
        if not _watch_env_enabled():
            _LOGGER.info("cairn: live file watching disabled (CAIRN_WATCH=0)")
            return False
        if _read_only_env():
            # A read-only server must never write; boot catch-up + `cairn
            # update` on the writable side cover freshness.
            _LOGGER.info("cairn: live file watching disabled (read-only server)")
            return False
        try:
            from watchdog.observers import Observer
        except ImportError:
            _LOGGER.info(
                "cairn: live file watching unavailable (watchdog not installed) "
                "-- install the [watch] extra to enable it; falling back to "
                "boot catch-up + `cairn update`"
            )
            return False

        repos = scanner_mod.discover_repos(self.workspace)
        if not repos:
            _LOGGER.info(
                "cairn: live file watching disabled (no repos found under %s)",
                self.workspace,
            )
            return False

        observer = Observer()  # a Thread subclass
        observer.daemon = True  # never block interpreter exit
        handler = _DebouncingHandler(self)
        scheduled = 0
        for repo in repos:
            try:
                # Duck-typed handler (see _DebouncingHandler): the observer's
                # dispatch loop only needs handler.dispatch(event).
                observer.schedule(handler, str(repo), recursive=True)  # type: ignore[arg-type]
                scheduled += 1
            except OSError as e:
                _LOGGER.warning(
                    "cairn: watcher cannot observe %s (%s)", repo, e
                )
        if not scheduled:
            _LOGGER.info(
                "cairn: live file watching disabled (no repos schedulable)"
            )
            return False

        try:
            observer.start()
        except Exception:
            # An observer that fails to start (platform emitter issue) must
            # never take the server down.
            _LOGGER.warning("cairn: watcher failed to start", exc_info=True)
            return False

        self._observer = observer
        self._started = True
        self._running = True
        _LOGGER.info(
            "cairn: watching %d repo(s) under %s for live edits", scheduled, self.workspace
        )
        return True

    def stop(self) -> None:
        """Stop watching and join the observer thread. Idempotent, never raises.

        A flush still inside its debounce window is dropped (not run
        synchronously): those edits are absorbed by the next boot's
        ``ensure_fresh_force`` catch-up, same as edits made while no server
        ran.
        """
        with self._lock:
            self._running = False
            timer = self._timer
            self._timer = None
            self._pending.clear()
        if timer is not None:
            timer.cancel()
        observer = self._observer
        self._observer = None
        self._started = False
        if observer is not None:
            try:
                # Observer-specific stop() (drains emitters) beyond Thread's
                # API -- see the _observer attribute's typing note.
                observer.stop()  # type: ignore[attr-defined]
                observer.join(timeout=_JOIN_TIMEOUT_S)
            except Exception:
                _LOGGER.debug("cairn: watcher stop hiccup", exc_info=True)

    # -- event intake (watchdog dispatch thread) ------------------------------

    def _on_event(self, event) -> None:
        """Record one filesystem event. Cheap: filter + set add + maybe timer.

        Directory events are skipped (a new file emits its own event); for
        moves both src (deleted) and dest (created) sides matter.
        """
        if getattr(event, "is_directory", False):
            return
        moved_dest = getattr(event, "dest_path", None)
        if moved_dest:
            self._offer(str(moved_dest))
        src = getattr(event, "src_path", None)
        if src:
            self._offer(str(src))

    def _offer(self, path_str: str) -> None:
        """Add a candidate path and arm the debounce timer if idle.

        The only filtering done here is the extension gate (pure dict lookup)
        so watchdog's dispatch thread never does IO. Full filtering
        (gitignore, config, size) happens in the batched update pass.
        """
        # .gitignore is not a source extension but must be seen (cache
        # invalidation); everything else non-source is dropped here.
        if (
            Path(path_str).suffix not in scanner_mod.EXTENSION_MAP
            and not path_str.endswith(".gitignore")
        ):
            return
        with self._lock:
            if not self._running:
                return
            self._pending.add(path_str)
            if self._timer is None:
                self._arm_timer_locked()

    def _arm_timer_locked(self) -> None:
        # Caller holds self._lock.
        t = threading.Timer(self._debounce_s, self._flush)
        t.daemon = True
        t.name = "cairn-watcher-flush"
        self._timer = t
        t.start()

    # -- debounced flush (timer thread) ---------------------------------------

    def _flush(self) -> None:
        """Drain the pending set into one update pass (timer thread).

        If a previous pass is still running (an update can legitimately take
        longer than the debounce window), the pending events are left for a
        re-armed timer instead of overlapping — at most one update pass per
        service runs at a time, so the build lock is only ever contended by
        EXTERNAL processes (CLI build/update), which is the absorbed case.
        """
        with self._lock:
            if not self._pending:
                self._timer = None
                return
            if self._busy:
                # Re-arm: the running pass's finally-block would also re-arm,
                # but arming here keeps the wait bounded even if its wake
                # window is missed.
                self._arm_timer_locked()
                return
            self._busy = True
            self._timer = None
            paths = self._pending
            self._pending = set()
        try:
            self._update_pass(paths)
        finally:
            with self._lock:
                self._busy = False
                if self._running and self._pending and self._timer is None:
                    self._arm_timer_locked()

    # -- the update pass ------------------------------------------------------

    def _update_pass(self, raw_paths: set[str]) -> None:
        """One debounced batch: mark pending_sync, reindex, clear leftovers."""
        source_paths: list[tuple[str, tuple[str, str, str]]] = []
        for p in sorted(raw_paths):
            if p.endswith(".gitignore"):
                # Not reindexed itself; the cache drop makes the NEXT pass's
                # (and any scanner run's) gitignore matching see new rules.
                invalidate_gitignore_cache(p)
                continue
            forms = self._path_forms(p)
            if forms is None:
                continue  # outside the workspace / not under a repo
            repo, rel, _abs_form = forms
            # Layer A (skip dirs) applies even to deletions -- a file under
            # node_modules/ was never indexed, so its deletion is a no-op.
            if scanner_mod._is_under_skip_dir(tuple(Path(rel).parts)):
                continue
            # Full 4-layer filter (gitignore + config + size) only while the
            # file exists: classify_file stat()s, and a deleted file must be
            # forwarded so reindex_paths can remove its rows. The repo root is
            # resolved so it prefixes the (FSEvents-resolved) event path.
            repo_root = Path(
                scanner_mod.resolve_repo_path(self.workspace, repo)
            ).resolve()
            if Path(p).resolve().exists() and scanner_mod._is_skipped(
                Path(p).resolve(), repo_root
            ):
                continue
            source_paths.append((p, forms))

        if not source_paths:
            return

        # (a) Mark pending in BOTH path forms. pending_sync.path is the PK, so
        # OR IGNORE dedupes repeat saves; reindex_paths deletes exactly these
        # two forms on completion, and the staleness banner matches the
        # repo-relative form (files.path).
        inserts: list[tuple[str, str, str]] = []
        cleanup_paths: set[str] = set()
        for p, (repo, rel, abs_form) in source_paths:
            now = datetime.now(timezone.utc).isoformat()
            inserts.append((rel, repo, now))
            inserts.append((abs_form, repo, now))
            cleanup_paths.update((rel, abs_form))
        try:
            conn = _connect(self.db_path)
            try:
                conn.executemany(
                    "INSERT OR IGNORE INTO pending_sync (path, repo_id, changed_at) "
                    "VALUES (?, ?, ?)",
                    inserts,
                )
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error:
            # Banner is best-effort (e.g. unmigrated DB); the reindex below
            # is the load-bearing half, so continue.
            _LOGGER.debug("cairn: watcher could not mark pending_sync", exc_info=True)

        # (b) Reindex. incremental_update opens its own connection and takes
        # the schema build lock; a concurrent CLI build/update holding that
        # lock raises RuntimeError here.
        try:
            from .incremental import incremental_update

            incremental_update(workspace=self.workspace, db_path=self.db_path)
        except RuntimeError as e:
            # Lock contention: absorb, keep the pending_sync rows (the banner
            # stays hot and flags exactly these files), retry on the next
            # event batch. Warn once per contiguous failure run.
            if not self._failure_latched:
                _LOGGER.warning(
                    "cairn: watcher update deferred (writer lock busy): %s", e
                )
                self._failure_latched = True
            return
        except Exception:
            # Anything else is still non-fatal (the service must survive for
            # the next batch), but loud once.
            if not self._failure_latched:
                _LOGGER.warning("cairn: watcher update failed", exc_info=True)
                self._failure_latched = True
            return

        self._failure_latched = False
        # (c) Clear this batch's leftover rows. reindex_paths already deleted
        # the rows for every path it reindexed; what remains are paths git
        # diff did not report (e.g. an edit that restored identical content).
        # Left standing they would fire the staleness banner forever on a
        # graph that is actually current.
        try:
            conn = _connect(self.db_path)
            try:
                conn.executemany(
                    "DELETE FROM pending_sync WHERE path = ?",
                    [(p,) for p in cleanup_paths],
                )
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error:
            _LOGGER.debug("cairn: watcher could not clear pending_sync", exc_info=True)

    def _path_forms(self, abs_path: str) -> tuple[str, str, str] | None:
        """Normalize an event path to (repo_name, repo_relative, abs_form).

        Event paths and workspace prefixes can disagree on symlinks (macOS
        FSEvents always reports resolved paths like ``/private/var/...`` even
        when the watch was scheduled on ``/var/...``), so both sides are
        resolved before matching.

        The returned ``abs_form`` is ``resolve_repo_path(workspace, repo) /
        rel`` -- i.e. the exact string ``incremental_update``/``reindex_paths``
        reconstruct for the same file -- and ``rel`` is byte-identical to what
        ``reindex_paths`` computes, so the pending_sync rows this service
        inserts are exactly the two forms its clear sites delete.
        """
        resolved = str(Path(abs_path).resolve())
        repo = scanner_mod.infer_repo_for_path(resolved, self.workspace)
        if not repo:
            return None
        repo_path = Path(scanner_mod.resolve_repo_path(self.workspace, repo))
        try:
            rel = str(Path(resolved).relative_to(repo_path.resolve()))
        except ValueError:
            return None
        return repo, rel, str(repo_path / rel)


def _connect(db_path: str) -> sqlite3.Connection:
    """A short-lived writable connection (schema-applied, busy-waiting)."""
    from .schema import get_db

    return get_db(db_path, busy_timeout_ms=20000)
