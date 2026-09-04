"""Recurrence gate + hook lifecycle tests.

Covers: ``failure_signature`` normalization, ``note_failure_signature``
prior-count semantics, the ``memory record --recurrence-key`` capture gate,
the ``post_tool_failure`` hook wiring (entrypoint registration + the
``--recurrence-key`` argv link), ``session_end`` transcript_path handling,
and ``session_start`` digest emission.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path


def _invoke_cli(args: list):
    """Run the cairn CLI in-process with the module-level import deferred."""
    from click.testing import CliRunner

    from cairn.cli.main import main

    return CliRunner().invoke(main, args, catch_exceptions=False)


def _knowledge_files(knowledge) -> list:
    root = Path(knowledge)
    if not root.exists():
        return []
    return [p for p in root.rglob("*") if p.is_file()]


# --------------------------------------------------------------------------
# failure_signature normalization
# --------------------------------------------------------------------------

def test_failure_signature_is_stable_and_hex_truncated():
    from cairn.memory.recurrence import failure_signature

    sig = failure_signature("Bash", "boom at run 12")
    assert sig == failure_signature("Bash", "boom at run 12")
    assert len(sig) == 16
    int(sig, 16)  # hex


def test_failure_signature_normalizes_case_paths_digits_uuid_hex():
    from cairn.memory.recurrence import failure_signature

    # Case + absolute paths + digit runs (line numbers, pids) collapse.
    base = failure_signature("Bash", "Error: /Users/me/proj/x.js:42 not found")
    assert base == failure_signature(
        "Bash", "error: /var/lib/other/y.py:99 NOT FOUND")
    # Digit runs inside prose collapse (attempt counts).
    assert failure_signature("Bash", "boom attempt 1 of 3") == \
        failure_signature("Bash", "boom attempt 27 of 3")
    # UUID runs are stripped.
    assert failure_signature(
        "Bash", "corrupt cache 550e8400-e29b-41d4-a716-446655440000") == \
        failure_signature(
            "Bash", "corrupt cache a1b2c3d4-e5f6-4a1b-8c2d-9e0f1a2b3c4d")
    # Hex runs are stripped.
    assert failure_signature("Bash", "checksum deadbeefcafe mismatch") == \
        failure_signature("Bash", "checksum 1234567890abcdef mismatch")
    # The tool name is part of the key.
    assert base != failure_signature("Read", "/Users/me/proj/x.js:42 not found")


# --------------------------------------------------------------------------
# note_failure_signature prior-count semantics
# --------------------------------------------------------------------------

def test_note_failure_signature_returns_count_before_this_occurrence(tmp_path):
    from cairn.graph.schema import get_db
    from cairn.memory.recurrence import note_failure_signature

    conn = get_db(str(tmp_path / "graph.db"))
    try:
        sig = "0123456789abcdef"
        assert note_failure_signature(conn, sig, "Bash") == 0
        assert note_failure_signature(conn, sig, "Bash") == 1
        assert note_failure_signature(conn, sig, "Bash") == 2
        row = conn.execute(
            "SELECT occurrences FROM memory_failure_signatures WHERE sig = ?",
            (sig,),
        ).fetchone()
        assert row[0] == 3
    finally:
        conn.close()


# --------------------------------------------------------------------------
# `memory record --recurrence-key` capture gate (TC-012/TC-013/TC-014)
# --------------------------------------------------------------------------

def test_memory_record_recurrence_key_gates_capture(tmp_path):
    db = tmp_path / "graph.db"
    knowledge = tmp_path / "knowledge"
    argv = [
        "memory", "record", "mistake", "Tool failure: Bash",
        "--body", "Bash failed.\n\nWhy: hook.\n\nHow to apply: check first.",
        "--recurrence-key", "0123456789abcdef",
        "--db", str(db), "--knowledge", str(knowledge),
    ]

    first = _invoke_cli(argv)
    assert first.exit_code == 0, first.output
    assert first.output == "", "first occurrence must exit quietly with no capture"
    assert _knowledge_files(knowledge) == []

    second = _invoke_cli(argv)
    assert second.exit_code == 0, second.output
    assert "Recorded" in second.output
    assert len(_knowledge_files(knowledge)) > 0


def test_memory_record_recurrence_key_is_per_signature(tmp_path):
    db = tmp_path / "graph.db"
    knowledge = tmp_path / "knowledge"

    def record(sig: str):
        return _invoke_cli([
            "memory", "record", "mistake", "Tool failure: Bash",
            "--body", "body", "--recurrence-key", sig,
            "--db", str(db), "--knowledge", str(knowledge),
        ])

    assert record("aaaaaaaaaaaaaaaa").output == ""
    # A different signature is its own first occurrence (TC-014).
    assert record("bbbbbbbbbbbbbbbb").output == ""
    assert _knowledge_files(knowledge) == []
    assert "Recorded" in record("aaaaaaaaaaaaaaaa").output
    assert len(_knowledge_files(knowledge)) > 0


# --------------------------------------------------------------------------
# post_tool_failure hook registration + wiring (TC-015)
# --------------------------------------------------------------------------

def test_post_tool_failure_is_a_registered_entrypoint_written_on_install(tmp_path):
    from cairn.agent_install import install
    from cairn.agent_install._common import _HOOK_ENTRYPOINTS

    assert "post_tool_failure" in _HOOK_ENTRYPOINTS

    ws = tmp_path / "ws"
    ws.mkdir()
    install(str(ws), clients=["claude"], transport="stdio")
    settings = json.loads(
        (ws / ".claude" / "settings.json").read_text(encoding="utf-8"))
    commands = [
        h["command"]
        for entry in settings["hooks"]["PostToolUse"]
        for h in entry["hooks"]
    ]
    assert any("cairn.hooks.claude_hooks post_tool_failure" in c for c in commands)


def test_post_tool_failure_passes_recurrence_key_to_record(tmp_path, monkeypatch):
    import cairn.hooks.claude_hooks as hooks

    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "pytest -q"},
        "error": "Traceback: /opt/venv/lib/site.py:1 boom pid 4242",
    }
    calls: list = []

    class _FakePopen:
        def __init__(self, argv, **kwargs):
            calls.append(argv)

    monkeypatch.setattr(hooks.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    hooks.post_tool_failure()

    assert len(calls) == 1, "hook must spawn exactly one record command"
    argv = calls[0]
    key_index = argv.index("--recurrence-key")
    from cairn.memory.privacy import strip_private_data
    from cairn.memory.recurrence import failure_signature

    expected = failure_signature(
        "Bash", strip_private_data(str(payload["error"])[:4000]))
    assert argv[key_index + 1] == expected


# --------------------------------------------------------------------------
# session_end transcript_path handling (TC-018/TC-019)
# --------------------------------------------------------------------------

def _write_jsonl(path: Path, records: list) -> None:
    lines = [json.dumps(r) for r in records]
    lines.append("{broken json")  # mid-write / partial trailing line
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_session_end(monkeypatch, capsys, payload: dict, calls: list) -> str:
    """Feed ``payload`` to session_end with the capture subprocess faked at
    the claude_hooks call site. Returns the hook's stdout."""
    import cairn.hooks.claude_hooks as hooks

    class _FakeCompleted:
        stdout = "queued memory-extract\n"

    def fake_run(argv, **kwargs):
        calls.append({"argv": argv, "kwargs": kwargs})
        return _FakeCompleted()

    monkeypatch.setattr(hooks.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    hooks.session_end()
    return capsys.readouterr().out


def test_session_end_reads_transcript_and_queues_capture(tmp_path, monkeypatch, capsys):
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(transcript, [
        {"type": "user", "message": {"role": "user",
                                     "content": "fix the flaky test"}},
        {"type": "attachment", "message": {"role": "user", "content": "noise"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "text": "private reasoning"},
            {"type": "text", "text": "root cause was init ordering"},
            {"type": "tool_use", "name": "Bash", "input": {}},
        ]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_result", "content": "ok"}]}},
        {"type": "user", "message": {"role": "user", "content": ""}},
        {"type": "system"},
        [1, 2, 3],  # non-dict record
    ])

    calls: list = []
    out = _run_session_end(
        monkeypatch, capsys,
        {"session_id": "sess-abc-123", "transcript_path": str(transcript)},
        calls,
    )
    assert len(calls) == 1, "a non-empty transcript must reach memory capture"
    argv = calls[0]["argv"]
    sid_index = argv.index("--session-id")
    assert argv[sid_index + 1] == "sess-abc-123"
    assert argv[sid_index - 1] == "--session-transcript-stdin"
    sent = json.loads(calls[0]["kwargs"]["input"])
    assert sent == [
        {"role": "user", "content": "fix the flaky test"},
        {"role": "assistant", "content": "root cause was init ordering"},
    ]
    assert out == "queued memory-extract\n"


def test_session_end_keeps_last_80_messages(tmp_path, monkeypatch, capsys):
    transcript = tmp_path / "session.jsonl"
    records = [
        {"type": "user", "message": {"role": "user", "content": f"m{i}"}}
        for i in range(90)
    ]
    _write_jsonl(transcript, records)

    calls: list = []
    _run_session_end(
        monkeypatch, capsys,
        {"session_id": "s", "transcript_path": str(transcript)},
        calls,
    )
    sent = json.loads(calls[0]["kwargs"]["input"])
    assert len(sent) == 80
    assert sent[0]["content"] == "m10"
    assert sent[-1]["content"] == "m89"


def test_session_end_without_transcript_queues_nothing(tmp_path, monkeypatch, capsys):
    empty_file = tmp_path / "empty.jsonl"
    empty_file.write_text('{"type": "system"}\n', encoding="utf-8")
    missing = tmp_path / "nope.jsonl"

    cases = [
        {},
        {"transcript_path": ""},
        {"transcript_path": str(missing)},
        {"transcript_path": str(empty_file)},
        {"messages": []},
    ]
    for payload in cases:
        calls: list = []
        out = _run_session_end(monkeypatch, capsys, payload, calls)
        assert calls == [], f"no capture for payload {payload!r}"
        assert "(no transcript; nothing to capture)" == out


# --------------------------------------------------------------------------
# session_start digest emission (TC-006/TC-007/TC-008)
# --------------------------------------------------------------------------

def _run_session_start(monkeypatch, capsys, digest_stdout: str, calls: list) -> str:
    """Feed session_start a faked `memory digest` subprocess at the
    claude_hooks call site. Returns the hook's stdout."""
    import cairn.hooks.claude_hooks as hooks

    class _FakeCompleted:
        stdout = digest_stdout

    def fake_run(argv, **kwargs):
        calls.append({"argv": argv, "kwargs": kwargs})
        return _FakeCompleted()

    monkeypatch.setattr(hooks.subprocess, "run", fake_run)
    hooks.session_start()
    return capsys.readouterr().out


def test_session_start_emits_digest_command_output(monkeypatch, capsys):
    digest = "  [0.92, refs-verified=1.0] Always run pre-commit\n"
    calls: list = []
    out = _run_session_start(monkeypatch, capsys, digest, calls)
    assert len(calls) == 1
    assert calls[0]["argv"][-4:] == ["memory", "digest", "--limit", "5"]
    assert calls[0]["kwargs"]["timeout"] == 15
    assert out == digest


def test_session_start_silent_on_empty_store_or_sentinel(monkeypatch, capsys):
    for digest_stdout in ("", "\n", "No tribal memories yet.\n"):
        calls: list = []
        out = _run_session_start(monkeypatch, capsys, digest_stdout, calls)
        assert out == "", f"nothing must be emitted for {digest_stdout!r}"


def test_memory_digest_empty_store_sentinel_is_pinned(tmp_path):
    """Couples session_start's suppression string to the CLI's real output."""
    db = tmp_path / "graph.db"
    knowledge = tmp_path / "knowledge"
    result = _invoke_cli([
        "memory", "digest", "--limit", "5",
        "--db", str(db), "--knowledge", str(knowledge),
    ])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "No tribal memories yet."


def test_session_start_is_a_registered_entrypoint_with_markers():
    from cairn.agent_install._common import _HOOK_ENTRYPOINTS, _hook_markers

    assert "session_start" in _HOOK_ENTRYPOINTS
    markers = _hook_markers()
    assert "cairn.hooks.claude_hooks session_start" in markers
    assert "src.hooks.claude_hooks session_start" in markers


def test_claude_hooks_block_wires_session_start_startup_only():
    """D-014: matcher is exactly "startup" — resume/clear/compact/fork are
    excluded, which is what makes once-per-session true by construction."""
    from cairn.agent_install.clients.claude import claude_hooks_block

    entries = claude_hooks_block()["SessionStart"]
    assert len(entries) == 1
    assert entries[0]["matcher"] == "startup"
    commands = [h["command"] for h in entries[0]["hooks"]]
    assert any("cairn.hooks.claude_hooks session_start" in c for c in commands)
    for source in ("resume", "clear", "compact", "fork"):
        assert source not in entries[0]["matcher"]
