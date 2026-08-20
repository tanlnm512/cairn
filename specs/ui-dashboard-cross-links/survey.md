# Survey: ui-dashboard-cross-links

**Created**: 2026-08-20 | **Baseline**: cairn-intel 0.12.1 @ `d781383`
Phase-A output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item Q1: "History/tokens/chains templates carry zero links — the dead ends FR-001/FR-002 target"
  evidence: grep -n "href" src/cairn/dashboard/templates/history.html
    src/cairn/dashboard/templates/tokens.html src/cairn/dashboard/templates/chains.html → no matches
  status: TODO
  verify: grep -n "href" src/cairn/dashboard/templates/{history,tokens,chains}.html
  gap: rows render plain text for tool_name and session_id — no anchors anywhere

item Q2: "The projects→graph row link already ships — FR-003 is a regression guard"
  evidence: src/cairn/dashboard/templates/projects.html — "<td><a
    href=\"/graph?scope=repo&amp;repo={{ p.id | urlencode }}\">{{ p.name }}</a></td>"
  status: DONE
  verify: grep -n "scope=repo" src/cairn/dashboard/templates/projects.html
  gap: None for FR-003's letter; a test asserting the link exists is the missing guard

item Q3: "History's tool/session URL filters already exist — the tokens→history destination"
  evidence: src/cairn/dashboard/app.py:202-207 — "def history(request):
    tool = request.query_params.get(\"tool\", \"\").strip() or None
    session = request.query_params.get(\"session\", \"\").strip() or None"
  status: DONE
  verify: grep -n "query_params.get" src/cairn/dashboard/app.py
  gap: None — /history?tool=X and /history?session=Y filter today

item Q4: "Chains has NO session URL param — the history→chains destination needs one added"
  evidence: src/cairn/dashboard/app.py:228-238 — def chains(request) reads no
    query params; calls get_session_chains(conn) unfiltered
  evidence: src/cairn/dashboard/data.py:379 — "def get_session_chains(conn:
    sqlite3.Connection) -> List[dict]" takes no filter arguments
  status: TODO
  verify: sed -n 228,238p src/cairn/dashboard/app.py
  gap: FR-002 needs a session filter param on /chains plus a data-layer filter
    (or template-side highlight) before a row link can focus a session

item Q5: "Graph URL surface: scope/focus/repo/depth — all read by the graph handler"
  evidence: src/cairn/dashboard/app.py:162-169 — scope from query_params
    validated against GRAPH_SCOPES; focus, repo, depth read and passed to get_graph
  evidence: src/cairn/dashboard/data.py:27 — "GRAPH_SCOPES = (\"symbol\",
    \"module\", \"impact\", \"deps\", \"repo\")"
  status: DONE
  verify: grep -n "GRAPH_SCOPES" src/cairn/dashboard/data.py
  gap: symbol-neighborhood preset exists via scope=symbol+focus — FR-004's
    inspect action navigates to it; needs a clickable affordance per node only

item Q6: "Graph view is the orphan — absent from both navs"
  evidence: src/cairn/dashboard/templates/base.html:12-19 — nav lists
    /projects /history /tokens /chains /health /memory /tasks — no /graph
  evidence: src/cairn/dashboard/templates/index.html — link-list lists the
    same seven destinations, no /graph
  status: TODO
  verify: grep -c "graph" src/cairn/dashboard/templates/base.html src/cairn/dashboard/templates/index.html
  gap: FR-006 adds /graph to the shared nav and the landing list

item Q7: "Node click handling in app.js — where FR-004's inspect action mounts"
  evidence: src/cairn/dashboard/static/app.js:48-55 — "new vis.Network(canvas,
    { nodes: nodes, edges: edges }, { autoResize: true, interaction: { dragNodes:
    true, dragView: true, zoomView: true } });" — no event listeners registered
  status: TODO
  verify: grep -n "on(" src/cairn/dashboard/static/app.js
  gap: no select/click events wired; the inspect action is new client behavior
```

## Supporting evidence

```
URL parameter inventory (verified this session):
- /history: tool, session (exact-match filters)
- /graph: scope (validated), focus, repo, depth (int)
- /chains, /tokens: none

Legacy session shape (verified this session):
- all 251 tool_metrics rows in the dev store carry session 'unknown' — a
  session link targets the giant chain set ui-dashboard-traffic-scale FR-004
  bounds; the link must stay functional for that shape
```

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
