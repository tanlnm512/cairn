/* Graph view: build vis-network DataSets from the server-serialized
   {nodes, edges, metadata} JSON block and render an interactive network
   (drag to pan, wheel to zoom). Double-clicking a node fetches
   /graph/neighbors and merges the reply into the live DataSets (id-keyed
   node updates, triple-deduped edge adds), then refreshes the shown
   counts. The #layout-control anchors toggle force-directed vs
   hierarchical (top-down) on the live network, camera preserved.
   Selecting a node (single click) swaps the #inspect-action hint for a
   plain anchor into that symbol's neighborhood view. No CDN
   — vis-network is vendored. */
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
  function nodeView(n) {
    return {
      id: n.id,
      label: n.id,
      title: [n.kind, n.file].filter(Boolean).join("\n"),
      group: n.kind || "other"
    };
  }
  function edgeKey(source, target, kind) {
    return source + "\u0000" + target + "\u0000" + (kind || "");
  }
  var known = {};
  data.nodes.forEach(function (n) {
    known[n.id] = true;
  });
  var nodes = new vis.DataSet(data.nodes.map(nodeView));
  var edges = new vis.DataSet(
    data.edges
      .filter(function (e) {
        return known[e.source] && known[e.target];
      })
      .map(function (e, i) {
        return {
          id: i,
          from: e.source,
          to: e.target,
          title: [e.kind, e.label].filter(Boolean).join(" — ")
        };
      })
  );
  var nextEdgeId = edges.length;
  var edgeKeys = {};
  data.edges.forEach(function (e) {
    if (known[e.source] && known[e.target]) {
      edgeKeys[edgeKey(e.source, e.target, e.kind)] = true;
    }
  });
  /* Layout (FR-004): the initial choice comes from the server-rendered
     data-layout attribute; "hier" starts the network hierarchical
     top-down. Toggling later swaps the option on the live instance. */
  var layout = canvas.getAttribute("data-layout") === "hier" ? "hier" : "force";

  function layoutOptions(kind) {
    return {
      hierarchical: { enabled: kind === "hier", direction: "UD" }
    };
  }

  var networkOptions = {
    autoResize: true,
    interaction: { dragNodes: true, dragView: true, zoomView: true }
  };
  if (layout === "hier") {
    networkOptions.layout = layoutOptions(layout);
  }
  var network = new vis.Network(
    canvas,
    { nodes: nodes, edges: edges },
    networkOptions
  );
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
    refreshCounts(!!(result && result.metadata && result.metadata.truncated));
  }

  network.on("doubleClick", function (event) {
    var id = event.nodes && event.nodes.length ? event.nodes[0] : null;
    if (!id || pending[id]) {
      return;
    }
    pending[id] = true;
    fetch("/graph/neighbors?name=" + encodeURIComponent(id))
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
     with DOM APIs only (no innerHTML with node data). */
  var inspectAction = document.getElementById("inspect-action");

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
    link.href = "/graph?scope=symbol&focus=" + encodeURIComponent(id);
    inspectAction.textContent = "";
    inspectAction.appendChild(link);
  }

  network.on("selectNode", function (event) {
    var id = event.nodes && event.nodes.length ? event.nodes[0] : null;
    renderInspect(id);
  });
  network.on("deselectNode", function () {
    renderInspect(null);
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
      network.setOptions({ layout: layoutOptions(kind) });
      network.once("afterDrawing", function () {
        network.moveTo({ position: position, scale: scale });
      });
      window.history.replaceState(null, "", layoutUrl(kind));
      renderControl(layout);
    });
  }
})();

/* Symbol search (confirm-to-focus): Enter in #symbol-search fetches
   /graph/candidates. One untruncated hit navigates straight to the
   symbol-focused graph; several hits render an inline candidate list
   where each entry focuses its symbol. Truncation, zero matches, and
   a failed request each leave one muted hint line. */
(function () {
  "use strict";
  var input = document.getElementById("symbol-search");
  var box = document.getElementById("symbol-candidates");
  if (!input || !box) {
    return;
  }

  function focusUrl(name) {
    return "/graph?scope=symbol&focus=" + encodeURIComponent(name);
  }

  function note(text) {
    var line = document.createElement("p");
    line.className = "muted";
    line.textContent = text;
    box.appendChild(line);
  }

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

  input.addEventListener("keydown", function (event) {
    if (event.key !== "Enter") {
      return;
    }
    event.preventDefault();
    box.textContent = "";
    var name = input.value.trim();
    if (!name) {
      return;
    }
    fetch("/graph/candidates?name=" + encodeURIComponent(name))
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
  });
})();
