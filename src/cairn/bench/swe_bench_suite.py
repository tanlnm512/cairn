"""SWE-bench bench suite: deterministic agent-effort loop over task inputs.

Runs the loader's five-field task dicts (``instance_id``, ``repo``,
``base_commit``, ``problem_statement``, ``environment_setup_commit``) through
the agent-suite harness (:mod:`cairn.bench.agent_suite`) — a cairn-equipped
arm vs a grep/read-only control arm — with no LLM, no network, no
subprocesses. ``tasks`` and ``workspaces`` are injected by the caller (the
CLI maps the loader's checked-out caches); this module never fetches and
never imports ``datasets``.

Per task, the probe target is the first code-like identifier in the
problem statement (contains an underscore, a digit, or internal/all-uppercase;
first-occurrence order, lexical determinism). The cairn arm plays the fixed
locator sequence ``find_definition`` -> ``get_callers`` ->
``impact_analysis(max_depth=3)``; the control arm greps the same name and
reads the matched files. Each arm runs ``runs`` times; per-task effort is
the run median, and ``medians`` are the cross-task medians.

Effort accounting matches :mod:`cairn.bench.agent_suite` (tool calls, JSON
payload chars capped at ``MAX_RESULT_CHARS``, token proxy chars /
:data:`CHARS_PER_TOKEN`) with one addition: uuid-minted symbol/file/repo ids
are masked before the char count. The ids differ across fresh builds of an
identical corpus, so counting them would make ``est_tokens`` a function of
build randomness; masked, calls and est_tokens reproduce exactly across
reruns (FR-002) while every stable byte still counts.

Env discipline matches ``run_agent_suite``: ``CAIRN_DB``,
``CAIRN_EMBED_BACKEND``, ``CAIRN_RERANK`` are pinned for the build+queries
and snapshot/restored around the run.
"""

from __future__ import annotations

import os
import re
import sqlite3
import statistics
import time
from collections import Counter
from typing import Any, Callable, Dict, List, Mapping, Sequence

from .agent_suite import (
    CHARS_PER_TOKEN,
    ArmEffort,
    TaskEffort,
    _CairnArm,
    _ControlAgent,
    _payload_chars,
    _result_cap,
)

# Token candidates in a problem statement and the code-like filter: plain
# English words never carry an underscore, digit, or internal uppercase.
_TOKEN_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# uuid4().hex minted for symbols/files/repos/edges; masked before counting.
_UUID_HEX_RX = re.compile(r"[0-9a-f]{32}")


def _is_code_like(token: str) -> bool:
    return (
        "_" in token
        or any(ch.isdigit() for ch in token)
        or token.isupper()
        or any(ch.isupper() for ch in token[1:])
    )


def _probe_target(task: Mapping[str, Any]) -> str:
    """The task's probe target: first code-like identifier in the statement."""
    for token in _TOKEN_RX.findall(task.get("problem_statement", "")):
        if _is_code_like(token):
            return token
    return ""


def _redact_ids(result: Any) -> Any:
    """Copy of a query result with uuid-hex id values masked as ``<id>``."""
    if isinstance(result, sqlite3.Row):
        return _redact_ids(dict(result))
    if isinstance(result, dict):
        return {k: _redact_ids(v) for k, v in result.items()}
    if isinstance(result, (list, tuple)):
        return [_redact_ids(v) for v in result]
    if isinstance(result, str) and _UUID_HEX_RX.fullmatch(result):
        return "<id>"
    return result


class _StableCairnArm(_CairnArm):
    """Cairn arm whose context chars ignore build-volatile id values."""

    def __call__(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        result = fn(*args, **kwargs)
        self.calls += 1
        self.chars += min(_payload_chars(_redact_ids(result)), _result_cap())
        return result


def _cairn_probes(conn: sqlite3.Connection, arm: _StableCairnArm, target: str) -> Any:
    from cairn.graph import queries as q

    arm(q.find_definition, conn, target, limit=10)
    arm(q.get_callers, conn, target, limit=100)
    return arm(q.impact_analysis, conn, target, max_depth=3, limit=100)


def _control_probes(agent: _ControlAgent, target: str) -> None:
    agent.read(agent.grep(target))


def _median_effort(samples: Sequence[Dict[str, Any]]) -> ArmEffort:
    """Run-median effort as an ArmEffort (est_tokens = chars // 4)."""
    chars = int(statistics.median(s["chars"] for s in samples))
    return ArmEffort(
        tool_calls=int(statistics.median(s["calls"] for s in samples)),
        chars=chars,
        est_tokens=chars // CHARS_PER_TOKEN,
        wall_seconds=float(statistics.median(s["wall"] for s in samples)),
    )


def _cross_task_medians(rows: List[dict]) -> Dict[str, dict]:
    """Cross-task median effort per arm, in the ArmEffort payload shape."""
    return {
        arm: ArmEffort(
            tool_calls=statistics.median(r[arm]["tool_calls"] for r in rows),
            chars=statistics.median(r[arm]["chars"] for r in rows),
            est_tokens=statistics.median(r[arm]["est_tokens"] for r in rows),
            wall_seconds=statistics.median(r[arm]["wall_ms"] for r in rows) / 1000,
        ).to_dict()
        for arm in ("cairn", "control")
    }


def run_swe_bench_suite(
    tasks: Sequence[Mapping[str, Any]],
    workspaces: Mapping[str, str],
    db_path: str,
    *,
    runs: int = 3,
) -> dict:
    """Run the deterministic SWE-bench suite over injected tasks.

    ``tasks`` is a sequence of the loader's five-field task dicts;
    ``workspaces`` maps each ``instance_id`` to its checked-out repo tree.
    Builds the graph per task's workspace into ``db_path`` (per-repo rebuild,
    closure included), plays both scripted arms ``runs`` times, and returns
    the report payload: ``tasks`` (input-order rows keyed by ``instance_id``
    with ArmEffort-shaped ``cairn``/``control`` arms and ``reduction``),
    ``medians`` (cross-task, both arms), ``runs``, ``chars_per_token``.

    Raises ValueError on duplicate instance ids, an instance id without a
    mapped workspace, or ``runs < 1``. Deterministic in calls/est_tokens
    across fresh builds; wall time is advisory. Env pins are restored before
    return.
    """
    task_list = list(tasks)
    ids = [t["instance_id"] for t in task_list]
    if not ids:
        raise ValueError("tasks: expected a non-empty sequence of task dicts")
    if runs < 1:
        raise ValueError(f"runs: expected >= 1, got {runs}")
    dupes = sorted(i for i, n in Counter(ids).items() if n > 1)
    if dupes:
        raise ValueError(
            "tasks: duplicate instance id(s): " + ", ".join(repr(i) for i in dupes)
        )
    missing = [i for i in ids if i not in workspaces]
    if missing:
        raise ValueError(
            "workspaces: no workspace mapped for instance id(s): "
            + ", ".join(repr(i) for i in missing)
        )

    # Same env discipline as run_agent_suite: pin DB/backend for the build
    # and the reranker OFF; snapshot + restore around the whole run.
    _saved = {
        var: os.environ.get(var)
        for var in ("CAIRN_DB", "CAIRN_EMBED_BACKEND", "CAIRN_RERANK")
    }

    def _restore_env() -> None:
        for var, val in _saved.items():
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val
        from cairn.graph import embeddings as _emb

        _emb.reset_backend_cache()

    os.environ["CAIRN_DB"] = db_path
    os.environ["CAIRN_EMBED_BACKEND"] = "hash"
    os.environ["CAIRN_RERANK"] = "0"

    from cairn.graph import embeddings as emb
    from cairn.graph.builder import build_graph
    from cairn.graph.dataflow import build_transitive_closure
    from cairn.graph.schema import get_db

    emb.reset_backend_cache()
    try:
        rows: List[dict] = []
        for task in task_list:
            instance_id = task["instance_id"]
            workspace = workspaces[instance_id]
            target = _probe_target(task)

            build_graph(workspace=workspace, db_path=db_path)
            conn = get_db(db_path)
            try:
                build_transitive_closure(conn)
                cairn_runs: List[Dict[str, Any]] = []
                control_runs: List[Dict[str, Any]] = []
                for _ in range(runs):
                    arm = _StableCairnArm(conn)
                    t0 = time.perf_counter()
                    _cairn_probes(conn, arm, target)
                    cairn_runs.append(
                        {
                            "calls": arm.calls,
                            "chars": arm.chars,
                            "wall": time.perf_counter() - t0,
                        }
                    )

                    agent = _ControlAgent(workspace)
                    t0 = time.perf_counter()
                    _control_probes(agent, target)
                    control_runs.append(
                        {
                            "calls": agent.calls,
                            "chars": agent.chars,
                            "wall": time.perf_counter() - t0,
                        }
                    )
            finally:
                conn.close()

            effort = TaskEffort(
                label=instance_id,
                question=task.get("problem_statement", ""),
                cairn=_median_effort(cairn_runs),
                control=_median_effort(control_runs),
            )
            row = effort.to_dict()
            row["instance_id"] = instance_id
            rows.append(row)

        return {
            "tasks": rows,
            "medians": _cross_task_medians(rows),
            "runs": runs,
            "chars_per_token": CHARS_PER_TOKEN,
        }
    finally:
        _restore_env()
