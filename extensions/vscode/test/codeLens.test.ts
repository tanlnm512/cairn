/**
 * Recorded code-lens behaviors (C-02): caller/callee counts rendered from
 * the frozen symbol-endpoint shapes, the explicit zero state, unknown or
 * unusable symbols render no lens, one request per distinct name, per-name
 * lookup failures never break the batch. Runs under plain Node against
 * fixture responses — no network, no editor.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {
  countsTitle,
  lensSpec,
  lensSpecsFor,
  LENS_REQUEST_DEPTH,
  type SymbolRef,
} from "../src/providers/codeLens";

/** The frozen found shape for a symbol with a caller and no callees. */
const WITH_CALLER = {
  found: true,
  symbol: "demo_util",
  kind: "function",
  qualified_name: "demo.util.demo_util",
  file: "src/demo/util.py",
  callers: { count: 1 },
  callees: { count: 0 },
  blast_radius: {
    depth: 2,
    total: 2,
    truncated: false,
    symbols: [
      { symbol: "demo_helper", file: "src/demo/core.py", repo: "demo", depth: 0 },
      { symbol: "demo_main", file: "src/demo/core.py", repo: "demo", depth: 1 },
    ],
  },
};

/** The frozen found shape for a symbol with one caller and one callee. */
const WITH_BOTH = {
  ...WITH_CALLER,
  symbol: "demo_helper",
  qualified_name: "demo.core.demo_helper",
  file: "src/demo/core.py",
  callers: { count: 1 },
  callees: { count: 1 },
};

/** The frozen populated zero shape for an unknown symbol. */
const NOT_FOUND = {
  found: false,
  symbol: "no_such_symbol",
  kind: "",
  qualified_name: "",
  file: "",
  callers: { count: 0 },
  callees: { count: 0 },
  blast_radius: { depth: 2, total: 0, truncated: false, symbols: [] },
};

test("the lens requests the smallest radius; depth is bounded in the request", () => {
  assert.equal(LENS_REQUEST_DEPTH, 1);
});

test("unknown, unusable, and non-found payloads render no lens", () => {
  assert.equal(lensSpec(3, NOT_FOUND), null);
  assert.equal(lensSpec(3, null), null);
  assert.equal(lensSpec(3, undefined), null);
  assert.equal(lensSpec(3, "garbage"), null);
  assert.equal(lensSpec(3, {}), null);
  assert.equal(lensSpec(3, { found: true, symbol: "x", callers: { count: "many" }, callees: { count: 0 }, blast_radius: { depth: 1, total: 0, truncated: false, symbols: [] } }), null);
  assert.equal(lensSpec(3, { found: true, symbol: "x", callers: { count: 1 }, blast_radius: { depth: 1, total: 0, truncated: false, symbols: [] } }), null);
});

test("counts render singular, plural, and the explicit zero state", () => {
  assert.equal(countsTitle(0, 0), "no callers · no callees");
  assert.equal(countsTitle(1, 0), "1 caller · 0 callees");
  assert.equal(countsTitle(1, 2), "1 caller · 2 callees");
  assert.equal(countsTitle(3, 1), "3 callers · 1 callee");
});

test("lenses land on the declaration lines of found symbols only, ordered by line", async () => {
  const refs: SymbolRef[] = [
    { name: "demo_util", line: 5 },
    { name: "demo_helper", line: 2 },
    { name: "no_such_symbol", line: 7 },
  ];
  const payloadFor = new Map<string, unknown>([
    ["demo_util", WITH_CALLER],
    ["demo_helper", WITH_BOTH],
    ["no_such_symbol", NOT_FOUND],
  ]);
  const specs = await lensSpecsFor(refs, async (name) => payloadFor.get(name));
  assert.deepEqual(specs, [
    { line: 2, title: "1 caller · 1 callee" },
    { line: 5, title: "1 caller · 0 callees" },
  ]);
});

test("one request per distinct name, shared across duplicate declarations", async () => {
  const requested: string[] = [];
  const refs: SymbolRef[] = [
    { name: "demo_util", line: 1 },
    { name: "demo_util", line: 9 },
    { name: "", line: 4 },
  ];
  const specs = await lensSpecsFor(refs, async (name) => {
    requested.push(name);
    return WITH_CALLER;
  });
  assert.deepEqual(requested, ["demo_util"]);
  assert.deepEqual(specs, [
    { line: 1, title: "1 caller · 0 callees" },
    { line: 9, title: "1 caller · 0 callees" },
  ]);
});

test("a failing lookup hides its lens without breaking the batch", async () => {
  const specs = await lensSpecsFor(
    [
      { name: "demo_util", line: 3 },
      { name: "boomed", line: 6 },
    ],
    async (name) => {
      if (name === "boomed") throw new Error("connection refused");
      return WITH_CALLER;
    },
  );
  assert.deepEqual(specs, [{ line: 3, title: "1 caller · 0 callees" }]);
});
