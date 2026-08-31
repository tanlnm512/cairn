# Tech Spec: <name>

**Spec**: [spec.md](spec.md) | **Created**: YYYY-MM-DD
**Every file/symbol citation below must come verbatim from [survey.md](survey.md)
or a grep run in this session — never from memory.**

## Architecture
<Components and data flow for this feature — a mermaid diagram plus one
paragraph. Show how it sits in the existing system (grounded in survey.md).>

## Solution
### Chosen approach
<What we're building and why this shape. Map FR-### coverage. Informed by
[research.md](research.md) when it exists.>

### Alternatives rejected
| Alternative | Why rejected |
|-------------|--------------|
| <...>       | <one line>   |

## Impact analysis
<!-- Blast radius: what existing code/symbols this touches, who calls them,
     what breaks if the approach is wrong. Use the workspace's graph /
     code-intelligence tool when one is available, else grep. Ground every
     claim in survey evidence. -->

## Code guide
<!-- Per area: where work lands, verified to exist by survey. -->
### <Area 1>
- Touches: `the \`symbol\` function in <file>` (survey.md evidence)
- Approach: <...>
- Verify before implementing: `<exact command>`
- Pitfalls: <known traps, from survey or history>

### <Area 2>
...

## References
<From research.md: docs, issues, benchmarks, prior art, related specs — each
with URL/path and one line on why it matters.>

## Decisions
<!-- ADR-lite. Append-only: decisions made during implementation land here too. -->
### D-001: <decision>
- **Context**: <why this needed deciding>
- **Decision**: <what was chosen>
- **Consequences**: <what this commits us to / rules out>
