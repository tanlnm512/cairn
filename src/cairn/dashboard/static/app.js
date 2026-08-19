/* Graph view: build vis-network DataSets from the server-serialized
   {nodes, edges, metadata} JSON block and render an interactive network
   (drag to pan, wheel to zoom). No CDN — vis-network is vendored. */
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
  var known = {};
  data.nodes.forEach(function (n) {
    known[n.id] = true;
  });
  var nodes = new vis.DataSet(
    data.nodes.map(function (n) {
      return {
        id: n.id,
        label: n.id,
        title: [n.kind, n.file].filter(Boolean).join("\n"),
        group: n.kind || "other"
      };
    })
  );
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
  new vis.Network(
    canvas,
    { nodes: nodes, edges: edges },
    {
      autoResize: true,
      interaction: { dragNodes: true, dragView: true, zoomView: true }
    }
  );
})();
