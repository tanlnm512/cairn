/* Cairn dashboard shell chrome: the behaviors every page shares — the
   topbar workspace selector, the sidebar collapse, and the command
   palette. app.js (the per-view graph/live-refresh logic) is a separate
   file with separate element ids; the two never touch.
   One outer IIFE keeps every helper file-local. */
(function () {
  "use strict";

  /* Switch the global workspace: rewrite the ?store= param on the
     current URL and reload — the tab stays and every view re-renders on
     the selected workspace (FR-003's seam stays the URL param). Back to
     the launch store (empty key) navigates bare, and clearing the
     remembered key matters: the head stickiness script would otherwise
     redirect the bare URL right back to the old store. */
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

  /* ---- Workspace selector (topbar) ---- */
  (function () {
    var select = document.getElementById("store-select");
    if (!select) {
      return;
    }
    select.addEventListener("change", function () {
      switchStore(select.value);
    });
  })();

  /* ---- Sidebar collapse ----
     The state persists (localStorage "cairn-sidebar") and base.html's
     head script re-applies it pre-paint on the next load. */
  (function () {
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
      var label = collapsing ? "Expand sidebar" : "Collapse sidebar";
      button.setAttribute("aria-label", label);
      button.title = label;
    });
  })();

  /* ---- Command palette (Ctrl/Cmd+K, Alpine component) ----
     A native <dialog>: showModal() provides the focus trap and
     Esc-to-close, and its close event (any path: Esc, click-outside,
     programmatic) restores focus to the element that launched the
     palette. This component owns the client state — open/toggle, the
     active row's highlight, keyboard navigation — while rows come from
     two sources: the server-rendered seed JSON (#palette-data) draws
     the initial unfiltered list the moment the palette opens, and
     typing re-fetches filtered rows as an htmx fragment
     (/palette/results — the same seed composition plus the server's
     symbol-suggest matches, decided server-side; never fetched here).
     Both row kinds carry their action as a data attribute, so
     activation is one code path. */
  function paletteRow(label, hint, attrs) {
    var li = document.createElement("li");
    li.className = "palette-row";
    li.setAttribute("role", "option");
    li.setAttribute("aria-selected", "false");
    if (attrs.href) {
      li.setAttribute("data-href", attrs.href);
    }
    if (attrs.storeKey) {
      li.setAttribute("data-store-key", attrs.storeKey);
    }
    var labelEl = document.createElement("span");
    labelEl.className = "palette-row-label";
    labelEl.textContent = label;
    li.appendChild(labelEl);
    if (hint) {
      var hintEl = document.createElement("span");
      hintEl.className = "palette-row-hint";
      hintEl.textContent = hint;
      li.appendChild(hintEl);
    }
    return li;
  }

  document.addEventListener("alpine:init", function () {
    if (!window.Alpine) {
      return;
    }
    window.Alpine.data("palette", function () {
      return {
        active: -1,
        lastFocus: null,
        pressTarget: null,

        init: function () {
          var self = this;
          // Platform-correct kbd hint (cosmetic; both modifiers work).
          var kbd = document.getElementById("palette-kbd");
          if (kbd && /Mac/i.test(navigator.platform || "")) {
            kbd.textContent = "\u2318K";
          }
          this.keyHandler = function (event) {
            if (
              (event.metaKey || event.ctrlKey) &&
              (event.key === "k" || event.key === "K")
            ) {
              event.preventDefault();
              self.toggle();
            }
          };
          document.addEventListener("keydown", this.keyHandler);
          // An htmx swap replaces the rows under the highlight; the
          // fresh list starts over at its first row.
          this.$refs.list.addEventListener("htmx:afterSwap", function () {
            self.active = self.rowEls().length ? 0 : -1;
            self.applyActive();
          });
          var openButton = document.getElementById("palette-open");
          if (openButton) {
            openButton.addEventListener("click", function () {
              self.show();
            });
          }
        },

        destroy: function () {
          document.removeEventListener("keydown", this.keyHandler);
        },

        toggle: function () {
          if (this.$refs.dialog.open) {
            this.$refs.dialog.close();
          } else {
            this.show();
          }
        },

        show: function () {
          if (this.$refs.dialog.open) {
            return;
          }
          this.lastFocus = document.activeElement;
          this.renderSeed();
          this.$refs.input.value = "";
          this.active = this.rowEls().length ? 0 : -1;
          this.applyActive();
          this.$refs.dialog.showModal();
          this.$refs.input.focus();
        },

        // Runs on the dialog's close event — every close path, Escape
        // included — so the launching element gets focus back.
        onClosed: function () {
          if (window.htmx && this.$refs.input) {
            window.htmx.trigger(this.$refs.input, "htmx:abort");
          }
          if (this.lastFocus && this.lastFocus.focus) {
            this.lastFocus.focus();
          }
          this.lastFocus = null;
        },

        // Click-outside dismiss: both press and release must land on the
        // dialog itself (a drag starting inside the card must not close).
        maybeDismiss: function (event) {
          if (
            event.target === this.$refs.dialog &&
            event.target === this.pressTarget
          ) {
            this.$refs.dialog.close();
          }
        },

        renderSeed: function () {
          var seed = {};
          try {
            seed = JSON.parse(
              document.getElementById("palette-data").textContent || "{}"
            );
          } catch (err) {
            /* unparsable seed: views/workspaces stay empty */
          }
          var list = this.$refs.list;
          list.textContent = "";
          (seed.views || []).forEach(function (view) {
            list.appendChild(
              paletteRow(view.label, "view", { href: view.href })
            );
          });
          (seed.workspaces || []).forEach(function (workspace) {
            list.appendChild(
              paletteRow(workspace.label, "workspace", {
                storeKey: workspace.key,
              })
            );
          });
        },

        rowEls: function () {
          return this.$refs.list.querySelectorAll(".palette-row");
        },

        move: function (delta) {
          var rows = this.rowEls();
          if (!rows.length) {
            return;
          }
          this.active = (this.active + delta + rows.length) % rows.length;
          this.applyActive();
        },

        applyActive: function () {
          var rows = this.rowEls();
          rows.forEach(function (row, i) {
            var on = i === this.active;
            row.classList.toggle("active", on);
            row.setAttribute("aria-selected", on ? "true" : "false");
            if (!row.id) {
              row.id = "palette-row-" + i;
            }
          }, this);
          var current = rows[this.active];
          if (current) {
            this.$refs.list.setAttribute(
              "aria-activedescendant",
              current.id
            );
            if (current.scrollIntoView) {
              current.scrollIntoView({ block: "nearest" });
            }
          } else {
            this.$refs.list.removeAttribute("aria-activedescendant");
          }
        },

        pick: function (event) {
          var row = event.target.closest(".palette-row");
          if (row) {
            this.activateRow(row);
          }
        },

        choose: function () {
          var row = this.rowEls()[this.active];
          if (row) {
            this.activateRow(row);
          }
        },

        activateRow: function (row) {
          var key = row.getAttribute("data-store-key");
          if (key) {
            this.$refs.dialog.close();
            switchStore(key);
            return;
          }
          var href = row.getAttribute("data-href");
          if (href) {
            this.$refs.dialog.close();
            window.location.assign(href);
          }
        },
      };
    });
  });
})();
