/* Cairn dashboard shell chrome: the behaviors every page shares — the
   topbar workspace selector today, sidebar collapse and the command
   palette alongside. app.js (the per-view graph/live-refresh logic) is a
   separate file with separate element ids; the two never touch. */
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
