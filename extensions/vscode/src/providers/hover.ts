/**
 * Hover provider (FR-001): one /editor/symbol round-trip per hover renders
 * caller/callee counts and the depth-limited precise blast radius. The
 * radius depth is bounded in the request, never client-side. Degraded
 * shells (the one aggregate isDegraded verdict) issue no request and render
 * nothing (FR-004); unknown symbols render nothing, never an error surface.
 * No runtime editor-API dependency — the platform hover is injected, so the
 * logic runs under plain Node.
 */
import type {
  CancellationToken,
  Hover,
  HoverProvider,
  Position,
  TextDocument,
} from "vscode";
import { EDITOR_ENDPOINTS, SERVER_HOST, SERVER_PORT, getJson, type ServerState } from "../server";
import { isDegraded } from "../statusModel";

/** Requested blast-radius hops; the server clamps into [1, 10]. */
export const DEFAULT_HOVER_DEPTH = 2;
const SYMBOL_TIMEOUT_MS = 1_500;

/** Frozen /editor/symbol response shape (D-004 contract); keys are wire-exact. */
export interface BlastRadiusEntry {
  symbol: string;
  file: string;
  repo: string;
  depth: number;
}

export interface BlastRadius {
  depth: number;
  total: number;
  truncated: boolean;
  symbols: BlastRadiusEntry[];
}

export interface SymbolPayload {
  found: boolean;
  symbol: string;
  kind: string;
  qualified_name: string;
  file: string;
  callers: { count: number };
  callees: { count: number };
  blast_radius: BlastRadius;
}

export type SymbolFetcher = (name: string, depth: number) => Promise<SymbolPayload | null>;

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asCount(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

/** Tolerant parse of the symbol response; null when the shape is not the frozen contract. */
export function parseSymbolPayload(raw: unknown): SymbolPayload | null {
  if (typeof raw !== "object" || raw === null) return null;
  const body = raw as Record<string, unknown>;
  const callers = body.callers as Record<string, unknown> | undefined;
  const callees = body.callees as Record<string, unknown> | undefined;
  const radius = body.blast_radius as Record<string, unknown> | undefined;
  if (typeof body.found !== "boolean" || typeof body.symbol !== "string") return null;
  const callerCount = asCount(callers?.count);
  const calleeCount = asCount(callees?.count);
  const depth = asCount(radius?.depth);
  const total = asCount(radius?.total);
  if (callerCount === null || calleeCount === null || depth === null || total === null) return null;
  if (typeof radius?.truncated !== "boolean" || !Array.isArray(radius.symbols)) return null;
  const symbols = (radius.symbols as unknown[]).map((entry) => {
    const item = (entry ?? {}) as Record<string, unknown>;
    return {
      symbol: asString(item.symbol),
      file: asString(item.file),
      repo: asString(item.repo),
      depth: typeof item.depth === "number" ? item.depth : 0,
    };
  });
  return {
    found: body.found,
    symbol: body.symbol,
    kind: asString(body.kind),
    qualified_name: asString(body.qualified_name),
    file: asString(body.file),
    callers: { count: callerCount },
    callees: { count: calleeCount },
    blast_radius: { depth, total, truncated: radius.truncated, symbols },
  };
}

/** One symbol-endpoint round-trip against a specific host:port; null on any failure. */
export function fetchSymbolAt(
  host: string,
  port: number,
  name: string,
  depth: number,
): Promise<SymbolPayload | null> {
  const path = `${EDITOR_ENDPOINTS.symbol}?name=${encodeURIComponent(name)}&depth=${depth}`;
  return getJson(host, port, path, SYMBOL_TIMEOUT_MS).then((raw) =>
    raw === null ? null : parseSymbolPayload(raw),
  );
}

export function defaultFetchSymbol(name: string, depth: number): Promise<SymbolPayload | null> {
  return fetchSymbolAt(SERVER_HOST, SERVER_PORT, name, depth);
}

/** Markdown hover for a found symbol; null for an unknown or non-conforming payload (renders nothing). */
export function renderSymbolHover(payload: SymbolPayload): string | null {
  if (
    !payload?.found ||
    typeof payload.callers?.count !== "number" ||
    typeof payload.callees?.count !== "number" ||
    !payload.blast_radius
  ) {
    return null;
  }
  const lines: string[] = [];
  lines.push(payload.kind ? `\`${payload.symbol}\` — ${payload.kind}` : `\`${payload.symbol}\``);
  if (payload.file) lines.push(`\`${payload.file}\``);
  lines.push("", `Callers: **${payload.callers.count}** · Callees: **${payload.callees.count}**`);
  const radius = payload.blast_radius;
  const hops = radius.depth === 1 ? "hop" : "hops";
  const plural = radius.total === 1 ? "" : "s";
  lines.push("", `**Blast radius** — ${radius.total} symbol${plural} within ${radius.depth} ${hops}`);
  for (const entry of radius.symbols) {
    lines.push(`- \`${entry.symbol}\` — ${entry.file} · distance ${entry.depth}`);
  }
  if (radius.truncated) lines.push("", "_radius truncated at the depth limit_");
  return lines.join("\n");
}

/** The word under the cursor, or "" when the position is not on one. */
export function symbolAt(document: TextDocument, position: Position): string {
  const range = document.getWordRangeAtPosition(position);
  return range ? document.getText(range) : "";
}

export interface HoverProviderOptions {
  /** Live shell states; the one aggregate isDegraded verdict gates every request (FR-004). */
  states: () => ServerState[];
  /** Builds the platform hover from rendered markdown. */
  hover: (markdown: string) => Hover;
  fetchSymbol?: SymbolFetcher;
  depth?: number;
}

export class SymbolHoverProvider implements HoverProvider {
  private readonly states: () => ServerState[];
  private readonly hover: (markdown: string) => Hover;
  private readonly fetchSymbol: SymbolFetcher;
  private readonly depth: number;

  constructor(options: HoverProviderOptions) {
    this.states = options.states;
    this.hover = options.hover;
    this.fetchSymbol = options.fetchSymbol ?? defaultFetchSymbol;
    this.depth = options.depth ?? DEFAULT_HOVER_DEPTH;
  }

  async provideHover(
    document: TextDocument,
    position: Position,
    token: CancellationToken,
  ): Promise<Hover | undefined> {
    if (isDegraded(this.states())) return undefined;
    if (token.isCancellationRequested) return undefined;
    const name = symbolAt(document, position);
    if (!name) return undefined;
    let payload: SymbolPayload | null;
    try {
      payload = await this.fetchSymbol(name, this.depth);
    } catch {
      payload = null;
    }
    if (token.isCancellationRequested) return undefined;
    const markdown = payload === null ? null : renderSymbolHover(payload);
    return markdown === null ? undefined : this.hover(markdown);
  }
}
