/* Knowledge relationship canvas: build vis-network DataSets from the
   server-serialized {nodes, edges, metadata} JSON block
   (#knowledge-graph-data) and render the doc relationship network — one
   node per stored knowledge doc, one directed edge per indexed
   knowledge_edges row, drawn exactly as stored (the index keeps a
   supersede pair in both directions — "supersedes" on the newer doc,
   "superseded-by" on the older — so the pair reads as the two-way link
   the related CLI and the detail panels report).

   Rendering model (the /graph machinery, edge-centric):
   - Edge styling encodes trust: kind extracted / derived render solid,
     kind inferred renders dashed — the low-trust critic-approved links
     are distinguishable at a glance, and every edge's tooltip names its
     relation + kind. Colors resolve through the getComputedStyle token
     proxy (no hex here); the legend's kind swatches preview the exact
     line style.
   - Legend as filter (the /graph pattern): chips for every edge kind
     and relation the data carries; clicking toggles that group via
     id-keyed DataSet updates — a filter change never reloads,
     refetches, or touches the server.
   - Node click opens the inspect panel: the summary fragment from
     /knowledge/graph/inspect (identity + relationships + the detail
     link) swaps into #knowledge-graph-panel; deselect hides it.
   - Theming: every canvas color derives from the stylesheet's CSS
     variables; shell.js broadcasts cairn:theme-changed on a flip and
     this script re-reads the palette — no observer here, and layout,
     physics, and camera state are never touched by a flip.
   - Reduced motion: the physics simulation is JS-driven motion the CSS
     media query cannot reach, so a matching prefers-reduced-motion:
     reduce disables physics outright and lays the graph on the
     deterministic spiral; an edgeless graph (isolated docs only) takes
     the same static constellation. */
(function () {
  "use strict";
  var block = document.getElementById("knowledge-graph-data");
  var canvas = document.getElementById("knowledge-graph-canvas");
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

  /* ---- Edge styling: kind -> token + dash ---- */

  /* Extracted (author-declared) reads strongest, derived (computed
     overlap) recedes, inferred (critic-approved LLM) wears the warning
     token AND the dash — trust maps to visual weight. */
  var KIND_TOKENS = {
    extracted: "--text-2",
    derived: "--text-3",
    inferred: "--warn"
  };
  function kindColor(kind) {
    var token = KIND_TOKENS[kind];
    return (token && cssVar(token)) || cssVar("--text-3");
  }
  function kindDashes(kind) {
    return kind === "inferred";
  }

  /* ---- Legend filters: whole kinds / relations, client-side ---- */

  var hiddenKinds = {};
  var hiddenRelations = {};
  var kindCounts = {};
  var relationCounts = {};
  data.edges.forEach(function (e) {
    kindCounts[e.kind] = (kindCounts[e.kind] || 0) + 1;
    relationCounts[e.relation] = (relationCounts[e.relation] || 0) + 1;
  });
  function edgeHidden(e) {
    return !!hiddenKinds[e.kind] || !!hiddenRelations[e.relation];
  }

  /* ---- Nodes: every doc, sized by degree ---- */

  var degree = {};
  data.edges.forEach(function (e) {
    degree[e.source] = (degree[e.source] || 0) + 1;
    degree[e.target] = (degree[e.target] || 0) + 1;
  });

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
     JS-driven motion the CSS media query cannot collapse. */
  var staticLayout = edgeless || prefersReducedMotion();

  function docColor() {
    var c = cssVar("--accent");
    return {
      background: c,
      border: c,
      highlight: { background: c, border: cssVar("--text-1") },
      hover: { background: c, border: cssVar("--text-1") }
    };
  }

  function nodeView(n) {
    var view = {
      id: n.id,
      label: n.title || n.id,
      title: [n.family, n.status, n.id].filter(Boolean).join("\n"),
      value: (degree[n.id] || 0) + 1,
      color: docColor()
    };
    if (staticLayout) {
      var p = spiralPosition();
      view.x = p.x;
      view.y = p.y;
    }
    return view;
  }

  function edgeView(e, i) {
    return {
      id: i,
      from: e.source,
      to: e.target,
      dashes: kindDashes(e.kind),
      color: {
        color: kindColor(e.kind),
        highlight: cssVar("--accent"),
        hover: cssVar("--accent")
      },
      title: [e.relation, e.kind].filter(Boolean).join(" — ")
    };
  }

  var nodes = new vis.DataSet(data.nodes.map(nodeView));
  var edges = new vis.DataSet(data.edges.map(edgeView));

  /* Color/typography options derived from the active theme's CSS
     variables — safe to re-apply on a theme flip because they carry no
     layout or physics state. Edge colors live on the edges themselves
     (per-kind), not here. */
  function themeOptions() {
    return {
      nodes: {
        shape: "dot",
        size: 9,
        borderWidth: 2,
        scaling: { min: 8, max: 26 },
        font: {
          face: cssVar("--font-sans"),
          size: 12,
          color: cssVar("--text-2"),
          strokeColor: cssVar("--bg-0"),
          strokeWidth: 3
        }
      },
      edges: {
        width: 1,
        selectionWidth: 1.5,
        hoverWidth: 1.5,
        arrows: { to: { enabled: true, scaleFactor: 0.4 } },
        smooth: { enabled: true, type: "continuous", roundness: 0.35 },
        font: {
          face: cssVar("--font-sans"),
          size: 10,
          color: cssVar("--text-3"),
          strokeColor: cssVar("--bg-0"),
          strokeWidth: 3
        }
      }
    };
  }

  function optionsFor() {
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
    if (staticLayout || prefersReducedMotion()) {
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
    optionsFor()
  );

  /* Canvas overlay controls: the same zoom/fit cluster /graph mounts. */
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

  /* ---- Counts: docs and the visible edge share ---- */

  function refreshCounts() {
    var el = document.getElementById("knowledge-graph-counts");
    if (!el) {
      return;
    }
    var shown = data.edges.filter(function (e) {
      return !edgeHidden(e);
    }).length;
    el.textContent =
      data.nodes.length +
      " docs — " +
      shown +
      " of " +
      data.edges.length +
      " edges shown";
  }

  /* ---- Legend: kind + relation chips, doubling as filters ---- */

  function appendGroup(el, group, counts, hiddenSet, kindStyled) {
    var keys = Object.keys(counts).sort(function (a, b) {
      return counts[b] - counts[a] || (a < b ? -1 : 1);
    });
    if (!keys.length) {
      return;
    }
    var label = document.createElement("span");
    label.className = "legend-group-label";
    label.textContent = group;
    el.appendChild(label);
    keys.forEach(function (k) {
      var item = document.createElement("button");
      item.type = "button";
      item.className = "legend-item" + (hiddenSet[k] ? " off" : "");
      item.setAttribute("data-group", group);
      item.setAttribute("data-value", k);
      item.title = "toggle " + k + " edges";
      /* Kind chips preview the exact edge line style; relation chips
         wear a neutral dot. */
      if (kindStyled) {
        var line = document.createElement("span");
        line.className =
          "legend-line" + (kindDashes(k) ? " legend-line-dashed" : "");
        line.style.borderColor = kindColor(k);
        item.appendChild(line);
      } else {
        var dot = document.createElement("span");
        dot.className = "legend-dot";
        dot.style.background = cssVar("--text-3");
        item.appendChild(dot);
      }
      var name = document.createElement("span");
      name.className = "legend-name";
      name.textContent = k;
      var count = document.createElement("span");
      count.className = "legend-count";
      count.textContent = counts[k];
      item.appendChild(name);
      item.appendChild(count);
      el.appendChild(item);
    });
  }

  function renderLegend() {
    var el = document.getElementById("knowledge-graph-legend");
    if (!el) {
      return;
    }
    el.textContent = "";
    appendGroup(el, "kind", kindCounts, hiddenKinds, true);
    appendGroup(el, "relation", relationCounts, hiddenRelations, false);
    el.hidden = false;
  }

  function applyFilters() {
    var updates = data.edges.map(function (e, i) {
      return { id: i, hidden: edgeHidden(e) };
    });
    if (updates.length) {
      edges.update(updates);
    }
    refreshCounts();
    renderLegend();
  }

  var legendBar = document.getElementById("knowledge-graph-legend");
  if (legendBar) {
    legendBar.addEventListener("click", function (event) {
      var item =
        event.target && event.target.closest
          ? event.target.closest(".legend-item")
          : null;
      if (!item || !legendBar.contains(item)) {
        return;
      }
      var group = item.getAttribute("data-group");
      var value = item.getAttribute("data-value");
      if (!group || !value) {
        return;
      }
      var set = group === "kind" ? hiddenKinds : hiddenRelations;
      set[value] = !set[value];
      applyFilters();
    });
  }
  renderLegend();
  refreshCounts();

  /* ---- Inspect panel: node click -> fragment swap ---- */

  /* Store selection: a selected store's page carries the selection as
     #knowledge-graph-data's data-store attribute; the inspect fetch
     stays on that store. Empty/absent = the launch store. */
  var storeKey = (block.getAttribute("data-store") || "").trim();
  var panel = document.getElementById("knowledge-graph-panel");
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
      "/knowledge/graph/inspect?doc=" +
      encodeURIComponent(id) +
      (storeKey ? "&store=" + encodeURIComponent(storeKey) : "")
    );
  }

  function inspectPanel(id) {
    if (!panel || typeof htmx === "undefined") {
      return;
    }
    latestInspectUrl = inspectUrl(id);
    panel.hidden = false;
    panel.textContent = "";
    panel.appendChild(el("p", "panel-empty", "loading '" + id + "'…"));
    htmx.ajax("GET", latestInspectUrl, { target: panel, swap: "innerHTML" });
  }

  /* One selection owns the panel: a superseded inspect fetch is aborted
     outright (htmx:beforeSend carries the live xhr), and htmx swaps
     inside ajax(), so a stale response completing late is cancelled at
     htmx:beforeSwap too — only the latest inspect request may swap. */
  var inFlightInspect = null;
  function abortInFlightInspect() {
    if (inFlightInspect) {
      try {
        inFlightInspect.abort();
      } catch (err) {
        /* abort() on an already-settled xhr is a no-op; nothing to free */
      }
      inFlightInspect = null;
    }
  }
  document.body.addEventListener("htmx:beforeSend", function (event) {
    var detail = event.detail || {};
    var path =
      detail.requestConfig && typeof detail.requestConfig.path === "string"
        ? detail.requestConfig.path
        : "";
    if (path.indexOf("/knowledge/graph/inspect") !== 0) {
      return;
    }
    abortInFlightInspect();
    inFlightInspect = detail.xhr;
  });
  document.body.addEventListener("htmx:beforeSwap", function (event) {
    var path =
      event.detail && event.detail.requestConfig
        ? event.detail.requestConfig.path
        : "";
    if (
      typeof path === "string" &&
      path.indexOf("/knowledge/graph/inspect") === 0 &&
      path !== latestInspectUrl
    ) {
      event.detail.shouldSwap = false;
    }
  });

  /* A failed inspect (HTTP error status or dead server) replaces the
     loading text with the failure note — htmx swaps only on 2xx, and an
     aborted predecessor fires neither event. */
  ["htmx:responseError", "htmx:sendError"].forEach(
    function (name) {
      document.body.addEventListener(name, function (event) {
        var detail = event.detail || {};
        var path = detail.requestConfig ? detail.requestConfig.path : "";
        if (
          !panel ||
          typeof path !== "string" ||
          path.indexOf("/knowledge/graph/inspect") !== 0 ||
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
    if (id) {
      inspectPanel(id);
    }
  });

  network.on("deselectNode", function () {
    if (!panel) {
      return;
    }
    abortInFlightInspect();
    latestInspectUrl = "";
    panel.hidden = true;
    panel.textContent = "";
  });

  /* ---- Theming: re-read the palette on the shell's broadcast ---- */

  function applyTheme() {
    network.setOptions(themeOptions());
    var nodeUpdates = [];
    nodes.get().forEach(function (n) {
      nodeUpdates.push({ id: n.id, color: docColor() });
    });
    if (nodeUpdates.length) {
      nodes.update(nodeUpdates);
    }
    var edgeUpdates = data.edges.map(function (e, i) {
      return {
        id: i,
        color: {
          color: kindColor(e.kind),
          highlight: cssVar("--accent"),
          hover: cssVar("--accent")
        }
      };
    });
    if (edgeUpdates.length) {
      edges.update(edgeUpdates);
    }
    renderLegend();
  }
  document.addEventListener("cairn:theme-changed", applyTheme);
})();
