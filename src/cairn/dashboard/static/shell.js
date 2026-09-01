/* Cairn dashboard shell chrome: the behaviors every page shares — the
   topbar workspace selector, the sidebar collapse, and the command
   palette. app.js (the per-view graph/live-refresh logic) is a separate
   file with separate element ids; the two never touch. */

/* Workspace selector: switching rewrites the ?store= param on the
   current URL and reloads, so the tab stays and every view re-renders on
   the selected workspace. */
(function () {
  "use strict";
  var select = document.getElementById("store-select");
  if (!select) {
    return;
  }
  select.addEventListener("change", function () {
    var key = select.value;
    var url = new URL(window.location.href);
    if (key) {
      url.searchParams.set("store", key);
    } else {
      /* Back to the launch store: a bare URL the stickiness script would
         redirect right back to the remembered store — clear the memory
         so the launch store is what loads. */
      url.searchParams.delete("store");
      try {
        window.localStorage.removeItem("cairn-store");
      } catch (err) {
        /* storage unavailable: the selection stays URL-only */
      }
    }
    window.location.assign(url.pathname + url.search + url.hash);
  });
})();

/* Sidebar collapse: the state persists (localStorage "cairn-sidebar")
   and base.html's head script re-applies it pre-paint on the next load. */
(function () {
  "use strict";
  var button = document.getElementById("sidebar-collapse");
  if (!button) {
    return;
  }
  button.addEventListener("click", function () {
    var root = document.documentElement;
    var collapsing = root.getAttribute("data-sidebar") !== "collapsed";
    if (collapsing) {
      root.setAttribute("data-sidebar", "collapsed");
    } else {
      root.removeAttribute("data-sidebar");
    }
    try {
      window.localStorage.setItem(
        "cairn-sidebar",
        collapsing ? "collapsed" : "expanded"
      );
    } catch (err) {
      /* storage unavailable: the choice lasts only for this page */
    }
    button.setAttribute(
      "aria-label",
      collapsing ? "Expand sidebar" : "Collapse sidebar"
    );
  });
})();

/* Command palette (Ctrl/Cmd+K): views and workspaces from the
   server-rendered seed (store-carrying hrefs), symbols live from
   /graph/suggest on the current store — the same endpoint and guarded
   store param the graph typeahead uses. */
(function () {
  "use strict";
  var overlay = document.getElementById("palette");
  var input = document.getElementById("palette-input");
  var list = document.getElementById("palette-list");
  var openButton = document.getElementById("palette-open");
  var dataEl = document.getElementById("palette-data");
  if (!overlay || !input || !list || !dataEl) {
    return;
  }

  var views = [];
  var workspaces = [];
  try {
    var seed = JSON.parse(dataEl.textContent || "{}");
    views = seed.views || [];
    workspaces = seed.workspaces || [];
  } catch (err) {
    /* unparsable seed: views/workspaces stay empty; symbols still work */
  }

  var rows = []; // {label, hint, action}
  var active = -1;
  var seq = 0; // symbol-fetch sequence: stale responses never render
  var timer = null;
  var lastFocus = null;

  // Platform-correct kbd hint (cosmetic; both modifiers always work).
  var kbd = document.getElementById("palette-kbd");
  if (kbd && /Mac/i.test(navigator.platform || "")) {
    kbd.textContent = "\u2318K";
  }

  function isOpen() {
    return !overlay.hidden;
  }

  function currentStore() {
    return new URL(window.location.href).searchParams.get("store") || "";
  }

  function switchStore(key) {
    var url = new URL(window.location.href);
    if (key) {
      url.searchParams.set("store", key);
    } else {
      url.searchParams.delete("store");
      try {
        window.localStorage.removeItem("cairn-store");
      } catch (err) {
        /* storage unavailable: the selection stays URL-only */
      }
    }
    window.location.assign(url.pathname + url.search + url.hash);
  }

  function symbolHref(name) {
    var store = currentStore();
    return (
      "/graph?scope=symbol&focus=" + encodeURIComponent(name) +
      (store ? "&store=" + encodeURIComponent(store) : "")
    );
  }

  function renderRows() {
    list.textContent = "";
    if (!rows.length) {
      var empty = document.createElement("li");
      empty.className = "palette-row palette-row-empty";
      empty.textContent = "no matches";
      list.appendChild(empty);
      list.removeAttribute("aria-activedescendant");
      return;
    }
    rows.forEach(function (row, i) {
      var li = document.createElement("li");
      li.id = "palette-row-" + i;
      li.className = "palette-row" + (i === active ? " active" : "");
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", i === active ? "true" : "false");
      var label = document.createElement("span");
      label.className = "palette-row-label";
      label.textContent = row.label;
      li.appendChild(label);
      if (row.hint) {
        var hint = document.createElement("span");
        hint.className = "palette-row-hint";
        hint.textContent = row.hint;
        li.appendChild(hint);
      }
      li.addEventListener("click", function () {
        row.action();
      });
      list.appendChild(li);
    });
    list.setAttribute(
      "aria-activedescendant",
      active >= 0 ? "palette-row-" + active : ""
    );
    var current = document.getElementById("palette-row-" + active);
    if (current && current.scrollIntoView) {
      current.scrollIntoView({ block: "nearest" });
    }
  }

  function fetchSymbols(query) {
    var mySeq = seq;
    if (timer) {
      clearTimeout(timer);
    }
    timer = setTimeout(function () {
      var url = "/graph/suggest?name=" + encodeURIComponent(query);
      var store = currentStore();
      if (store) {
        url += "&store=" + encodeURIComponent(store);
      }
      fetch(url)
        .then(function (resp) {
          return resp.json();
        })
        .then(function (data) {
          if (mySeq !== seq) {
            return; // a newer keystroke owns the list now
          }
          var matches = (data && data.matches) || [];
          matches.slice(0, 8).forEach(function (m) {
            rows.push({
              label: m.name,
              hint: m.kind + (m.file ? " — " + m.file : ""),
              action: function () {
                window.location.assign(symbolHref(m.name));
              },
            });
          });
          if (data && data.truncated) {
            rows.push({
              label: "more matches…",
              hint: "keep typing to narrow",
              action: function () {},
            });
          }
          if (active < 0 && rows.length) {
            active = 0;
          }
          renderRows();
        })
        .catch(function () {
          /* fetch failed: views/workspaces still listed */
        });
    }, 160);
  }

  function refresh(query) {
    seq += 1; // any in-flight symbol response is stale now
    rows = [];
    var q = (query || "").toLowerCase();
    views.forEach(function (v) {
      if (!q || v.label.toLowerCase().indexOf(q) !== -1) {
        rows.push({
          label: v.label,
          hint: "view",
          action: function () {
            window.location.assign(v.href);
          },
        });
      }
    });
    workspaces.forEach(function (w) {
      if (!q || (w.label || "").toLowerCase().indexOf(q) !== -1) {
        rows.push({
          label: w.label,
          hint: "workspace",
          action: function () {
            switchStore(w.key);
          },
        });
      }
    });
    active = rows.length ? 0 : -1;
    renderRows();
    if (q.length >= 2) {
      fetchSymbols(query);
    }
  }

  function open() {
    lastFocus = document.activeElement;
    overlay.hidden = false;
    input.value = "";
    refresh("");
    input.focus();
  }

  function close() {
    overlay.hidden = true;
    seq += 1;
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    if (lastFocus && lastFocus.focus) {
      lastFocus.focus();
    }
  }

  if (openButton) {
    openButton.addEventListener("click", open);
  }
  overlay.addEventListener("click", function (event) {
    var target = event.target;
    if (target && target.getAttribute && target.getAttribute("data-palette-close") !== null) {
      close();
    }
  });
  input.addEventListener("input", function () {
    refresh(input.value);
  });
  input.addEventListener("keydown", function (event) {
    var key = event.key || "";
    if (key === "ArrowDown") {
      event.preventDefault();
      if (rows.length) {
        active = (active + 1) % rows.length;
        renderRows();
      }
    } else if (key === "ArrowUp") {
      event.preventDefault();
      if (rows.length) {
        active = (active - 1 + rows.length) % rows.length;
      }
      renderRows();
    } else if (key === "Enter") {
      event.preventDefault();
      if (active >= 0 && rows[active]) {
        rows[active].action();
      }
    }
  });
  document.addEventListener("keydown", function (event) {
    var key = event.key || "";
    if ((event.metaKey || event.ctrlKey) && (key === "k" || key === "K")) {
      event.preventDefault();
      if (isOpen()) {
        close();
      } else {
        open();
      }
    } else if (key === "Escape" && isOpen()) {
      event.preventDefault();
      close();
    }
  });
})();
