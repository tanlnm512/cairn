"""Semantic embeddings for the symbol corpus: build, store, and query dense
vector representations of symbols so agents can find code by meaning.

Backend selection is env-var driven via ``CAIRN_EMBED_BACKEND`` (each knob
also reads the persistent ``$CAIRN_HOME/config.json``, env winning -- see
``_config_or_env``): ``local`` (default, sentence-transformers), ``hash``
(dep-free fallback), ``openai`` (opt-in API), or the ``server`` family —
``server``/``omlx``/``ollama``, OpenAI-compatible /v1 endpoints where
omlx/ollama differ only in their preset base URL. ``embeddings_available()``
reports whether a real backend is wired so callers degrade with an install
hint.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
import struct
import threading
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from .schema import note_contention, rebuild_term_df

# ---------------------------------------------------------------------------
# Model identity. Stored per-row so a model swap invalidates and re-embeds.
# Bumping this string forces embed_all to re-embed every symbol on the next run.
# ---------------------------------------------------------------------------

DEFAULT_LOCAL_MODEL = "BAAI/bge-m3"
HASH_MODEL = "hash-256-v1"  # deterministic fallback; tests + offline smoke
DEFAULT_DIM = 256           # dimensionality of the hash fallback embedder


_CORPUS_MODEL_ENV = {
    "knowledge": "CAIRN_EMBED_KNOWLEDGE_MODEL",
    "memory": "CAIRN_EMBED_MEMORY_MODEL",
}


def current_model(corpus: str = "code") -> str:
    """The model name rows are stamped with for the effective backend.

    ``corpus`` selects between the code corpus (default) and the
    knowledge/memory corpora, each of which can be pinned to a different
    local model via its own env var (falls back to CAIRN_EMBED_LOCAL_MODEL).
    Only applies to the local backend.

    Server-family backends stamp ``server/{netloc}/{model}`` — the netloc
    of the resolved base URL (scheme and path stripped) plus the request
    model id — so staleness, purge, and vec0 table names react to producer
    swaps with no schema change (FR-004). CAIRN_EMBED_MODEL_STAMP, when
    set, is returned verbatim: a pure override with no derivation and no
    validation. The ladder's rung-1 session adoption (checked between the
    env stamp and the derived stamp) pins the stored corpus stamp so an
    adopted candidate serves the existing rows with zero re-embed (FR-012).
    One server model serves every corpus (D-005), so ``corpus`` is ignored
    for server backends.
    """
    backend = _effective_backend()
    if backend == "hash":
        return HASH_MODEL
    if backend == "openai":
        return os.environ.get("CAIRN_EMBED_OPENAI_MODEL", "text-embedding-3-small")
    if backend == "server":
        override = _config_or_env("CAIRN_EMBED_MODEL_STAMP")
        if override:
            return override
        if _SESSION_STAMP_OVERRIDE:
            return _SESSION_STAMP_OVERRIDE
        netloc = urlsplit(_server_base_url()).netloc
        return f"server/{netloc}/{_server_model()}"
    env_name = _CORPUS_MODEL_ENV.get(corpus)
    if env_name:
        corpus_model = _config_or_env(env_name)
        if corpus_model:
            return corpus_model
    return _config_or_env("CAIRN_EMBED_LOCAL_MODEL") or DEFAULT_LOCAL_MODEL


# ---------------------------------------------------------------------------
# Availability — callers gate on this to degrade cleanly.
# ---------------------------------------------------------------------------


def embeddings_available() -> bool:
    """True iff an embedding backend can be loaded right now.

    The default 'local' backend falls back to the hash embedder when
    sentence_transformers is missing. Returns False when openai is selected
    but OPENAI_API_KEY is missing, or when a server-family backend fails its
    availability probe: GET {base}/models must return 200 AND list the
    configured model id (FR-002). The probe verdict is cached per process;
    reset_backend_cache() invalidates it.
    """
    backend = _backend_name()
    if backend == "hash":
        return True
    if backend == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    if backend in _SERVER_FAMILY:
        # Probe here, before the local import attempt: the ImportError branch
        # below stamps 'hash' into the shared cache, which a server config
        # must never reach (FR-002: server never resolves to hash).
        # Rung-2 session adoption (FR-012) already proved local availability
        # before switching, so it answers without the (still failing) probe.
        if _SESSION_BACKEND_OVERRIDE:
            return True
        return _server_probe_available()
    # local (default) — fall back to hash when sentence_transformers missing
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        _EFFECTIVE_BACKEND_CACHE["effective"] = "hash"
        return True


def install_hint() -> str:
    """The message shown to a user who invokes semantic_search without the extra."""
    return (
        "Semantic search requires the 'semantic' extra. "
        "Install it with: pip install 'cairn-intel[semantic]'. "
        "Or set CAIRN_EMBED_BACKEND=omlx or ollama (or server + "
        "CAIRN_EMBED_BASE_URL) to use a local OpenAI-compatible embeddings "
        "server -- no model install needed. "
        "Or set CAIRN_EMBED_BACKEND=hash for a dep-free smoke test."
    )


# ---------------------------------------------------------------------------
# Chunking — build the text that gets embedded.
# ---------------------------------------------------------------------------


# All selectable chunking recipes (see chunk_for_symbol). Order is the
# ablation ladder: A legacy baseline, B default, C maximal, then the T013
# field-dropout variants. The TC-008 identity floor (qualified name, file
# path, signature, docstring) is present in EVERY entry; tests iterate this
# tuple to enforce it. Values are case-normalized (upper) before matching.
CHUNK_VARIANTS = (
    "A", "B", "C",
    "B_NO_SCOPE", "B_NO_SIG", "B_IDENTITIES", "C_TRIM",
)


def chunk_for_symbol(
    row: sqlite3.Row,
    signature: Optional[str] = None,
    variant: Optional[str] = None,
    max_tokens: int = 512,
) -> str:
    """Build the embedding chunk for one symbol.

    Variants: A (kind + name + first signature line), B (A + docstring +
    parameters + return_type + full signature), C (B + body + context).

    Field-dropout variants of B (T013, FR-002/D-004 -- each removes one field
    family so the retrieval-quality ablation can measure its contribution;
    the TC-008 identity floor holds in every one):

    * ``B_NO_SCOPE`` -- B minus ``Enclosing Scope``/``Imports`` (file path
      stays; tests the contextual-scope fields).
    * ``B_NO_SIG`` -- B minus ``Parameters``/``Return Type`` (``Signature``
      stays per the floor; tests the structured signature metadata).
    * ``B_IDENTITIES`` -- the minimal legal variant: ONLY the identity floor
      (qualified name + file path + signature + docstring).
    * ``C_TRIM`` -- B plus the body truncated to half the chunk budget
      (tests whether a trimmed body keeps C's gains at lower size).

    ``variant`` (explicit) overrides ``CAIRN_CHUNK_VARIANT`` (env, default B)
    without ever mutating the environment (D-008 doctrine); both are
    case-insensitive.
    """
    v = (variant or os.environ.get("CAIRN_CHUNK_VARIANT", "B")).upper()
    kind = (row["kind"] or "").strip() if row["kind"] is not None else ""
    qname = (row["qualified_name"] or row["name"] or "").strip()
    doc = (row["docstring"] or "").strip() if "docstring" in row.keys() else ""
    sig = (signature or "").strip()

    params_raw = row["parameters"] if "parameters" in row.keys() and row["parameters"] else None
    ret_type = row["return_type"] if "return_type" in row.keys() and row["return_type"] else None

    file_path = row["file_path"] if "file_path" in row.keys() and row["file_path"] else None
    parent_scope = row["parent_scope"] if "parent_scope" in row.keys() and row["parent_scope"] else None
    imports_summary = row["imports_summary"] if "imports_summary" in row.keys() and row["imports_summary"] else None

    # Field-dropout variants that remove the contextual scope extras; the
    # File: line itself always stays (TC-008 floor). A/B/C never take this
    # branch, so their output stays byte-identical to pre-T013.
    drop_scope_extras = v in ("B_NO_SCOPE", "B_IDENTITIES")

    scope_header = []
    if file_path:
        scope_header.append(f"File: {file_path}")
    if parent_scope and not drop_scope_extras:
        scope_header.append(f"Enclosing Scope: {parent_scope}")
    if imports_summary and not drop_scope_extras:
        scope_header.append(f"Imports: {imports_summary}")

    parts = []
    if scope_header:
        parts.append("\n".join(scope_header))

    header = f"{kind} {qname}".strip()
    if header:
        parts.append(header)

    if v == "A":
        # Baseline
        first_line = sig.split("\n")[0] if sig else ""
        if first_line and first_line != header:
            parts.append(first_line)
        if doc:
            parts.append(doc)
    elif v in ("B", "C", "B_NO_SCOPE", "B_NO_SIG", "B_IDENTITIES", "C_TRIM"):
        if sig and sig != header:
            parts.append(f"Signature: {sig}")
        if params_raw and v not in ("B_NO_SIG", "B_IDENTITIES"):
            parts.append(f"Parameters: {params_raw}")
        if ret_type and v not in ("B_NO_SIG", "B_IDENTITIES"):
            parts.append(f"Return Type: {ret_type}")
        if doc:
            parts.append(f"Docstring: {doc}")

        if v in ("C", "C_TRIM") and "body" in row.keys() and row["body"]:
            body = row["body"]
            if v == "C_TRIM":
                # Body prefix capped at half the chunk's truncation budget;
                # identity fields (which precede the body) keep the full budget.
                body = body[: max_tokens * 4 // 2]
            parts.append(f"Body:\n{body}")

    res = "\n".join(parts) if parts else qname or kind
    # Simple character truncation approximation for max_tokens (approx 4 chars per token)
    max_chars = max_tokens * 4
    if len(res) > max_chars:
        res = res[:max_chars]
    return res


def _signature_lines_for_rows(rows: Sequence[sqlite3.Row]) -> dict:
    """Read each symbol's declaration line from disk, grouped by file.

    Returns ``{symbol_id: signature_line}``. A missing/moved file or a symbol
    with no file_path/line_start just gets no signature (chunk_for_symbol
    falls back to kind+qname+doc), never raises.
    """
    from ..paths import resolve_workspace
    from .scanner import resolve_file_path

    workspace = str(resolve_workspace())
    # Group by (repo, file_path) so each file is opened once.
    by_file: dict = {}
    for r in rows:
        path = r["file_path"] if "file_path" in r.keys() else None
        repo = r["repo"] if "repo" in r.keys() else None
        ls = r["line_start"] if "line_start" in r.keys() else None
        if not path or not ls or ls < 1:
            continue
        by_file.setdefault((repo, path), []).append((r["id"], ls))

    out: dict = {}
    for (repo, path), entries in by_file.items():
        abs_path = resolve_file_path(workspace, repo, path)
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                all_lines = fh.readlines()
        except OSError:
            continue  # file deleted/moved since index — skip silently
        for sid, ls in entries:
            if ls - 1 < len(all_lines):
                out[sid] = all_lines[ls - 1].strip()
    return out


# Multi-vector kinds (spec retrieval-quality-v2 FR-005). Deliberately NOT
# CHUNK_VARIANTS entries: the TC-008 identity-floor tests iterate
# CHUNK_VARIANTS and would break for minimal per-kind texts. Each kind has
# its own producer below and its own content-hash staleness over that text.
MV_KINDS = ("name", "docstring")


def name_text_for_symbol(row: sqlite3.Row, signature: Optional[str] = None) -> str:
    """Name-only multi-vector text: symbol kind + qualified name + signature
    line (the variant-A header shape minus the docstring).

    Mirrors chunk_for_symbol's header construction so the two texts stay
    consistent. Returns "" only when the symbol has neither name nor kind.
    """
    kind = (row["kind"] or "").strip() if row["kind"] is not None else ""
    qname = (row["qualified_name"] or row["name"] or "").strip()
    sig = (signature or "").strip()
    header = f"{kind} {qname}".strip()
    parts = [header] if header else []
    first_line = sig.split("\n")[0] if sig else ""
    if first_line and first_line != header:
        parts.append(first_line)
    return "\n".join(parts)


def docstring_text_for_symbol(row: sqlite3.Row) -> str:
    """Docstring-only multi-vector text: the symbol's docstring, stripped.

    Returns "" when the symbol has no docstring -- the caller must skip
    embedding (and drop any stale row) for that symbol/kind pair.
    """
    doc = row["docstring"] if "docstring" in row.keys() else None
    return (doc or "").strip()


def mv_text_for_kind(
    row: sqlite3.Row, vector_kind: str, signature: Optional[str] = None
) -> str:
    """Dispatch to the producer for ``vector_kind`` ('name' | 'docstring').

    Contract: one text per (symbol, kind); unknown kinds raise ValueError so
    a typo in a future kind can never silently produce empty vectors.
    """
    if vector_kind == "name":
        return name_text_for_symbol(row, signature=signature)
    if vector_kind == "docstring":
        return docstring_text_for_symbol(row)
    raise ValueError(f"unknown vector_kind: {vector_kind!r}")


# ---------------------------------------------------------------------------
# Backend abstraction — local (sentence-transformers) / hash / openai.
# Each backend exposes _embed(texts) -> List[bytes] (float32 BLOBs).
# ---------------------------------------------------------------------------


def _config_or_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """D-008 choke point for the CAIRN_EMBED_* knobs: env var > config file
    > ``default``. Env and file values are stripped, so a blank env value
    falls through to the file exactly as blanks used to fall through to
    defaults. File values live in $CAIRN_HOME/config.json under the same
    env-var name (paths.CONFIG_FILE); no config file means env-or-default,
    byte-identical to the pre-FR-010 behavior.
    """
    from ..paths import get_config_value

    env = (os.environ.get(name) or "").strip()
    if env:
        return env
    file_val = get_config_value(name)
    if isinstance(file_val, str) and file_val.strip():
        return file_val.strip()
    return default


def _backend_name() -> str:
    return (_config_or_env("CAIRN_EMBED_BACKEND") or "local").strip().lower()


# The server family: omlx/ollama are preset aliases of the same 'server' arm
# (OpenAI-compatible /v1 endpoint); only the default base URL differs.
_SERVER_FAMILY = frozenset({"server", "omlx", "ollama"})

_SERVER_PRESET_BASE_URL = {
    "omlx": "http://127.0.0.1:8000/v1",
    "ollama": "http://127.0.0.1:11434/v1",
}


# Cache the loaded model so repeated calls don't reload weights.
# Guarded by _MODEL_CACHE_LOCK: the lazy load is reachable from both the embed
# flusher thread and tool threads (audit F5), and an unsynchronized load could
# double-load the weights or -- with two different model keys racing the
# single-entry eviction -- KeyError the loser on the final lookup.
_MODEL_CACHE: dict = {}
_MODEL_CACHE_LOCK = threading.Lock()

# Cache for the effective backend after fallback resolution. Set once per
# process by embeddings_available().
_EFFECTIVE_BACKEND_CACHE: dict = {"effective": None}

# Cached availability-probe verdict for the server family (FR-002), stamped
# by _server_probe_available(); invalidated by reset_backend_cache().
_SERVER_PROBE_CACHE: dict = {"available": None}

# FR-002 fixes the probe timeout at 2 s: a down server must fail the
# availability gate fast, independent of CAIRN_EMBED_TIMEOUT (which governs
# embed requests). Tests inject a shorter value via this module attribute.
_PROBE_TIMEOUT_S = 2.0

# FR-005 alias-gate verdicts keyed by the CAIRN_EMBED_MODEL_STAMP value: the
# parity check costs up to 16 embeds, so it runs once per process per stamp.
_ALIAS_GATE_CACHE: dict = {}
_ALIAS_GATE_LOCK = threading.Lock()

# Session-scoped ladder adoptions (FR-012, set only by graph.embed_ladder
# after a parity pass): the rung-1 alias binding (stored stamp pinned so
# reads/writes stay on the corpus while requests go through the adopted
# model id), the adopted request model id, and the rung-2 local fallback.
# Explicit env vars always win over these; reset_backend_cache() clears all.
_SESSION_STAMP_OVERRIDE: Optional[str] = None
_SESSION_SERVER_MODEL: Optional[str] = None
_SESSION_BACKEND_OVERRIDE: Optional[str] = None


def reset_backend_cache() -> None:
    """Clear the cached effective-backend resolution, the server probe, and
    the alias-gate verdicts, plus the ladder's cached verdict and session
    adoptions.

    Call this in test setup/teardown whenever CAIRN_EMBED_BACKEND is changed,
    since none of these caches are invalidated mid-process.
    """
    _EFFECTIVE_BACKEND_CACHE["effective"] = None
    _SERVER_PROBE_CACHE["available"] = None
    with _ALIAS_GATE_LOCK:
        _ALIAS_GATE_CACHE.clear()
    # Config-file reads are cached in paths (mtime-stamped); drop that cache
    # too so doctor/tests force a re-read.
    from .. import paths

    paths.reset_config_cache()
    # Lazy import: embed_ladder imports this module at top level, so the
    # ladder hook can only be reached from here, never the other way round.
    from . import embed_ladder

    embed_ladder.reset_cache()


def _alias_preflight(conn: sqlite3.Connection) -> None:
    """FR-005 alias gate: parity-verify stored rows before any writer INSERT.

    Runs only for the server family with CAIRN_EMBED_MODEL_STAMP set; zero
    stored rows under the stamp is check_parity's vacuous pass. The verdict
    is evaluated once per process per stamp (reset_backend_cache() clears
    it). Raises RuntimeError on failure -- measured mean cosine, or both
    dims on a dim mismatch -- before any row is written.
    """
    stamp = (_config_or_env("CAIRN_EMBED_MODEL_STAMP") or "").strip()
    if not stamp or _effective_backend() != "server":
        return
    with _ALIAS_GATE_LOCK:
        verdict = _ALIAS_GATE_CACHE.get(stamp)
    if verdict is None:
        from . import embed_ladder

        verdict = embed_ladder.check_parity(conn, stamp)
        with _ALIAS_GATE_LOCK:
            _ALIAS_GATE_CACHE[stamp] = verdict
    if not verdict.passed:
        raise RuntimeError(
            f"CAIRN_EMBED_MODEL_STAMP '{stamp}' failed the alias parity "
            f"preflight ({verdict.reason}); no rows were written"
        )


def _effective_backend() -> str:
    """The backend actually used for embedding (after fallback resolution).

    When CAIRN_EMBED_BACKEND is unset (default 'local') but
    sentence_transformers isn't installed, falls back to 'hash'.
    Otherwise returns the configured backend unchanged. The server family
    (server/omlx/ollama) resolves to 'server' with no dependency probing,
    so it can never coalesce into 'hash'. The ladder's rung-2 session
    adoption (FR-012) switches a server-family config to local for the
    process lifetime; it applies only while the env config stays
    server-family and is never 'hash' (D-003).
    """
    if _EFFECTIVE_BACKEND_CACHE["effective"] is not None:
        return _EFFECTIVE_BACKEND_CACHE["effective"]
    backend = _backend_name()
    if backend == "local":
        try:
            import sentence_transformers  # noqa: F401
            _EFFECTIVE_BACKEND_CACHE["effective"] = "local"
        except ImportError:
            _EFFECTIVE_BACKEND_CACHE["effective"] = "hash"
    else:
        resolved = "server" if backend in _SERVER_FAMILY else backend
        if resolved == "server" and _SESSION_BACKEND_OVERRIDE:
            resolved = _SESSION_BACKEND_OVERRIDE
        _EFFECTIVE_BACKEND_CACHE["effective"] = resolved
    return _EFFECTIVE_BACKEND_CACHE["effective"]


def _server_base_url() -> str:
    """The base URL for the active server backend.

    CAIRN_EMBED_BASE_URL (env or config file, D-008) overrides the
    per-backend preset; bare 'server' has no preset and requires it.
    Raises RuntimeError when unresolvable — at resolution time, never at
    import.
    """
    configured = _config_or_env("CAIRN_EMBED_BASE_URL") or ""
    if configured:
        return configured
    preset = _SERVER_PRESET_BASE_URL.get(_backend_name())
    if preset:
        return preset
    raise RuntimeError(
        "CAIRN_EMBED_BACKEND=server requires CAIRN_EMBED_BASE_URL "
        "(an OpenAI-compatible base URL ending in /v1); "
        "or use the 'omlx'/'ollama' presets"
    )


def _server_model() -> str:
    """The model id sent in server embedding requests.

    The ladder's rung-1 session adoption wins over CAIRN_EMBED_SERVER_MODEL:
    the adopted id is parity-proven against the stored corpus (D-009), while
    the env id is the failed producer the ladder is replacing. Otherwise the
    env-or-config-file value (D-008) wins over the default preset id.
    """
    if _SESSION_SERVER_MODEL:
        return _SESSION_SERVER_MODEL
    env = _config_or_env("CAIRN_EMBED_SERVER_MODEL")
    if env:
        return env
    return "bge-m3"


def _server_probe_available() -> bool:
    """The per-process cached server-family availability verdict (FR-002).

    True only when GET {base}/models returns 200 AND lists the configured
    model id. Both outcomes are cached for the process lifetime;
    reset_backend_cache() forces the next call to re-probe.
    """
    if _SERVER_PROBE_CACHE["available"] is None:
        _SERVER_PROBE_CACHE["available"] = _run_server_probe()
    return _SERVER_PROBE_CACHE["available"]


def _run_server_probe() -> bool:
    """One uncached availability probe: GET {base}/models (FR-002).

    Returns False on connection failure, timeout, non-200 status, or an
    unparseable / model-missing listing. Never raises — callers gate on it.
    """
    import http.client
    import json
    import urllib.request

    try:
        base = _server_base_url().rstrip("/")
    except RuntimeError:
        return False  # bare 'server' without CAIRN_EMBED_BASE_URL can't serve
    headers = {}
    api_key = _config_or_env("CAIRN_EMBED_API_KEY") or ""
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        req = urllib.request.Request(f"{base}/models", headers=headers)
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT_S) as resp:
            if resp.status != 200:
                return False
            body = resp.read()
    except (OSError, http.client.HTTPException, ValueError):
        return False
    try:
        listing = json.loads(body.decode("utf-8"))
    except ValueError:
        return False
    data = listing.get("data") if isinstance(listing, dict) else None
    if not isinstance(data, list):
        return False
    return any(
        isinstance(entry, dict) and entry.get("id") == _server_model()
        for entry in data
    )


def is_hash_fallback() -> bool:
    """True when embeddings silently use the dep-free hash backend.

    The configured backend is ``local`` (the default) but sentence-transformers
    isn't installed, so ``_embed`` returns token-overlap-only vectors. Query
    paths check this to flag degraded results. Returns False when the user
    explicitly set ``CAIRN_EMBED_BACKEND=hash`` or a real backend is active.
    """
    return _effective_backend() == "hash" and _backend_name() == "local"


# Process-global guard so the one-time warning fires at most once per process.
_HASH_FALLBACK_WARNED: bool = False


def warn_hash_fallback_once(logger, context: str = "") -> None:
    """Emit one hash-fallback warning per process.

    No-op when a real backend is active or the hash backend was explicitly
    chosen. ``context`` is a short string identifying the calling path.
    """
    global _HASH_FALLBACK_WARNED
    if not _HASH_FALLBACK_WARNED and is_hash_fallback():
        # Durable event (spec §6.4); the WARNING below keeps the human detail.
        try:
            from cairn.telemetry import HASH_FALLBACK, emit as _emit

            _emit(HASH_FALLBACK)
        except Exception:
            pass
        suffix = f" [{context}]" if context else ""
        logger.warning(
            "Embeddings are using the dep-free hash backend (%s). Results carry "
            "token-overlap signal, not real semantic meaning. Install once with "
            "`cairn embed --install-deps`.%s",
            current_model(),
            suffix,
        )
        _HASH_FALLBACK_WARNED = True


def model_is_cached(model_name: Optional[str] = None) -> bool:
    """Check whether the model weights are present in the local HuggingFace cache."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False

    m_name = model_name or current_model()
    result = try_to_load_from_cache(m_name, "config.json")
    return result is not None


def download_model(model_name: Optional[str] = None) -> bool:
    """Download model weights into the local HuggingFace cache if not present.

    The fetch runs in a child interpreter behind the quiet progress helper:
    constructing the model in-process let HuggingFace print one tqdm bar
    per repo file (plus transformers warnings) straight into the terminal
    -- a wall of lines for an ~836 MB multi-file model. The child shares
    the parent's HF cache, so afterwards any process (this one included)
    loads the weights from cache.
    """
    import subprocess
    import sys

    m_name = model_name or current_model()
    if model_is_cached(m_name):
        print(f"Model '{m_name}' is already cached — skipping download.")
        return True

    print(f"Downloading '{m_name}' model weights into local cache...")
    # Constructing the model IS the download (weights land in the HF cache).
    # trust_remote_code mirrors _get_local_model so what gets cached matches
    # what the runtime path loads.
    trust = os.environ.get("CAIRN_EMBED_TRUST_REMOTE_CODE") == "1"
    code = (
        "from sentence_transformers import SentenceTransformer; "
        f"SentenceTransformer({m_name!r}, trust_remote_code={trust!r})"
    )
    try:
        _run_subprocess_with_progress(
            [sys.executable, "-c", code],
            f"Downloading {m_name}",
            env={**os.environ, "PYTHONPATH": _lib_pythonpath()},
        )
    except subprocess.CalledProcessError:
        # The helper already printed the child's captured output above (the
        # HF error -- or a ModuleNotFoundError when the [semantic] extra
        # isn't importable anywhere the child can see).
        print(
            f"Failed to download model '{m_name}' (see the output above; if "
            "it is an import error, run `cairn embed --install-deps` first)"
        )
        return False
    print(f"Model '{m_name}' downloaded successfully.")
    return True


def _run_subprocess_with_progress(
    cmd: list[str], description: str, env: Optional[dict] = None
) -> str:
    """Run a subprocess under a progress bar, draining its output.

    Returns the combined stdout+stderr. Raises CalledProcessError (with the
    captured output) when the subprocess exits non-zero.
    """
    import subprocess
    import time

    from ..cli.display import progress_bar

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )

    # Drain stdout in the loop: pip writes progress to the combined pipe, and
    # if output exceeds the OS pipe buffer (~64 KB) pip blocks on write.
    output_lines = []
    with progress_bar(description, total=None, unit="") as bar:
        while proc.poll() is None:
            bar.advance(0)
            # Drain any pending output so the pipe never fills.
            if proc.stdout:
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    output_lines.append(line)
            time.sleep(0.2)
        # Drain any trailing output after the process exits.
        if proc.stdout:
            for line in proc.stdout:
                output_lines.append(line)

    output = "".join(output_lines)
    if proc.returncode != 0:
        # Print captured output so the user sees the actual pip error.
        if output:
            print(output)
        raise subprocess.CalledProcessError(proc.returncode, cmd, output)
    return output


def _run_install_with_progress(cmd: list[str], lib_dir) -> None:
    """Run a pip/uv install subprocess with a single-line progress indicator."""
    print(f"Installing semantic deps into {lib_dir} (one-time, ~hundreds of MB via torch)...")
    _run_subprocess_with_progress(cmd, "Installing semantic deps")


def _install_cmd(packages: list[str], lib_dir) -> Optional[list[str]]:
    """Build the pip/uv install command targeting the RUNNING interpreter.

    Returns None when neither installer is available. The uv branch pins
    ``--python sys.executable``: unpinned, uv resolves wheels for whichever
    interpreter IT discovers (an active venv, a managed default), which can
    be a different ABI than the one running cairn -- the install would
    "succeed" while every import in this process keeps failing.
    """
    import importlib.util
    import shutil
    import sys

    if importlib.util.find_spec("pip") is not None:
        # pip install --target writes into the shared lib dir, which is
        # prepended to sys.path at import time (paths.py).
        return [
            sys.executable, "-m", "pip", "install",
            "--target", str(lib_dir), *packages,
        ]
    # No pip in this interpreter (uv tool env); use uv pip install.
    uv = shutil.which("uv")
    if uv is None:
        return None
    return [
        uv, "pip", "install", "--python", sys.executable,
        "--target", str(lib_dir), *packages,
    ]


def _lib_pythonpath() -> str:
    """PYTHONPATH for child interpreters: shared lib dirs first, then existing.

    Mirrors the in-process sys.path order paths._inject_shared_libs
    establishes (ABI dir, then the legacy flat dir under the default
    layout), so a child interpreter resolves the semantic stack from the
    same places the parent would -- the venv's site-packages still apply
    via the child's own interpreter.
    """
    from ..paths import SHARED_LIB, shared_lib_path

    dirs = [shared_lib_path()]
    if not os.environ.get("CAIRN_LIB"):
        dirs.append(SHARED_LIB)
    parts = [str(d) for d in dirs]
    if os.environ.get("PYTHONPATH"):
        parts.append(os.environ["PYTHONPATH"])
    return os.pathsep.join(parts)


def _verify_install(lib_dir) -> None:
    """Import the fresh stack in a FRESH subprocess, under a progress bar.

    The first import of a freshly installed stack is slow (30s+ on macOS:
    dyld validates ~150 new .so files before any of them load). Importing
    in-process left the CLI completely silent for that window after the
    install spinner had exited -- users read it as a hang and killed the
    command. The subprocess pays the same one-time cost but shows live
    progress, and warms the dyld/file caches so the next `cairn embed`
    imports at full speed. Raises CalledProcessError (after the helper has
    printed the child's captured output) when the import fails.
    """
    import sys

    print(
        "Verifying install (first import loads hundreds of native "
        "libraries; this can take a minute)..."
    )
    _run_subprocess_with_progress(
        [
            sys.executable,
            "-c",
            "import sentence_transformers, numpy, sqlite_vec",
        ],
        "Verifying install",
        env={**os.environ, "PYTHONPATH": _lib_pythonpath()},
    )


def ensure_semantic_deps(auto_install: bool = True) -> bool:
    """Ensure sentence-transformers is installed.

    If missing and ``auto_install=True``, installs the dependency into the
    shared lib directory (``~/.cairn/lib/cp<major><minor>``, one dir per
    interpreter ABI -- see paths.shared_lib_path), which survives
    reinstalls. A verification failure triggers one wipe-and-reinstall of
    the dir: pip's --target skip-if-satisfied semantics cannot repair an
    interrupted or foreign-ABI install in place. Model-weight downloading
    is handled separately by ``download_model``.
    """
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        if not auto_install:
            return False

    import shutil
    import subprocess
    import sys

    from ..paths import shared_lib_path

    lib_dir = shared_lib_path()
    packages = ["sentence-transformers", "numpy", "sqlite-vec"]
    try:
        cmd = _install_cmd(packages, lib_dir)
        if cmd is None:
            raise RuntimeError(
                "no 'pip' module in this interpreter and 'uv' not found on PATH -- "
                "install pip or run: uv pip install --python "
                f"{sys.executable} --target {lib_dir} sentence-transformers numpy "
                "sqlite-vec"
            )
        lib_dir.mkdir(parents=True, exist_ok=True)
        _run_install_with_progress(cmd, lib_dir)
        try:
            _verify_install(lib_dir)
        except subprocess.CalledProcessError:
            # pip install --target skips any package already present at a
            # satisfying version, so an install interrupted mid-unpack (or
            # written by a different interpreter ABI) can NEVER be repaired
            # by running pip over it again -- pip reports success while the
            # dir stays broken. Wipe and reinstall once from empty.
            print(
                f"Install verification failed; wiping {lib_dir} and "
                "reinstalling once from scratch..."
            )
            shutil.rmtree(lib_dir, ignore_errors=True)
            lib_dir.mkdir(parents=True, exist_ok=True)
            _run_install_with_progress(cmd, lib_dir)
            _verify_install(lib_dir)  # a second failure is terminal
        reset_backend_cache()
        _EFFECTIVE_BACKEND_CACHE["effective"] = "local"
        # Re-add the lib dir to sys.path in case paths.py ran before it
        # existed, so a later lazy in-process import finds the new install.
        if str(lib_dir) not in sys.path:
            sys.path.insert(0, str(lib_dir))
        return True
    except subprocess.CalledProcessError:
        # The helper already printed the child's captured output above.
        print(
            "Failed to auto-install dependencies: install reported success "
            "but importing in a fresh interpreter failed (see the error above)"
        )
        return False
    except Exception as exc:
        print(f"Failed to auto-install dependencies: {exc}")
        return False


def _get_local_model(model_name: Optional[str] = None):
    """Lazily load the sentence-transformers model (cached per process).

    Double-checked locking over _MODEL_CACHE (audit F5): the load is expensive
    (seconds) and reachable from concurrent threads, so exactly one thread
    loads per key. The loaded model is returned via a local reference rather
    than a final dict lookup -- a concurrent load of a DIFFERENT key evicting
    this entry must not turn a successful load into a KeyError.
    """
    m_name = model_name or current_model()
    key = ("local", m_name)
    model = _MODEL_CACHE.get(key)
    if model is None:
        with _MODEL_CACHE_LOCK:
            model = _MODEL_CACHE.get(key)
            if model is None:
                from sentence_transformers import SentenceTransformer

                trust = os.environ.get("CAIRN_EMBED_TRUST_REMOTE_CODE") == "1"
                kwargs = {"trust_remote_code": trust}
                if os.environ.get("CAIRN_EMBED_FP16") == "1":
                    kwargs["model_kwargs"] = {"torch_dtype": "float16"}
                model = SentenceTransformer(m_name, **kwargs)
                max_len = os.environ.get("CAIRN_EMBED_MAX_SEQ_LEN", "512")
                if max_len:
                    model.max_seq_length = int(max_len)
                # Single-model cache: evict any other entry on a key change.
                if _MODEL_CACHE and next(iter(_MODEL_CACHE)) != key:
                    _MODEL_CACHE.clear()
                _MODEL_CACHE[key] = model
    return model


def purge_stale_models(conn: sqlite3.Connection, active_model: Optional[str] = None) -> int:
    """Purge vectors and tables for all retired/superseded embedding models."""
    target_model = active_model or current_model()
    cur = conn.cursor()

    c1 = cur.execute("DELETE FROM embeddings WHERE model != ?", (target_model,)).rowcount
    try:
        c2 = cur.execute("DELETE FROM knowledge_embeddings WHERE model != ?", (target_model,)).rowcount
    except Exception:
        c2 = 0
    try:
        c3 = cur.execute("DELETE FROM memory_embeddings WHERE model != ?", (target_model,)).rowcount
    except Exception:
        c3 = 0
    # Multi-vector rows carry the same model stamp as their base row (FR-005);
    # a model swap orphans them identically, so they purge with it. The table
    # is created unconditionally by SCHEMA_SQL, so no try/except is needed.
    c4 = cur.execute("DELETE FROM embeddings_mv WHERE model != ?", (target_model,)).rowcount

    # Both vec0 table families are model-scoped and purge together: vec_<model>
    # (embeddings) and vecmv_<model> (embeddings_mv, D-007). '_' must be
    # escaped in the LIKE patterns -- it is a single-char wildcard, so the old
    # unescaped 'vec_%' also swept up vecmv_<model> tables (and any unrelated
    # "vecX..." name), and the keep-test below then dropped the ACTIVE vecmv
    # index because it never equals vec_<model>.
    tables = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND (name LIKE 'vec\\_%' ESCAPE '\\' OR name LIKE 'vecmv\\_%' ESCAPE '\\')"
    ).fetchall()
    # Resolve the active ANN table names once so an ImportError surfaces loudly
    # rather than being swallowed per-iteration.
    from .ann_index import _table_name as ann_table_name
    keep = {ann_table_name(target_model), ann_table_name(target_model, "embeddings_mv")}
    for (tname,) in tables:
        if tname not in keep:
            cur.execute(f"DROP TABLE IF EXISTS {tname}")

    conn.commit()
    return c1 + c2 + c3 + c4


def _embed_local(texts: Sequence[str]) -> Tuple[List[bytes], int]:
    model = _get_local_model()
    # normalize_embeddings=True makes cosine similarity a plain dot product.
    vecs = model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
    dim = int(vecs.shape[1])
    blobs = [_vec_to_blob(vecs[i]) for i in range(len(texts))]
    return blobs, dim


def _embed_openai(texts: Sequence[str]) -> Tuple[List[bytes], int]:
    import urllib.request
    import json

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("CAIRN_EMBED_BACKEND=openai requires OPENAI_API_KEY")
    model = current_model()
    url = "https://api.openai.com/v1/embeddings"
    payload = json.dumps({"model": model, "input": list(texts)}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    data = body["data"]
    data.sort(key=lambda d: d["index"])  # preserve input order
    dim = len(data[0]["embedding"])
    blobs = [_floats_to_blob(d["embedding"]) for d in data]
    return blobs, dim


def _embed_server(texts: Sequence[str]) -> Tuple[List[bytes], int]:
    """Embed texts via an OpenAI-compatible ``/v1/embeddings`` server endpoint.

    Returns (float32-LE BLOBs in input order, vector dim). Chunks into
    CAIRN_EMBED_SERVER_BATCH-sized POSTs (default 32); retries connection
    errors / timeouts / 5xx / 429 up to 3 times with exponential backoff
    (0.5/1/2 s, jittered); fails other 4xx immediately with the server's
    error message verbatim; honors CAIRN_EMBED_TIMEOUT (default 30 s);
    sends a bearer header only when CAIRN_EMBED_API_KEY is set; rejects
    batches whose embeddings disagree in dimensionality. The three knobs
    resolve env > config file > default (D-008).
    """
    if not texts:
        return [], 0
    import http.client
    import json
    import random
    import time
    import urllib.error
    import urllib.request

    base = _server_base_url().rstrip("/")
    model = _server_model()
    timeout_raw = _config_or_env("CAIRN_EMBED_TIMEOUT")
    timeout = float(timeout_raw) if timeout_raw else 30.0
    batch_raw = _config_or_env("CAIRN_EMBED_SERVER_BATCH")
    batch = int(batch_raw) if batch_raw else 32
    headers = {"Content-Type": "application/json"}
    api_key = _config_or_env("CAIRN_EMBED_API_KEY") or ""
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    max_retries = 3
    blobs: List[bytes] = []
    dim = 0
    for start in range(0, len(texts), batch):
        chunk = list(texts[start:start + batch])
        payload = json.dumps({"model": model, "input": chunk}).encode("utf-8")
        req = urllib.request.Request(
            f"{base}/embeddings", data=payload, headers=headers
        )
        raw: Optional[bytes] = None
        last_error = ""
        for attempt in range(max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")
                if e.code == 429 or e.code >= 500:
                    last_error = f"HTTP {e.code}"
                else:
                    try:
                        parsed = json.loads(detail)
                    except ValueError:
                        parsed = None
                    err = parsed.get("error") if isinstance(parsed, dict) else None
                    # OpenAI-shaped error.message carries the server's own
                    # remediation text (oMLX not_found_error lists valid ids).
                    message = detail
                    if isinstance(err, dict) and err.get("message"):
                        message = err["message"]
                    raise RuntimeError(
                        f"embedding server rejected the request "
                        f"(HTTP {e.code}): {message}"
                    ) from None
            except (OSError, http.client.HTTPException) as e:
                # URLError/ConnectionError/socket.timeout are OSError
                # subclasses; HTTPException covers drops mid-body.
                last_error = f"{type(e).__name__}: {e}"
            if attempt < max_retries:
                delay = 0.5 * (2 ** attempt)
                time.sleep(random.uniform(delay / 2, delay * 1.5))
        if raw is None:
            raise RuntimeError(
                f"embedding server unreachable after {max_retries} retries: "
                f"{last_error}"
            )
        data = json.loads(raw.decode("utf-8"))["data"]
        data.sort(key=lambda d: d["index"])  # preserve input order
        embeddings = [d["embedding"] for d in data]
        if len(embeddings) != len(chunk):
            raise RuntimeError(
                f"embedding server returned {len(embeddings)} vectors "
                f"for {len(chunk)} inputs"
            )
        for vec in embeddings:
            if dim == 0:
                dim = len(vec)
            elif len(vec) != dim:
                raise RuntimeError(
                    f"mixed-dimension batch: expected {dim}-dim vectors, "
                    f"got {len(vec)}"
                )
            blobs.append(_floats_to_blob(vec))
    return blobs, dim


# --- Hash fallback embedder (no deps; deterministic; low quality) ----------
#
# Maps a text to a fixed-size float32 vector via SHA-256 hashing. This is NOT a
# real semantic embedding -- two unrelated strings may collide -- but it is
# deterministic and dependency-free, so the wiring can be tested end-to-end
# without torch.


def _hash_vec(text: str, dim: int = DEFAULT_DIM) -> List[float]:
    """Deterministic hash-based pseudo-embedding.

    Produces a unit-norm vector of `dim` floats. Tokenizes on non-alphanumeric
    boundaries so the same token contributes the same signal regardless of
    position. Unrelated texts are largely orthogonal; identical tokens overlap.
    """
    vec = [0.0] * dim
    seen = set()
    # Simple tokenizer: lowercase, split on non-alphanumeric.
    tokens = []
    cur = []
    for ch in text.lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            tokens.append("".join(cur))
            cur = []
    if cur:
        tokens.append("".join(cur))
    for tok in tokens:
        if tok in seen:
            continue
        seen.add(tok)
        # Hash the token to pick a dimension and a sign; hash again for magnitude.
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "little") % dim
        sign = 1.0 if (h[4] & 1) == 0 else -1.0
        mag = (int.from_bytes(h[5:9], "little") % 1000) / 1000.0  # 0..1
        vec[idx] += sign * (0.5 + mag)
    # L2-normalize so cosine = dot product.
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _embed_hash(texts: Sequence[str]) -> Tuple[List[bytes], int]:
    blobs = [_floats_to_blob(_hash_vec(t)) for t in texts]
    return blobs, DEFAULT_DIM


def _embed(texts: Sequence[str]) -> Tuple[List[bytes], int]:
    """Dispatch to the effective backend (after fallback). Returns (blobs, dim)."""
    backend = _effective_backend()
    if backend == "hash":
        return _embed_hash(texts)
    if backend == "openai":
        return _embed_openai(texts)
    if backend == "server":
        return _embed_server(texts)
    return _embed_local(texts)


# ---------------------------------------------------------------------------
# BLOB encode/decode — float32 little-endian, matching queries.semantic_search.
# ---------------------------------------------------------------------------


def _vec_to_blob(vec) -> bytes:
    """numpy array row → float32 little-endian BLOB."""
    import numpy as np

    return np.ascontiguousarray(vec, dtype="<f4").tobytes()


def _floats_to_blob(floats: Sequence[float]) -> bytes:
    """Python float sequence → float32 little-endian BLOB."""
    return struct.pack(f"<{len(floats)}f", *floats)


# ---------------------------------------------------------------------------
# Corpus embedding — the `cairn embed` batch pass.
# ---------------------------------------------------------------------------


def _chunk_hash(chunk: str) -> str:
    """Stable content hash for a chunk, used to detect edits under a fixed model."""
    return hashlib.sha256(chunk.encode("utf-8")).hexdigest()


def _embed_mv_kinds(
    conn: sqlite3.Connection,
    rows: Sequence[sqlite3.Row],
    signatures: dict,
    model: str,
    batch_size: int,
    limit: Optional[int] = None,
    progress=None,
) -> int:
    """Populate/refresh ``embeddings_mv`` rows for every MV_KINDS entry.

    The opt-in FR-005 pass behind ``embed_all(multivector=True)``. Mirrors
    the base chunk flow's shape per kind: build the kind-specific text via
    :func:`mv_text_for_kind`, hash it with :func:`_chunk_hash` (per-kind
    staleness -- the name row and docstring row of one symbol refresh
    independently), and upsert keyed ``(symbol_id, model, vector_kind)``
    only when the stored hash is missing or different. A symbol whose
    docstring disappeared since the last pass has its stale docstring row
    deleted (the kind has no text to serve). Returns the number of mv rows
    embedded; commits per batch and swallows lock contention exactly like
    ``embed_all``'s main loop.
    """
    existing = {
        (r[0], r[1]): r[2]
        for r in conn.execute(
            "SELECT symbol_id, vector_kind, content_hash "
            "FROM embeddings_mv WHERE model = ?",
            (model,),
        )
    }

    stale: List[Tuple[str, str, str, str]] = []  # (symbol_id, kind, text, hash)
    emptied_docstring: List[str] = []
    for r in rows:
        sid = r["id"]
        for kind in MV_KINDS:
            text = mv_text_for_kind(r, kind, signature=signatures.get(sid))
            if not text.strip():
                if kind == "docstring" and (sid, kind) in existing:
                    emptied_docstring.append(sid)
                continue
            chash = _chunk_hash(text)
            if existing.get((sid, kind)) != chash:
                stale.append((sid, kind, text, chash))

    if emptied_docstring:
        conn.executemany(
            "DELETE FROM embeddings_mv "
            "WHERE model = ? AND vector_kind = 'docstring' AND symbol_id = ?",
            [(model, sid) for sid in emptied_docstring],
        )
        conn.commit()

    if limit is not None:
        stale = stale[:limit]

    total = len(stale)
    embedded = 0
    now = datetime.now(timezone.utc).isoformat()
    for i in range(0, total, batch_size):
        batch = stale[i : i + batch_size]
        texts = [t for _, _, t, _ in batch]
        blobs, _dim = _embed(texts)
        for (sid, kind, text, chash), blob in zip(batch, blobs):
            dim = len(blob) // 4
            # Same rowid-stable upsert contract as the base table (see the
            # comment in embed_all): preserves rowids so the FR-005 vecmv
            # ANN sync (T019) can key on them like the vec0 tables do.
            conn.execute(
                "INSERT INTO embeddings_mv "
                "(symbol_id, model, vector_kind, dim, vec, chunk, content_hash, embedded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(symbol_id, model, vector_kind) DO UPDATE SET "
                "dim=excluded.dim, vec=excluded.vec, chunk=excluded.chunk, "
                "content_hash=excluded.content_hash, embedded_at=excluded.embedded_at",
                (sid, model, kind, dim, blob, text, chash, now),
            )
        try:
            conn.commit()
            embedded += len(batch)
        except sqlite3.OperationalError as e:
            note_contention("embeddings.mv_batch_flush", error=e)
        if progress:
            progress(min(i + batch_size, total), total)
    return embedded


def reap_orphaned_embeddings(conn: sqlite3.Connection) -> int:
    """Delete embedding rows whose symbol no longer exists.

    Covers both the base ``embeddings`` table and the parallel
    ``embeddings_mv`` multi-vector table (FR-005): an orphaned mv row is the
    same garbage as an orphaned base row, regardless of which pass wrote it,
    so the mv DELETE is unconditional (a no-op when the table is empty, i.e.
    on every default flag-off build). The mv table has no vec0 rows of its
    own yet, so there is nothing index-side to clean here.

    Returns the number of rows removed across both tables. Safe to call any
    time. When the ANN backend is on, the vec0 rows for the reaped embeddings
    are deleted in the SAME transaction: a stale vec0 entry survives keyed on
    a rowid SQLite may later reuse for a different embedding, which would
    pair the ann_query join with an unrelated vector (wrong results, not just
    missing ones). The vec sync itself is a no-op when no vec0 table exists
    for a model.
    """
    from .ann_index import ann_backend_enabled, delete_index_rows

    # Collect the (model, rowid) pairs about to be deleted first -- the bulk
    # DELETE below can't report them, and each rowid must be removed from
    # exactly its own model's vec0 table. Skipped entirely when the ANN
    # backend is off so the reap stays a pure no-op (same single DELETE as
    # before, no extra scan).
    doomed: dict = {}
    if ann_backend_enabled():
        for r in conn.execute(
            "SELECT model, rowid FROM embeddings "
            "WHERE symbol_id NOT IN (SELECT id FROM symbols)"
        ).fetchall():
            doomed.setdefault(r[0], []).append(r[1])

    cur = conn.execute(
        "DELETE FROM embeddings WHERE symbol_id NOT IN (SELECT id FROM symbols)"
    )
    mv_cur = conn.execute(
        "DELETE FROM embeddings_mv WHERE symbol_id NOT IN (SELECT id FROM symbols)"
    )
    for model, rowids in doomed.items():
        delete_index_rows(conn, model, rowids)
    conn.commit()
    reaped = cur.rowcount if cur.rowcount is not None and cur.rowcount > 0 else 0
    reaped_mv = (
        mv_cur.rowcount if mv_cur.rowcount is not None and mv_cur.rowcount > 0 else 0
    )
    return reaped + reaped_mv


def embed_all(
    conn: sqlite3.Connection,
    batch_size: int = 64,
    limit: Optional[int] = None,
    progress=None,
    reap_orphans: bool = True,
    variant: Optional[str] = None,
    multivector: bool = False,
) -> dict:
    """Embed every symbol missing or stale under the current model.

    Idempotent: skips symbols whose stored ``content_hash`` still matches the
    current chunk text. Re-embeds on a model swap (model name change) or on a
    content edit (chunk hash change). Rows with ``content_hash IS NULL`` are
    treated as stale and self-heal.

    ``variant`` selects the chunking recipe (see ``chunk_for_symbol`` /
    ``CHUNK_VARIANTS``). ``None`` (default) resolves via the
    ``CAIRN_CHUNK_VARIANT`` env var exactly as before; an explicit string
    overrides it WITHOUT touching the process environment (D-008
    no-env-mutation doctrine) -- this is the seam per-variant sweep runs
    (T014/T015) use to re-embed the corpus under each recipe.

    ``multivector`` (FR-005, opt-in, default False) additionally populates
    the parallel ``embeddings_mv`` table with the ``name`` and ``docstring``
    kinds, each with its own per-kind ``_chunk_hash`` staleness (see
    ``MV_KINDS`` / ``mv_text_for_kind``). When False -- the default -- the
    run performs ZERO ``embeddings_mv`` writes and the ``embeddings``-table
    flow (upserts, staleness, reaping) is byte-identical to a pre-FR-005
    build (D-006/TC-020). ``limit`` caps stale base rows and stale mv rows
    independently. The summary gains ``mv_embedded`` only when the flag is
    on, so flag-off summaries keep their exact prior shape.

    When ``reap_orphans`` is True (default), also deletes embedding rows for
    symbols that no longer exist. Always refreshes the persisted ``term_df``
    DF table (D-005), so enrichment's IDF signal stays current with the
    embedded corpus. ``progress`` is an optional
    callable(n_done, n_total). Returns a dict summary
    {model, embedded, skipped, total, reaped}.
    """
    _alias_preflight(conn)
    model = current_model()
    # Fetch every column chunk_for_symbol reads, so variant-B/C chunk sections
    # (parameters/return_type/parent_scope/imports_summary/body) are populated.
    all_rows = conn.execute(
        """SELECT s.id, s.name, s.qualified_name, s.kind, s.docstring,
                  s.line_start, s.parameters, s.return_type,
                  s.parent_scope, s.imports_summary, s.body,
                  f.path AS file_path, f.repo_id AS repo,
                  e.content_hash AS existing_hash
           FROM symbols s
           JOIN files f ON s.file_id = f.id
           LEFT JOIN embeddings e ON e.symbol_id = s.id AND e.model = ?
           WHERE s.kind IS NOT NULL
           ORDER BY s.id""",
        (model,),
    ).fetchall()

    # One line of real source per symbol (the declaration line) gives the
    # embedding model actual code, not just an identifier.
    signatures = _signature_lines_for_rows(all_rows)

    # Filter to rows that are missing or whose chunk changed since last embed.
    stale_rows = []
    for r in all_rows:
        chunk = chunk_for_symbol(r, signature=signatures.get(r["id"]), variant=variant)
        if not chunk.strip():
            continue
        new_hash = _chunk_hash(chunk)
        if r["existing_hash"] is None or r["existing_hash"] != new_hash:
            stale_rows.append((r["id"], chunk, new_hash))

    if limit is not None:
        stale_rows = stale_rows[:limit]

    total = len(stale_rows)
    attempted = total
    embedded = 0
    failed_batches = 0
    now = datetime.now(timezone.utc).isoformat()
    for i in range(0, total, batch_size):
        batch = stale_rows[i : i + batch_size]
        texts = [c for _, c, _ in batch]
        blobs, _dim = _embed(texts)
        for (sid, chunk, chash), blob in zip(batch, blobs):
            # Decode dim from the BLOB length so a backend change is detected
            # even if current_model() didn't change.
            dim = len(blob) // 4
            # Rowid-stable upsert: ON CONFLICT ... DO UPDATE preserves the
            # existing rowid. STILL LOAD-BEARING for the vec0 sync: the index
            # keys on embeddings.rowid, and INSERT OR REPLACE (which assigns
            # a NEW rowid) would orphan the old vec0 entry and leave the new
            # row pointing at a vec key that doesn't exist. Bulk rows made
            # here stay unsynced on purpose -- the wholesale rebuild at the
            # end of `cairn embed` realigns the whole table at ~9x lower
            # per-row cost than delete+insert (see ann_index.sync_index_row).
            conn.execute(
                "INSERT INTO embeddings "
                "(symbol_id, model, dim, vec, chunk, content_hash, embedded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(symbol_id, model) DO UPDATE SET "
                "dim=excluded.dim, vec=excluded.vec, chunk=excluded.chunk, "
                "content_hash=excluded.content_hash, embedded_at=excluded.embedded_at",
                (sid, model, dim, blob, chunk, chash, now),
            )
        try:
            conn.commit()
            embedded += len(batch)
        except sqlite3.OperationalError as e:
            note_contention("embeddings.batch_flush", error=e)
            # Lock contention (e.g. daemon holding the WAL) — the batch is
            # buffered in the connection; a later commit or retry will flush it.
            failed_batches += 1
        if progress:
            progress(min(i + batch_size, total), total)

    reaped = reap_orphaned_embeddings(conn) if reap_orphans else 0

    # FR-005 opt-in: after the base flow (so flag-off runs never reach this
    # line), refresh the parallel mv table for the two extra kinds. Reaping
    # already ran above is fine -- it only removes rows for DEAD symbols, and
    # the rows written here are for live ones.
    mv_embedded = (
        _embed_mv_kinds(conn, all_rows, signatures, model, batch_size, limit, progress)
        if multivector
        else None
    )

    # D-005: the DF table's refresh rides the embed pass, so a `cairn embed`
    # --driven build leaves term_df current with the corpus it just embedded.
    rebuild_term_df(conn)

    summary = {
        "model": model,
        "embedded": embedded,
        "attempted": attempted,
        "failed_batches": failed_batches,
        "skipped": len(all_rows) - total,
        "total": len(all_rows),
        "reaped": reaped,
    }
    if multivector:
        summary["mv_embedded"] = mv_embedded
    return summary


def embed_symbols(
    conn: sqlite3.Connection,
    symbol_ids: Sequence[str],
    sync_ann: bool = True,
    variant: Optional[str] = None,
) -> dict:
    """(Re-)embed specific symbols now -- the per-upsert ANN sync seam.

    The targeted counterpart to :func:`embed_all`: one ``_embed`` call for
    every requested symbol, then each upsert keeps the vec0 ANN index in sync
    via ``ann_index.sync_index_row`` INSIDE the same transaction (delete +
    insert by the embeddings rowid; vec0 has no replace semantics). This is
    the seam single-symbol write paths should use so a new/changed symbol is
    visible to ``ann_query`` immediately, without waiting for the next
    wholesale ``cairn embed`` rebuild.

    Bulk passes deliberately do NOT come through here: per-row vec sync runs
    ~27 us/row (delete+insert+commit) vs ~3 us/row for the rebuild's INSERT
    ... SELECT, so ``embed_all`` over thousands of rows keeps its wholesale
    ``rebuild_index`` at the end (spike notes in ``ann_index.sync_index_row``).
    A handful of symbols pays microseconds and avoids the drift outright.

    Idempotent like ``embed_all``: symbols whose stored ``content_hash``
    still matches are skipped, as are empty-chunk symbols; unknown ids are
    dropped silently (nothing to embed). ``variant`` selects the chunking
    recipe exactly as in ``embed_all`` (None = env resolution, explicit
    string overrides without env mutation). Returns ``{model, embedded,
    skipped, ann_synced}`` where
    ``ann_synced`` is the number of rows whose vec0 entry was actually
    written (0 when the backend is off, no index exists yet, or sync
    failed -- each a documented no-op/best-effort, never an error).
    """
    _alias_preflight(conn)
    model = current_model()
    ids = [sid for sid in symbol_ids if sid]
    if not ids:
        return {"model": model, "embedded": 0, "skipped": 0, "ann_synced": 0}

    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""SELECT s.id, s.name, s.qualified_name, s.kind, s.docstring,
                   s.line_start, s.parameters, s.return_type,
                   s.parent_scope, s.imports_summary, s.body,
                   f.path AS file_path, f.repo_id AS repo,
                   e.content_hash AS existing_hash
            FROM symbols s
            JOIN files f ON s.file_id = f.id
            LEFT JOIN embeddings e ON e.symbol_id = s.id AND e.model = ?
            WHERE s.kind IS NOT NULL AND s.id IN ({placeholders})
            ORDER BY s.id""",
        (model, *ids),
    ).fetchall()

    signatures = _signature_lines_for_rows(rows)

    stale_rows = []
    for r in rows:
        chunk = chunk_for_symbol(r, signature=signatures.get(r["id"]), variant=variant)
        if not chunk.strip():
            continue
        new_hash = _chunk_hash(chunk)
        if r["existing_hash"] is None or r["existing_hash"] != new_hash:
            stale_rows.append((r["id"], chunk, new_hash))

    if not stale_rows:
        return {
            "model": model,
            "embedded": 0,
            "skipped": len(rows),
            "ann_synced": 0,
        }

    # One embedder call for the whole batch (the model forward pass, not the
    # SQL, is the expensive part).
    texts = [c for _, c, _ in stale_rows]
    blobs, _dim = _embed(texts)
    now = datetime.now(timezone.utc).isoformat()

    from .ann_index import sync_index_row

    embedded = 0
    ann_synced = 0
    for (sid, chunk, chash), blob in zip(stale_rows, blobs):
        dim = len(blob) // 4
        # Same rowid-preserving upsert contract as embed_all (see the comment
        # there): the vec0 sync below keys on this row's rowid, which ON
        # CONFLICT DO UPDATE keeps stable across re-embeds.
        conn.execute(
            "INSERT INTO embeddings "
            "(symbol_id, model, dim, vec, chunk, content_hash, embedded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(symbol_id, model) DO UPDATE SET "
            "dim=excluded.dim, vec=excluded.vec, chunk=excluded.chunk, "
            "content_hash=excluded.content_hash, embedded_at=excluded.embedded_at",
            (sid, model, dim, blob, chunk, chash, now),
        )
        if sync_ann:
            # lastrowid is unreliable under ON CONFLICT DO UPDATE, so read the
            # rowid back inside the same transaction (own uncommitted writes
            # are visible; the lookup hits the (symbol_id, model) PK).
            row = conn.execute(
                "SELECT rowid FROM embeddings WHERE symbol_id = ? AND model = ?",
                (sid, model),
            ).fetchone()
            if row is not None and sync_index_row(conn, model, row[0], blob):
                ann_synced += 1
    try:
        conn.commit()
        embedded = len(stale_rows)
    except sqlite3.OperationalError as e:
        # Mirrors embed_all's batch flush: lock contention leaves the batch
        # buffered on the connection; a later commit or retry flushes it.
        note_contention("embeddings.embed_symbols", error=e)

    return {
        "model": model,
        "embedded": embedded,
        "skipped": len(rows) - len(stale_rows),
        "ann_synced": ann_synced,
    }


def embed_query(text: str) -> Tuple[bytes, int]:
    """Embed a natural-language query with the current backend.

    Returns (blob, dim); the blob is float32 little-endian, comparable to
    stored rows via cosine similarity.
    """
    blobs, dim = _embed([text])
    return blobs[0], dim


def embed_count(conn: sqlite3.Connection) -> int:
    """Number of embeddings stored under the current model."""
    r = conn.execute(
        "SELECT COUNT(*) AS c FROM embeddings WHERE model = ?", (current_model(),)
    ).fetchone()
    return r["c"] if r else 0


def embed_knowledge(conn, bundle, batch_size=64, progress=None):
    """Embed all knowledge concepts not yet embedded under current model.

    Reads from the OKF bundle (not symbols table). Each concept = one chunk
    (title + description + body).
    """
    _alias_preflight(conn)
    model = current_model(corpus="knowledge")
    # Get all knowledge concept IDs (trailing slash for path-segment matching).
    cids = bundle.list_concepts(prefix="knowledge/")
    if not cids:
        return {"model": model, "embedded": 0, "skipped": 0, "total": 0}

    # Filter to concepts not yet embedded under current model
    existing = {
        r[0] for r in conn.execute(
            "SELECT doc_id FROM knowledge_embeddings WHERE model = ? AND chunk_index = 0",
            (model,),
        ).fetchall()
    }
    to_embed = [cid for cid in cids if cid not in existing]

    total = len(to_embed)
    embedded = 0
    now = datetime.now(timezone.utc).isoformat()

    for i in range(0, total, batch_size):
        batch = to_embed[i:i + batch_size]
        pairs = []
        for cid in batch:
            try:
                concept = bundle.read_concept(cid)
            except Exception:
                continue
            chunk = " ".join(filter(None, [concept.title, concept.description, concept.body]))
            if chunk.strip():
                pairs.append((cid, chunk))
        if not pairs:
            continue
        texts = [c for _, c in pairs]
        blobs, _dim = _embed(texts)  # reuse same dispatch
        for (cid, chunk), blob in zip(pairs, blobs):
            dim = len(blob) // 4
            # Rowid-stable upsert: ON CONFLICT ... DO UPDATE preserves the
            # existing rowid so the vec0 ANN index (keyed on rowid) stays
            # aligned across re-embeds.
            conn.execute(
                "INSERT INTO knowledge_embeddings "
                "(doc_id, chunk_index, model, dim, vec, chunk, embedded_at) "
                "VALUES (?, 0, ?, ?, ?, ?, ?) "
                "ON CONFLICT(doc_id, chunk_index, model) DO UPDATE SET "
                "dim=excluded.dim, vec=excluded.vec, chunk=excluded.chunk, "
                "embedded_at=excluded.embedded_at",
                (cid, model, dim, blob, chunk, now),
            )
        embedded += len(pairs)
        conn.commit()
        if progress:
            progress(min(i + batch_size, total), total)

    return {"model": model, "embedded": embedded, "skipped": total - embedded, "total": total}


def embed_knowledge_count(conn):
    """Count knowledge embeddings under current model."""
    r = conn.execute(
        "SELECT COUNT(*) AS c FROM knowledge_embeddings WHERE model = ?",
        (current_model(corpus="knowledge"),),
    ).fetchone()
    return r["c"] if r else 0


# ---------------------------------------------------------------------------
# Memory embeddings — chunked, keyed by concept_id (see memory_embeddings
# table). Unlike knowledge docs, a memory's concept_id is NOT stable
# (promote/demote/decay move it to a new path with a fresh uuid suffix), so
# rows here can go stale on a tier move; reap_orphaned_memory_embeddings
# cleans those up periodically rather than every write path carrying the old
# row forward.
# ---------------------------------------------------------------------------

_CHUNK_SPLIT_RE = re.compile(r"\n(?=Why:|How to apply:)")
MAX_MEMORY_CHUNKS = 5


def chunk_memory_body(concept) -> List[str]:
    """Split a memory concept into embeddable chunks.

    record_memory's own guidance asks agents to structure a memory body as
    the fact, then a `Why:` line and a `How to apply:` line -- a natural,
    marker-based chunk boundary that needs no NLP. Falls back to blank-line
    paragraph splits for memories that don't follow that structure. The
    title is prepended to the first chunk for context (description is
    skipped: create_memory always sets it equal to title, so including both
    would just duplicate it). Capped at MAX_MEMORY_CHUNKS so a very long body
    can't blow up embedding cost.
    """
    body = concept.body or ""
    parts = [p for p in _CHUNK_SPLIT_RE.split(body) if p.strip()]
    if len(parts) < 2:
        parts = [p for p in body.split("\n\n") if p.strip()]
    if not parts:
        parts = [body]
    header = concept.title or ""
    chunks = []
    for i, part in enumerate(parts[:MAX_MEMORY_CHUNKS]):
        text = f"{header} {part}".strip() if i == 0 else part.strip()
        if text:
            chunks.append(text)
    return chunks or ([header] if header else [])


def embed_memory_concepts(conn: sqlite3.Connection, bundle, concept_ids: Sequence[str]) -> int:
    """(Re-)embed specific memory concepts by concept_id. Returns count embedded.

    Delete+reinsert rather than upsert: a re-embed of an edited memory may
    have a different chunk count than before, so there is no stable
    chunk_index to upsert against.

    Batches the (potentially expensive) ``_embed`` call: all chunks across
    every concept in ``concept_ids`` are embedded in a SINGLE call, then
    sliced back out per concept for the DELETE+INSERT. Read failures are
    still isolated per concept (a deleted/moved concept is skipped before any
    embedding happens), so one bad concept_id can't abort the batch.
    """
    _alias_preflight(conn)
    model = current_model(corpus="memory")
    now = datetime.now(timezone.utc).isoformat()

    # Phase 1: read + chunk each concept, isolating read failures per-cid.
    plan: List[Tuple[str, List[str]]] = []  # (cid, [chunks])
    for cid in concept_ids:
        try:
            concept = bundle.read_concept(cid)
        except Exception:
            continue  # deleted/moved since being queued -- nothing to embed
        chunks = chunk_memory_body(concept)
        if chunks:
            plan.append((cid, chunks))
    if not plan:
        return 0

    # Phase 2: ONE embed call for every chunk across every concept.
    all_chunks = [chunk for _cid, chunks in plan for chunk in chunks]
    blobs, _dim = _embed(all_chunks)

    # Phase 3: slice the flat blob list back out per concept and persist.
    embedded = 0
    offset = 0
    for cid, chunks in plan:
        cid_blobs = blobs[offset:offset + len(chunks)]
        offset += len(chunks)
        conn.execute(
            "DELETE FROM memory_embeddings WHERE doc_id = ? AND model = ?", (cid, model)
        )
        for idx, (chunk, blob) in enumerate(zip(chunks, cid_blobs)):
            dim = len(blob) // 4
            conn.execute(
                "INSERT INTO memory_embeddings "
                "(doc_id, chunk_index, model, dim, vec, chunk, embedded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cid, idx, model, dim, blob, chunk, now),
            )
        embedded += 1
    return embedded


def embed_memory(conn, bundle, batch_size=64, progress=None):
    """Embed all memory concepts not yet embedded under the current model.

    Batch backfill for memories captured before this feature existed, or
    after a model swap. Ongoing capture/evolve calls embed via the buffered
    per-concept path (embed_memory_concepts) instead of waiting for this.
    """
    model = current_model(corpus="memory")
    cids = bundle.list_concepts(prefix="memory/")
    if not cids:
        return {"model": model, "embedded": 0, "skipped": 0, "total": 0}

    existing = {
        r[0] for r in conn.execute(
            "SELECT doc_id FROM memory_embeddings WHERE model = ? AND chunk_index = 0",
            (model,),
        ).fetchall()
    }
    to_embed = [cid for cid in cids if cid not in existing]

    total = len(to_embed)
    embedded = 0
    for i in range(0, total, batch_size):
        batch = to_embed[i:i + batch_size]
        embedded += embed_memory_concepts(conn, bundle, batch)
        conn.commit()
        if progress:
            progress(min(i + batch_size, total), total)

    return {"model": model, "embedded": embedded, "skipped": total - embedded, "total": total}


def embed_memory_count(conn):
    """Count memory embeddings (distinct concepts, chunk_index=0) under current model."""
    r = conn.execute(
        "SELECT COUNT(*) AS c FROM memory_embeddings WHERE model = ? AND chunk_index = 0",
        (current_model(corpus="memory"),),
    ).fetchone()
    return r["c"] if r else 0


def memory_is_embedded(conn: sqlite3.Connection, doc_id: str) -> bool:
    """True if ``doc_id`` has at least one embedding row under the current memory model."""
    r = conn.execute(
        "SELECT 1 FROM memory_embeddings WHERE doc_id = ? AND model = ? LIMIT 1",
        (doc_id, current_model(corpus="memory")),
    ).fetchone()
    return r is not None


def unembedded_memory_hint(conn: sqlite3.Connection, bundle) -> str:
    """One-line footnote for recall/digest output when some memories lack embeddings.

    Returns "" when every memory is embedded (or none exist), so callers can
    append it unconditionally. Compares the persisted embedding count against
    the on-disk memory concept count; the ``list_concepts`` scan is cheap
    because the curated memory corpus stays small. recall/digest use a
    read-only conn, so this never writes -- it only tells the user to run
    ``cairn memory embed`` on the writable side.
    """
    total = len(bundle.list_concepts(prefix="memory/"))
    if total == 0:
        return ""
    embedded = embed_memory_count(conn)
    if embedded < total:
        return (
            f"({total - embedded} of {total} memories not yet embedded -- "
            "run `cairn memory embed` for semantic recall)"
        )
    return ""


def rename_memory_embedding(conn: sqlite3.Connection, old_id: str, new_id: str) -> int:
    """Move a memory's embedding row(s) from ``old_id`` to ``new_id`` in place.

    Used by promote/demote, which move a memory to a new concept_id WITHOUT
    changing its content: renaming the persisted embedding avoids re-running
    the embedder on unchanged text (and the orphan+re-embed it would otherwise
    leave behind). Rows for ALL models are moved so a stale prior-model row
    travels too (harmless -- reads are model-scoped). Does NOT commit; the
    caller owns the transaction boundary. Returns rows moved (0 when the
    memory had no embedding yet, in which case the caller should embed at
    ``new_id`` instead).
    """
    cur = conn.execute(
        "UPDATE memory_embeddings SET doc_id = ? WHERE doc_id = ?",
        (new_id, old_id),
    )
    return cur.rowcount if cur.rowcount is not None and cur.rowcount > 0 else 0


def reap_orphaned_memory_embeddings(conn: sqlite3.Connection, bundle) -> int:
    """Delete memory_embeddings rows whose concept no longer resolves in the bundle.

    Covers rows left behind by promote/demote/decay tier moves (which change
    a memory's concept_id without carrying its embedding forward). Safe to
    call any time; best-effort like the rest of the embedding pipeline.
    """
    doc_ids = {
        r[0] for r in conn.execute("SELECT DISTINCT doc_id FROM memory_embeddings").fetchall()
    }
    orphaned = [cid for cid in doc_ids if not _concept_exists(bundle, cid)]
    if not orphaned:
        return 0
    placeholders = ",".join("?" for _ in orphaned)
    cur = conn.execute(
        f"DELETE FROM memory_embeddings WHERE doc_id IN ({placeholders})", orphaned
    )
    conn.commit()
    return cur.rowcount if cur.rowcount is not None and cur.rowcount > 0 else 0


def _concept_exists(bundle, concept_id: str) -> bool:
    try:
        bundle.read_concept(concept_id)
        return True
    except Exception:
        return False
