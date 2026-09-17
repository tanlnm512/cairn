/**
 * Recorded compass/memory panel behaviors (C-02): tolerant payload parsing,
 * the degraded/empty/content view mapping, HTML escaping with memory order,
 * and the one in-flight latest-wins request runner. Runs under plain Node
 * against fixture responses — no editor, no network.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {
  createLatestWinsRunner,
  degradedNote,
  fileRequestPath,
  hasContent,
  panelView,
  parseFilePayload,
  renderPanelHtml,
  type FilePanelPayload,
  type PanelView,
} from "../src/panel/compassModel";
import type { ServerState } from "../src/server";

const FIXTURE: FilePanelPayload = {
  path: "src/demo/core.py",
  module: "src/demo",
  compass: { found: true, title: "demo core", body: "demo core: entry points and helpers." },
  memories: [
    { id: "memory/tribal/m1", type: "pattern", title: "src/demo retry rule", body: "Body without a path mention." },
    { id: "memory/tribal/m2", type: "decision", title: "Freeze before consumers", body: "Ships only after src/demo review." },
  ],
};

const ZERO_COMPASS = { found: false, title: "", body: "" };

function state(partial: Partial<ServerState>): ServerState {
  return { root: "/ws", phase: "connecting", index: "unknown", ...partial };
}

/** One macrotask: lets the runner's microtask chain settle. */
function tick(): Promise<void> {
  return new Promise((resolve) => setImmediate(resolve));
}

test("parses the frozen file-endpoint response into the panel payload", () => {
  const payload = parseFilePayload(JSON.parse(JSON.stringify(FIXTURE)));
  assert.deepEqual(payload, FIXTURE);
});

test("parses malformed bodies into the zero shape without throwing", () => {
  const bodies: unknown[] = [null, 42, "nope", {}, { compass: 7 }, { memories: "many" }, { memories: [null, 3, { title: 5 }] }];
  for (const body of bodies) {
    const payload = parseFilePayload(body);
    assert.equal(typeof payload.path, "string");
    assert.equal(typeof payload.module, "string");
    assert.deepEqual(payload.compass, ZERO_COMPASS);
    assert.ok(Array.isArray(payload.memories));
    for (const memory of payload.memories) {
      assert.deepEqual(Object.keys(memory).sort(), ["body", "id", "title", "type"]);
    }
  }
  assert.deepEqual(parseFilePayload(null), {
    path: "",
    module: "",
    compass: ZERO_COMPASS,
    memories: [],
  });
});

test("content is the compass excerpt or the memories, never the file identity alone", () => {
  assert.equal(hasContent(FIXTURE), true);
  assert.equal(hasContent({ ...FIXTURE, compass: ZERO_COMPASS }), true);
  assert.equal(hasContent({ ...FIXTURE, memories: [] }), true);
  assert.equal(hasContent({ ...FIXTURE, compass: ZERO_COMPASS, memories: [] }), false);
});

test("the aggregate degradation verdict becomes the panel's status note", () => {
  const offline = degradedNote([state({ phase: "offline", detail: "server exited with code 1" })]);
  assert.match(offline ?? "", /unreachable/);
  assert.match(offline ?? "", /server exited with code 1/);
  assert.match(degradedNote([state({ phase: "served", index: "unindexed" })]) ?? "", /not indexed/);
  assert.equal(degradedNote([state({ phase: "served", index: "indexed" })]), null);
});

test("view mapping: degraded wins, then empty, then content", () => {
  assert.equal(panelView(FIXTURE, "cairn: offline").kind, "degraded");
  assert.deepEqual(panelView(null, null), { kind: "empty", path: "" });
  assert.deepEqual(panelView({ ...FIXTURE, compass: ZERO_COMPASS, memories: [] }, null), {
    kind: "empty",
    path: "src/demo/core.py",
  });
  assert.deepEqual(panelView(FIXTURE, null), { kind: "content", payload: FIXTURE });
});

test("content render keeps newest-first memory order", () => {
  const html = renderPanelHtml(panelView(FIXTURE, null));
  const first = html.indexOf("src/demo retry rule");
  const second = html.indexOf("Freeze before consumers");
  assert.ok(first !== -1 && second !== -1 && first < second, "memories must render in delivered order");
});

test("content render escapes knowledge-base markup", () => {
  const hostile: FilePanelPayload = {
    path: "src/x.py",
    module: "src",
    compass: { found: true, title: "<script>alert(1)</script>", body: "body & <b>bold</b>" },
    memories: [{ id: "m", type: "pattern", title: "safe title", body: "<img src=x onerror=alert(2)>" }],
  };
  const html = renderPanelHtml(panelView(hostile, null));
  assert.ok(!html.includes("<script>alert"), "raw markup must be escaped");
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(html, /body &amp; &lt;b&gt;bold&lt;\/b&gt;/);
  assert.ok(!html.includes("<img"), "raw markup must be escaped in memory bodies");
});

test("degraded render shows the status note and no data", () => {
  const html = renderPanelHtml(panelView(FIXTURE, "cairn: local server unreachable"));
  assert.match(html, /cairn: local server unreachable/);
  assert.ok(!html.includes("demo core"), "degraded panel must not render payload data");
});

test("empty render is a clean no-content state, never an error", () => {
  const named: PanelView = { kind: "empty", path: "docs/readme.md" };
  assert.match(renderPanelHtml(named), /No compass or memory content/);
  assert.match(renderPanelHtml(named), /docs\/readme\.md/);
  assert.match(renderPanelHtml({ kind: "empty", path: "" }), /No compass or memory content\./);
});

test("file requests address the frozen endpoint with the path encoded", () => {
  assert.equal(fileRequestPath("src/demo/core.py"), "/editor/file?path=src%2Fdemo%2Fcore.py");
});

test("requests run one in flight and the latest path wins", async () => {
  const fetched: string[] = [];
  const delivered: string[] = [];
  let releaseFirst!: () => void;
  const gated = new Promise<void>((resolve) => {
    releaseFirst = resolve;
  });
  const runner = createLatestWinsRunner(
    async (path: string): Promise<string> => {
      fetched.push(path);
      if (path === "slow") await gated;
      return `done:${path}`;
    },
    (path, result) => delivered.push(`${path}=${result}`),
  );

  runner.request("slow");
  await tick();
  runner.request("quick");
  runner.request("latest");
  releaseFirst();
  await tick();
  await tick();

  assert.deepEqual(fetched, ["slow", "latest"]);
  assert.deepEqual(delivered, ["latest=done:latest"]);
});

test("requests settle in delivery order when nothing supersedes", async () => {
  const delivered: string[] = [];
  const runner = createLatestWinsRunner(
    async (path: string): Promise<string> => `done:${path}`,
    (path, result) => delivered.push(`${path}=${result}`),
  );
  runner.request("a");
  await tick();
  runner.request("b");
  await tick();
  assert.deepEqual(delivered, ["a=done:a", "b=done:b"]);
});

test("a failed request delivers nothing and the runner stays usable", async () => {
  const delivered: string[] = [];
  let failing = true;
  const runner = createLatestWinsRunner(
    async (path: string): Promise<string> => {
      if (failing) throw new Error("transport down");
      return `done:${path}`;
    },
    (path, result) => delivered.push(`${path}=${result}`),
  );
  runner.request("a");
  await tick();
  assert.deepEqual(delivered, []);
  failing = false;
  runner.request("b");
  await tick();
  assert.deepEqual(delivered, ["b=done:b"]);
});
