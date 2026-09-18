/**
 * Pure compass/memory panel model (FR-002): file-endpoint payload parsing,
 * panel view mapping, HTML rendering, and the latest-wins request runner.
 * No editor API here so the mapping is testable under plain Node.
 */
import { EDITOR_ENDPOINTS, SERVER_HOST, SERVER_PORT, getJson, type ServerState } from "../server";
import { aggregateDisplay, isDegraded } from "../statusModel";

export interface CompassExcerpt {
  found: boolean;
  title: string;
  body: string;
}

export interface MemoryItem {
  id: string;
  type: string;
  title: string;
  body: string;
}

/** The frozen /editor/file response shape (D-004); memories arrive newest-first. */
export interface FilePanelPayload {
  path: string;
  module: string;
  compass: CompassExcerpt;
  memories: MemoryItem[];
}

export type PanelView =
  | { kind: "degraded"; note: string }
  | { kind: "empty"; path: string }
  | { kind: "content"; payload: FilePanelPayload };

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function parseCompass(value: unknown): CompassExcerpt {
  if (value === null || typeof value !== "object") {
    return { found: false, title: "", body: "" };
  }
  const record = value as Record<string, unknown>;
  return {
    found: record.found === true,
    title: asText(record.title),
    body: asText(record.body),
  };
}

function parseMemories(value: unknown): MemoryItem[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is Record<string, unknown> => item !== null && typeof item === "object")
    .map((item) => ({
      id: asText(item.id),
      type: asText(item.type),
      title: asText(item.title),
      body: asText(item.body),
    }));
}

/** Normalizes any response body into the contract shape; never throws. */
export function parseFilePayload(payload: unknown): FilePanelPayload {
  if (payload === null || typeof payload !== "object") {
    return { path: "", module: "", compass: { found: false, title: "", body: "" }, memories: [] };
  }
  const record = payload as Record<string, unknown>;
  return {
    path: asText(record.path),
    module: asText(record.module),
    compass: parseCompass(record.compass),
    memories: parseMemories(record.memories),
  };
}

export function hasContent(payload: FilePanelPayload): boolean {
  return payload.compass.found || payload.memories.length > 0;
}

/** The aggregate status note while inline data is withheld (FR-004), else null. */
export function degradedNote(states: ServerState[]): string | null {
  return isDegraded(states) ? aggregateDisplay(states).tooltip : null;
}

export function panelView(payload: FilePanelPayload | null, note: string | null): PanelView {
  if (note !== null) return { kind: "degraded", note };
  if (payload === null || !hasContent(payload)) {
    return { kind: "empty", path: payload === null ? "" : payload.path };
  }
  return { kind: "content", payload };
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const PANEL_CSS = `
  body { font-family: var(--vscode-font-family); color: var(--vscode-editor-foreground); padding: 12px; }
  h1 { font-size: 1.1em; margin: 0 0 8px; }
  h2 { font-size: 1em; margin: 16px 0 4px; }
  pre { white-space: pre-wrap; word-break: break-word; font-family: var(--vscode-editor-font-family); margin: 4px 0; }
  ul { padding-left: 20px; margin: 4px 0; }
  li { margin: 8px 0; }
  .note { color: var(--vscode-descriptionForeground); }
  .type { color: var(--vscode-descriptionForeground); }
`;

function renderBody(view: PanelView): string {
  switch (view.kind) {
    case "degraded":
      return `<h1>cairn</h1><p class="note">${escapeHtml(view.note)}</p>`;
    case "empty": {
      const target = view.path ? ` for <code>${escapeHtml(view.path)}</code>` : "";
      return `<h1>cairn</h1><p class="note">No compass or memory content${target}.</p>`;
    }
    case "content": {
      const compassSection = view.payload.compass.found
        ? `<h2>Compass — ${escapeHtml(view.payload.compass.title)}</h2>` +
          `<pre>${escapeHtml(view.payload.compass.body)}</pre>`
        : "";
      const memorySection = view.payload.memories.length
        ? `<h2>Memories</h2><ul>${view.payload.memories
            .map((memory) => {
              const type = memory.type ? ` <span class="type">[${escapeHtml(memory.type)}]</span>` : "";
              const body = memory.body ? `<pre>${escapeHtml(memory.body)}</pre>` : "";
              return `<li><strong>${escapeHtml(memory.title)}</strong>${type}${body}</li>`;
            })
            .join("")}</ul>`
        : "";
      return `<h1>cairn</h1>${compassSection}${memorySection}`;
    }
  }
}

export function renderPanelHtml(view: PanelView): string {
  return [
    "<!DOCTYPE html>",
    '<html><head><meta charset="utf-8">',
    "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline';\">",
    `<style>${PANEL_CSS}</style></head><body>`,
    renderBody(view),
    "</body></html>",
  ].join("");
}

export interface LatestWinsRunner {
  request(path: string): void;
}

/**
 * One in-flight request; a request arriving mid-flight supersedes the one
 * still awaited (latest wins). Failed fetches deliver nothing and leave
 * the runner ready.
 */
export function createLatestWinsRunner<R>(
  fetcher: (path: string) => Promise<R>,
  deliver: (path: string, result: R) => void,
): LatestWinsRunner {
  let pending: string | null = null;
  let running = false;
  const pump = async (): Promise<void> => {
    while (pending !== null) {
      const target = pending;
      pending = null;
      try {
        const result = await fetcher(target);
        if (pending === null) deliver(target, result);
      } catch {
        // degradation is the status layer's verdict; a failed fetch only skips its render
      }
    }
    running = false;
  };
  return {
    request(path: string): void {
      pending = path;
      if (running) return;
      running = true;
      void pump();
    },
  };
}

const FETCH_TIMEOUT_MS = 5_000;

/** Query for the frozen file endpoint; the path is workspace-relative POSIX. */
export function fileRequestPath(path: string): string {
  return `${EDITOR_ENDPOINTS.file}?path=${encodeURIComponent(path)}`;
}

/** One GET against the local server's frozen file endpoint; rejects on transport or parse failure. */
export function defaultFileFetcher(path: string): Promise<FilePanelPayload> {
  return getJson(SERVER_HOST, SERVER_PORT, fileRequestPath(path), FETCH_TIMEOUT_MS).then((raw) => {
    if (raw === null) throw new Error("unparsable file-endpoint response");
    return parseFilePayload(raw);
  });
}
