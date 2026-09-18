/**
 * Code-lens provider (FR-001): caller/callee counts above symbol
 * declarations, read from the same frozen /editor/symbol response the hover
 * consumes — one endpoint, no second contract. Degraded shells (the one
 * aggregate isDegraded verdict) issue no request and render no lens
 * (FR-004); unknown symbols render no lens, never an error surface. No
 * runtime editor-API dependency — the platform API is injected, so the lens
 * logic runs under plain Node.
 */
import type {
  CodeLens,
  CodeLensProvider,
  DocumentSymbol,
  ExtensionContext,
  TextDocument,
} from "vscode";
import type { ServerState } from "../server";
import { isDegraded } from "../statusModel";
import { defaultFetchSymbol, parseSymbolPayload, type SymbolPayload } from "./hover";

/** Lenses show counts only; the smallest depth still bounds the radius server-side. */
export const LENS_REQUEST_DEPTH = 1;

/** One rendered lens: the declaration line and its inline title. */
export interface LensSpec {
  line: number;
  title: string;
}

/** A symbol declaration in the open document. */
export interface SymbolRef {
  name: string;
  line: number;
}

/** Compact inline counts; an explicit zero state for isolated symbols. */
export function countsTitle(callers: number, callees: number): string {
  if (callers === 0 && callees === 0) return "no callers · no callees";
  const noun = (count: number, word: string): string => `${count} ${word}${count === 1 ? "" : "s"}`;
  return `${noun(callers, "caller")} · ${noun(callees, "callee")}`;
}

/** The lens for one symbol response; unknown or unusable symbols get none. */
export function lensSpec(line: number, payload: unknown): LensSpec | null {
  const parsed: SymbolPayload | null = parseSymbolPayload(payload);
  if (!parsed || !parsed.found) return null;
  return { line, title: countsTitle(parsed.callers.count, parsed.callees.count) };
}

export type SymbolLookup = (name: string) => Promise<unknown>;

export function defaultLookup(name: string): Promise<unknown> {
  return defaultFetchSymbol(name, LENS_REQUEST_DEPTH);
}

/**
 * Lenses for the document's declarations: one lookup per distinct non-blank
 * name, unknown and failing symbols skipped, output ordered by line.
 */
export async function lensSpecsFor(refs: SymbolRef[], lookup: SymbolLookup): Promise<LensSpec[]> {
  const names = [...new Set(refs.map((ref) => ref.name).filter((name) => name.length > 0))];
  const payloads = new Map<string, unknown>();
  await Promise.all(
    names.map(async (name) => {
      try {
        payloads.set(name, await lookup(name));
      } catch {
        payloads.set(name, null);
      }
    }),
  );
  return refs
    .map((ref) => lensSpec(ref.line, payloads.get(ref.name)))
    .filter((spec): spec is LensSpec => spec !== null)
    .sort((a, b) => a.line - b.line);
}

type VscodeApi = typeof import("vscode");

export interface CodeLensWiring {
  context: ExtensionContext;
  vscodeApi: VscodeApi;
  /** Live shell states; the one aggregate degraded verdict gates every request (FR-004). */
  states: () => ServerState[];
  lookup?: SymbolLookup;
}

const DEGRADED_POLL_MS = 2_000;

async function documentSymbolRefs(vscodeApi: VscodeApi, document: TextDocument): Promise<SymbolRef[]> {
  const symbols = (await vscodeApi.commands.executeCommand(
    "vscode.executeDocumentSymbolProvider",
    document.uri,
  )) as DocumentSymbol[] | undefined;
  const refs: SymbolRef[] = [];
  const walk = (list: DocumentSymbol[]): void => {
    for (const symbol of list) {
      const name = symbol.name.trim();
      if (name) refs.push({ name, line: symbol.selectionRange.start.line });
      walk(symbol.children);
    }
  };
  walk(symbols ?? []);
  return refs;
}

/** Registers the lens provider on local files; hidden while degraded. */
export function registerCodeLenses(wiring: CodeLensWiring): void {
  const { context, vscodeApi, states } = wiring;
  const lookup = wiring.lookup ?? defaultLookup;

  const changed = new vscodeApi.EventEmitter<void>();
  const provider: CodeLensProvider = {
    onDidChangeCodeLenses: changed.event,
    async provideCodeLenses(document: TextDocument): Promise<CodeLens[]> {
      try {
        if (isDegraded(states())) return [];
        const refs = await documentSymbolRefs(vscodeApi, document);
        const specs = await lensSpecsFor(refs, lookup);
        return specs.map((spec) => new vscodeApi.CodeLens(new vscodeApi.Range(spec.line, 0, spec.line, 0)));
      } catch {
        return [];
      }
    },
  };

  // Lens refresh fires on edits; the verdict poll covers degradation flips between edits.
  let degraded = isDegraded(states());
  const poll = setInterval(() => {
    const now = isDegraded(states());
    if (now !== degraded) {
      degraded = now;
      changed.fire();
    }
  }, DEGRADED_POLL_MS);

  context.subscriptions.push(
    vscodeApi.languages.registerCodeLensProvider([{ scheme: "file" }], provider),
    new vscodeApi.Disposable(() => clearInterval(poll)),
    changed,
  );
}
