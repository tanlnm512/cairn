/**
 * Recorded hover behaviors (C-02) against fixture responses mirroring the
 * frozen /editor/symbol contract: counts and the depth-limited radius
 * render readably, unknown symbols and failed fetches render nothing,
 * degraded shells issue no request, and the depth is bounded in the
 * request. A loopback server pins the exact request path the provider
 * sends. Runs under plain Node — no network beyond 127.0.0.1.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { createServer, type Server } from "node:http";
import type { CancellationToken, Hover, Position, TextDocument } from "vscode";
import {
  DEFAULT_HOVER_DEPTH,
  SymbolHoverProvider,
  fetchSymbolAt,
  parseSymbolPayload,
  renderSymbolHover,
  symbolAt,
  type SymbolPayload,
} from "../src/providers/hover";
import type { ServerState } from "../src/server";

function fixturePayload(partial: Partial<SymbolPayload> = {}): SymbolPayload {
  return {
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
    ...partial,
  };
}

function state(partial: Partial<ServerState>): ServerState {
  return { root: "/ws", phase: "served", index: "indexed", ...partial };
}

function fakeDocument(word: string | undefined): TextDocument {
  return {
    getWordRangeAtPosition: () => (word === undefined ? undefined : { line: 0, character: 0 }),
    getText: () => word ?? "",
  } as unknown as TextDocument;
}

const cursor = { line: 3, character: 10 } as Position;

function fakeToken(cancelled = false): CancellationToken {
  return { isCancellationRequested: cancelled } as CancellationToken;
}

interface Harness {
  provider: SymbolHoverProvider;
  hovers: string[];
  fetches: Array<[string, number]>;
}

function makeProvider(options: {
  states?: ServerState[];
  payload?: SymbolPayload | null;
  depth?: number;
}): Harness {
  const hovers: string[] = [];
  const fetches: Array<[string, number]> = [];
  const provider = new SymbolHoverProvider({
    states: () => options.states ?? [state({})],
    hover: (markdown) => {
      hovers.push(markdown);
      return markdown as unknown as Hover;
    },
    fetchSymbol: async (name, depth) => {
      fetches.push([name, depth]);
      return options.payload === undefined ? fixturePayload() : options.payload;
    },
    depth: options.depth,
  });
  return { provider, hovers, fetches };
}

async function listenOnEphemeralPort(handler: (url: string, respond: (body: string) => void) => void): Promise<{
  host: string;
  port: number;
  close: () => Promise<void>;
}> {
  const server: Server = createServer((request, response) => {
    handler(request.url ?? "", (body) => {
      response.setHeader("content-type", "application/json");
      response.end(body);
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address !== null && typeof address === "object", "server must expose an address");
  return {
    host: address.address,
    port: address.port,
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  };
}

test("hover renders both counts and the depth-limited radius for a known symbol", async () => {
  const { provider, hovers, fetches } = makeProvider({});
  const hover = await provider.provideHover(fakeDocument("demo_util"), cursor, fakeToken());
  assert.ok(hover, "a known symbol must produce a hover");
  assert.deepEqual(fetches, [["demo_util", DEFAULT_HOVER_DEPTH]]);
  const markdown = hovers[0];
  assert.match(markdown, /Callers: \*\*1\*\*/);
  assert.match(markdown, /Callees: \*\*0\*\*/);
  assert.match(markdown, /\*\*Blast radius\*\* — 2 symbols within 2 hops/);
  assert.match(markdown, /`demo_helper` — src\/demo\/core\.py · distance 0/);
  assert.match(markdown, /`demo_main` — src\/demo\/core\.py · distance 1/);
});

test("a symbol with no callers or callees renders a readable zero state", async () => {
  const payload = fixturePayload({
    symbol: "demo_leaf",
    callers: { count: 0 },
    callees: { count: 0 },
    blast_radius: { depth: 2, total: 0, truncated: false, symbols: [] },
  });
  const { provider, hovers } = makeProvider({ payload });
  const hover = await provider.provideHover(fakeDocument("demo_leaf"), cursor, fakeToken());
  assert.ok(hover);
  assert.match(hovers[0], /Callers: \*\*0\*\* · Callees: \*\*0\*\*/);
  assert.match(hovers[0], /0 symbols within 2 hops/);
});

test("a truncated radius says so", () => {
  const payload = fixturePayload({
    blast_radius: {
      depth: 2,
      total: 9,
      truncated: true,
      symbols: [{ symbol: "demo_helper", file: "src/demo/core.py", repo: "demo", depth: 0 }],
    },
  });
  const markdown = renderSymbolHover(payload);
  assert.ok(markdown);
  assert.match(markdown, /radius truncated at the depth limit/);
  assert.match(markdown, /9 symbols within 2 hops/);
});

test("unknown symbols render nothing, never an error surface", async () => {
  const payload = fixturePayload({
    found: false,
    symbol: "no_such_symbol",
    blast_radius: { depth: 2, total: 0, truncated: false, symbols: [] },
  });
  const { provider, hovers } = makeProvider({ payload });
  assert.equal(await provider.provideHover(fakeDocument("no_such_symbol"), cursor, fakeToken()), undefined);
  assert.equal(hovers.length, 0);
});

test("a failed or malformed fetch renders nothing", async () => {
  for (const payload of [null, { found: "yes" } as unknown as SymbolPayload]) {
    const { provider, hovers } = makeProvider({ payload });
    assert.equal(await provider.provideHover(fakeDocument("demo_util"), cursor, fakeToken()), undefined);
    assert.equal(hovers.length, 0);
  }
});

test("a rejected fetcher renders nothing", async () => {
  const provider = new SymbolHoverProvider({
    states: () => [state({})],
    hover: () => null as unknown as Hover,
    fetchSymbol: async () => {
      throw new Error("boom");
    },
  });
  assert.equal(await provider.provideHover(fakeDocument("demo_util"), cursor, fakeToken()), undefined);
});

test("degraded shells render nothing and issue no request", async () => {
  for (const degraded of [
    state({ phase: "offline" }),
    state({ phase: "connecting" }),
    state({ index: "unindexed" }),
  ]) {
    const { provider, hovers, fetches } = makeProvider({ states: [state({}), degraded] });
    assert.equal(await provider.provideHover(fakeDocument("demo_util"), cursor, fakeToken()), undefined);
    assert.equal(fetches.length, 0, `no request while degraded (${degraded.phase}/${degraded.index})`);
    assert.equal(hovers.length, 0);
  }
});

test("the requested depth travels in the request, not client-side", async () => {
  const { provider, fetches } = makeProvider({ depth: 3 });
  await provider.provideHover(fakeDocument("demo_util"), cursor, fakeToken());
  assert.deepEqual(fetches, [["demo_util", 3]]);
});

test("a cursor off a symbol issues no request", async () => {
  const { provider, fetches } = makeProvider({});
  assert.equal(await provider.provideHover(fakeDocument(undefined), cursor, fakeToken()), undefined);
  assert.equal(fetches.length, 0);
});

test("cancellation before or during the request renders nothing", async () => {
  const { provider, fetches } = makeProvider({});
  assert.equal(await provider.provideHover(fakeDocument("demo_util"), cursor, fakeToken(true)), undefined);
  assert.equal(fetches.length, 0);

  const late = { isCancellationRequested: false } as CancellationToken;
  const provider2 = new SymbolHoverProvider({
    states: () => [state({})],
    hover: () => null as unknown as Hover,
    fetchSymbol: async () => {
      late.isCancellationRequested = true;
      return fixturePayload();
    },
  });
  assert.equal(await provider2.provideHover(fakeDocument("demo_util"), cursor, late), undefined);
});

test("symbolAt extracts the word under the cursor", () => {
  assert.equal(symbolAt(fakeDocument("demo_util"), cursor), "demo_util");
  assert.equal(symbolAt(fakeDocument(undefined), cursor), "");
});

test("payload parsing accepts the frozen shape and rejects everything else", () => {
  const raw = JSON.parse(JSON.stringify(fixturePayload()));
  assert.deepEqual(parseSymbolPayload(raw), fixturePayload());
  const zero = parseSymbolPayload(JSON.parse(JSON.stringify(fixturePayload({ found: false }))));
  assert.equal(zero?.found, false);
  assert.equal(parseSymbolPayload(null), null);
  assert.equal(parseSymbolPayload("nope"), null);
  assert.equal(parseSymbolPayload({}), null);
  assert.equal(parseSymbolPayload({ found: true, symbol: "x" }), null);
});

test("the fetch issues a GET on the frozen path with the encoded name and depth", async () => {
  const seen: string[] = [];
  const server = await listenOnEphemeralPort((url, respond) => {
    seen.push(url);
    respond(seen.length === 1 ? JSON.stringify(fixturePayload()) : "not json");
  });
  try {
    const payload = await fetchSymbolAt(server.host, server.port, "demo util", 2);
    assert.deepEqual(payload, fixturePayload());
    assert.deepEqual(seen.slice(0, 1), ["/editor/symbol?name=demo%20util&depth=2"]);
    const malformed = await fetchSymbolAt(server.host, server.port, "demo_util", DEFAULT_HOVER_DEPTH);
    assert.equal(malformed, null);
  } finally {
    await server.close();
  }
});

test("an unreachable server resolves null instead of throwing", async () => {
  const server = await listenOnEphemeralPort((_url, respond) => respond("{}"));
  await server.close();
  const payload = await fetchSymbolAt(server.host, server.port, "demo_util", DEFAULT_HOVER_DEPTH);
  assert.equal(payload, null);
});
