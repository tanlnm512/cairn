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

  /* Store selection (FR-003): search fetches stay on the store the page
     serves — the selection rides #graph-data's data-store attribute. An
     absent block (never on /graph) or empty value is the launch store:
     no param appended, the URL unchanged. */
  var graphData = document.getElementById("graph-data");
  var storeKey = graphData
    ? (graphData.getAttribute("data-store") || "").trim()
    : "";

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
  });
})();

/* Live refresh (FR-001): on traffic views a #refresh-region element
   marks the swappable body. This loop re-fetches the current URL on a
   re-arming setTimeout — never setInterval, so background-tab
   throttling cannot stack timers (D-001) — and swaps the region's
   children for the fetched document's, built with importNode (never
   innerHTML on the fetched text). The swap is atomic full-region
   replacement: ordering is the server's ORDER BY, so re-fetching the
   same data renders the same rows and idempotency holds by
   construction (D-002). Hidden tabs skip fetches entirely (D-003).
   FR-003: the filter form lives inside the region, so the wholesale
   swap would wipe in-progress input and reset scroll — field values
   and window scroll are captured immediately before the swap and
   restored immediately after (a field-count mismatch skips field
   restoration: the server changed the form shape). The interval is
   LIVE_REFRESH_MS unless <body data-refresh-ms> (numeric, > 500)
   overrides it.
   Pages without #refresh-region are untouched. FR-004: #live-pause
   toggles the loop — pause clears the pending timer and the state word
   holds "paused" until the user resumes (pause is explicit user intent
   and wins over visibility, D-003); a failed poll raises the
   disconnected banner (FR-005), which clears on the next successful
   one. */
(function () {
  "use strict";
  var LIVE_REFRESH_MS = 5000;
  /* FR-001 "configurable interval": a numeric <body data-refresh-ms>
     greater than 500 overrides the default; an absent, non-numeric,
     or out-of-range value falls back. Read once, before the first
     arm — history.html does not set the attribute, so the default
     applies there. */
  var configuredMs = document.body
    ? parseInt(document.body.getAttribute("data-refresh-ms"), 10)
    : NaN;
  var refreshMs = configuredMs > 500 ? configuredMs : LIVE_REFRESH_MS;
  var region = document.getElementById("refresh-region");
  var controls = document.getElementById("live-controls");
  if (!region) {
    return;
  }
  if (controls) {
    controls.removeAttribute("hidden");
  }
  var stateText = document.getElementById("live-state");
  /* FR-005: the disconnected banner (US3-AC2). Created once here —
     after the state word, before the pause button — so ticks only
     ever write its text: "connection lost — retrying" on a failure
     while running, "" on the next successful poll (the self-heal).
     Paused ticks fetch nothing, so the banner is untouched while
     paused. */
  var banner = null;
  if (controls) {
    banner = document.createElement("span");
    banner.id = "live-banner";
    controls.insertBefore(
      banner,
      document.getElementById("live-pause")
    );
  }
  var timer = null;

  var STATE_WORDS = {
    running: "live",
    disconnected: "disconnected",
    paused: "paused"
  };

  function setState(state) {
    if (controls) {
      controls.dataset.state = state;
    }
    if (stateText) {
      stateText.textContent = STATE_WORDS[state] || state;
    }
  }

  function arm() {
    timer = setTimeout(tick, refreshMs);
  }

  /* Pause/resume (FR-004, D-003): the #live-pause click is explicit
     user intent and wins over visibility — while paused the loop is
     fully stopped (pending timer cleared, ticks no-op), so no fetch
     happens regardless of tab state, and the state word stays
     "paused" until the user resumes (US3-AC1). Resume re-arms at
     once, so the next tick arrives on the normal schedule rather
     than never. */
  var pauseButton = document.getElementById("live-pause");
  var paused = false;

  if (pauseButton) {
    pauseButton.addEventListener("click", function () {
      paused = !paused;
      if (paused) {
        if (timer !== null) {
          clearTimeout(timer);
          timer = null;
        }
        setState("paused");
        pauseButton.textContent = "Resume";
      } else {
        setState("running");
        pauseButton.textContent = "Pause";
        arm();
      }
    });
  }

  /* FR-003: the swap replaces the region's children wholesale, which
     would destroy whatever the user has typed into the filter form
     (the fields live INSIDE the region) and drop the page scroll (the
     region has no internal scroll container — window scroll is the
     honest anchor). Harvest field state in DOM order immediately
     before the swap; re-apply by DOM order immediately after. */
  function harvestFields() {
    var fields = region.querySelectorAll("input, select, textarea");
    var state = [];
    var i;
    for (i = 0; i < fields.length; i += 1) {
      state.push({
        type: fields[i].type || fields[i].tagName.toLowerCase(),
        checked: fields[i].checked,
        value: fields[i].value
      });
    }
    return state;
  }

  function restoreFields(state) {
    var fields = region.querySelectorAll("input, select, textarea");
    var i;
    if (fields.length !== state.length) {
      /* The fresh fragment's field count differs from what was
         harvested — the server changed the form shape; skip rather
         than misapply stale values. */
      return;
    }
    for (i = 0; i < fields.length; i += 1) {
      if (state[i].type === "checkbox" || state[i].type === "radio") {
        fields[i].checked = state[i].checked;
      }
      fields[i].value = state[i].value;
    }
  }

  /* D-002: replace the region's children wholesale with the fetched
     document's #refresh-region children (imported copies, so the
     parsed document's iteration is never disturbed mid-loop). */
  function swap(fetchedDoc) {
    var fresh = fetchedDoc.getElementById("refresh-region");
    if (!fresh) {
      return;
    }
    var fieldState = harvestFields();
    var scrollX = window.scrollX;
    var scrollY = window.scrollY;
    while (region.firstChild) {
      region.removeChild(region.firstChild);
    }
    var child = fresh.firstChild;
    while (child) {
      region.appendChild(document.importNode(child, true));
      child = child.nextSibling;
    }
    restoreFields(fieldState);
    window.scrollTo(scrollX, scrollY);
  }

  function tick() {
    timer = null;
    if (paused) {
      /* D-003: pause is explicit user intent and wins over visibility
         — no fetch while paused regardless of tab state, and no
         re-arm either: the resume handler restarts the loop. */
      return;
    }
    if (document.hidden) {
      /* D-003: a hidden tab fetches nothing this tick; the re-arm
         keeps the loop alive so a visible tab refreshes again on a
         later tick. */
      arm();
      return;
    }
    fetch(window.location.href)
      .then(function (resp) {
        if (!resp.ok) {
          throw new Error("refresh request failed");
        }
        return resp.text();
      })
      .then(function (html) {
        if (paused) {
          /* Paused mid-flight: the response is dropped — no swap, no
             state flip, no re-arm; resume's own cycle brings fresher
             data. */
          return;
        }
        swap(new DOMParser().parseFromString(html, "text/html"));
        /* FR-005: a successful poll is the recovery — the banner
           clears as the state word returns to "live". */
        if (banner) {
          banner.textContent = "";
        }
        setState("running");
        arm();
      })
      .catch(function () {
        if (paused) {
          /* Disconnected is only for failures while running (D-003):
             a pause that landed mid-request must not overwrite the
             paused word. */
          return;
        }
        /* A failed cycle while running flips the state word and
           raises the banner; the loop re-arms and both recover on
           the first successful poll (FR-005, US3-AC2). */
        if (banner) {
          banner.textContent = "connection lost — retrying";
        }
        setState("disconnected");
        arm();
      });
  }

  setState("running");
  arm();
})();
