"""Tests for ``OKFBundle.lock()`` -- the fcntl advisory lock + thread-local
re-entrancy guard that serializes cross-process/cross-thread read-modify-write
of an OKF bundle (the centerpiece of the SSE-daemon lock-contention fix).

Covers re-entrancy, timeout, release, and scope. Two contention strategies,
because ``flock`` is per-process on macOS (a second ``os.open``+``flock`` in
the *same* test thread does not block, so the cheap same-process pre-acquire
trick can't reproduce exclusion there):

* ``test_contention_timeout_logic`` patches ``fcntl.flock`` to always report
  EAGAIN -- deterministic, fast, exercises the busy-wait/deadline/TimeoutError
  wrapping logic every run.
* ``test_cross_process_contention_and_release`` spawns a real child process
  holding the lock -- the only reliable way to exercise genuine flock conflict
  (and release) on macOS.
"""
from __future__ import annotations

import errno
import fcntl
import multiprocessing as mp
import time
from pathlib import Path

import pytest

from cairn.okf.bundle import OKFBundle


@pytest.fixture
def bundle(tmp_path):
    return OKFBundle(str(tmp_path / "knowledge"))


def _hold_okf_lock(root_str: str, ready, release) -> None:
    """Child-process target: acquire the OKF lock, signal ``ready``, hold it
    until ``release`` is set (then exit the context manager, releasing)."""
    from cairn.okf.bundle import OKFBundle as _B

    b = _B(root_str)
    with b.lock(timeout=5):
        ready.set()
        release.wait(30)  # hold until the parent is done probing


class TestBundleLockReentrancy:
    def test_nested_lock_does_not_self_deadlock(self, bundle):
        """The re-entrancy fast path: a second ``lock()`` in the same thread
        no-ops instead of re-flocking (flock is per-fd, so a second flock from
        the same thread on a new fd would self-deadlock)."""
        start = time.monotonic()
        with bundle.lock():
            with bundle.lock():
                pass
        # A self-deadlock would block until the 5s default timeout then raise;
        # completing well under a second proves the fast path fired.
        assert time.monotonic() - start < 1.0

    def test_depth_resets_after_nested(self, bundle):
        """After a nested sequence exits, the per-thread depth returns to 0, so
        a fresh ``lock()`` re-acquires the real OS lock instead of silently
        no-op'ing forever (which would let a concurrent writer interleave)."""
        from cairn.okf.bundle import _LOCK_DEPTH

        key = str(Path(bundle.root).resolve())
        with bundle.lock():
            with bundle.lock():
                pass
        depth = getattr(_LOCK_DEPTH, "depth", {})
        assert depth.get(key, 0) == 0

    def test_exception_in_body_still_resets_depth(self, bundle):
        """An exception inside the ``with`` body still runs the unlock path
        (the nested try/finally), so depth returns to 0 and the OS lock is
        released -- the next holder isn't blocked forever by a crashed one."""

        class _Boom(Exception):
            pass

        from cairn.okf.bundle import _LOCK_DEPTH

        key = str(Path(bundle.root).resolve())
        with pytest.raises(_Boom):
            with bundle.lock():
                raise _Boom
        depth = getattr(_LOCK_DEPTH, "depth", {})
        assert depth.get(key, 0) == 0


class TestBundleLockContention:
    def test_contention_timeout_logic(self, bundle, monkeypatch):
        """When flock reports the lock unavailable, ``lock(timeout=...)``
        busy-waits until its deadline then raises the built-in TimeoutError.
        Patches flock to always raise EAGAIN so the wrapping logic is exercised
        deterministically without a second process."""

        def _always_busy(fd, op):
            raise OSError(errno.EAGAIN, "simulated contention")

        monkeypatch.setattr(fcntl, "flock", _always_busy)
        with pytest.raises(TimeoutError):
            with bundle.lock(timeout=0.1):
                pass

    def test_cross_process_contention_and_release(self, bundle):
        """A child process holding the real OS lock blocks the parent (raised
        TimeoutError); once the child releases, the parent acquires -- proving
        genuine flock conflict + release, which same-process tests can't."""
        ctx = mp.get_context("spawn")
        ready = ctx.Event()
        release = ctx.Event()
        p = ctx.Process(target=_hold_okf_lock, args=(str(bundle.root), ready, release))
        p.start()
        try:
            assert ready.wait(5), "child failed to acquire the lock"
            # Child holds the OS lock -> parent times out.
            with pytest.raises(TimeoutError):
                with bundle.lock(timeout=0.3):
                    pass
            # Child releases -> parent acquires (also proves release works).
            release.set()
            p.join(5)
            assert p.exitcode == 0, "child should exit cleanly after release"
            with bundle.lock(timeout=2):
                pass
        finally:
            release.set()
            p.join(5)
            if p.is_alive():
                p.terminate()
                p.join(2)


class TestBundleLockScope:
    def test_separate_roots_do_not_block_each_other(self, tmp_path):
        """The lock is keyed by resolved bundle root, so two unrelated bundles
        can be held simultaneously."""
        a = OKFBundle(str(tmp_path / "a"))
        b = OKFBundle(str(tmp_path / "b"))
        with a.lock():
            start = time.monotonic()
            with b.lock():
                pass
            assert time.monotonic() - start < 1.0
