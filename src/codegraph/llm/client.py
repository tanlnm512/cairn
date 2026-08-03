"""LLM client: the boundary between codegraph and any agent/LLM.

Codegraph never calls an LLM directly. This module provides:
  - LLMClient protocol: synthesize/revise/judge/extract (all return strings/dicts)
  - SubprocessBackend: runs an agent CLI synchronously (droid/opencode/claude)
  - FileQueueBackend: writes a Task and waits for any agent to complete it

Which backend is used is configured via env CODEGRAPH_LLM_BACKEND:
  unset | "file-queue" -> FileQueueBackend (fully decoupled; default)
  "droid"  | "opencode" | "claude" -> SubprocessBackend with that CLI

All backends degrade gracefully: if unavailable, codegraph falls back to the
deterministic graph-derived output (never fails, never hallucinates).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from ..okf.bundle import OKFBundle
from . import tasks as task_mod


class LLMClient(Protocol):
    """The contract every call site uses. Prompt_id keys into the skill."""

    def synthesize(self, prompt_id: str, facts: Dict[str, Any], timeout: int = 120) -> str: ...
    def revise(self, prompt_id: str, draft: str, errors: List[str], facts: Dict[str, Any]) -> str: ...
    def judge(self, prompt_id: str, draft: str, facts: Dict[str, Any]) -> Dict[str, Any]: ...
    def extract(self, transcript: str) -> List[Dict[str, Any]]: ...


def get_client(bundle: OKFBundle) -> "LLMClient":
    """Return the configured client. Defaults to FileQueueBackend (decoupled)."""
    backend = os.environ.get("CODEGRAPH_LLM_BACKEND", "file-queue").lower()
    if backend in ("droid", "opencode", "claude"):
        return SubprocessBackend(bundle, cli=backend)
    return FileQueueBackend(bundle)


class FileQueueBackend:
    """Decoupled: writes a Task, waits for any agent to complete it.

    Fully agent-agnostic. The agent loads the codegraph skill, runs
    `cg task list` / `cg task claim` / `cg task complete`. No subprocess spawn.
    """

    def __init__(self, bundle: OKFBundle, poll_interval: float = 2.0, max_wait: float = 600):
        self.bundle = bundle
        self.poll_interval = poll_interval
        self.max_wait = max_wait

    def _run_task(self, kind: str, resource: str, facts: Dict[str, Any]) -> str:
        task = task_mod.create_task(self.bundle, kind, resource, facts=facts)
        waited = 0.0
        while waited < self.max_wait:
            t = task_mod.get_task(self.bundle, task.id)
            if t and t.status == "done":
                result = task_mod.read_result(self.bundle, task.id)
                return result or ""
            time.sleep(self.poll_interval)
            waited += self.poll_interval
        return ""  # timed out; caller falls back to deterministic output

    def synthesize(self, prompt_id: str, facts: Dict[str, Any], timeout: int = 120) -> str:
        return self._run_task(prompt_id, facts.get("resource", prompt_id), facts)

    def revise(self, prompt_id: str, draft: str, errors: List[str], facts: Dict[str, Any]) -> str:
        facts = {**facts, "previous_draft": draft, "errors": errors}
        return self._run_task(f"{prompt_id}-revise", facts.get("resource", prompt_id), facts)

    def judge(self, prompt_id: str, draft: str, facts: Dict[str, Any]) -> Dict[str, Any]:
        # Quality judgment is optional from the agent; default to neutral.
        facts = {**facts, "draft": draft}
        raw = self._run_task(f"{prompt_id}-judge", facts.get("resource", prompt_id), facts)
        try:
            return json.loads(raw) if raw.strip() else {"score": 0.5}
        except json.JSONDecodeError:
            return {"score": 0.5}

    def extract(self, transcript: str) -> List[Dict[str, Any]]:
        raw = self._run_task("memory-extract", "session", {"transcript": transcript})
        out = []
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out


class SubprocessBackend:
    """Synchronous: invokes an agent CLI (droid/opencode/claude exec).

    Convenience for interactive use where you want results inline. The agent
    must support a headless `exec` / `run` mode that takes a prompt and exits.
    Falls back to FileQueueBackend if the CLI is missing.
    """

    def __init__(self, bundle: OKFBundle, cli: str = "droid"):
        self.bundle = bundle
        self.cli = cli
        self._fallback = FileQueueBackend(bundle)

    def _exec(self, prompt: str, timeout: int = 120) -> str:
        try:
            if self.cli == "claude":
                # Real Claude Code CLI: non-interactive mode is `-p`/`--print`,
                # not an `exec` subcommand. Scoped to read-only tools since this
                # call only needs to read source and print synthesized prose.
                cmd = [self.cli, "-p", prompt, "--allowedTools", "Read,Glob,Grep"]
            elif self.cli == "droid":
                # Factory's droid CLI: `exec` takes the prompt positionally,
                # there is no --prompt flag. Default (no --auto) mode is
                # already read-only, which is all this call needs.
                cmd = [self.cli, "exec", prompt]
            else:
                cmd = [self.cli, "exec", "--prompt", prompt]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode == 0:
                return result.stdout
        except (FileNotFoundError, subprocess.SubprocessError):
            pass
        return ""  # signal fallback

    def synthesize(self, prompt_id: str, facts: Dict[str, Any], timeout: int = 120) -> str:
        prompt = _build_prompt(prompt_id, facts)
        out = self._exec(prompt, timeout)
        return out or self._fallback.synthesize(prompt_id, facts, timeout)

    def revise(self, prompt_id: str, draft: str, errors: List[str], facts: Dict[str, Any]) -> str:
        prompt = _build_prompt(f"{prompt_id}-revise", {**facts, "previous_draft": draft, "errors": errors})
        out = self._exec(prompt)
        return out or self._fallback.revise(prompt_id, draft, errors, facts)

    def judge(self, prompt_id: str, draft: str, facts: Dict[str, Any]) -> Dict[str, Any]:
        raw = self._exec(_build_prompt(f"{prompt_id}-judge", {**facts, "draft": draft}))
        if not raw:
            return self._fallback.judge(prompt_id, draft, facts)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"score": 0.5}

    def extract(self, transcript: str) -> List[Dict[str, Any]]:
        raw = self._exec(_build_prompt("memory-extract", {"transcript": transcript}))
        if not raw:
            return self._fallback.extract(transcript)
        return [json.loads(l) for l in raw.splitlines() if l.strip().startswith("{")]


def _build_prompt(prompt_id: str, facts: Dict[str, Any]) -> str:
    """Render a minimal prompt. The agent's skill provides the full template.

    We send the prompt_id + facts; the loaded skill maps prompt_id to the real
    instructions. This keeps prompt wording out of codegraph (versionable in SKILL.md).

    The output spec (e.g. the compass 5-section format) is reused verbatim from
    the file-queue task body (`task_mod._output_spec`) so both backends produce
    the same shape -- otherwise a synchronous subprocess run free-styles its own
    headings instead of matching the rest of the corpus.
    """
    facts_str = json.dumps(facts, indent=2, default=str)
    spec = task_mod._output_spec(prompt_id)
    return (
        f"[codegraph task: {prompt_id}]\n\n"
        f"Use the codegraph skill to process this. Facts (graph-grounded):\n{facts_str}\n\n"
        f"## Output spec\n{spec}\n\n"
        f"Output ONLY the result content, nothing else."
    )
