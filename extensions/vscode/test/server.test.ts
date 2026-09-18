/**
 * Recorded spawn/attach behaviors of the server shell (C-02): attach-first
 * adoption, per-root spawn with polling, timeout/exit/missing-CLI degradation,
 * and dispose semantics (adopted servers survive, spawned ones do not).
 * Runs under plain Node against injected fakes — no real process, no network.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {
  ServerManager,
  SERVER_HOST,
  SERVER_PORT,
  dashboardCommand,
  parseIndexState,
  type SpawnedServer,
} from "../src/server";

interface FakeChild extends SpawnedServer {
  emit(event: "error", error: Error): void;
  emit(event: "exit", code: number | null, signal: NodeJS.Signals | null): void;
  readonly killed: boolean;
}

/** One macrotask: lets ensure()'s pre-spawn probe settle and the child listeners register. */
function tick(): Promise<void> {
  return new Promise((resolve) => setImmediate(resolve));
}

function makeChild(): FakeChild {
  const listeners = new Map<string, Array<(...args: never[]) => void>>();
  const child = {
    killed: false,
    once(event: string, listener: (...args: never[]) => void): unknown {
      const existing = listeners.get(event) ?? [];
      existing.push(listener);
      listeners.set(event, existing);
      return child;
    },
    kill(): boolean {
      child.killed = true;
      return true;
    },
    emit(event: string, ...args: never[]): void {
      for (const listener of [...(listeners.get(event) ?? [])]) listener(...args);
    },
  };
  return child as FakeChild;
}

test("attaches to an already-running server without spawning", async () => {
  let spawns = 0;
  const manager = new ServerManager({
    probe: async () => ({ index: "indexed" }),
    spawnServer: () => {
      spawns += 1;
      throw new Error("must not spawn when a server answers");
    },
  });
  const state = await manager.ensure("/ws/a");
  assert.equal(state.phase, "served");
  assert.equal(state.index, "indexed");
  assert.equal(spawns, 0);
  manager.dispose();
});

test("spawns the CLI at the workspace root and polls until it answers", async () => {
  const spawnRoots: string[] = [];
  let probes = 0;
  const manager = new ServerManager({
    probe: async () => {
      probes += 1;
      return probes >= 3 ? { index: "indexed" } : null;
    },
    spawnServer: (root) => {
      spawnRoots.push(root);
      return makeChild();
    },
    pollIntervalMs: 5,
  });
  const phases: string[] = [];
  manager.onStateChange((states) => phases.push(states[0]?.phase ?? "none"));
  const state = await manager.ensure("/ws/b");
  assert.equal(state.phase, "served");
  assert.deepEqual(spawnRoots, ["/ws/b"]);
  assert.ok(phases.includes("connecting"), "status feed must surface the connecting state");
  assert.equal(phases[phases.length - 1], "served");
  manager.dispose();
});

test("spawns the loopback dashboard on the frozen editor port", () => {
  assert.deepEqual(dashboardCommand(), [
    "cairn",
    "dashboard",
    "--host",
    SERVER_HOST,
    "--port",
    String(SERVER_PORT),
  ]);
  assert.equal(SERVER_HOST, "127.0.0.1");
  assert.equal(SERVER_PORT, 8765);
});

test("reports offline without throwing when the server never answers", async () => {
  const manager = new ServerManager({
    probe: async () => null,
    spawnServer: () => makeChild(),
    pollIntervalMs: 5,
    startTimeoutMs: 40,
  });
  const state = await manager.ensure("/ws/c");
  assert.equal(state.phase, "offline");
  assert.ok(state.detail);
  manager.dispose();
});

test("reports offline with a reason when the CLI is missing", async () => {
  const child = makeChild();
  const manager = new ServerManager({
    probe: async () => null,
    spawnServer: () => child,
    pollIntervalMs: 5,
    startTimeoutMs: 5_000,
  });
  const pending = manager.ensure("/ws/d");
  await tick();
  child.emit("error", Object.assign(new Error("spawn cairn ENOENT"), { code: "ENOENT" }));
  const state = await pending;
  assert.equal(state.phase, "offline");
  assert.match(state.detail ?? "", /ENOENT/);
  manager.dispose();
});

test("reports offline when the spawned server exits before answering", async () => {
  const child = makeChild();
  const manager = new ServerManager({
    probe: async () => null,
    spawnServer: () => child,
    pollIntervalMs: 5,
    startTimeoutMs: 5_000,
  });
  const pending = manager.ensure("/ws/e");
  await tick();
  child.emit("exit", 1, null);
  const state = await pending;
  assert.equal(state.phase, "offline");
  assert.match(state.detail ?? "", /code 1/);
  manager.dispose();
});

test("flips to offline when the spawned server dies after being served", async () => {
  let probes = 0;
  const child = makeChild();
  const manager = new ServerManager({
    probe: async () => {
      probes += 1;
      return probes >= 2 ? { index: "indexed" } : null;
    },
    spawnServer: () => child,
    pollIntervalMs: 5,
    startTimeoutMs: 5_000,
  });
  const served = await manager.ensure("/ws/f");
  assert.equal(served.phase, "served");
  child.emit("exit", 0, null);
  const state = manager.states()[0];
  assert.equal(state.phase, "offline");
  assert.match(state.detail ?? "", /code 0/);
  manager.dispose();
});

test("disposes mid-flight attempts and never kills an adopted server", async () => {
  const adopted = new ServerManager({ probe: async () => ({ index: "indexed" }) });
  await adopted.ensure("/ws/g");
  adopted.dispose();
  assert.equal(adopted.states().length, 0);

  const child = makeChild();
  const manager = new ServerManager({
    probe: async () => null,
    spawnServer: () => child,
    pollIntervalMs: 5,
    startTimeoutMs: 5_000,
  });
  const pending = manager.ensure("/ws/h");
  await tick();
  manager.dispose();
  const state = await pending;
  assert.equal(state.phase, "connecting");
  assert.equal(child.killed, true, "only the spawned child is killed on dispose");
});

test("parses the frozen status payload tolerantly", () => {
  assert.equal(parseIndexState({ indexed: true }), "indexed");
  assert.equal(parseIndexState({ indexed: false }), "unindexed");
  assert.equal(parseIndexState({ indexed: "yes" }), "unknown");
  assert.equal(parseIndexState({}), "unknown");
  assert.equal(parseIndexState("not json"), "unknown");
  assert.equal(parseIndexState(null), "unknown");
});

test("degrades to offline when an adopted server stops answering", async () => {
  let answering = true;
  const manager = new ServerManager({
    probe: async () => (answering ? { index: "indexed" } : null),
    watchIntervalMs: 5,
  });
  const served = await manager.ensure("/ws/i");
  assert.equal(served.phase, "served");
  const offline = new Promise<void>((resolve) =>
    manager.onStateChange((states) => {
      if (states[0]?.phase === "offline") resolve();
    }),
  );
  answering = false;
  await offline;
  const state = manager.states()[0];
  assert.equal(state.phase, "offline");
  assert.ok(state.detail);
  manager.dispose();
});

test("the served watch refreshes index state when the workspace gets indexed", async () => {
  let indexed = false;
  const manager = new ServerManager({
    probe: async () => ({ index: indexed ? "indexed" : "unindexed" }),
    watchIntervalMs: 5,
  });
  const served = await manager.ensure("/ws/j");
  assert.equal(served.phase, "served");
  assert.equal(served.index, "unindexed");
  const refreshed = new Promise<void>((resolve) =>
    manager.onStateChange((states) => {
      if (states[0]?.phase === "served" && states[0]?.index === "indexed") resolve();
    }),
  );
  indexed = true;
  await refreshed;
  assert.equal(manager.states()[0]?.index, "indexed");
  manager.dispose();
});
