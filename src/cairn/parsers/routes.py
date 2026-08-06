"""Framework-aware route detection (URL -> code).

Runs as a post-parse pass over an already-parsed ``ParsedFile`` (TypeScript/
JavaScript only). It produces:

  - route ``Symbol``s (``kind='route'``, ``metadata={'http_method', 'path',
    'framework', 'handler', 'provenance'}``)
  - ``references`` ``Edge``s from the route's name to its handler function/
    class/method name

The builder merges these into the ``ParsedFile``'s own ``symbols``/``edges``
before the normal insert + resolver passes run. A route symbol is inserted
into the SAME file as its handler, so the resolver's same-file tier resolves
the route -> handler ``references`` edge exactly.

Implemented detectors (scoped to this workspace's actual stack):

  - **NestJS**: ``@Controller(prefix)`` class + ``@Get/@Post/@Put/@Delete/
    @Patch/@Options/@Head/@All(path)`` method decorators (captured as
    modifiers by ``src/parsers/typescript.py``). Structured, exact --
    ``provenance='exact'``.
  - **React Router**: JSX ``<Route path="..." element={<X/>}>`` and the
    ``createBrowserRouter([{ path, element/Component }])`` config-array form.
    Neither JSX nor object-literal route configs are represented in the
    generic Symbol/Edge model the base parser produces, so this detector
    does its own light regex pass over the file's raw source rather than a
    second AST parse. Regex means it can miss reformatted/multi-line
    variants -- tagged ``provenance='heuristic'``.
  - **Next.js file-based routing**: route path inferred purely from the
    file's location under a ``pages/`` or ``app/`` directory (App Router
    requires the file be literally named ``page.*``). The handler is the
    file's default export (inferred from raw source via ``export default``),
    falling back to the first exported function/class, then the first
    function/class -- tagged ``provenance='heuristic'``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .base import Edge, ParsedFile, Symbol

HTTP_DECORATOR_METHODS = {"Get", "Post", "Put", "Delete", "Patch", "Options", "Head", "All"}
PAGE_FILE_STEMS = {"page", "index"}
# Next.js special files under app/ that are NOT routes themselves.
APP_ROUTER_NON_ROUTE_STEMS = {
    "layout", "loading", "error", "not-found", "template", "default",
    "route",  # route.ts (API handler) is handled as its own case below
}


@dataclass
class RouteExtraction:
    routes: List[Symbol] = field(default_factory=list)
    references: List[Edge] = field(default_factory=list)

    def extend(self, other: "RouteExtraction") -> None:
        self.routes.extend(other.routes)
        self.references.extend(other.references)


def candidate_frameworks(path: str, language: str) -> List[str]:
    """Cheap name/language gate: which detectors are even worth trying.

    Avoids running every detector against every file -- e.g. a plain utility
    module with no decorators and not under pages/app gets nothing to do.
    """
    if language not in ("typescript", "javascript"):
        return []
    frameworks = ["nestjs", "react_router"]
    parts_lower = {p.lower() for p in Path(path).parts}
    if "pages" in parts_lower or "app" in parts_lower:
        frameworks.append("nextjs")
    return frameworks


def detect_routes(pf: ParsedFile, language: str) -> Optional[RouteExtraction]:
    """Dispatch to every candidate framework detector for this file and merge
    their results. Returns None if nothing was found (the common case)."""
    frameworks = candidate_frameworks(pf.path, language)
    if not frameworks:
        return None
    extraction = RouteExtraction()
    for fw in frameworks:
        detector = _DETECTORS.get(fw)
        if detector is None:
            continue
        result = detector(pf)
        if result:
            extraction.extend(result)
    return extraction if extraction.routes else None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _route_qualified_name(pf: ParsedFile, discriminator: str) -> str:
    stem = Path(pf.path).stem
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", discriminator).strip("_")
    return f"{stem}.route.{safe}"


def _join_path(*parts: str) -> str:
    """Join route path segments, collapsing slashes, always leading '/'."""
    joined = "/".join(p.strip("/") for p in parts if p and p.strip("/"))
    return "/" + joined if joined else "/"


# ---------------------------------------------------------------------------
# NestJS: @Controller(prefix) class + @Get/@Post/...(path) methods
# ---------------------------------------------------------------------------

_CONTROLLER_RE = re.compile(r"@Controller\(\s*(?:['\"]([^'\"]*)['\"])?\s*\)")
_HTTP_METHOD_RE = re.compile(
    r"@(" + "|".join(HTTP_DECORATOR_METHODS) + r")\(\s*(?:['\"]([^'\"]*)['\"])?\s*\)"
)


def _detect_nestjs(pf: ParsedFile) -> Optional[RouteExtraction]:
    extraction = RouteExtraction()
    controllers = [s for s in pf.symbols if s.kind == "class"]
    for controller in controllers:
        prefix = None
        for mod in controller.modifiers:
            m = _CONTROLLER_RE.search(mod)
            if m:
                prefix = m.group(1) or ""
                break
        if prefix is None:
            continue  # not a @Controller class

        # Methods that belong to this controller: qualified_name is
        # "<file_stem>.<ControllerName>.<method>" (file-stem-prefixed FQN).
        owned_prefix = f"{controller.qualified_name}."
        for method in pf.symbols:
            if method.kind != "method":
                continue
            if not (method.qualified_name or "").startswith(owned_prefix):
                continue
            for mod in method.modifiers:
                m = _HTTP_METHOD_RE.search(mod)
                if not m:
                    continue
                http_method = m.group(1).upper()
                route_path = _join_path(prefix, m.group(2) or "")
                route_name = f"{http_method} {route_path}"
                extraction.routes.append(
                    Symbol(
                        name=route_name,
                        kind="route",
                        qualified_name=_route_qualified_name(pf, route_name),
                        line_start=method.line_start,
                        line_end=method.line_end,
                        metadata={
                            "http_method": http_method,
                            "path": route_path,
                            "framework": "nestjs",
                            "handler": method.name,
                            "provenance": "exact",
                        },
                    )
                )
                extraction.references.append(
                    Edge(route_name, "references", method.name, method.line_start)
                )
                break  # one HTTP-method decorator per method is the norm
    return extraction if extraction.routes else None


# ---------------------------------------------------------------------------
# React Router: JSX <Route> and createBrowserRouter([...]) config objects.
#
# Regex-based (not a second AST parse) because JSX elements and object-literal
# route configs aren't part of the generic Symbol/Edge model -- see module
# docstring. Always tagged provenance='heuristic'.
# ---------------------------------------------------------------------------

_JSX_ROUTE_RE = re.compile(
    # Both gaps use [^<>]*? (not [^>]*?) so they CANNOT span past a '<'.
    # That prevents the regex from escaping the current <Route ...> element
    # and pairing an unrelated `path` with an unrelated component elsewhere
    # in the file (the worst over-pairing on reformatted/multi-line JSX).
    # It can still miss genuinely reformatted variants -- tagged
    # provenance='heuristic'.
    r"<Route\b[^<>]*?\bpath\s*=\s*[\"']([^\"']+)[\"'][^<>]*?"
    r"(?:element\s*=\s*\{\s*<\s*([A-Za-z_][\w.]*)|Component\s*=\s*\{?\s*([A-Za-z_][\w.]*))",
    re.DOTALL,
)
_CONFIG_ROUTE_RE = re.compile(
    r"\{\s*path\s*:\s*[\"']([^\"']+)[\"'][^{}]*?"
    r"(?:element\s*:\s*<\s*([A-Za-z_][\w.]*)|Component\s*:\s*([A-Za-z_][\w.]*))",
    re.DOTALL,
)


def _detect_react_router(pf: ParsedFile) -> Optional[RouteExtraction]:
    try:
        source = Path(pf.path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if "<Route" not in source and "createBrowserRouter" not in source and "createHashRouter" not in source:
        return None  # cheap short-circuit before regex scanning

    extraction = RouteExtraction()
    seen = set()
    for pattern in (_JSX_ROUTE_RE, _CONFIG_ROUTE_RE):
        for m in pattern.finditer(source):
            route_path = m.group(1)
            handler = m.group(2) or m.group(3)
            if not handler:
                continue
            key = (route_path, handler)
            if key in seen:
                continue
            seen.add(key)
            line = source.count("\n", 0, m.start()) + 1
            route_name = f"ROUTE {route_path}"
            extraction.routes.append(
                Symbol(
                    name=route_name,
                    kind="route",
                    qualified_name=_route_qualified_name(pf, route_name),
                    line_start=line,
                    line_end=line,
                    metadata={
                        "http_method": None,
                        "path": route_path,
                        "framework": "react_router",
                        "handler": handler,
                        "provenance": "heuristic",
                    },
                )
            )
            extraction.references.append(Edge(route_name, "references", handler, line))
    return extraction if extraction.routes else None


# ---------------------------------------------------------------------------
# Next.js: file-based routing under pages/ or app/.
# ---------------------------------------------------------------------------

def _nextjs_segment(part: str) -> str:
    if part.startswith("[") and part.endswith("]"):
        inner = part[1:-1]
        if inner.startswith("..."):
            return "*" + inner[3:]
        return ":" + inner
    return part


def _is_exported(sym: Symbol, source: str) -> bool:
    """True if ``sym`` is a named or default export, inferred from raw source.

    A symbol counts as exported if its declaration is preceded by ``export``
    (named export) OR it is referenced by an ``export default <name>`` statement
    anywhere in the file.
    """
    name = re.escape(sym.name)
    # Named export at the declaration site:
    #   export function Foo      /  export async function Foo
    #   export class Foo         /  export abstract class Foo
    #   export const Foo =
    if re.search(
        r"\bexport\b[ \t]*(?:async[ \t]+|abstract[ \t]+)?(?:function|class)[ \t]+"
        + name + r"\b",
        source,
    ):
        return True
    if re.search(r"\bexport\b[ \t]+const[ \t]+" + name + r"\b", source):
        return True
    # Referenced by a default-export statement:  export default Foo;
    if re.search(r"\bexport\b[ \t]+default[ \t]+" + name + r"\b", source):
        return True
    return False


def _default_exported_name(source: str) -> Optional[str]:
    """Name carried by the file's ``export default`` statement, or None.

    Covers both forms used by Next.js page components:
      - inline declaration: ``export default function Foo()`` / ``export default class Foo``
      - bare reference:     ``export default Foo;`` (Foo declared elsewhere in the file)
    """
    m = re.search(
        r"\bexport\b[ \t]+default[ \t]*(?:async[ \t]+)?(?:function|class)[ \t]+([A-Za-z_$][\w$]*)",
        source,
    )
    if m:
        return m.group(1)
    m = re.search(
        r"\bexport\b[ \t]+default[ \t]+([A-Za-z_$][\w$]*)\s*;?[ \t]*$",
        source,
        re.MULTILINE,
    )
    if m:
        return m.group(1)
    return None


def _select_handler_symbol(pf: ParsedFile, source: str) -> Optional[Symbol]:
    """Pick the Next.js route handler symbol from the file's symbols.

    A route handler is the file's default export (App Router pages REQUIRE a
    default export; Pages Router conventionally default-exports the page).
    Preference order:

      1. the symbol named by ``export default`` (if any and present);
      2. otherwise the first exported function/class;
      3. otherwise the first function/class (fallback when export info
         cannot be derived, e.g. unreadable source).
    """
    candidates = [s for s in pf.symbols if s.kind in ("function", "class")]
    if not candidates:
        return None

    default_name = _default_exported_name(source)
    if default_name:
        for s in candidates:
            if s.name == default_name:
                return s

    exported = [s for s in candidates if _is_exported(s, source)]
    if exported:
        return exported[0]
    return candidates[0]


def _detect_nextjs(pf: ParsedFile) -> Optional[RouteExtraction]:
    p = Path(pf.path)
    parts = p.parts
    lower_parts = [part.lower() for part in parts]
    try:
        # Use the FIRST (outermost) "app"/"pages" directory as the router root.
        # Next.js treats the top-most app/pages dir as the anchor; deeper
        # same-named dirs (e.g. ``app/ui/app/page.tsx`` in a monorepo) are
        # route segments, not a second router root. ``min`` picks the first
        # occurrence where ``max`` would erroneously re-anchor on a later one.
        anchor_idx = min(
            i for i, part in enumerate(lower_parts) if part in ("pages", "app")
        )
    except ValueError:
        return None
    anchor = lower_parts[anchor_idx]
    rel_dir_parts = parts[anchor_idx + 1: -1]
    stem = p.stem

    if anchor == "app":
        if stem not in PAGE_FILE_STEMS or stem in APP_ROUTER_NON_ROUTE_STEMS:
            return None
        segments = [_nextjs_segment(part) for part in rel_dir_parts]
    else:  # pages/
        if stem.startswith("_"):  # _app, _document, _error -- not routes
            return None
        segments = [_nextjs_segment(part) for part in rel_dir_parts]
        if stem not in PAGE_FILE_STEMS:
            segments.append(_nextjs_segment(stem))

    route_path = _join_path(*segments)

    try:
        source = Path(pf.path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        source = ""  # degrade to first-candidate behavior

    handler_sym = _select_handler_symbol(pf, source)
    if handler_sym is None:
        return None

    route_name = f"ROUTE {route_path}"
    extraction = RouteExtraction()
    extraction.routes.append(
        Symbol(
            name=route_name,
            kind="route",
            qualified_name=_route_qualified_name(pf, route_name),
            line_start=1,
            line_end=1,
            metadata={
                "http_method": None,
                "path": route_path,
                "framework": "nextjs",
                "handler": handler_sym.name,
                "provenance": "heuristic",
            },
        )
    )
    extraction.references.append(Edge(route_name, "references", handler_sym.name, 1))
    return extraction


_DETECTORS: Dict[str, Callable[[ParsedFile], Optional[RouteExtraction]]] = {
    "nestjs": _detect_nestjs,
    "react_router": _detect_react_router,
    "nextjs": _detect_nextjs,
}
