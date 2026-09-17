/**
 * Recorded status-item behaviors (C-02): one aggregate indicator over all
 * workspace roots, worst state wins, detail text only in the tooltip.
 */
import test from "node:test";
import assert from "node:assert/strict";
import type { ServerState } from "../src/server";
import { aggregateDisplay, isDegraded, statusDisplay, worstState } from "../src/statusModel";

function state(partial: Partial<ServerState>): ServerState {
  return { root: "/ws", phase: "connecting", index: "unknown", ...partial };
}

test("worst state wins across workspace roots", () => {
  const states = [
    state({ root: "/ws/a", phase: "served", index: "indexed" }),
    state({ root: "/ws/b", phase: "served", index: "unindexed" }),
    state({ root: "/ws/c", phase: "offline", detail: "server exited" }),
  ];
  assert.equal(worstState(states).root, "/ws/c");
  assert.equal(
    worstState([state({ phase: "served", index: "indexed" }), state({ phase: "connecting" })]).phase,
    "connecting",
  );
  assert.equal(
    worstState([state({ phase: "connecting" }), state({ phase: "served", index: "unindexed" })]).phase,
    "served",
  );
});

test("indexed state renders the connected indicator", () => {
  const display = statusDisplay(state({ phase: "served", index: "indexed" }));
  assert.equal(display.text, "$(check) cairn");
  assert.match(display.tooltip, /connected/);
});

test("unindexed state renders the degraded indicator without an error surface", () => {
  const display = statusDisplay(state({ phase: "served", index: "unindexed" }));
  assert.equal(display.text, "$(circle-slash) cairn: unindexed");
  assert.match(display.tooltip, /not indexed/);
});

test("offline state carries the reason in the tooltip only", () => {
  const display = statusDisplay(state({ phase: "offline", detail: "server exited with code 1" }));
  assert.equal(display.text, "$(circle-slash) cairn: offline");
  assert.match(display.tooltip, /server exited with code 1/);
});

test("aggregate display reflects the worst root", () => {
  const display = aggregateDisplay([
    state({ root: "/ws/a", phase: "served", index: "indexed" }),
    state({ root: "/ws/b", phase: "offline", detail: "server did not answer in time" }),
  ]);
  assert.equal(display.text, "$(circle-slash) cairn: offline");
});

test("degradation verdict covers the stopped-server and unindexed paths", () => {
  assert.equal(isDegraded([state({ phase: "offline", detail: "server exited" })]), true);
  assert.equal(isDegraded([state({ phase: "connecting" })]), true);
  assert.equal(isDegraded([state({ phase: "served", index: "unindexed" })]), true);
  assert.equal(isDegraded([state({ phase: "served", index: "unknown" })]), false);
  assert.equal(isDegraded([state({ phase: "served", index: "indexed" })]), false);
});

test("degradation verdict is aggregate-level and total on an empty feed", () => {
  assert.equal(isDegraded([]), false);
  assert.equal(
    isDegraded([
      state({ root: "/ws/a", phase: "served", index: "indexed" }),
      state({ root: "/ws/b", phase: "offline" }),
    ]),
    true,
  );
});
