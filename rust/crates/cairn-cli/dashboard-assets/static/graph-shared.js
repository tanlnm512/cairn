/* Shared helpers for the vis-network canvas views (graph, database,
   knowledge graph): theme-token color access, motion preference, the
   deterministic spiral layout, the common vis option blocks, the overlay
   zoom/fit wiring, and the htmx inspect-panel race machinery. Each view
   script owns its per-view tuning (kind tokens, theme options, physics
   constants); include this file before the view script that consumes it. */
(function () {
  "use strict";

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

  /* Deterministic golden-angle spread: one factory per view script so
     each canvas consumes positions from its own sequence. */
  function makeSpiralPosition() {
    var GOLDEN = Math.PI * (3 - Math.sqrt(5));
    var cursor = 0;
    return function () {
      var i = cursor;
      cursor += 1;
      var r = 100 * Math.sqrt(i + 0.6);
      var a = i * GOLDEN;
      return { x: Math.round(r * Math.cos(a)), y: Math.round(r * Math.sin(a)) };
    };
  }

  /* The interaction block every canvas shares; callers add their
     themed options and physics on top. */
  function interactionOptions() {
    return {
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
  }

  /* Barnes-Hut physics with per-view tuning (the schema graph is far
     smaller than the code graph); stabilization's enabled/fit are the
     vis defaults spelled out. */
  function physicsOptions(barnesHut, iterations) {
    return {
      enabled: true,
      solver: "barnesHut",
      barnesHut: barnesHut,
      stabilization: { enabled: true, iterations: iterations, fit: true }
    };
  }

  /* Canvas overlay zoom/fit cluster: wires the overlay buttons carrying
     ``actionAttr``; animate=false snaps, animate=true checks the motion
     preference per click (the eased camera move is animation). */
  function wireOverlayControls(canvas, network, actionAttr, zoomStep, animate) {
    var overlay = canvas.parentNode
      ? canvas.parentNode.querySelector(".graph-overlay")
      : null;
    if (!overlay) {
      return;
    }
    overlay.addEventListener("click", function (event) {
      var btn =
        event.target && event.target.closest
          ? event.target.closest("button[" + actionAttr + "]")
          : null;
      if (!btn || !overlay.contains(btn)) {
        return;
      }
      var action = btn.getAttribute(actionAttr);
      var animation =
        animate && !prefersReducedMotion()
          ? { duration: 250, easingFunction: "easeInOutQuad" }
          : false;
      if (action === "zoom-in") {
        network.moveTo({
          scale: network.getScale() * zoomStep,
          animation: animation
        });
      } else if (action === "zoom-out") {
        network.moveTo({
          scale: Math.max(network.getScale() / zoomStep, 0.05),
          animation: animation
        });
      } else if (action === "fit") {
        network.fit({ animation: animation });
      }
    });
  }

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

  /* The inspect panel's one-selection race machinery: a superseded
     fetch for ``pathPrefix`` is aborted outright (htmx:beforeSend carries
     the live xhr), and htmx swaps inside ajax(), so a stale response
     completing late is cancelled at htmx:beforeSwap too — only the
     latest request may swap. buildUrl(id) produces the fragment URL. */
  function wireInspectPanel(panel, pathPrefix, buildUrl) {
    var latest = "";
    var inFlight = null;
    function abortInFlight() {
      if (inFlight) {
        try {
          inFlight.abort();
        } catch (err) {
          /* abort() on an already-settled xhr is a no-op; nothing to free */
        }
        inFlight = null;
      }
    }
    function select(id) {
      if (!panel || !id || typeof htmx === "undefined") {
        return;
      }
      latest = buildUrl(id);
      panel.hidden = false;
      panel.textContent = "";
      panel.appendChild(el("p", "panel-empty", "loading '" + id + "'…"));
      htmx.ajax("GET", latest, { target: panel, swap: "innerHTML" });
    }
    function deselect() {
      if (!panel) {
        return;
      }
      abortInFlight();
      latest = "";
      panel.hidden = true;
      panel.textContent = "";
    }
    document.body.addEventListener("htmx:beforeSend", function (event) {
      var detail = event.detail || {};
      var path =
        detail.requestConfig && typeof detail.requestConfig.path === "string"
          ? detail.requestConfig.path
          : "";
      if (path.indexOf(pathPrefix) !== 0) {
        return;
      }
      abortInFlight();
      inFlight = detail.xhr;
    });
    document.body.addEventListener("htmx:beforeSwap", function (event) {
      var path =
        event.detail && event.detail.requestConfig
          ? event.detail.requestConfig.path
          : "";
      if (
        typeof path === "string" &&
        path.indexOf(pathPrefix) === 0 &&
        path !== latest
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
            path.indexOf(pathPrefix) !== 0 ||
            path !== latest
          ) {
            return;
          }
          panel.textContent = "";
          panel.appendChild(el("p", "panel-empty", "inspect request failed"));
        });
      }
    );
    return { select: select, deselect: deselect, abort: abortInFlight };
  }

  window.CairnGraphShared = {
    cssVar: cssVar,
    prefersReducedMotion: prefersReducedMotion,
    makeSpiralPosition: makeSpiralPosition,
    interactionOptions: interactionOptions,
    physicsOptions: physicsOptions,
    wireOverlayControls: wireOverlayControls,
    el: el,
    wireInspectPanel: wireInspectPanel
  };
})();
