# Cairn: Task Queue — Processing LLM Synthesis Tasks

Cairn is **agent-decoupled**: it never calls an LLM directly. Instead it
queues synthesis tasks as OKF files. Any agent (opencode, Claude, droid) with
this skill can process them. A deterministic critic fact-checks every result
**before it ships** — but its check is scoped: it verifies file and symbol
*references* (the backtick-quoted names) actually exist in the graph. It does
**not** vet plain-prose claims, so interpretive sentences are on your honesty.
Keep references precise and backtick-quoted; keep prose clearly hedged.

## How to process pending tasks

1. **See what's queued:**
   ```bash
   cg task list --status pending
   ```

2. **Inspect a task** (shows the graph-grounded facts + output spec):
   ```bash
   cg task show <task_id>
   ```
   The `## Facts` section is **ground truth from the L1 graph**. You may ONLY
   reference the files and symbols listed there. Inventing file paths or symbol
   names is the one hard rule — the critic will catch and reject them.

3. **Claim it** (sets status in-progress, prevents double-processing):
   ```bash
   cg task claim <task_id> --as <your-name>
   ```

4. **Synthesize the result** following the task's `## Output spec`. For a
   `compass-synthesize` task, write a 25-35 line compass with the 5 sections.
   For a `flow-synthesize` task, write a 5-section flow compass using the
   traced call chain in facts.chain. Write the result to a file.

5. **Submit it** — cairn stores the result and the critic runs:
   ```bash
   cg task complete <task_id> --result-file <path>
   ```

6. If the critic found factual errors, cairn queues a `*-revise` task
   containing your draft + the error list. Process it the same way. Up to 3
   revise cycles; after that the task is dropped.

## Task kinds

| Kind | Output |
|------|--------|
| `compass-synthesize` | 5-section module compass file (25-35 lines) |
| `compass-revise` | Fixed compass addressing listed errors |
| `flow-synthesize` | 5-section flow compass (call chain, branches, terminals) |
| `flow-revise` | Fixed flow compass addressing listed errors |
| `wiki` | Architectural wiki article |
| `memory-extract` | JSON lines: `{type, title, body, confidence}` per candidate |
| `memory-critic` | JSON lines: `{title, keep, score, reason}` per draft |

## Rule: facts are sacred, prose is yours

- Files/symbols in the compass body MUST come from the Facts section.
- You add value in *interpretation*: why a file matters, what's non-obvious,
  what to watch out for. That judgment is what the graph can't produce.
- If you genuinely need a file/symbol not in the facts, query it first:
  `cg def <name>` or `cg search <pattern>`, and only cite it if it resolves.
- Plain-prose claims (no backticks) are NOT checked by the critic. If you assert something factual in prose, you are responsible for it being true.
