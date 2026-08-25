"""Stray-sweeper safety tests (audit F1/F2/F4, scope-3 concurrency audit).

Guards the `cairn serve` stray sweeper against its two P2 failure modes plus
the pid-reuse P3:

- F1: orphaned stdio servers (argv shape ``cairn serve`` -- what editors
  actually spawn; db is passed via CAIRN_DB env, never argv) MUST be caught.
  The old argv-pattern scan (`cairn serve.*<db>` / `cairn serve run`) matched
  neither real stdio shape, so the v0.9.x lock-contention root cause was
  invisible to the sweeper.
- F2: a candidate is only killed when it verifiably holds THIS db (lsof); a
  foreground server on a different db is never ours to kill, and when lsof
  verification is impossible NOTHING is killed.
- F4: terminate_pid re-verifies the pid's command line before SIGTERM and
  before SIGKILL, so a pid reused during the TERM->KILL window is never
  killed.

Everything here is SIMULATED: subprocess.run (pgrep/ps/lsof), os.kill, and
_pid_cmdline are mocked -- no real process is ever signalled.
"""
from __future__ import annotations

import os
import signal
from unittest.mock import MagicMock

import pytest

from cairn.mcp_server import lifecycle as lc

DB = "/repos/acme/.cairn/graph.db"


def _fake_pid(offset: int) -> int:
    """A fake pid guaranteed outside find_strays' protected set.

    find_strays never treats the sweeping process itself as a stray
    (protected = {daemon, os.getpid(), daemon children}). Hardcoded fake
    pids (4101, 4200, ...) intermittently EQUALED the real pytest pid on
    CI runners (pid namespaces there reach the thousands -- observed twice
    on 2026-08-25), silently filtering the fake orphan as "self" and
    failing these tests with e.g. ``assert [] == [4101]``. Anchoring every
    fake pid to ``os.getpid() + offset`` keeps it out of the protected set
    on every host, deterministically.
    """
    return os.getpid() + offset


def _fake_subprocess(processes=None, pgrep_pids=None, child_pids=None,
                     lsof_pids=None, lsof_fail=False):
    """Build a subprocess.run stub simulating a process table for lifecycle.

    processes: {pid: cmdline} -- what `ps -p <pid> -o command=` returns.
    pgrep_pids: what `pgrep -f 'cairn serve'` returns (candidate superset).
    child_pids: what `pgrep -P <ppid>` returns.
    lsof_pids: pids `lsof -F p <db>` reports as holding the db file
        (None + not lsof_fail => "file open by nobody": exit 1, no stderr).
    lsof_fail: make lsof verification impossible (error exit + stderr).
    """
    processes = processes or {}
    pgrep_pids = pgrep_pids or []
    child_pids = child_pids or []

    def fake_run(args, *rest, **kw):
        res = MagicMock()
        res.stderr = ""
        if args[0] == "pgrep" and "-P" in args:
            res.returncode = 0
            res.stdout = "\n".join(str(p) for p in child_pids) + "\n"
        elif args[0] == "pgrep":
            res.returncode = 0 if pgrep_pids else 1
            res.stdout = "\n".join(str(p) for p in pgrep_pids) + "\n"
        elif args[0] == "ps":
            pid = int(args[args.index("-p") + 1])
            cmdline = processes.get(pid)
            res.returncode = 0 if cmdline else 1
            res.stdout = (cmdline + "\n") if cmdline else ""
        elif args[0] == "lsof":
            if lsof_fail:
                res.returncode = 2
                res.stderr = "lsof: bogus failure"
                res.stdout = ""
            else:
                res.returncode = 0 if lsof_pids else 1
                res.stdout = "".join(f"p{p}\n" for p in (lsof_pids or []))
        else:
            res.returncode = 1
            res.stdout = ""
        return res

    return fake_run


def _setup(monkeypatch, *, processes=None, pgrep_pids=None, child_pids=None,
           lsof_pids=None, lsof_fail=False, daemon_pid=None):
    monkeypatch.setattr(lc, "running_pid", lambda: daemon_pid)
    monkeypatch.setattr(
        "subprocess.run",
        _fake_subprocess(processes, pgrep_pids, child_pids, lsof_pids, lsof_fail),
    )


# ---------------------------------------------------------------------------
# F1: the orphaned stdio server (the actual v0.9.x root cause) must be caught
# ---------------------------------------------------------------------------

class TestOrphanedStdioServerCaught:
    def test_editor_shape_cairn_serve_is_caught(self, monkeypatch):
        """The argv shape editors spawn (`cairn serve`, db via env) is a stray
        when it holds the db and isn't the daemon/self."""
        orphan = _fake_pid(4101)
        _setup(
            monkeypatch,
            processes={orphan: "/usr/local/bin/cairn serve"},
            pgrep_pids=[orphan],
            lsof_pids=[orphan],
        )
        assert lc.find_strays(DB) == [orphan]

    def test_foreground_server_on_this_db_is_caught(self, monkeypatch):
        """A `cairn serve run` foreground server that holds THIS db (and is
        not the launchd daemon) is a stray."""
        fg = _fake_pid(4200)
        _setup(
            monkeypatch,
            processes={fg: "/usr/local/bin/cairn serve run --port 9999"},
            pgrep_pids=[fg],
            lsof_pids=[fg],
        )
        assert lc.find_strays(DB) == [fg]


# ---------------------------------------------------------------------------
# Protected set: daemon, its children, and self are never strays
# ---------------------------------------------------------------------------

class TestProtectedSet:
    def test_daemon_and_children_protected_even_when_holding_db(self, monkeypatch):
        """The launchd daemon + its children are excluded even though their
        cmdline is a server shape AND they hold the db."""
        daemon, child = _fake_pid(5000), _fake_pid(5001)
        _setup(
            monkeypatch,
            processes={
                daemon: "/usr/local/bin/cairn serve run --port 9876 --read-only",
                child: "/bin/zsh -c cairn helper",
            },
            pgrep_pids=[daemon, child],
            child_pids=[child],
            lsof_pids=[daemon, child],
            daemon_pid=daemon,
        )
        assert lc.find_strays(DB) == []

    def test_self_is_ignored(self, monkeypatch):
        """The sweeping process itself is never a stray, even when its cmdline
        and db-holding look exactly like a server's."""
        own = os.getpid()
        _setup(
            monkeypatch,
            processes={own: "/usr/local/bin/cairn serve"},
            pgrep_pids=[own],
            lsof_pids=[own],
        )
        assert lc.find_strays(DB) == []


# ---------------------------------------------------------------------------
# F1 (cont): non-server cmdlines the old pattern scan false-positived on
# ---------------------------------------------------------------------------

class TestNonServersIgnored:
    def test_unrelated_and_transient_processes_ignored(self, monkeypatch):
        """`cairn build`, `grep cairn serve`, editor buffers, and the transient
        `cairn serve status` lifecycle command are all ignored -- even when
        pgrep lists them AND (perversely) lsof claims they hold the db."""
        base = _fake_pid(4100)
        procs = {
            base + 1: "/usr/local/bin/cairn build",
            base + 2: "grep cairn serve",
            base + 3: "vi src/cairn/server.py",
            base + 4: "/usr/local/bin/cairn serve status",
            base + 5: "/usr/local/bin/cairncafe serve",
        }
        _setup(
            monkeypatch,
            processes=procs,
            pgrep_pids=list(procs),
            lsof_pids=list(procs),
        )
        assert lc.find_strays(DB) == []


# ---------------------------------------------------------------------------
# F2: db verification before killing
# ---------------------------------------------------------------------------

class TestDbVerification:
    def test_foreground_server_on_different_db_not_killed(self, monkeypatch):
        """A server-shaped process that does NOT hold this db (it serves a
        different workspace) is not a stray for THIS db."""
        other = _fake_pid(4300)
        daemon = _fake_pid(5000)
        _setup(
            monkeypatch,
            processes={
                other: "/usr/local/bin/cairn serve run --port 8123",
                daemon: "/usr/local/bin/cairn serve run --port 9876 --read-only",
            },
            pgrep_pids=[other, daemon],
            lsof_pids=[daemon],  # only the daemon holds THIS db
            daemon_pid=daemon,
        )
        assert lc.find_strays(DB) == []

    def test_no_kill_when_lsof_verification_impossible(self, monkeypatch, capsys):
        """lsof failing means verification is impossible: nothing is killed
        and the skip is logged (an unverifiable kill is worse than a missed
        sweep)."""
        orphan = _fake_pid(4101)
        killed = []
        _setup(
            monkeypatch,
            processes={orphan: "/usr/local/bin/cairn serve"},
            pgrep_pids=[orphan],
            lsof_pids=[orphan],
            lsof_fail=True,
        )
        monkeypatch.setattr(lc, "terminate_pid", lambda *a, **k: killed.append(a))

        assert lc.find_strays(DB) == []
        assert lc.sweep_strays(DB) == 0
        assert killed == []
        assert "skipped" in capsys.readouterr().err

    def test_unheld_db_is_a_valid_empty_answer(self, monkeypatch, capsys):
        """lsof exit 1 with no stderr = 'nobody holds this file' (verified
        empty), not a failure: no strays, and no skip warning."""
        orphan = _fake_pid(4101)
        _setup(
            monkeypatch,
            processes={orphan: "/usr/local/bin/cairn serve"},
            pgrep_pids=[orphan],
            lsof_pids=None,  # exit 1, empty stderr -> verified empty set
        )
        assert lc.find_strays(DB) == []
        assert "skipped" not in capsys.readouterr().err

    def test_sweep_passes_cmdline_recheck_to_terminate(self, monkeypatch):
        """sweep_strays arms terminate_pid with the anchored cmdline check so
        a pid reused between find and kill is never SIGKILLed (F4 wiring)."""
        orphan = _fake_pid(4101)
        _setup(
            monkeypatch,
            processes={orphan: "/usr/local/bin/cairn serve"},
            pgrep_pids=[orphan],
            lsof_pids=[orphan],
        )
        recorded = {}

        def fake_terminate(pid, timeout=5.0, cmd_check=None):
            recorded["pid"] = pid
            recorded["cmd_check"] = cmd_check

        monkeypatch.setattr(lc, "terminate_pid", fake_terminate)
        assert lc.sweep_strays(DB) == 1
        assert recorded["pid"] == orphan
        assert recorded["cmd_check"] is lc._is_cairn_serve_cmdline


# ---------------------------------------------------------------------------
# F4: terminate_pid pid-reuse guard
# ---------------------------------------------------------------------------

class TestTerminatePidReuseGuard:
    def _armed(self, monkeypatch, cmdlines):
        """Patch _pid_cmdline with a scripted sequence + record os.kill sigs.

        The liveness probes (sig 0) are filtered out of the recorded list so
        assertions see only real signals.
        """
        sent = []
        seq = iter(cmdlines)
        monkeypatch.setattr(lc, "_pid_cmdline", lambda pid: next(seq))
        monkeypatch.setattr(
            "os.kill", lambda pid, sig: sent.append(sig) if sig != 0 else None
        )
        monkeypatch.setattr("time.sleep", lambda s: None)
        return sent

    def test_reused_pid_before_sigkill_aborts(self, monkeypatch):
        """The target dies during the TERM->KILL wait and the kernel reuses
        the pid: only SIGTERM was delivered, SIGKILL is withheld."""
        sent = self._armed(
            monkeypatch,
            ["/usr/local/bin/cairn serve", "/usr/sbin/nginx -c /etc/nginx.conf"],
        )
        lc.terminate_pid(4101, timeout=0.01, cmd_check=lc._is_cairn_serve_cmdline)
        assert sent == [signal.SIGTERM]

    def test_still_matching_pid_is_sigkilled(self, monkeypatch):
        sent = self._armed(
            monkeypatch, ["/usr/local/bin/cairn serve", "/usr/local/bin/cairn serve"]
        )
        lc.terminate_pid(4101, timeout=0.01, cmd_check=lc._is_cairn_serve_cmdline)
        assert signal.SIGTERM in sent and signal.SIGKILL in sent

    def test_reused_pid_before_sigterm_sends_nothing(self, monkeypatch):
        """Pid already reused by the time we signal: not even SIGTERM."""
        sent = self._armed(monkeypatch, ["/usr/sbin/nginx -c /etc/nginx.conf"])
        lc.terminate_pid(4101, timeout=0.01, cmd_check=lc._is_cairn_serve_cmdline)
        assert sent == []

    def test_unreadable_cmdline_aborts(self, monkeypatch):
        """_pid_cmdline returning None (can't confirm) aborts the kill."""
        sent = self._armed(monkeypatch, [None, None])
        lc.terminate_pid(4101, timeout=0.01, cmd_check=lc._is_cairn_serve_cmdline)
        assert sent == []

    def test_no_cmdcheck_is_legacy_best_effort(self, monkeypatch):
        """Without cmd_check, terminate_pid keeps the legacy contract: no
        cmdline reads, TERM then KILL."""
        sent = []

        def _unexpected(pid):
            raise AssertionError("_pid_cmdline must not be called without cmd_check")

        monkeypatch.setattr(lc, "_pid_cmdline", _unexpected)
        monkeypatch.setattr(
            "os.kill", lambda pid, sig: sent.append(sig) if sig != 0 else None
        )
        monkeypatch.setattr("time.sleep", lambda s: None)
        lc.terminate_pid(4101, timeout=0.01)
        assert signal.SIGTERM in sent and signal.SIGKILL in sent


# ---------------------------------------------------------------------------
# Pure helpers: the anchored token match + lsof parsing
# ---------------------------------------------------------------------------

class TestIsCairnServeCmdline:
    @pytest.mark.parametrize("cmdline,expected", [
        # Real server shapes (editors + launchd + explicit flags).
        ("cairn serve", True),
        ("/usr/local/bin/cairn serve", True),
        ("/Users/x/.local/bin/cairn serve run --port 9876 --read-only", True),
        ("/usr/local/bin/cairn serve --port 8123 --read-write", True),
        ("/usr/local/bin/cairn serve --db /x/y.db", True),
        # Transient lifecycle subcommands -- never servers.
        ("/usr/local/bin/cairn serve start", False),
        ("/usr/local/bin/cairn serve stop", False),
        ("/usr/local/bin/cairn serve status", False),
        ("/usr/local/bin/cairn serve restart", False),
        # False positives a pattern scan would match.
        ("grep cairn serve", False),
        ("vi src/cairn/server.py", False),
        ("/usr/local/bin/cairn build", False),
        ("/usr/local/bin/cairncafe serve", False),  # argv[0] must END with cairn
        # Degenerate shapes.
        ("cairn", False),
        ("", False),
    ])
    def test_matrix(self, cmdline, expected):
        assert lc._is_cairn_serve_cmdline(cmdline) is expected


class TestDbHolderPids:
    def _run_stub(self, returncode, stdout="", stderr=""):
        def fake_run(args, *rest, **kw):
            res = MagicMock()
            res.returncode = returncode
            res.stdout = stdout
            res.stderr = stderr
            return res
        return fake_run

    def test_parses_p_fields_and_ignores_fd_lines(self, monkeypatch):
        monkeypatch.setattr("subprocess.run", self._run_stub(0, "p42480\nf3\np42481\n"))
        assert lc._db_holder_pids(DB) == {42480, 42481}

    def test_exit1_no_stderr_is_verified_empty(self, monkeypatch):
        monkeypatch.setattr("subprocess.run", self._run_stub(1, "", ""))
        assert lc._db_holder_pids(DB) == set()

    def test_exit1_with_stderr_is_verification_failure(self, monkeypatch):
        monkeypatch.setattr("subprocess.run", self._run_stub(1, "", "lsof: nope"))
        assert lc._db_holder_pids(DB) is None

    def test_missing_lsof_binary_is_verification_failure(self, monkeypatch):
        def boom(args, *rest, **kw):
            raise FileNotFoundError("lsof")
        monkeypatch.setattr("subprocess.run", boom)
        assert lc._db_holder_pids(DB) is None
