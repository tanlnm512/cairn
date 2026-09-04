"""Claude Code hook handlers.

Called by Claude Code's hooks system. Reads tool/session details from stdin
and dispatches to the appropriate cairn command.

Path-free: resolves the `cairn` binary via PATH (shutil.which), falling back to
`python -m cairn.cli.main` using the running interpreter. Works regardless of install
method (pipx, wheel, editable) and regardless of cwd — cairn resolves the central
store from the workspace context itself.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys


def _read_stdin() -> dict:
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return {}


def _cg_command() -> list[str]:
    """Resolve a cairn invocation. Prefers a `cairn` binary on PATH; falls back to
    `python -m cairn.cli.main` so the hook works from an editable install / source
    checkout that isn't on PATH."""
    cairn_bin = shutil.which("cairn")
    if cairn_bin:
        return [cairn_bin]
    return [sys.executable, "-m", "cairn.cli.main"]


def _run_cg(args: list, timeout: int = 30, stdin: str | None = None) -> str:
    """Run a cairn command. Returns stdout.

    ``stdin`` (if given) is piped to the child's stdin — use this for large
    payloads (e.g. a session transcript) that would otherwise blow past
    ARG_MAX (~256KB on macOS) when passed as an argv element.
    """
    try:
        result = subprocess.run(
            _cg_command() + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=stdin,
        )
        return result.stdout
    except (subprocess.SubprocessError, OSError) as e:
        return f"error: {e}"


def post_edit():
    """Called after Claude Code edits a file. Triggers incremental graph update
    and marks concepts referencing the edited file as stale.

    Reads the edited file path from the Claude Code hook payload and rebuilds
    just that file's repo for fast incremental updates.
    """
    data = _read_stdin()  # Claude Code sends tool details on stdin
    # Extract the file path from common payload shapes.
    file_path = ""
    try:
        file_path = (
            data.get("tool_input", {}).get("file_path")
            or data.get("tool_input", {}).get("path")
            or data.get("file_path")
            or ""
        )
    except AttributeError:
        pass
    args = ["update"]
    if file_path:
        args += ["--file", str(file_path)]
    out = _run_cg(args, timeout=30)
    # Mark stale concepts referencing the edited file.
    if file_path:
        _run_cg(["validate-paths", "--mark"], timeout=30)
    sys.stdout.write(out)


def session_end():
    """Called when Claude Code session ends. Captures memories from the transcript.

    Reads the JSONL transcript at the payload's ``transcript_path``, reduces it
    to text-only ``user``/``assistant`` messages (tail window of ~80), and
    pipes them to ``memory capture``. A missing/unreadable/empty transcript
    degrades to a quiet no-capture return -- the hook never raises.
    """
    data = _read_stdin()
    messages: list = []
    path = data.get("transcript_path") or ""
    if path:
        # The transcript is append-only and may be mid-write: parse line by
        # line, skipping partial/malformed lines. Any read failure degrades to
        # the no-transcript path rather than raising out of the hook.
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(rec, dict):
                        continue
                    if rec.get("type") not in ("user", "assistant"):
                        continue
                    msg = rec.get("message")
                    if not isinstance(msg, dict):
                        continue
                    content = msg.get("content")
                    if isinstance(content, str):
                        text = content
                    else:
                        blocks = content if isinstance(content, list) else []
                        text = "\n".join(
                            block.get("text", "") for block in blocks
                            if isinstance(block, dict)
                            and block.get("type") == "text"
                        )
                    if not text:
                        continue
                    messages.append(
                        {"role": msg.get("role", ""), "content": text})
        except Exception:
            messages = []
        messages = messages[-80:]
    if not messages:
        # Even without a transcript, queue a capture so an agent can process it.
        sys.stdout.write("(no transcript; nothing to capture)")
        return
    # Pass the transcript via stdin rather than as an argv element: a long
    # session can be hundreds of KB / MB of JSON, which exceeds ARG_MAX
    # (~256KB on macOS) and would yield E2BIG on the subprocess exec. The
    # `memory capture` command reads it when --session-transcript-stdin is set.
    transcript = json.dumps(messages)
    session_id = str(data.get("session_id") or "claude")
    out = _run_cg(
        ["memory", "capture", "--session-transcript-stdin",
         "--session-id", session_id],
        timeout=60,
        stdin=transcript,
    )
    sys.stdout.write(out or "(no memories captured)")


def session_start():
    """Called when Claude Code starts a session. Emits the top score-ranked
    tribal memories for the workspace as plain-text context.

    Reuses the CLI's digest command so the ranking lives in one place and this
    module stays path-free (no ``cairn.*`` import). Emits nothing when the
    store is empty, so no placeholder or error reaches the agent's context.
    """
    out = _run_cg(["memory", "digest", "--limit", "5"], timeout=15)
    text = (out or "").strip()
    if not text or text == "No tribal memories yet.":
        return
    sys.stdout.write(out)


def post_tool_failure():
    """Called after a tool use fails. Auto-captures the failure as a raw
    ``mistake`` memory when the same ``(tool_name, normalized_error)``
    signature has already occurred once before; a first occurrence only
    registers its signature and captures nothing.

    This is the highest-signal auto-capture: failures the agent didn't bother
    to record. The captured memory lands in the ``raw`` tier at low confidence
    and relies on the existing promotion/critic pipeline to promote the worthy
    ones -- graph_verification will naturally score down memories citing
    nonexistent symbols.

    Privacy: the tool input and error text are passed through the privacy
    filter (``strip_private_data``) before storage, so secrets in error output
    are scrubbed.

    Non-blocking: spawns the cairn CLI as a detached subprocess
    (``start_new_session=True``) and returns immediately. The agent's next
    prompt is never delayed.
    """
    data = _read_stdin()
    # Skip interrupts -- those aren't real failures worth capturing.
    if data.get("is_interrupt") or data.get("isInterrupt"):
        return
    tool_name = data.get("tool_name") or data.get("toolName") or "unknown"
    tool_input = data.get("tool_input") or data.get("toolArgs") or {}
    error = data.get("error") or ""

    if not error:
        return

    # Privacy-filter before storage. Tool error output can contain API keys,
    # bearer tokens, etc. from the failing command's stderr.
    try:
        from cairn.memory.privacy import strip_private_data
    except ImportError:
        # If the filter isn't importable (unlikely), bail -- never store
        # unfiltered error output.
        return

    safe_input = strip_private_data(json.dumps(tool_input)[:4000])
    safe_error = strip_private_data(str(error)[:4000])
    title = f"Tool failure: {tool_name}"

    # Recurrence gate: the signature comes from the already-filtered error,
    # and the child CLI process captures only when this signature was seen
    # before (first occurrence registers quietly).
    try:
        from cairn.memory.recurrence import failure_signature
    except ImportError:
        return
    recurrence_key = failure_signature(tool_name, safe_error)

    # Build the memory body with the Why/How structure record_memory expects.
    body = (
        f"{tool_name} failed during use.\n\n"
        f"Input: {safe_input}\n\n"
        f"Error: {safe_error}\n\n"
        f"Why: Auto-captured by post_tool_failure hook.\n"
        f"How to apply: Check if this error pattern is recurring before "
        f"using this tool the same way again."
    )

    # Detached subprocess -- fire and forget, never blocks the agent.
    try:
        subprocess.Popen(
            _cg_command() + [
                "memory", "record", "mistake", title,
                "--body", body,
                "--confidence", "0.3",  # low: raw capture, unreviewed
                "--recurrence-key", recurrence_key,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # detach so it survives the hook exit
        )
    except (subprocess.SubprocessError, OSError):
        pass  # never let capture break the agent


if __name__ == "__main__":
    hook_type = sys.argv[1] if len(sys.argv) > 1 else "post_edit"
    if hook_type == "post_edit":
        post_edit()
    elif hook_type == "session_end":
        session_end()
    elif hook_type == "post_tool_failure":
        post_tool_failure()
    elif hook_type == "session_start":
        session_start()
    else:
        sys.stderr.write(f"unknown hook: {hook_type}\n")
        sys.exit(1)
