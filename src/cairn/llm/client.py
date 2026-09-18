"""LLM client: the boundary between cairn and any agent/LLM."""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Dict, List, Protocol

from ..okf.bundle import OKFBundle
from . import tasks as task_mod


_DEFAULT_TIMEOUT = 120.0


class LLMClient(Protocol):
    """The contract every call site uses. Prompt_id keys into the skill."""

    def synthesize(
        self, prompt_id: str, facts: Dict[str, Any], timeout: float = 120
    ) -> str: ...
    def revise(
        self,
        prompt_id: str,
        draft: str,
        errors: List[str],
        facts: Dict[str, Any],
        timeout: float = 120,
    ) -> str: ...
    def judge(
        self, prompt_id: str, draft: str, facts: Dict[str, Any], timeout: float = 120
    ) -> Dict[str, Any]: ...
    def extract(
        self, transcript: str, timeout: float = 120
    ) -> List[Dict[str, Any]]: ...


def get_client(bundle: OKFBundle) -> "LLMClient":
    """Return the configured client. Defaults to FileQueueBackend (decoupled)."""
    backend = os.environ.get("CAIRN_LLM_BACKEND", "file-queue").lower()
    if backend in ("droid", "opencode", "claude"):
        return SubprocessBackend(bundle, cli=backend)
    return FileQueueBackend(bundle)


def _parse_extract_lines(raw: str) -> List[Dict[str, Any]]:
    """Parse JSON-object lines, skipping malformed records."""
    records: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


class FileQueueBackend:
    """Decoupled: writes a Task, waits for any agent to complete it.

    Fully agent-agnostic. The agent loads the cairn skill, runs
    `cairn task list` / `cairn task claim` / `cairn task complete`. No subprocess spawn.
    """

    def __init__(self, bundle: OKFBundle, poll_interval: float = 2.0, max_wait: float = 600):
        self.bundle = bundle
        self.poll_interval = poll_interval
        self.max_wait = max_wait

    def _run_task(
        self,
        kind: str,
        resource: str,
        facts: Dict[str, Any],
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> str:
        task = task_mod.create_task(self.bundle, kind, resource, facts=facts)
        deadline = time.monotonic() + max(0.0, min(self.max_wait, timeout))
        while True:
            t = task_mod.get_task(self.bundle, task.id)
            if t and t.status == "done":
                result = task_mod.read_result(self.bundle, task.id)
                return result or ""
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(self.poll_interval, remaining))
        return ""  # timed out; caller falls back to deterministic output

    def _run_task_call(
        self, kind: str, resource: str, facts: Dict[str, Any], timeout: float
    ) -> str:
        if timeout == _DEFAULT_TIMEOUT:
            return self._run_task(kind, resource, facts)
        return self._run_task(kind, resource, facts, timeout=timeout)

    def synthesize(
        self, prompt_id: str, facts: Dict[str, Any], timeout: float = 120
    ) -> str:
        return self._run_task_call(
            prompt_id, facts.get("resource", prompt_id), facts, timeout
        )

    def revise(
        self,
        prompt_id: str,
        draft: str,
        errors: List[str],
        facts: Dict[str, Any],
        timeout: float = 120,
    ) -> str:
        facts = {**facts, "previous_draft": draft, "errors": errors}
        return self._run_task_call(
            f"{prompt_id}-revise", facts.get("resource", prompt_id), facts, timeout
        )

    def judge(
        self, prompt_id: str, draft: str, facts: Dict[str, Any], timeout: float = 120
    ) -> Dict[str, Any]:
        # Quality judgment is optional from the agent; default to neutral.
        facts = {**facts, "draft": draft}
        raw = self._run_task_call(
            f"{prompt_id}-judge", facts.get("resource", prompt_id), facts, timeout
        )
        try:
            return json.loads(raw) if raw.strip() else {"score": 0.5}
        except json.JSONDecodeError:
            return {"score": 0.5}

    def extract(self, transcript: str, timeout: float = 120) -> List[Dict[str, Any]]:
        raw = self._run_task_call(
            "memory-extract", "session", {"transcript": transcript}, timeout
        )
        return _parse_extract_lines(raw)


class SubprocessBackend:
    """Synchronous LLM backend that invokes an external agent CLI."""

    def __init__(self, bundle: OKFBundle, cli: str = "droid"):
        self.bundle = bundle
        self.cli = cli
        self._fallback = FileQueueBackend(bundle)

    def _exec(self, prompt: str, timeout: float = 120) -> str:
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

    def synthesize(
        self, prompt_id: str, facts: Dict[str, Any], timeout: float = 120
    ) -> str:
        prompt = _build_prompt(prompt_id, facts)
        out = self._exec(prompt, timeout)
        return out or self._fallback.synthesize(prompt_id, facts, timeout)

    def revise(
        self,
        prompt_id: str,
        draft: str,
        errors: List[str],
        facts: Dict[str, Any],
        timeout: float = 120,
    ) -> str:
        prompt = _build_prompt(
            f"{prompt_id}-revise", {**facts, "previous_draft": draft, "errors": errors}
        )
        out = self._exec(prompt, timeout)
        return out or self._fallback.revise(prompt_id, draft, errors, facts, timeout)

    def judge(
        self, prompt_id: str, draft: str, facts: Dict[str, Any], timeout: float = 120
    ) -> Dict[str, Any]:
        raw = self._exec(
            _build_prompt(f"{prompt_id}-judge", {**facts, "draft": draft}), timeout
        )
        if not raw:
            return self._fallback.judge(prompt_id, draft, facts, timeout)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"score": 0.5}

    def extract(self, transcript: str, timeout: float = 120) -> List[Dict[str, Any]]:
        raw = self._exec(
            _build_prompt("memory-extract", {"transcript": transcript}), timeout
        )
        if not raw:
            return self._fallback.extract(transcript, timeout)
        return _parse_extract_lines(raw)


def _build_prompt(prompt_id: str, facts: Dict[str, Any]) -> str:
    """Render a minimal prompt. The agent's skill provides the full template.

    Sends prompt_id + facts; the loaded skill maps prompt_id to the real
    instructions. The output spec is reused from task_mod._output_spec so both
    backends produce the same shape.
    """
    facts_str = json.dumps(facts, indent=2, default=str)
    spec = task_mod._output_spec(prompt_id)
    return (
        f"[cairn task: {prompt_id}]\n\n"
        f"Use the cairn skill to process this. Facts (graph-grounded):\n{facts_str}\n\n"
        f"## Output spec\n{spec}\n\n"
        f"Output ONLY the result content, nothing else."
    )
