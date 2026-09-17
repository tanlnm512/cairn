/**
 * Per-workspace lifecycle for the local cairn server process.
 *
 * Attach-or-start: health-check the loopback editor endpoints first and adopt
 * any answering server; spawn `cairn dashboard` at the workspace root only
 * when none answers. Adopted servers are never killed; spawned ones are, so
 * the process lifetime follows the editor session. Served bindings keep a
 * low-frequency health watch so a server that stops answering degrades to
 * offline. No daemon registration, no MCP configuration.
 */
import { spawn as nodeSpawn, type ChildProcess } from "node:child_process";
import { request as httpRequest } from "node:http";

export const SERVER_HOST = "127.0.0.1";
export const SERVER_PORT = 8765;

/** Frozen editor JSON contract (D-004); status is the shell's health-check target. */
export const EDITOR_ENDPOINTS = {
  status: "/editor/status",
  symbol: "/editor/symbol",
  file: "/editor/file",
} as const;

const PROBE_TIMEOUT_MS = 1_500;
const DEFAULT_POLL_INTERVAL_MS = 500;
const DEFAULT_START_TIMEOUT_MS = 15_000;
const DEFAULT_WATCH_INTERVAL_MS = 5_000;

export type IndexState = "indexed" | "unindexed" | "unknown";
export type ServerPhase = "connecting" | "served" | "offline";

export interface ServerState {
  root: string;
  phase: ServerPhase;
  index: IndexState;
  detail?: string;
}

/** null = nothing answered on the port. */
export type ProbeResult = { index: IndexState } | null;
export type ProbeFn = (root: string) => Promise<ProbeResult>;

export interface SpawnedServer {
  once(event: "error", listener: (error: Error) => void): unknown;
  once(event: "exit", listener: (code: number | null, signal: NodeJS.Signals | null) => void): unknown;
  kill(signal?: NodeJS.Signals): boolean;
}
export type SpawnFn = (root: string) => SpawnedServer;

export function dashboardCommand(): string[] {
  return ["cairn", "dashboard", "--host", SERVER_HOST, "--port", String(SERVER_PORT)];
}

export function defaultSpawnServer(root: string): ChildProcess {
  const [command, ...args] = dashboardCommand();
  return nodeSpawn(command, args, { cwd: root, stdio: "ignore" });
}

export function parseIndexState(payload: unknown): IndexState {
  if (payload !== null && typeof payload === "object" && "indexed" in payload) {
    const indexed = (payload as { indexed: unknown }).indexed;
    if (indexed === true) return "indexed";
    if (indexed === false) return "unindexed";
  }
  return "unknown";
}

/**
 * One GET against the local server: the parsed JSON body, or null on
 * timeout, transport failure, or a non-JSON body.
 */
export function getJson(host: string, port: number, path: string, timeoutMs: number): Promise<unknown | null> {
  return new Promise((resolve) => {
    const request = httpRequest({ host, port, path, timeout: timeoutMs }, (response) => {
      const chunks: Buffer[] = [];
      response.on("data", (chunk: Buffer) => chunks.push(chunk));
      response.on("end", () => {
        try {
          resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
        } catch {
          resolve(null);
        }
      });
      response.on("error", () => resolve(null));
    });
    request.on("timeout", () => {
      request.destroy();
      resolve(null);
    });
    request.on("error", () => resolve(null));
    request.end();
  });
}

function defaultProbe(): Promise<ProbeResult> {
  return new Promise((resolve) => {
    const request = httpRequest(
      { host: SERVER_HOST, port: SERVER_PORT, path: EDITOR_ENDPOINTS.status, timeout: PROBE_TIMEOUT_MS },
      (response) => {
        const chunks: Buffer[] = [];
        response.on("data", (chunk: Buffer) => chunks.push(chunk));
        response.on("end", () => {
          let index: IndexState = "unknown";
          try {
            index = parseIndexState(JSON.parse(Buffer.concat(chunks).toString("utf8")));
          } catch {
            index = "unknown";
          }
          resolve({ index });
        });
        response.on("error", () => resolve(null));
      },
    );
    request.on("timeout", () => {
      request.destroy();
      resolve(null);
    });
    request.on("error", () => resolve(null));
    request.end();
  });
}

interface Binding {
  root: string;
  child: SpawnedServer | null;
  state: ServerState;
  settled: boolean;
  pollTimer: NodeJS.Timeout | null;
  deadlineTimer: NodeJS.Timeout | null;
  watchTimer: NodeJS.Timeout | null;
  onSettled: (() => void) | null;
}

export interface ServerManagerOptions {
  probe?: ProbeFn;
  spawnServer?: SpawnFn;
  pollIntervalMs?: number;
  startTimeoutMs?: number;
  watchIntervalMs?: number;
}

export class ServerManager {
  private readonly probe: ProbeFn;
  private readonly spawnServer: SpawnFn;
  private readonly pollIntervalMs: number;
  private readonly startTimeoutMs: number;
  private readonly watchIntervalMs: number;
  private readonly bindings = new Map<string, Binding>();
  private changeListener: ((states: ServerState[]) => void) | null = null;

  constructor(options: ServerManagerOptions = {}) {
    this.probe = options.probe ?? ((): Promise<ProbeResult> => defaultProbe());
    this.spawnServer = options.spawnServer ?? defaultSpawnServer;
    this.pollIntervalMs = options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS;
    this.startTimeoutMs = options.startTimeoutMs ?? DEFAULT_START_TIMEOUT_MS;
    this.watchIntervalMs = options.watchIntervalMs ?? DEFAULT_WATCH_INTERVAL_MS;
  }

  onStateChange(listener: (states: ServerState[]) => void): void {
    this.changeListener = listener;
    listener(this.states());
  }

  states(): ServerState[] {
    return [...this.bindings.values()].map((binding) => binding.state);
  }

  /**
   * Attach to a running server for the root, or start one; resolves when the
   * attempt settles. A served or in-flight binding for the root is reused.
   */
  async ensure(root: string): Promise<ServerState> {
    const existing = this.bindings.get(root);
    if (existing && existing.state.phase !== "offline") return existing.state;
    if (existing) this.drop(existing);
    const binding: Binding = {
      root,
      child: null,
      state: { root, phase: "connecting", index: "unknown" },
      settled: false,
      pollTimer: null,
      deadlineTimer: null,
      watchTimer: null,
      onSettled: null,
    };
    this.bindings.set(root, binding);
    this.emit();

    const probed = await this.probe(root);
    if (this.bindings.get(root) !== binding) return binding.state;
    if (probed) {
      this.settle(binding, { root, phase: "served", index: probed.index });
      return binding.state;
    }
    return this.startServer(binding);
  }

  remove(root: string): void {
    const binding = this.bindings.get(root);
    if (binding) this.drop(binding);
  }

  dispose(): void {
    for (const binding of [...this.bindings.values()]) this.drop(binding);
    this.emit();
  }

  private startServer(binding: Binding): Promise<ServerState> {
    const { root } = binding;
    if (binding.settled) return Promise.resolve(binding.state);
    return new Promise<ServerState>((resolve) => {
      binding.onSettled = () => resolve(binding.state);
      let child: SpawnedServer;
      try {
        child = this.spawnServer(root);
      } catch (error) {
        this.settle(binding, {
          root,
          phase: "offline",
          index: "unknown",
          detail: `failed to launch the cairn CLI (${error instanceof Error ? error.message : "unknown error"})`,
        });
        return;
      }
      binding.child = child;

      child.once("error", (error) => {
        this.settle(binding, {
          root,
          phase: "offline",
          index: "unknown",
          detail: `cairn CLI failed to start (${error.message})`,
        });
      });
      child.once("exit", (code, signal) => {
        if (this.bindings.get(root) !== binding || binding.state.phase === "offline") return;
        const detail = code !== null ? `server exited with code ${code}` : `server exited (${signal})`;
        this.finish(binding, { root, phase: "offline", index: "unknown", detail });
      });

      binding.pollTimer = setInterval(() => {
        void this.probe(root).then((probed) => {
          if (this.bindings.get(root) !== binding || binding.settled) return;
          if (probed) this.settle(binding, { root, phase: "served", index: probed.index });
        });
      }, this.pollIntervalMs);

      binding.deadlineTimer = setTimeout(() => {
        this.settle(binding, {
          root,
          phase: "offline",
          index: "unknown",
          detail: "server did not answer in time",
        });
      }, this.startTimeoutMs);
    });
  }

  private settle(binding: Binding, state: ServerState): boolean {
    if (binding.settled) return false;
    this.finish(binding, state);
    return true;
  }

  private finish(binding: Binding, state: ServerState): void {
    binding.settled = true;
    this.clearTimers(binding);
    binding.state = state;
    if (state.phase === "served") this.armWatch(binding);
    const notify = binding.onSettled;
    binding.onSettled = null;
    this.emit();
    notify?.();
  }

  /** While served, keep probing the health endpoint so a stopped server degrades the state (FR-004). */
  private armWatch(binding: Binding): void {
    binding.watchTimer = setInterval(() => {
      void this.probe(binding.root).then((probed) => {
        if (this.bindings.get(binding.root) !== binding || binding.state.phase !== "served") return;
        if (!probed) {
          this.finish(binding, {
            root: binding.root,
            phase: "offline",
            index: "unknown",
            detail: "server stopped answering",
          });
          return;
        }
        if (probed.index !== binding.state.index) {
          binding.state = { ...binding.state, index: probed.index };
          this.emit();
        }
      });
    }, this.watchIntervalMs);
  }

  private drop(binding: Binding): void {
    binding.settled = true;
    this.clearTimers(binding);
    binding.child?.kill("SIGTERM");
    if (this.bindings.get(binding.root) === binding) this.bindings.delete(binding.root);
    const notify = binding.onSettled;
    binding.onSettled = null;
    this.emit();
    notify?.();
  }

  private clearTimers(binding: Binding): void {
    if (binding.pollTimer) clearInterval(binding.pollTimer);
    if (binding.deadlineTimer) clearTimeout(binding.deadlineTimer);
    if (binding.watchTimer) clearInterval(binding.watchTimer);
    binding.pollTimer = null;
    binding.deadlineTimer = null;
    binding.watchTimer = null;
  }

  private emit(): void {
    this.changeListener?.(this.states());
  }
}
