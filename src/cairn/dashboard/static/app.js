/* Graph view: build vis-network DataSets from the server-serialized
   {nodes, edges, metadata} JSON block and render an interactive network
   (drag to pan, wheel to zoom). Double-clicking a node fetches
   /graph/neighbors and merges the reply into the live DataSets (id-keyed
   node updates, triple-deduped edge adds), then refreshes the shown
   counts. The #layout-control anchors toggle force-directed vs
   hierarchical (top-down) on the live network, camera preserved.
   Selecting a node (single click) swaps the #inspect-action hint for a
   plain anchor into that symbol's neighborhood view. No CDN
   — vis-network is vendored.

   Rendering model (GitNexus-inspired, ported to vis-network):
   - Kind coloring & filters: nodes color by symbol kind (function,
     method, class, interface, enum, module, external) from the same
     palette the legend renders; legend items are clickable filters
     that hide/show every node of that kind — vis hides connected
     edges with their endpoints.
   - Label discipline: dense graphs (>30 nodes with edges) label only
     the top quarter by degree; edgeless or small graphs label
     everything. Zooming out past 0.45 strips labels graph-wide (the
     sigma labelRenderThreshold equivalent); zooming back in restores.
   - Edgeless graphs (repo/module overviews) skip physics: nodes take
     deterministic golden-angle spiral positions — a spread
     constellation instead of a center clump.
   - Node size scales with degree (vis `value` scaling) so hubs read as
     hubs.
   - Theming: every canvas color — edge/font options and the per-kind
     node palette — derives from the stylesheet's CSS variables through
     the getComputedStyle token proxy (cssVar), so the palette ships
     with the theme, never with this script. shell.js broadcasts
     cairn:theme-changed on a data-theme flip; re-applying the
     theme-derived options and re-coloring every node from the tokens
     rides that event. Layout and physics state are never touched by a
     theme flip.
   - Reduced motion: the physics simulation is JS-driven motion the
     CSS media query cannot reach, so a matching
     prefers-reduced-motion: reduce disables physics outright and lays
     the graph out on the deterministic spiral (a static constellation,
     no stabilization animation); overlay zoom/fit skip their eased
     animation too. */
(function () {
  "use strict";
  var block = document.getElementById("graph-data");
  var canvas = document.getElementById("graph-canvas");
  if (!block || !canvas || typeof vis === "undefined") {
    return;
  }
  var data;
  try {
    data = JSON.parse(block.textContent);
  } catch (err) {
    return;
  }
  if (!data.nodes || !data.nodes.length) {
    return;
  }
  /* The token proxy: colors resolve from the live stylesheet at read
     time, so a theme flip re-reads to the new palette with no JS-side
     color state to keep in sync. */
  function cssVar(name) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(
      name
    );
    return v ? v.trim() : "";
  }
  function prefersReducedMotion() {
    return !!(
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );
  }

  /* ---- Kind coloring & filters ---- */

  /* Kind colors are theme tokens: each kind maps to a --kind-* variable
     declared in both theme blocks, and unknown kinds fall back to the
     muted text token (the pre-token palettes' shared default). */
  var KIND_TOKENS = {
    function: "--kind-function",
    method: "--kind-method",
    class: "--kind-class",
    interface: "--kind-interface",
    enum: "--kind-enum",
    module: "--kind-module",
    external: "--kind-external"
  };
  function kindColor(kind) {
    var token = KIND_TOKENS[kind];
    return (token && cssVar(token)) || cssVar("--text-3");
  }
  function kindOf(n) {
    return n.kind && KIND_TOKENS[n.kind] ? n.kind : "other";
  }

  var idKind = {};
  var kindCounts = {};
  var hiddenKinds = {};

  function registerKind(n) {
    /* merge() re-runs nodeView over already-known nodes (id-keyed
       updates); a node's kind is counted once. */
    if (idKind[n.id] !== undefined) {
      return idKind[n.id];
    }
    var k = kindOf(n);
    kindCounts[k] = (kindCounts[k] || 0) + 1;
    idKind[n.id] = k;
    return k;
  }
  function colorForKey(k) {
    var c = kindColor(k);
    return {
      background: c,
      border: c,
      highlight: { background: c, border: cssVar("--text-1") },
      hover: { background: c, border: cssVar("--text-1") }
    };
  }

  /* Legend items double as filters: toggling a kind hides every node
     of that kind — vis hides the connected edges with them. */
  function setKindHidden(kind, hidden) {
    hiddenKinds[kind] = hidden;
    var updates = [];
    nodes.get().forEach(function (node) {
      if (idKind[node.id] === kind) {
        updates.push({ id: node.id, hidden: hidden });
      }
    });
    if (updates.length) {
      nodes.update(updates);
    }
  }

  /* ---- Degree / labels / positions ---- */

  var degree = {};
  data.edges.forEach(function (e) {
    degree[e.source] = (degree[e.source] || 0) + 1;
    degree[e.target] = (degree[e.target] || 0) + 1;
  });

  var labelEligible = {};
  (function computeEligibility() {
    var ns = data.nodes;
    if (!data.edges.length || ns.length <= 30) {
      ns.forEach(function (n) {
        labelEligible[n.id] = true;
      });
      return;
    }
    var sorted = ns.slice().sort(function (a, b) {
      return (degree[b.id] || 0) - (degree[a.id] || 0);
    });
    var take = Math.max(10, Math.floor(ns.length * 0.25));
    sorted.forEach(function (n, i) {
      labelEligible[n.id] = i < take && (degree[n.id] || 0) >= 1;
    });
  })();

  var labelsShown = true;

  var GOLDEN = Math.PI * (3 - Math.sqrt(5));
  var spiralCursor = 0;
  function spiralPosition() {
    var i = spiralCursor;
    spiralCursor += 1;
    var r = 100 * Math.sqrt(i + 0.6);
    var a = i * GOLDEN;
    return { x: Math.round(r * Math.cos(a)), y: Math.round(r * Math.sin(a)) };
  }
  var edgeless = !data.edges.length;

  /* A static layout (no simulation): edgeless graphs always, and any
     graph under prefers-reduced-motion — the physics simulation is
     JS-driven motion the CSS media query cannot collapse. Nodes take
     deterministic spiral positions either way, so the physics-off
     canvas is a spread constellation instead of a center clump. */
  var staticLayout = edgeless || prefersReducedMotion();

  function nodeView(n) {
    var kind = registerKind(n);
    var view = {
      id: n.id,
      label: labelEligible[n.id] ? n.id : "",
      title: [n.kind, n.file].filter(Boolean).join("\n"),
      value: (degree[n.id] || 0) + 1,
      color: colorForKey(kind),
      /* merged nodes of an already-filtered kind arrive hidden */
      hidden: !!hiddenKinds[kind]
    };
    if (staticLayout) {
      var p = spiralPosition();
      view.x = p.x;
      view.y = p.y;
    }
    return view;
  }
  function edgeKey(source, target, kind) {
    return source + "\u0000" + target + "\u0000" + (kind || "");
  }
  var known = {};
  data.nodes.forEach(function (n) {
    known[n.id] = true;
  });
  var nodes = new vis.DataSet(data.nodes.map(nodeView));
  /* Parallel edges (same source, target AND kind) collapse to one vis
     edge. The viz layer dedupes its name-based join, but a store built
     before that fix still serves duplicates — and parallel edges
     multiply the force layout's spring forces until the simulation
     never converges (the blank-canvas bug). edgeKeys doubles as
     merge()'s dedupe set below. */
  var edgeKeys = {};
  var edgeViews = [];
  data.edges.forEach(function (e) {
    if (!known[e.source] || !known[e.target]) {
      return;
    }
    var key = edgeKey(e.source, e.target, e.kind);
    if (edgeKeys[key]) {
      return;
    }
    edgeKeys[key] = true;
    edgeViews.push({
      id: edgeViews.length,
      from: e.source,
      to: e.target,
      title: [e.kind, e.label].filter(Boolean).join(" — ")
    });
  });
  var edges = new vis.DataSet(edgeViews);
  var nextEdgeId = edges.length;
  /* Layout (FR-004): the initial choice comes from the server-rendered
     data-layout attribute; "hier" starts the network hierarchical
     top-down. Toggling later swaps the option on the live instance. */
  var layout = canvas.getAttribute("data-layout") === "hier" ? "hier" : "force";

  /* Store selection (FR-003): a selected store's graph page carries the
     selection as #graph-data's data-store attribute; expansion fetches
     must stay on that store. Empty/absent attribute = the launch store:
     no param appended, the URL unchanged. */
  var storeKey = (block.getAttribute("data-store") || "").trim();

  function layoutOptions(kind) {
    return {
      hierarchical: { enabled: kind === "hier", direction: "UD" }
    };
  }

  /* Color/typography options derived from the active theme's CSS
     variables — safe to re-apply on a theme flip because they carry no
     layout or physics state. Node colors live on the nodes themselves
     (community palette), not here. */
  function themeOptions() {
    var accent = cssVar("--accent");
    return {
      nodes: {
        shape: "dot",
        size: 9,
        borderWidth: 2,
        scaling: { min: 7, max: 24 },
        color: {
          background: accent,
          border: accent,
          highlight: {
            background: accent,
            border: cssVar("--text-1")
          },
          hover: { background: accent, border: cssVar("--text-1") }
        },
        font: {
          face: cssVar("--font-sans"),
          size: 12,
          color: cssVar("--text-3"),
          strokeColor: cssVar("--bg-0"),
          strokeWidth: 3
        }
      },
      edges: {
        color: {
          color: cssVar("--line-1"),
          highlight: cssVar("--accent"),
          hover: cssVar("--accent")
        },
        width: 0.75,
        selectionWidth: 1.5,
        hoverWidth: 1.5,
        arrows: { to: { enabled: true, scaleFactor: 0.4 } },
        smooth: { enabled: true, type: "continuous", roundness: 0.35 }
      }
    };
  }

  function optionsFor(kind) {
    var options = {
      autoResize: true,
      interaction: {
        dragNodes: true,
        dragView: true,
        zoomView: true,
        hover: true,
        tooltipDelay: 120,
        selectConnectedEdges: true,
        hoverConnectedEdges: true
      }
    };
    var themed = themeOptions();
    Object.keys(themed).forEach(function (key) {
      options[key] = themed[key];
    });
    if (kind === "hier") {
      options.layout = layoutOptions(kind);
      options.physics = { enabled: false };
    } else if (staticLayout || prefersReducedMotion()) {
      /* Spiral positions are final; physics would only drag the
         constellation back into a clump — and under reduced motion the
         simulation is motion, so it never runs at all. */
      options.physics = { enabled: false };
    } else {
      options.physics = {
        enabled: true,
        solver: "barnesHut",
        barnesHut: {
          gravitationalConstant: -6000,
          springLength: 160,
          springConstant: 0.04,
          damping: 0.45,
          avoidOverlap: 0.4
        },
        stabilization: { enabled: true, iterations: 350, fit: true }
      };
    }
    return options;
  }

  var network = new vis.Network(
    canvas,
    { nodes: nodes, edges: edges },
    optionsFor(layout)
  );

  /* Zoom label discipline (sigma's labelRenderThreshold equivalent):
     crossing the scale threshold flips the graph-wide label state once,
     never per-zoom-tick. */
  function applyLabels() {
    var updates = [];
    nodes.get().forEach(function (n) {
      updates.push({
        id: n.id,
        label: labelsShown && labelEligible[n.id] ? n.id : ""
      });
    });
    if (updates.length) {
      nodes.update(updates);
    }
  }
  network.on("zoom", function () {
    var want = network.getScale() >= 0.45;
    if (want !== labelsShown) {
      labelsShown = want;
      applyLabels();
    }
  });

  /* Canvas overlay controls (GitNexus-style zoom/fit cluster at the
     canvas's bottom-left); the overlay markup lives in graph.html. */
  var overlay = canvas.parentNode
    ? canvas.parentNode.querySelector(".graph-overlay")
    : null;
  if (overlay) {
    overlay.addEventListener("click", function (event) {
      var btn =
        event.target && event.target.closest
          ? event.target.closest("button[data-graph-action]")
          : null;
      if (!btn || !overlay.contains(btn)) {
        return;
      }
      var action = btn.getAttribute("data-graph-action");
      /* The eased camera move is animation; reduced motion snaps. */
      var animation = prefersReducedMotion()
        ? false
        : { duration: 250, easingFunction: "easeInOutQuad" };
      if (action === "zoom-in") {
        network.moveTo({
          scale: network.getScale() * 1.35,
          animation: animation
        });
      } else if (action === "zoom-out") {
        network.moveTo({
          scale: Math.max(network.getScale() / 1.35, 0.05),
          animation: animation
        });
      } else if (action === "fit") {
        network.fit({ animation: animation });
      }
    });
  }

  /* ---- Legend: kind swatches with counts, doubling as filters ---- */

  function renderLegend() {
    var el = document.getElementById("graph-legend");
    if (!el) {
      return;
    }
    el.textContent = "";
    Object.keys(kindCounts)
      .sort(function (a, b) {
        return kindCounts[b] - kindCounts[a] || (a < b ? -1 : 1);
      })
      .forEach(function (k) {
        var item = document.createElement("button");
        item.type = "button";
        item.className = "legend-item" + (hiddenKinds[k] ? " off" : "");
        item.setAttribute("data-kind", k);
        item.title = "toggle " + k + " nodes";
        var dot = document.createElement("span");
        dot.className = "legend-dot";
        dot.style.background = kindColor(k);
        var name = document.createElement("span");
        name.className = "legend-name";
        name.textContent = k;
        var count = document.createElement("span");
        count.className = "legend-count";
        count.textContent = kindCounts[k];
        item.appendChild(dot);
        item.appendChild(name);
        item.appendChild(count);
        el.appendChild(item);
      });
    el.hidden = false;
  }

  var legendBar = document.getElementById("graph-legend");
  if (legendBar) {
    legendBar.addEventListener("click", function (event) {
      var item =
        event.target && event.target.closest
          ? event.target.closest(".legend-item")
          : null;
      if (!item || !legendBar.contains(item)) {
        return;
      }
      var kind = item.getAttribute("data-kind");
      if (!kind || !(kind in kindCounts)) {
        return;
      }
      setKindHidden(kind, !hiddenKinds[kind]);
      renderLegend();
    });
  }
  renderLegend();

  /* A theme flip re-colors the live network: theme-derived options via
     setOptions, node colors via a single batched update from the token
     palette, then the legend re-renders. The flip arrives as the shell's
     cairn:theme-changed broadcast — this file owns no observer. Layout,
     physics, and camera state survive untouched. */
  function applyTheme() {
    network.setOptions(themeOptions());
    var updates = [];
    nodes.get().forEach(function (n) {
      var k = idKind[n.id];
      if (k !== undefined) {
        updates.push({ id: n.id, color: colorForKey(k) });
      }
    });
    if (updates.length) {
      nodes.update(updates);
    }
    renderLegend();
  }
  document.addEventListener("cairn:theme-changed", applyTheme);
  var pending = {};

  function refreshCounts(truncated) {
    var counts = document.getElementById("graph-counts");
    if (!counts) {
      return;
    }
    var text =
      "expanded view: " +
      nodes.length +
      " nodes, " +
      edges.length +
      " edges shown";
    if (truncated) {
      text += " — some neighbors capped";
    }
    counts.textContent = text;
  }

  function merge(result) {
    var fetched = (result && result.nodes) || [];
    fetched.forEach(function (n) {
      known[n.id] = true;
    });
    nodes.update(fetched.map(nodeView));
    var added = [];
    ((result && result.edges) || []).forEach(function (e) {
      if (!known[e.source] || !known[e.target]) {
        return;
      }
      var key = edgeKey(e.source, e.target, e.kind);
      if (edgeKeys[key]) {
        return;
      }
      edgeKeys[key] = true;
      added.push({
        id: nextEdgeId,
        from: e.source,
        to: e.target,
        title: [e.kind, e.label].filter(Boolean).join(" — ")
      });
      nextEdgeId += 1;
    });
    if (added.length) {
      edges.add(added);
    }
    renderLegend();
    refreshCounts(!!(result && result.metadata && result.metadata.truncated));
  }

  network.on("doubleClick", function (event) {
    var id = event.nodes && event.nodes.length ? event.nodes[0] : null;
    if (!id || pending[id]) {
      return;
    }
    pending[id] = true;
    fetch(
      "/graph/neighbors?name=" +
        encodeURIComponent(id) +
        (storeKey ? "&store=" + encodeURIComponent(storeKey) : "")
    )
      .then(function (resp) {
        if (!resp.ok) {
          throw new Error("neighbors request failed");
        }
        return resp.json();
      })
      .then(
        function (result) {
          delete pending[id];
          merge(result);
        },
        function () {
          /* A failed fetch leaves the view untouched — no merge, no
             count change; the node stays expandable. */
          delete pending[id];
        }
      );
  });

  /* Node inspect (FR-004, D-004): single click selects a node (the vis
     default) and #inspect-action's hint becomes a plain anchor into the
     symbol-neighborhood view; deselecting restores the hint. Normal
     anchor navigation — full page load, browser-back returns. Built
     with DOM APIs only (no innerHTML with node data). The selection
     rides #graph-data's data-store attribute, mirroring focusUrl. */
  var inspectAction = document.getElementById("inspect-action");
  var inspectStore = (block.getAttribute("data-store") || "").trim();

  function renderInspect(id) {
    if (!inspectAction) {
      return;
    }
    if (!id) {
      inspectAction.textContent = "select a node to inspect";
      return;
    }
    var link = document.createElement("a");
    link.textContent = "inspect '" + id + "'";
    link.href =
      "/graph?scope=symbol&focus=" +
      encodeURIComponent(id) +
      (inspectStore ? "&store=" + encodeURIComponent(inspectStore) : "");
    inspectAction.textContent = "";
    inspectAction.appendChild(link);
  }

  /* Side panel (graph-tab info panel): selecting a node fetches
     /graph/inspect as an htmx fragment — htmx.ajax carries the
     HX-Request header the route's fragment branch keys on — and swaps
     the server-rendered panel (identity, callers, callees, impact view
     with affected tests) into #graph-panel. Deselecting hides the
     panel. The loading text is the only client-authored content; the
     panel body is server-authored template markup. */
  var panel = document.getElementById("graph-panel");
  var latestInspectUrl = "";

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null && text !== "") {
      node.textContent = text;
    }
    return node;
  }

  function inspectUrl(id) {
    return (
      "/graph/inspect?name=" +
      encodeURIComponent(id) +
      (inspectStore ? "&store=" + encodeURIComponent(inspectStore) : "")
    );
  }

  function inspectPanel(id) {
    if (!panel) {
      return;
    }
    if (!id || typeof htmx === "undefined") {
      latestInspectUrl = "";
      panel.hidden = true;
      panel.textContent = "";
      return;
    }
    latestInspectUrl = inspectUrl(id);
    panel.hidden = false;
    panel.textContent = "";
    panel.appendChild(el("p", "panel-empty", "loading '" + id + "'…"));
    htmx.ajax("GET", latestInspectUrl, { target: panel, swap: "innerHTML" });
  }

  /* One selection owns the panel: htmx swaps inside ajax(), so a stale
     response (an earlier selection completing late) is cancelled at
     htmx:beforeSwap — only the latest inspect request may swap. */
  document.body.addEventListener("htmx:beforeSwap", function (event) {
    var path =
      event.detail && event.detail.requestConfig
        ? event.detail.requestConfig.path
        : "";
    if (
      typeof path === "string" &&
      path.indexOf("/graph/inspect") === 0 &&
      path !== latestInspectUrl
    ) {
      event.detail.shouldSwap = false;
    }
  });

  /* A failed inspect (HTTP error status or dead server) replaces the
     loading text with the failure note — htmx swaps only on 2xx. */
  ["htmx:responseError", "htmx:sendError", "htmx:timeout"].forEach(
    function (name) {
      document.body.addEventListener(name, function (event) {
        var detail = event.detail || {};
        var path = detail.requestConfig ? detail.requestConfig.path : "";
        if (
          !panel ||
          typeof path !== "string" ||
          path.indexOf("/graph/inspect") !== 0 ||
          path !== latestInspectUrl
        ) {
          return;
        }
        panel.textContent = "";
        panel.appendChild(el("p", "panel-empty", "inspect request failed"));
      });
    }
  );

  network.on("selectNode", function (event) {
    var id = event.nodes && event.nodes.length ? event.nodes[0] : null;
    renderInspect(id);
    inspectPanel(id);
  });
  network.on("deselectNode", function () {
    renderInspect(null);
    inspectPanel(null);
  });

  /* Layout toggle (FR-004): clicking an anchor in #layout-control
     re-layouts the LIVE network (no reload). The camera (view position
     + scale) is captured before the switch and restored once the new
     layout has drawn (afterDrawing once-listener), so the current
     focus survives; history.replaceState persists the choice in the
     URL so refresh/share round-trips (D-003). */
  var layoutControl = document.getElementById("layout-control");
  if (layoutControl) {
    var LAYOUT_LINKS = [
      ["force", "force"],
      ["hier", "hierarchical"]
    ];

    function layoutUrl(kind) {
      var url = new URL(window.location.href);
      url.searchParams.set("layout", kind);
      return url.pathname + url.search + url.hash;
    }

    function renderControl(active) {
      var parts = [];
      LAYOUT_LINKS.forEach(function (pair) {
        parts.push(
          pair[0] === active
            ? "<strong>" + pair[1] + "</strong>"
            : '<a href="' +
              layoutUrl(pair[0]) +
              '" data-layout="' +
              pair[0] +
              '">' +
              pair[1] +
              "</a>"
        );
      });
      layoutControl.innerHTML = "Layout: " + parts.join(" · ");
    }

    layoutControl.addEventListener("click", function (event) {
      var anchor =
        event.target && event.target.closest
          ? event.target.closest("a")
          : null;
      if (!anchor || !layoutControl.contains(anchor)) {
        return;
      }
      var kind = anchor.getAttribute("data-layout");
      if (kind !== "force" && kind !== "hier") {
        return;
      }
      event.preventDefault();
      if (kind === layout) {
        return;
      }
      layout = kind;
      var position = network.getViewPosition();
      var scale = network.getScale();
      /* Full option swap: the layout change rides the physics change it
         implies (hier disables physics, force re-enables barnesHut with
         a fresh stabilization — unless the graph is edgeless or under
         reduced motion, where force stays physics-off on spiral
         positions). */
      network.setOptions(optionsFor(kind));
      network.once("afterDrawing", function () {
        network.moveTo({ position: position, scale: scale });
      });
      window.history.replaceState(null, "", layoutUrl(kind));
      renderControl(layout);
    });
  }
})();

/* Symbol search (typeahead): typing fetches /graph/suggest and shows
   a live dropdown of matching symbols — the options are visible while
   typing; ArrowUp/Down move, Enter or click picks, Escape closes.
   Enter with no dropdown open keeps the confirm-to-focus flow (exact
   /graph/candidates: one untruncated hit navigates straight to the
   symbol-focused graph; several render the inline disambiguation list
   below). */
(function () {
  "use strict";
  var input = document.getElementById("symbol-search");
  var box = document.getElementById("symbol-candidates");
  var dd = document.getElementById("symbol-suggest");
  if (!input || !box || !dd) {
    return;
  }

  /* Store selection (FR-003): search fetches stay on the store the page
     serves — the selection rides #graph-data's data-store attribute. An
     absent block (never on /graph) or empty value is the launch store:
     no param appended, the URL unchanged. */
  var graphData = document.getElementById("graph-data");
  var storeKey = graphData
    ? (graphData.getAttribute("data-store") || "").trim()
    : "";

  function suggestUrl(prefix) {
    return (
      "/graph/suggest?name=" +
      encodeURIComponent(prefix) +
      (storeKey ? "&store=" + encodeURIComponent(storeKey) : "")
    );
  }

  function focusUrl(name) {
    return (
      "/graph?scope=symbol&focus=" +
      encodeURIComponent(name) +
      (storeKey ? "&store=" + encodeURIComponent(storeKey) : "")
    );
  }

  function note(text) {
    var line = document.createElement("p");
    line.className = "muted";
    line.textContent = text;
    box.appendChild(line);
  }

  /* ---- Confirm-to-focus (Enter with no dropdown open) ---- */
  function render(name, result) {
    var matches = result.matches || [];
    if (matches.length === 1 && !result.truncated) {
      window.location.assign(focusUrl(matches[0].name));
      return;
    }
    if (!matches.length) {
      note("no symbol named '" + name + "'");
      return;
    }
    var list = document.createElement("ul");
    list.className = "link-list";
    matches.forEach(function (m) {
      var label = m.name;
      if (m.kind) {
        label += " — " + m.kind;
      }
      if (m.file) {
        label += " (" + m.file + ")";
      }
      var link = document.createElement("a");
      link.href = focusUrl(m.name);
      link.textContent = label;
      var item = document.createElement("li");
      item.appendChild(link);
      list.appendChild(item);
    });
    box.appendChild(list);
    if (result.truncated) {
      note("more than " + matches.length + " matches — refine the name");
    }
  }

  function confirmFocus() {
    box.textContent = "";
    var name = input.value.trim();
    if (!name) {
      return;
    }
    fetch(
      "/graph/candidates?name=" +
        encodeURIComponent(name) +
        (storeKey ? "&store=" + encodeURIComponent(storeKey) : "")
    )
      .then(function (resp) {
        if (!resp.ok) {
          throw new Error("candidates request failed");
        }
        return resp.json();
      })
      .then(function (result) {
        render(name, result);
      })
      .catch(function () {
        box.textContent = "";
        note("search unavailable");
      });
  }

  /* ---- Typeahead dropdown ---- */
  var items = [];
  var active = -1;
  var timer = null;
  var seq = 0;
  var SUGGEST_DEBOUNCE_MS = 160;

  function close() {
    dd.hidden = true;
    dd.textContent = "";
    items = [];
    active = -1;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }

  function highlight(i) {
    var rows = dd.querySelectorAll(".suggest-item");
    for (var r = 0; r < rows.length; r++) {
      rows[r].classList.toggle("active", r === i);
    }
    active = i;
    if (i >= 0 && rows[i]) {
      rows[i].scrollIntoView({ block: "nearest" });
      input.setAttribute("aria-activedescendant", rows[i].id);
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  function entry(m, idx) {
    var el = document.createElement("div");
    el.className = "suggest-item";
    el.id = "suggest-opt-" + idx;
    el.setAttribute("role", "option");
    var main = document.createElement("span");
    main.className = "suggest-name";
    main.textContent = m.name;
    el.appendChild(main);
    var ctx = [];
    if (m.kind) {
      ctx.push(m.kind);
    }
    if (m.file) {
      ctx.push(m.file);
    }
    if (ctx.length) {
      var sub = document.createElement("span");
      sub.className = "suggest-ctx";
      sub.textContent = ctx.join(" — ");
      el.appendChild(sub);
    }
    /* mousedown beats the document closer and the input blur — the
       pick runs before anything can dismiss the dropdown. */
    el.addEventListener("mousedown", function (event) {
      event.preventDefault();
      pick(m.name);
    });
    return el;
  }

  function open(result) {
    var matches = result.matches || [];
    dd.textContent = "";
    items = matches;
    active = -1;
    if (!matches.length) {
      var empty = document.createElement("div");
      empty.className = "suggest-empty";
      empty.textContent = "no matching symbol";
      dd.appendChild(empty);
    } else {
      matches.forEach(function (m, idx) {
        dd.appendChild(entry(m, idx));
      });
      if (result.truncated) {
        var more = document.createElement("div");
        more.className = "suggest-empty";
        more.textContent =
          "more than " + matches.length + " — keep typing to refine";
        dd.appendChild(more);
      }
      highlight(0);
    }
    dd.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  function pick(name) {
    close();
    input.value = name;
    box.textContent = "";
    window.location.assign(focusUrl(name));
  }

  function refresh() {
    var prefix = input.value.trim();
    if (!prefix) {
      close();
      return;
    }
    var my = ++seq;
    fetch(suggestUrl(prefix))
      .then(function (resp) {
        if (!resp.ok) {
          throw new Error("suggest request failed");
        }
        return resp.json();
      })
      .then(function (result) {
        if (my === seq) {
          open(result);
        }
      })
      .catch(function () {
        if (my === seq) {
          close();
        }
      });
  }

  input.addEventListener("input", function () {
    clearTimeout(timer);
    timer = setTimeout(refresh, SUGGEST_DEBOUNCE_MS);
  });

  input.addEventListener("keydown", function (event) {
    if (event.key === "ArrowDown" && !dd.hidden && items.length) {
      event.preventDefault();
      highlight((active + 1) % items.length);
      return;
    }
    if (event.key === "ArrowUp" && !dd.hidden && items.length) {
      event.preventDefault();
      highlight((active - 1 + items.length) % items.length);
      return;
    }
    if (event.key === "Escape" && !dd.hidden) {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Enter") {
      return;
    }
    event.preventDefault();
    if (!dd.hidden && items.length) {
      pick(items[active >= 0 ? active : 0].name);
      return;
    }
    close();
    confirmFocus();
  });

  /* A click outside the search wrapper closes the dropdown; a click on
     the input itself keeps it open. */
  document.addEventListener("mousedown", function (event) {
    if (!dd.hidden && !dd.parentNode.contains(event.target)) {
      close();
    }
  });
})();

/* Live refresh (FR-001): on traffic views the #refresh-region element is
   an htmx poll trigger (hx-trigger="every 5s", hx-swap="morph" in the
   region templates): each cycle re-fetches the view as a cheap region
   fragment — the route's HX-Request branch — and the alpine-morph
   extension morphs it over the region in place. Nodes outside the region
   (the filter form) are never touched, so in-progress filter input
   survives every poll by construction; the region root itself survives
   the morph, so the poller keeps ticking with no re-arming code here.
   This module owns only the chrome around that loop, driven entirely by
   the htmx event lifecycle:
   - #live-controls (topbar): the state word + pause toggle. Pause is a
     beforeRequest refusal: htmx's poll timer keeps its schedule but
     every fetch is refused while paused (explicit user intent, and it
     wins over tab visibility — paused means no fetch, period). A hidden
     tab refuses its fetches the same way; the next visible tick catches
     up. Resume simply stops refusing; the next scheduled tick fetches.
   - FR-005: a failed poll (HTTP error or dead server) raises the
     disconnected banner; the next successful poll clears it — self-heal
     without a reload, since the timer never stopped.
   - Window scroll is re-anchored around each region swap (captured in
     beforeSwap, restored in afterSwap): a shrinking fragment must not
     pull the page out from under the reader.
   Pages without #refresh-region are untouched. */
(function () {
  "use strict";
  var region = document.getElementById("refresh-region");
  var controls = document.getElementById("live-controls");
  if (!region || !controls) {
    return;
  }
  controls.removeAttribute("hidden");
  var stateText = document.getElementById("live-state");
  var pauseButton = document.getElementById("live-pause");
  var banner = document.createElement("span");
  banner.id = "live-banner";
  controls.insertBefore(banner, pauseButton);

  var STATE_WORDS = {
    running: "live",
    disconnected: "disconnected",
    paused: "paused"
  };

  function setState(state) {
    controls.dataset.state = state;
    if (stateText) {
      stateText.textContent = STATE_WORDS[state] || state;
    }
  }

  function fromRegion(event) {
    return !!(event.detail && event.detail.elt === region);
  }

  /* Pause/resume (FR-004): the toggle only flips the refusal flag and
     the visible word — htmx owns the schedule, so there is no timer to
     clear and resume needs no re-arm. */
  var paused = false;
  if (pauseButton) {
    pauseButton.addEventListener("click", function () {
      paused = !paused;
      if (paused) {
        setState("paused");
        pauseButton.textContent = "Resume";
      } else {
        setState("running");
        pauseButton.textContent = "Pause";
      }
    });
  }

  document.body.addEventListener("htmx:beforeRequest", function (event) {
    if (!fromRegion(event)) {
      return;
    }
    if (paused || document.hidden) {
      event.preventDefault();
    }
  });

  document.body.addEventListener("htmx:afterRequest", function (event) {
    if (!fromRegion(event) || paused || !event.detail.successful) {
      return;
    }
    /* A successful poll is the recovery: the banner clears as the state
       word returns to "live" (FR-005, US3-AC2). */
    banner.textContent = "";
    setState("running");
  });

  ["htmx:responseError", "htmx:sendError", "htmx:timeout"].forEach(
    function (name) {
      document.body.addEventListener(name, function (event) {
        if (!fromRegion(event) || paused) {
          return;
        }
        banner.textContent = "connection lost — retrying";
        setState("disconnected");
      });
    }
  );

  /* The swap replaces the region's children, which can change the page
     height; window scroll is the honest anchor (the region has no
     internal scroll container). Capture immediately before the morph,
     restore immediately after. */
  var scrollX = 0;
  var scrollY = 0;
  document.body.addEventListener("htmx:beforeSwap", function (event) {
    if (event.detail && event.detail.target === region) {
      scrollX = window.scrollX;
      scrollY = window.scrollY;
    }
  });
  document.body.addEventListener("htmx:afterSwap", function (event) {
    if (event.detail && event.detail.target === region) {
      window.scrollTo(scrollX, scrollY);
    }
  });

  setState("running");
})();
