"""Agent-effort benchmark: tool calls + context cost, cairn vs grep/read-only.

Answers "how much agent harness does answering task-shaped questions cost,
cairn's query tools vs a plain grep+read loop?". Where the perf suite measures
*latency* of individual operations, this suite measures *agent effort*: how
many tool calls a scripted agent issues per question and how much context
those calls return (token proxy: chars / 4 — the same ~4-chars-per-token
approximation the embeddings chunker uses, and the one
``metric_buffering.MAX_RESULT_CHARS`` is calibrated against).

Deterministic and CI-safe: no LLM, no network, no subprocesses. Both arms are
fixed scripts over the same synthetic corpus (``generate_corpus``):

- **cairn arm** — the queries-layer call sequence an agent would make per
  task (``find_definition`` / ``get_callers`` / ``impact_analysis`` /
  ``trace_flow`` / ``semantic_search``). Each call counts once; payload chars
  are the JSON-serialized result an MCP client would receive, capped at
  ``MAX_RESULT_CHARS`` so the result-size ceiling agents actually hit in
  deployment is mirrored honestly.
- **control arm** — a scripted grep/read loop (stdlib ``re`` over the corpus
  files, deterministic sorted order) answering the same question without
  cairn: grep the symbol name, read the matched files, follow hops by grepping
  the names those files define or call. Each grep invocation and each file
  read counts once; chars are the content of files actually read (matched
  files only — an agent does not read the whole repo).

The six tasks are chosen so both arms can genuinely answer them, including
one (common-name impact) where the control arm *must* over-read — every file
mentions ``method_N`` — which is the honest point of the comparison: cairn's
resolved-edge answer vs grep's lexical match on a colliding name.

Report medians over ``runs`` measured runs; the call/char counts are
deterministic within a build (same corpus + seed), wall time is not. One
caveat: symbol ids are random per build, so a tie-bounded result set
(``semantic_search``'s limit cutoff) can swap one near-tied row between
rebuilds — observed drift is a few chars, far below the 15% compare gate.
Reranker and embed backend are pinned (``CAIRN_RERANK=0``, hash backend) so
results do not depend on optional extras or machine state; env vars are
snapshot/restored like the perf suite does.
"""
from __future__ import annotations

import json
import os
import random
import re
import sqlite3
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Sequence

from .corpus import DEFAULT_SEED, corpus_stats

# Token proxy shared by the embeddings chunker and the MCP result cap: ~4
# chars per token. Every "est_tokens" number in this suite is chars / 4.
CHARS_PER_TOKEN = 4

# Bound on how many names one control-arm alternation grep chases per hop.
# Keeps the regex (and the recipe) bounded on dense corpora; the first 40 in
# sorted order — deterministic. Generous vs a real agent's attention.
_MAX_GREP_NAMES = 40

# Top-level definitions ("class X" / "def X") and call sites ("X(") the
# control arm extracts from file text to decide its next grep.
_DEF_RX = re.compile(r"^\s*(?:class|def)\s+([A-Za-z_]\w*)", re.MULTILINE)
_CALL_RX = re.compile(r"([A-Za-z_]\w*)\s*\(")


def _result_cap() -> int:
    """The deployment result-size ceiling (lazy import; reads env once)."""
    from ..mcp_server.metric_buffering import MAX_RESULT_CHARS

    return MAX_RESULT_CHARS


def _payload_chars(result: Any) -> int:
    """Chars of context one cairn call returns, as the agent receives it.

    Rows are rendered to dicts and JSON-serialized — the same content an MCP
    client sees (the tool layer serializes rows to text of comparable size).
    """
    try:
        if isinstance(result, (list, tuple)):
            data = [dict(r) if isinstance(r, sqlite3.Row) else r for r in result]
        else:
            data = result
        return len(json.dumps(data, default=str))
    except (TypeError, ValueError):
        return len(str(result))


class _CairnArm:
    """Scripted cairn agent: counts calls and returned context chars."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.calls = 0
        self.chars = 0

    def __call__(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        result = fn(*args, **kwargs)
        self.calls += 1
        # Mirror the MCP layer's hard cap: an agent never receives more than
        # MAX_RESULT_CHARS per call, so the benchmark must not either.
        self.chars += min(_payload_chars(result), _result_cap())
        return result


class _ControlAgent:
    """Scripted grep/read-only agent (stdlib re, no subprocess).

    Deterministic by construction: files are visited in sorted order and
    every hop-following rule is a fixed regex recipe. ``grep`` is one tool
    call returning the matched files (an rg-style invocation); ``read`` is one
    tool call per file not read before in this task.
    """

    def __init__(self, workspace: str):
        self.workspace = Path(workspace)
        self.calls = 0
        self.chars = 0
        self._read: set = set()

    def grep(self, pattern: str, *, regex: bool = False) -> List[Path]:
        """One grep tool call: files (sorted) whose text matches ``pattern``."""
        self.calls += 1
        rx = re.compile(pattern) if regex else None
        hits: List[Path] = []
        for path in sorted(self.workspace.rglob("*.py")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            found = rx.search(text) if rx is not None else pattern in text
            if found:
                hits.append(path)
        return hits

    def read(self, paths: Iterable[Path]) -> List[str]:
        """One tool call per not-yet-read file; returns the new file texts."""
        texts: List[str] = []
        for path in paths:
            if path in self._read:
                continue
            self._read.add(path)
            self.calls += 1
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            self.chars += len(text)
            texts.append(text)
        return texts

    @staticmethod
    def grep_pattern(names: Sequence[str]) -> str:
        """A single alternation pattern for a hop's names (bounded, sorted)."""
        bounded = sorted(set(names))[:_MAX_GREP_NAMES]
        return "|".join(re.escape(n) for n in bounded)


@dataclass
class _Task:
    """One task-shaped question with both arms' scripted call sequences."""

    label: str
    question: str
    cairn_calls: Callable[[sqlite3.Connection, _CairnArm, Dict[str, str]], Any]
    control_calls: Callable[[_ControlAgent, Dict[str, str]], Any]


# --- the six task recipes --------------------------------------------------
# Each pair answers the SAME question; ``t`` carries the seeded target names.


def _cairn_definition(conn, arm, t):
    from cairn.graph import queries as q

    return arm(q.find_definition, conn, t["target_class"], limit=10)


def _control_definition(agent, t):
    hits = agent.grep(f"class {t['target_class']}:")
    agent.read(hits)


def _cairn_callers(conn, arm, t):
    from cairn.graph import queries as q

    arm(q.find_definition, conn, t["target_class"], limit=5)
    return arm(q.get_callers, conn, t["target_class"], limit=100)


def _control_callers(agent, t):
    hits = agent.grep(t["target_class"])
    agent.read(hits)


def _cairn_impact(conn, arm, t):
    from cairn.graph import queries as q

    arm(q.find_definition, conn, t["target_class"], limit=5)
    return arm(q.impact_analysis, conn, t["target_class"], max_depth=3, limit=100)


def _control_impact(agent, t):
    # Fixed recipe: grep the target, read matches, then follow hops by
    # grepping the names those files *define* (things that, if used
    # elsewhere, make those files impacted too), for 3 rounds.
    chasing: set = {t["target_class"]}
    for _round in range(3):
        if not chasing:
            break
        hits = agent.grep(agent.grep_pattern(chasing), regex=True)
        texts = agent.read(hits)
        defined: set = set()
        for text in texts:
            defined.update(_DEF_RX.findall(text))
        chasing = defined - chasing if texts else set()


def _cairn_flow(conn, arm, t):
    from cairn.graph import queries as q

    # entry_id pins WHICH same-named method is the entry: names are shared
    # by every class, and an unpinned lookup returns whichever row the
    # (insertion-ordered) table yields first — not stable across rebuilds.
    return arm(q.trace_flow, conn, t["entry_method"], max_depth=4, limit=100,
               entry_id=t["entry_id"])


def _control_flow(agent, t):
    # Fixed recipe: grep the entry, read matches, then follow hops by grepping
    # the names those files *call*, for 4 rounds (mirroring max_depth=4).
    chasing: set = {t["entry_method"]}
    seen: set = set(chasing)
    for _round in range(4):
        if not chasing:
            break
        hits = agent.grep(agent.grep_pattern(chasing), regex=True)
        texts = agent.read(hits)
        called: set = set()
        for text in texts:
            called.update(_CALL_RX.findall(text))
        chasing = called - seen
        seen |= chasing


def _cairn_concept(conn, arm, t):
    from cairn.graph import queries as q

    return arm(q.semantic_search, conn, t["concept_query"], limit=10)


def _control_concept(agent, t):
    hits = agent.grep(t["concept_query"])
    agent.read(hits)


def _cairn_common_impact(conn, arm, t):
    from cairn.graph import queries as q

    # The documented escalation: precise impact first (resolved edges only —
    # for a shared method name it is honestly empty), then fuzzy=True for the
    # name-only candidate list.
    arm(q.impact_analysis, conn, t["common_method"], max_depth=3, limit=100)
    return arm(q.impact_analysis, conn, t["common_method"], max_depth=3, limit=100, fuzzy=True)


def _control_common_impact(agent, t):
    # Grep cannot tell definition from call site on a colliding name: every
    # file that defines OR calls method_N matches, so the control arm reads
    # (nearly) the whole corpus. That over-read is the honest comparison.
    hits = agent.grep(f"{t['common_method']}(")
    agent.read(hits)


def _build_tasks(targets: Dict[str, str]) -> List[_Task]:
    """The six tasks, with question text bound to the seeded targets."""
    return [
        _Task(
            label="definition-lookup",
            question=f"Where is {targets['target_class']} defined?",
            cairn_calls=_cairn_definition,
            control_calls=_control_definition,
        ),
        _Task(
            label="caller-enumeration",
            question=f"Which code calls {targets['target_class']}?",
            cairn_calls=_cairn_callers,
            control_calls=_control_callers,
        ),
        _Task(
            label="blast-radius-depth3",
            question=f"What breaks if {targets['target_class']} changes (callers of callers, depth 3)?",
            cairn_calls=_cairn_impact,
            control_calls=_control_impact,
        ),
        _Task(
            label="entry-to-leaf-flow",
            question=f"What does {targets['entry_method']} execute end-to-end?",
            cairn_calls=_cairn_flow,
            control_calls=_control_flow,
        ),
        _Task(
            label="concept-search",
            question=f"Which code is related to the {targets['concept_query']} cluster?",
            cairn_calls=_cairn_concept,
            control_calls=_control_concept,
        ),
        _Task(
            label="common-name-impact",
            question=f"What breaks if {targets['common_method']} changes? (name shared by many definitions)",
            cairn_calls=_cairn_common_impact,
            control_calls=_control_common_impact,
        ),
    ]


def _select_targets(conn: sqlite3.Connection, seed: int) -> Dict[str, str]:
    """Pick the tasks' target symbols deterministically from the built graph.

    Caller/impact targets are classes with at least one resolved caller (so
    both arms have something to find); the flow entry is a method with
    outgoing edges, pinned by symbol id (same-named methods would otherwise
    resolve to whichever row the insertion-ordered table yields first); the
    common-name target is the most-defined method name (ties broken
    lexically — in the generated corpus every ``method_N`` ties, so the pick
    is stable for any corpus size).
    """
    rng = random.Random(seed)
    caller_classes = [
        r["name"]
        for r in conn.execute(
            """SELECT s.name FROM symbols s
               WHERE s.kind = 'class'
                 AND EXISTS (SELECT 1 FROM edges e WHERE e.target_id = s.id)
               ORDER BY s.name"""
        )
    ]
    all_classes = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM symbols WHERE kind = 'class' ORDER BY name"
        )
    ]
    pool = caller_classes or all_classes or ["main"]
    if len(pool) >= 2:
        target_class, concept_class = rng.sample(pool, 2)
    else:
        target_class = concept_class = pool[0]

    flow_sources = [
        (r["name"], r["id"])
        for r in conn.execute(
            """SELECT s.name, s.id FROM symbols s
               JOIN edges e ON e.source_id = s.id
               WHERE s.kind = 'method'
               GROUP BY s.id ORDER BY s.qualified_name, s.id"""
        )
    ]
    if flow_sources:
        entry_method, entry_id = rng.choice(flow_sources)
    else:
        entry_method, entry_id = "method_0", ""

    common_row = conn.execute(
        """SELECT name FROM symbols WHERE kind = 'method'
           GROUP BY name ORDER BY COUNT(*) DESC, name LIMIT 1"""
    ).fetchone()
    common_method = common_row["name"] if common_row else "method_0"

    # Concept query = the module's class prefix ("Cls0007_3" -> "Cls0007"),
    # i.e. "everything about module 0007's class cluster".
    concept_query = concept_class.rsplit("_", 1)[0]
    return {
        "target_class": target_class,
        "concept_class": concept_class,
        "concept_query": concept_query,
        "entry_method": entry_method,
        "entry_id": entry_id,
        "common_method": common_method,
    }


# --- report shape ----------------------------------------------------------


@dataclass
class ArmEffort:
    """One arm's median effort for a task over the measured runs."""

    tool_calls: int
    chars: int
    est_tokens: int
    wall_seconds: float

    def to_dict(self) -> dict:
        return {
            "tool_calls": self.tool_calls,
            "chars": self.chars,
            "est_tokens": self.est_tokens,
            "wall_ms": round(self.wall_seconds * 1000, 1),
        }


@dataclass
class TaskEffort:
    """Both arms' median effort for one task + the cairn reduction."""

    label: str
    question: str
    cairn: ArmEffort
    control: ArmEffort

    def _reduction(self, cairn_val: float, control_val: float) -> float:
        if control_val <= 0:
            return 0.0
        return (1 - cairn_val / control_val) * 100

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "question": self.question,
            "cairn": self.cairn.to_dict(),
            "control": self.control.to_dict(),
            "reduction": {
                "calls_pct": round(self._reduction(self.cairn.tool_calls, self.control.tool_calls), 1),
                "tokens_pct": round(self._reduction(self.cairn.est_tokens, self.control.est_tokens), 1),
                "time_ratio": (
                    round(self.control.wall_seconds / self.cairn.wall_seconds, 1)
                    if self.cairn.wall_seconds > 0
                    else 0.0
                ),
            },
        }


@dataclass
class AgentReport:
    """Results of an agent-suite run: per-task effort, both arms, medians."""

    corpus: dict = field(default_factory=dict)
    seed: int = DEFAULT_SEED
    runs: int = 3
    embed_backend: str = "hash"
    tasks: List[TaskEffort] = field(default_factory=list)

    def _totals(self, arm: str) -> ArmEffort:
        return ArmEffort(
            tool_calls=sum(getattr(t, arm).tool_calls for t in self.tasks),
            chars=sum(getattr(t, arm).chars for t in self.tasks),
            est_tokens=sum(getattr(t, arm).est_tokens for t in self.tasks),
            wall_seconds=sum(getattr(t, arm).wall_seconds for t in self.tasks),
        )

    def to_dict(self) -> dict:
        cairn_total = self._totals("cairn")
        control_total = self._totals("control")
        reduction = TaskEffort("totals", "", cairn_total, control_total)
        return {
            "corpus": self.corpus,
            "seed": self.seed,
            "runs": self.runs,
            "embed_backend": self.embed_backend,
            "chars_per_token": CHARS_PER_TOKEN,
            "tasks": [t.to_dict() for t in self.tasks],
            "totals": {
                "cairn": cairn_total.to_dict(),
                "control": control_total.to_dict(),
                "reduction": reduction.to_dict()["reduction"],
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_table(self) -> str:
        """Render the report as a rich table via cli.display."""
        from ..cli.display import print_table

        rows = []
        for t in self.tasks:
            red = t.to_dict()["reduction"]
            rows.append([
                t.label,
                str(t.cairn.tool_calls),
                str(t.control.tool_calls),
                f"{t.cairn.est_tokens:,}",
                f"{t.control.est_tokens:,}",
                f"{red['tokens_pct']:.0f}%",
                f"{t.cairn.wall_seconds * 1000:.0f}",
                f"{t.control.wall_seconds * 1000:.0f}",
            ])
        c, k = self._totals("cairn"), self._totals("control")
        rows.append([
            "TOTAL",
            str(c.tool_calls),
            str(k.tool_calls),
            f"{c.est_tokens:,}",
            f"{k.est_tokens:,}",
            f"{(1 - c.est_tokens / k.est_tokens) * 100:.0f}%" if k.est_tokens else "-",
            f"{c.wall_seconds * 1000:.0f}",
            f"{k.wall_seconds * 1000:.0f}",
        ])
        print_table(
            f"cairn agent-effort benchmark  ({self.corpus.get('files', 0)} files,"
            f" {self.runs} runs, seed {self.seed:#x}, tokens = chars/{CHARS_PER_TOKEN})",
            columns=[
                "task", "cairn calls", "grep calls",
                "cairn tok", "grep tok", "tok saved",
                "cairn ms", "grep ms",
            ],
            rows=rows,
        )
        return self.to_json()


def compare_agent_reports(baseline: dict, current: dict, threshold: float = 0.15) -> dict:
    """Compare two agent-suite reports on the cairn arm's context cost.

    ``baseline``/``current`` are dicts from ``AgentReport.to_dict()``. The
    gated metric is per-task ``est_tokens`` (the effort a cairn change can
    regress — the control arm only changes when the corpus or recipe does).
    Returns {task label -> {baseline_tokens, current_tokens, delta_pct,
    regressed}}; "regressed" means current tokens exceed baseline by more
    than ``threshold`` (default 15%).
    """
    base = {t["label"]: t["cairn"]["est_tokens"] for t in baseline.get("tasks", [])}
    cur = {t["label"]: t["cairn"]["est_tokens"] for t in current.get("tasks", [])}
    result = {}
    for label, cur_tokens in cur.items():
        base_tokens = base.get(label)
        if base_tokens is None or base_tokens == 0:
            continue
        delta = (cur_tokens - base_tokens) / base_tokens
        result[label] = {
            "baseline_tokens": base_tokens,
            "current_tokens": cur_tokens,
            "delta_pct": round(delta * 100, 1),
            "regressed": delta > threshold,
        }
    return result


# --- the suite --------------------------------------------------------------


def run_agent_suite(
    workspace: str,
    db_path: str,
    *,
    runs: int = 3,
    seed: int = DEFAULT_SEED,
    embed_backend: str = "hash",
    progress=None,
) -> AgentReport:
    """Run the agent-effort benchmark against ``workspace``.

    Assumes the corpus already exists at ``workspace`` (use
    :func:`generate_corpus` first, or point at a real repo). Builds the graph,
    hash embeddings, and the transitive-closure index once into ``db_path``
    (the deployment state users query against), then runs each task's two
    scripted arms ``runs`` times and reports medians. ``seed`` selects the
    task targets from the built graph (the corpus itself is seeded by
    ``generate_corpus``'s own default).
    """
    # Same env discipline as the perf suite: pin the DB/backend for the
    # build+embed, and pin the reranker OFF so results never depend on the
    # optional cross-encoder or its auto-enable marker. Snapshot + restore.
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
    os.environ["CAIRN_EMBED_BACKEND"] = embed_backend
    os.environ["CAIRN_RERANK"] = "0"

    from cairn.graph import embeddings as emb
    from cairn.graph.builder import build_graph
    from cairn.graph.dataflow import build_transitive_closure
    from cairn.graph.schema import get_db

    emb.reset_backend_cache()
    try:
        build_stats = build_graph(workspace=workspace, db_path=db_path)
        if progress:
            progress("build_done", symbols=build_stats.get("symbols", 0),
                     edges=build_stats.get("edges", 0))

        conn = get_db(db_path)
        try:
            emb.embed_all(conn, reap_orphans=False)
            build_transitive_closure(conn)

            targets = _select_targets(conn, seed)
            report = AgentReport(
                corpus=corpus_stats(Path(workspace)),
                seed=seed,
                runs=runs,
                embed_backend=embed_backend,
            )

            for task in _build_tasks(targets):
                cairn_runs: List[dict] = []
                control_runs: List[dict] = []
                for _ in range(runs):
                    arm = _CairnArm(conn)
                    t0 = time.perf_counter()
                    task.cairn_calls(conn, arm, targets)
                    cairn_runs.append({
                        "calls": arm.calls,
                        "chars": arm.chars,
                        "wall": time.perf_counter() - t0,
                    })

                    agent = _ControlAgent(workspace)
                    t0 = time.perf_counter()
                    task.control_calls(agent, targets)
                    control_runs.append({
                        "calls": agent.calls,
                        "chars": agent.chars,
                        "wall": time.perf_counter() - t0,
                    })

                def _median_arm(samples: List[dict]) -> ArmEffort:
                    chars = int(statistics.median(s["chars"] for s in samples))
                    return ArmEffort(
                        tool_calls=int(statistics.median(s["calls"] for s in samples)),
                        chars=chars,
                        est_tokens=chars // CHARS_PER_TOKEN,
                        wall_seconds=float(statistics.median(s["wall"] for s in samples)),
                    )

                report.tasks.append(TaskEffort(
                    label=task.label,
                    question=task.question,
                    cairn=_median_arm(cairn_runs),
                    control=_median_arm(control_runs),
                ))
                if progress:
                    last = report.tasks[-1]
                    progress("task_done", task=last.label,
                             cairn_calls=last.cairn.tool_calls,
                             control_calls=last.control.tool_calls)

            if progress:
                progress("agent_done", tasks=len(report.tasks))
            return report
        finally:
            conn.close()
    finally:
        _restore_env()
