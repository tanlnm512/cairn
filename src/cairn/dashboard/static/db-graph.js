/* Database view: the store's schema as a relationship network. Tables are
   nodes sized by row count; edges are declared foreign keys (solid) or
   *_id references implied by column naming (dashed). Theme-derived colors
   come from CSS variables, so a theme flip re-applies them through
   setOptions without touching layout or camera state — same contract as
   the graph view. */
(function () {
  "use strict";

  var dataEl = document.getElementById("db-graph-data");
  var canvas = document.getElementById("db-graph-canvas");
  if (!dataEl || !canvas || typeof vis === "undefined") {
    return;
  }
  var schema = JSON.parse(dataEl.textContent);
  var byName = {};
  schema.tables.forEach(function (t) {
    byName[t.name] = t;
  });

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function edgeStyle(kind) {
    var fk = kind === "fk";
    return {
      color: cssVar(fk ? "--accent" : "--muted"),
      width: fk ? 1.5 : 1,
      dashes: fk ? false : [5, 5]
    };
  }

  function themeOptions() {
    var accent = cssVar("--accent");
    return {
      nodes: {
        shape: "dot",
        borderWidth: 2,
        color: {
          background: accent,
          border: accent,
          highlight: { background: accent, border: cssVar("--text") },
          hover: { background: accent, border: cssVar("--text") }
        },
        font: {
          face: cssVar("--font-sans"),
          size: 12,
          color: cssVar("--muted"),
          strokeColor: cssVar("--canvas"),
          strokeWidth: 3
        }
      },
      edges: {
        color: {
          color: cssVar("--border"),
          highlight: accent,
          hover: accent
        },
        width: 1,
        selectionWidth: 1.5,
        hoverWidth: 1.5,
        arrows: { to: { enabled: true, scaleFactor: 0.4 } },
        smooth: { enabled: true, type: "continuous", roundness: 0.35 }
      }
    };
  }

  var maxRows = 1;
  schema.tables.forEach(function (t) {
    if (t.rows > maxRows) {
      maxRows = t.rows;
    }
  });

  var nodes = new vis.DataSet(
    schema.tables.map(function (t) {
      return {
        id: t.name,
        label: t.name,
        size: 8 + 30 * Math.sqrt(t.rows / maxRows),
        title: t.name + " — " + t.rows + " rows",
        shape: "dot"
      };
    })
  );
  var edges = new vis.DataSet(
    schema.edges.map(function (e, i) {
      var style = edgeStyle(e.kind);
      return {
        id: i,
        from: e.from,
        to: e.to,
        color: { color: style.color, highlight: cssVar("--accent") },
        width: style.width,
        dashes: style.dashes,
        title: e.columns.join(", ")
      };
    })
  );

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
  options.physics = {
    solver: "barnesHut",
    barnesHut: { gravitationalConstant: -2200, springLength: 110 },
    stabilization: { iterations: 250 }
  };

  var network = new vis.Network(canvas, { nodes: nodes, edges: edges }, options);

  /* Theme toggle re-colors the live network: theme-derived options via
     setOptions, then edge colors/dashes rebatched from the new palette
     (edge kind lives on the schema payload, not the edge objects). */
  function applyTheme() {
    network.setOptions(themeOptions());
    var updates = schema.edges.map(function (e, i) {
      var style = edgeStyle(e.kind);
      return {
        id: i,
        color: { color: style.color, highlight: cssVar("--accent") },
        width: style.width,
        dashes: style.dashes
      };
    });
    edges.update(updates);
  }
  if (typeof MutationObserver !== "undefined") {
    new MutationObserver(function () {
      applyTheme();
    }).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"]
    });
  }

  var panel = document.getElementById("db-panel");

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function columnRow(col) {
    var type = col.type ? ' <span class="muted">' + escapeHtml(col.type) + "</span>" : "";
    var pk = col.pk ? ' <span class="muted">· pk</span>' : "";
    return (
      '<div class="panel-row"><span class="panel-name">' +
      escapeHtml(col.name) +
      type +
      pk +
      "</span></div>"
    );
  }

  function edgeList(name, direction) {
    var rows = schema.edges.filter(function (e) {
      return e[direction] === name;
    });
    if (!rows.length) {
      return '<p class="panel-empty">none</p>';
    }
    return rows
      .map(function (e) {
        var other = direction === "from" ? e.to : e.from;
        var verb = direction === "from" ? "→" : "←";
        return (
          '<div class="panel-row"><span class="panel-name">' +
          verb + " " + escapeHtml(other) +
          ' <span class="muted">(' + escapeHtml(e.kind) + ": " +
          escapeHtml(e.columns.join(", ")) + ")</span></span></div>"
        );
      })
      .join("");
  }

  function showPanel(name) {
    var t = byName[name];
    if (!t) {
      return;
    }
    panel.innerHTML =
      '<div class="panel-head-row"><span class="panel-kind">table</span>' +
      '<span class="panel-title">' + escapeHtml(name) + "</span></div>" +
      '<p class="panel-sub">' + t.rows + " rows · " + t.columns.length + " columns</p>" +
      '<div class="panel-section"><div class="panel-head">Columns</div>' +
      '<div class="panel-list">' + t.columns.map(columnRow).join("") + "</div></div>" +
      '<div class="panel-section"><div class="panel-head">References (out)</div>' +
      edgeList(name, "from") + "</div>" +
      '<div class="panel-section"><div class="panel-head">Referenced by (in)</div>' +
      edgeList(name, "to") + "</div>";
    panel.hidden = false;
  }

  network.on("click", function (params) {
    if (params.nodes.length) {
      showPanel(params.nodes[0]);
    } else {
      panel.hidden = true;
      panel.innerHTML = "";
    }
  });

  /* Overlay buttons mirror the graph view's zoom controls. */
  document.querySelectorAll("button[data-db-action]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var action = btn.getAttribute("data-db-action");
      if (action === "zoom-in") {
        network.moveTo({ scale: network.getScale() * 1.25 });
      } else if (action === "zoom-out") {
        network.moveTo({ scale: network.getScale() * 0.8 });
      } else if (action === "fit") {
        network.fit({ animation: false });
      }
    });
  });
})();
