"""Cursor hook handlers. Same interface as claude_hooks; Cursor fires
afterFileEdit / afterSessionEnd events."""
from __future__ import annotations

from .claude_hooks import post_edit, session_end

# Cursor hooks are functionally identical to Claude Code hooks: both call cg
# update on edit and cg memory capture on session end. Re-export for clarity.
afterFileEdit = post_edit
afterSessionEnd = session_end

if __name__ == "__main__":
    import sys

    hook = sys.argv[1] if len(sys.argv) > 1 else "afterFileEdit"
    if hook in ("afterFileEdit", "post_edit"):
        post_edit()
    elif hook in ("afterSessionEnd", "session_end"):
        session_end()
