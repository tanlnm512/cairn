"""Semantic embeddings for the symbol corpus.

Builds, stores, and queries dense vector representations of symbols so agents
can find code by *meaning* (synonyms, paraphrase, cross-language concepts)
rather than just by exact tokens (FTS5) or graph edges (resolver).

Three layers, each independently useful:

* **Chunking** — ``chunk_for_symbol`` builds the text fed to the model:
  ``"{kind} {qualified_name}\\n{signature}\\n{docstring}"``. Reuses the spans
  the builder already stored; no extra parsing.
* **Embedding** — ``embed_all`` (corpus) and ``embed_query`` (query) call a
  backend model to produce float32 vectors, stored as BLOBs in the
  ``embeddings`` table. Idempotent: skips symbols already embedded under the
  current model.
* **Backend selection** — env-var driven, mirroring ``src/llm/client.py``:
    - ``CODEGRAPH_EMBED_BACKEND`` unset / ``local``  → sentence-transformers
      (default; preserves the local-only promise)
    - ``CODEGRAPH_EMBED_BACKEND=hash``               → deterministic hash
      embedder (no deps; for tests/offline smoke checks; low quality)
    - ``CODEGRAPH_EMBED_BACKEND=openai``             → OpenAI API (opt-in;
      needs OPENAI_API_KEY; source text leaves the machine)

The default install (no ``[semantic]`` extra) has neither torch nor numpy.
``embeddings_available()`` reports whether a real backend is wired; callers
(MCP tool, CLI) use it to degrade with an install hint instead of crashing.
The cosine scan in ``queries.semantic_search`` is pure-Python by default and
uses numpy when present (transparent ~50x speedup at scale).
"""
from __future__ import annotations

import hashlib
import math
import os
import sqlite3
import struct
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Model identity. Stored per-row so a model swap invalidates and re-embeds
# (same invalidation pattern as the FTS5 rebuild in schema.py:get_db). Bumping
# this string forces embed_all to re-embed every symbol on the next run.
# ---------------------------------------------------------------------------

DEFAULT_LOCAL_MODEL = "BAAI/bge-m3"
HASH_MODEL = "hash-256-v1"  # deterministic fallback; tests + offline smoke
DEFAULT_DIM = 256           # dimensionality of the hash fallback embedder


def current_model(corpus: str = "code") -> str:
    """The model name rows are stamped with for the effective backend.

    Uses _effective_backend() so that when 'local' falls back to 'hash',
    the stamp says 'hash-256-v1' (not the sentence-transformers model name),
    and openai stamps its own model id. This ensures consistency: rows
    embedded with one backend are re-embedded if a different backend later
    becomes active.

    ``corpus`` selects between the code corpus (default) and the knowledge
    corpus, which can be pinned to a different local model via
    CODEGRAPH_EMBED_KNOWLEDGE_MODEL (falls back to CODEGRAPH_EMBED_LOCAL_MODEL).
    Only applies to the local backend -- hash and openai stamps don't vary by
    corpus.
    """
    backend = _effective_backend()
    if backend == "hash":
        return HASH_MODEL
    if backend == "openai":
        return os.environ.get("CODEGRAPH_EMBED_OPENAI_MODEL", "text-embedding-3-small")
    if corpus == "knowledge":
        kn_model = os.environ.get("CODEGRAPH_EMBED_KNOWLEDGE_MODEL")
        if kn_model:
            return kn_model.strip()
    return (os.environ.get("CODEGRAPH_EMBED_LOCAL_MODEL") or DEFAULT_LOCAL_MODEL).strip()


# ---------------------------------------------------------------------------
# Availability — callers gate on this to degrade cleanly.
# ---------------------------------------------------------------------------


def embeddings_available() -> bool:
    """True iff an embedding backend can be loaded right now.

    Checks the configured backend (via CODEGRAPH_EMBED_BACKEND). When the
    default 'local' backend isn't available (no sentence_transformers),
    falls back to the hash embedder so semantic search works out of the box
    with token-overlap quality. Callers that need to know whether the result
    is *truly* semantic can check ``_effective_backend()``.

    Returns False only when openai is selected but OPENAI_API_KEY is missing,
    or when the configured backend explicitly fails to load.
    """
    backend = _backend_name()
    if backend == "hash":
        return True
    if backend == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
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
        "Install it with: pip install 'cg-intel[semantic]' "
        "(or set CODEGRAPH_EMBED_BACKEND=hash for a dep-free smoke test)."
    )


# ---------------------------------------------------------------------------
# Chunking — build the text that gets embedded.
# ---------------------------------------------------------------------------


def chunk_for_symbol(
    row: sqlite3.Row,
    signature: Optional[str] = None,
    variant: Optional[str] = None,
    max_tokens: int = 512,
) -> str:
    """Build the embedding chunk for one symbol, supporting variants A, B, and C.

    Variant A: kind + qualified_name + first signature line
    Variant B: A + docstring + parameters + return_type + full signature
    Variant C: B + body + context re-add (enclosing class + file imports)
    """
    v = (variant or os.environ.get("CODEGRAPH_CHUNK_VARIANT", "B")).upper()
    kind = (row["kind"] or "").strip() if row["kind"] is not None else ""
    qname = (row["qualified_name"] or row["name"] or "").strip()
    doc = (row["docstring"] or "").strip() if "docstring" in row.keys() else ""
    sig = (signature or "").strip()

    params_raw = row["parameters"] if "parameters" in row.keys() and row["parameters"] else None
    ret_type = row["return_type"] if "return_type" in row.keys() and row["return_type"] else None

    file_path = row["file_path"] if "file_path" in row.keys() and row["file_path"] else None
    parent_scope = row["parent_scope"] if "parent_scope" in row.keys() and row["parent_scope"] else None
    imports_summary = row["imports_summary"] if "imports_summary" in row.keys() and row["imports_summary"] else None

    scope_header = []
    if file_path:
        scope_header.append(f"File: {file_path}")
    if parent_scope:
        scope_header.append(f"Enclosing Scope: {parent_scope}")
    if imports_summary:
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
    elif v in ("B", "C"):
        if sig and sig != header:
            parts.append(f"Signature: {sig}")
        if params_raw:
            parts.append(f"Parameters: {params_raw}")
        if ret_type:
            parts.append(f"Return Type: {ret_type}")
        if doc:
            parts.append(f"Docstring: {doc}")

        if v == "C" and "body" in row.keys() and row["body"]:
            parts.append(f"Body:\n{row['body']}")

    res = "\n".join(parts) if parts else qname or kind
    # Simple character truncation approximation for max_tokens (approx 4 chars per token)
    max_chars = max_tokens * 4
    if len(res) > max_chars:
        res = res[:max_chars]
    return res


def _signature_lines_for_rows(rows: Sequence[sqlite3.Row]) -> dict:
    """Read each symbol's declaration line from disk, grouped by file.

    Reads only ``line_start`` (one line per symbol), not the full body —
    cheap enough to do for the whole corpus on every ``embed_all`` run.
    Mirrors ``queries._read_source_spans``'s file-grouping/graceful-degrade
    pattern: a missing/moved file or a symbol with no file_path/line_start
    just gets no signature (chunk_for_symbol falls back to kind+qname+doc),
    never raises. Returns ``{symbol_id: signature_line}``.
    """
    by_file: dict = {}
    for r in rows:
        path = r["file_path"] if "file_path" in r.keys() else None
        ls = r["line_start"] if "line_start" in r.keys() else None
        if not path or not ls or ls < 1:
            continue
        by_file.setdefault(path, []).append((r["id"], ls))

    out: dict = {}
    for path, entries in by_file.items():
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                all_lines = fh.readlines()
        except OSError:
            continue  # file deleted/moved since index — skip silently
        for sid, ls in entries:
            if ls - 1 < len(all_lines):
                out[sid] = all_lines[ls - 1].strip()
    return out


# ---------------------------------------------------------------------------
# Backend abstraction — local (sentence-transformers) / hash / openai.
# Each backend exposes _embed(texts) -> List[bytes] (float32 BLOBs).
# ---------------------------------------------------------------------------


def _backend_name() -> str:
    return (os.environ.get("CODEGRAPH_EMBED_BACKEND") or "local").strip().lower()


# Cache the loaded model so repeated calls (embed_all batches, embed_query)
# don't reload weights on every invocation.
_MODEL_CACHE: dict = {}

# Cache for the effective backend after fallback resolution. Set once per
# process by embeddings_available() so _embed() can consult it without
# re-checking imports on every call.
_EFFECTIVE_BACKEND_CACHE: dict = {"effective": None}


def reset_backend_cache() -> None:
    """Clear the cached effective-backend resolution.

    _EFFECTIVE_BACKEND_CACHE is set once per process and never invalidated,
    which is fine in production (the backend doesn't change mid-process) but
    means tests that flip CODEGRAPH_EMBED_BACKEND between cases can observe a
    stale cached backend from an earlier test. Call this in test
    setup/teardown whenever the env var is changed.
    """
    _EFFECTIVE_BACKEND_CACHE["effective"] = None


def _effective_backend() -> str:
    """The backend actually used for embedding (after fallback resolution).

    When CODEGRAPH_EMBED_BACKEND is unset (default 'local') but
    sentence_transformers isn't installed, falls back to 'hash'.
    Otherwise returns the configured backend unchanged.
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
        _EFFECTIVE_BACKEND_CACHE["effective"] = backend
    return _EFFECTIVE_BACKEND_CACHE["effective"]


def model_is_cached(model_name: Optional[str] = None) -> bool:
    """Check whether the model weights are present in the local HuggingFace cache.

    Uses ``huggingface_hub.try_to_load_from_cache`` (bundled with
    sentence-transformers) to probe the cache without triggering a download.
    Returns True when the model's ``config.json`` snapshot is found locally.
    """
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False

    m_name = model_name or current_model()
    # try_to_load_from_cache returns a HubCacheHitInfo on success or None.
    result = try_to_load_from_cache(m_name, "config.json")
    return result is not None


def download_model(model_name: Optional[str] = None) -> bool:
    """Download model weights into the local HuggingFace cache.

    Checks the cache first; if the model is already present, reports it and
    returns True without re-downloading. Otherwise loads the model (which
    triggers the download) and returns True on success.

    Requires ``sentence_transformers`` (and transitively ``huggingface_hub``).
    """
    m_name = model_name or current_model()
    if model_is_cached(m_name):
        print(f"Model '{m_name}' is already cached — skipping download.")
        return True

    try:
        print(f"Downloading '{m_name}' model weights into local cache...")
        _get_local_model(m_name)
        print(f"Model '{m_name}' downloaded successfully.")
        return True
    except Exception as exc:
        print(f"Failed to download model '{m_name}': {exc}")
        return False


def _clear_import_cache(package_names: list[str]) -> None:
    """Remove cached import failures from sys.modules after a subprocess install.

    When a top-level package fails to import, Python may leave a partial entry
    in ``sys.modules`` (or cache the negative result via importlib).  After a
    subprocess ``pip install`` puts the package on disk, we must clear those
    entries so the current process can ``import`` it without a restart.

    ``package_names`` should be the top-level package names (e.g.
    ``["sentence_transformers", "sqlite_vec"]``), **not** pip specifiers.
    """
    import sys

    for name in package_names:
        # Normalise pip specifier "sentence-transformers" → import name
        # "sentence_transformers" (replace hyphens with underscores).
        key = name.replace("-", "_")
        sys.modules.pop(key, None)
        # Also clear any submodules that may have been partially loaded.
        to_drop = [k for k in sys.modules if k == key or k.startswith(key + ".")]
        for k in to_drop:
            sys.modules.pop(k, None)


def ensure_semantic_deps(auto_install: bool = True) -> bool:
    """Ensure sentence-transformers is installed.

    If sentence-transformers is missing and auto_install=True, automatically
    installs the dependency into the shared lib directory
    (``~/.codegraph/lib``), which survives ``uv tool install --force``
    reinstalls. The deps are downloaded once and reused across sessions and
    tool reinstalls. Model-weight downloading is handled separately by
    ``download_model`` so callers can control the two concerns independently.
    """
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        if not auto_install:
            return False

    import importlib.util
    import shutil
    import subprocess
    import sys

    from ..paths import shared_lib_path

    lib_dir = shared_lib_path()
    print(f"sentence-transformers not found. Installing semantic deps into {lib_dir} (one-time)...")
    packages = ["sentence-transformers", "numpy", "sqlite-vec"]
    try:
        if importlib.util.find_spec("pip") is not None:
            # pip install --target writes into the shared lib dir, which is
            # prepended to sys.path at import time (paths.py). This keeps the
            # deps OUTSIDE the tool's own venv so they survive reinstalls.
            lib_dir.mkdir(parents=True, exist_ok=True)
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--target", str(lib_dir), *packages]
            )
        else:
            # No pip in this interpreter (uv tool env). Use uv pip install
            # --target into the same shared dir.
            uv = shutil.which("uv")
            if not uv:
                raise RuntimeError(
                    "no 'pip' module in this interpreter and 'uv' not found on PATH -- "
                    "install pip or run: uv pip install --target "
                    f"{lib_dir} sentence-transformers numpy sqlite-vec"
                )
            print(f"No pip in this interpreter; installing via uv into {lib_dir}...")
            lib_dir.mkdir(parents=True, exist_ok=True)
            subprocess.check_call(
                [uv, "pip", "install", "--target", str(lib_dir), *packages]
            )
        # Verify the import resolves now (from the shared lib dir, which
        # paths.py already added to sys.path at startup).
        _clear_import_cache(packages)
        # Re-add the lib dir to sys.path in case this process's paths.py ran
        # before the dir existed (the import-time injection only fires once).
        if str(lib_dir) not in sys.path:
            sys.path.insert(0, str(lib_dir))
        try:
            import sentence_transformers  # noqa: F401
        except ImportError as imp:
            raise RuntimeError(
                "install reported success but `sentence_transformers` still won't import "
                f"({imp}). The shared lib dir is {lib_dir}; ensure it's on sys.path."
            ) from imp
        reset_backend_cache()
        _EFFECTIVE_BACKEND_CACHE["effective"] = "local"
        return True
    except Exception as exc:
        print(f"Failed to auto-install dependencies: {exc}")
        return False


def _get_local_model(model_name: Optional[str] = None):
    """Lazily load the sentence-transformers model (cached per process)."""
    m_name = model_name or current_model()
    key = ("local", m_name)
    if key not in _MODEL_CACHE:
        from sentence_transformers import SentenceTransformer

        trust = os.environ.get("CODEGRAPH_EMBED_TRUST_REMOTE_CODE") == "1"
        kwargs = {"trust_remote_code": trust}
        if os.environ.get("CODEGRAPH_EMBED_FP16") == "1":
            kwargs["model_kwargs"] = {"torch_dtype": "float16"}
        model = SentenceTransformer(m_name, **kwargs)
        max_len = os.environ.get("CODEGRAPH_EMBED_MAX_SEQ_LEN", "512")
        if max_len:
            model.max_seq_length = int(max_len)
        # Single-model cache: a key change means the previous model's tensors
        # are stale, so evict any other entry rather than letting it leak.
        if _MODEL_CACHE and next(iter(_MODEL_CACHE)) != key:
            _MODEL_CACHE.clear()
        _MODEL_CACHE[key] = model
    return _MODEL_CACHE[key]


def purge_stale_models(conn: sqlite3.Connection, active_model: Optional[str] = None) -> int:
    """Purge vectors and tables for all retired/superseded embedding models."""
    target_model = active_model or current_model()
    cur = conn.cursor()

    c1 = cur.execute("DELETE FROM embeddings WHERE model != ?", (target_model,)).rowcount
    try:
        c2 = cur.execute("DELETE FROM knowledge_embeddings WHERE model != ?", (target_model,)).rowcount
    except Exception:
        c2 = 0

    tables = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'vec_%'").fetchall()
    for (tname,) in tables:
        try:
            from .ann_index import ann_table_name
            if tname != ann_table_name(target_model):
                cur.execute(f"DROP TABLE IF EXISTS {tname}")
        except Exception:
            pass

    conn.commit()
    return c1 + c2


def _embed_local(texts: Sequence[str]) -> Tuple[List[bytes], int]:
    model = _get_local_model()
    # sentence-transformers returns a (n, dim) numpy array. Convert each row to
    # a float32 little-endian BLOB for storage. normalize_embeddings=True makes
    # cosine similarity a plain dot product (cheaper at query time).
    vecs = model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
    dim = int(vecs.shape[1])
    blobs = [_vec_to_blob(vecs[i]) for i in range(len(texts))]
    return blobs, dim


def _embed_openai(texts: Sequence[str]) -> Tuple[List[bytes], int]:
    import urllib.request
    import json

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("CODEGRAPH_EMBED_BACKEND=openai requires OPENAI_API_KEY")
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


# --- Hash fallback embedder (no deps; deterministic; low quality) ----------
#
# Maps a text to a fixed-size float32 vector via SHA-256 hashing: each of the
# `dim` output dimensions is seeded by a slice of the hash digest, and a handful
# of orthogonal hashes are averaged to spread signal across dimensions. This is
# NOT a real semantic embedding — two unrelated strings may collide — but it is
# deterministic and dependency-free, which lets the wiring (table, cosine scan,
# MCP tool, CLI, fallback) be tested end-to-end without torch. Real users get
# sentence-transformers; tests and offline smoke checks use this.


def _hash_vec(text: str, dim: int = DEFAULT_DIM) -> List[float]:
    """Deterministic hash-based pseudo-embedding.

    Produces a unit-norm vector of `dim` floats. Tokenizes on non-alphanumeric
    boundaries (mirroring FTS5 unicode61) so the same token contributes the
    same signal regardless of position — the only semantic-ish property of this
    fallback. Unrelated texts are largely orthogonal; identical tokens overlap.
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
# Corpus embedding — the `cg embed` batch pass.
# ---------------------------------------------------------------------------


def _chunk_hash(chunk: str) -> str:
    """Stable content hash for a chunk, used to detect edits under a fixed model."""
    return hashlib.sha256(chunk.encode("utf-8")).hexdigest()


def reap_orphaned_embeddings(conn: sqlite3.Connection) -> int:
    """Delete embedding rows whose symbol no longer exists.

    A symbol deleted or renamed (new id) by a reindex leaves its old embedding
    row behind forever under the "only embed if missing" model -- it never
    surfaces as wrong in isolation, but it can still be returned by
    semantic_search as a stale, misleading hit. Safe to call any time; cheap
    (single DELETE) relative to the embed batch it typically follows.
    Returns the number of rows removed.
    """
    cur = conn.execute(
        "DELETE FROM embeddings WHERE symbol_id NOT IN (SELECT id FROM symbols)"
    )
    conn.commit()
    return cur.rowcount if cur.rowcount is not None and cur.rowcount > 0 else 0


def embed_all(
    conn: sqlite3.Connection,
    batch_size: int = 64,
    limit: Optional[int] = None,
    progress=None,
    reap_orphans: bool = True,
) -> dict:
    """Embed every symbol missing or stale under the current model.

    Idempotent: skips symbols whose stored ``content_hash`` still matches the
    current chunk text. Safe to re-run (the ``cg embed`` command). Two
    independent invalidation triggers, both handled here:

    1. Model swap -- switching ``CODEGRAPH_EMBED_BACKEND``/model name changes
       ``current_model()``, so every symbol looks unembedded under the new
       stamp (unchanged from before).
    2. Content edit -- a docstring/signature change under the *same* model
       now also triggers re-embedding, because the freshly computed chunk
       hash no longer matches the row's stored ``content_hash``. Before this,
       only trigger 1 existed, so an edited symbol's embedding went stale
       silently until someone thought to bump the model name.

    Rows written before the ``content_hash`` column existed have
    ``content_hash IS NULL`` and are treated as stale, so they self-heal on
    the next run instead of needing a backfill pass.

    When ``reap_orphans`` is True (default), also deletes embedding rows for
    symbols that no longer exist -- see ``reap_orphaned_embeddings``.

    ``progress`` is an optional callable(n_done, n_total) for CLI progress
    reporting. Returns a dict summary {model, embedded, skipped, total, reaped}.
    """
    model = current_model()
    # Fetch every symbol column chunk_for_symbol reads behind `if "X" in
    # row.keys()` guards, so the variant-B/C chunk sections are actually
    # populated: parameters/return_type ("Parameters:"/"Return Type:") and
    # parent_scope/imports_summary/body ("Enclosing Scope:"/"Imports:"/"Body:").
    # All five are additive TEXT columns on `symbols` (see schema.py
    # SYMBOL_*_MIGRATION constants); None when no data was written, in which
    # case chunk_for_symbol omits that section.
    all_rows = conn.execute(
        """SELECT s.id, s.name, s.qualified_name, s.kind, s.docstring,
                  s.line_start, s.parameters, s.return_type,
                  s.parent_scope, s.imports_summary, s.body,
                  f.path AS file_path,
                  e.content_hash AS existing_hash
           FROM symbols s
           JOIN files f ON s.file_id = f.id
           LEFT JOIN embeddings e ON e.symbol_id = s.id AND e.model = ?
           WHERE s.kind IS NOT NULL
           ORDER BY s.id""",
        (model,),
    ).fetchall()

    # One line of real source per symbol (the declaration line) gives the
    # embedding model actual code, not just an identifier -- most symbols here
    # have no docstring. Read once per file up front; missing files degrade
    # to no signature (chunk_for_symbol falls back to kind+qname+doc).
    signatures = _signature_lines_for_rows(all_rows)

    # Filter to rows that are missing or whose chunk changed since last embed.
    # This is a full scan of the symbol table each run (not just unembedded
    # rows), which is the cost of catching content edits under a stable model
    # name -- acceptable at codegraph's scale (chunking is cheap; only the
    # actual model call is expensive, and that's still skipped for fresh rows).
    stale_rows = []
    for r in all_rows:
        chunk = chunk_for_symbol(r, signature=signatures.get(r["id"]))
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
            # existing rowid, unlike INSERT OR REPLACE (which deletes + reinserts
            # and can assign a NEW rowid). The vec0 ANN index keys on
            # embeddings.rowid, so a non-stable re-embed would make ann_query's
            # `JOIN embeddings e ON e.rowid = v.rowid` resolve to the wrong
            # symbol until rebuild_index runs. New symbols still require a
            # rebuild (handled by the CLI after embed_all); existing symbols
            # stay correctly keyed.
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
            # Only increment embedded after successful commit
            embedded += len(batch)
        except sqlite3.OperationalError:
            # Lock contention (e.g. daemon holding the WAL) — the batch is
            # buffered in the connection; a later commit or a retry via
            # `cg embed` will flush it. Don't crash the whole embed run.
            failed_batches += 1
        if progress:
            progress(min(i + batch_size, total), total)

    reaped = reap_orphaned_embeddings(conn) if reap_orphans else 0

    return {
        "model": model,
        "embedded": embedded,
        "attempted": attempted,
        "failed_batches": failed_batches,
        "skipped": len(all_rows) - total,
        "total": len(all_rows),
        "reaped": reaped,
    }


def embed_query(text: str) -> Tuple[bytes, int]:
    """Embed a natural-language query with the current backend.

    Returns (blob, dim). The blob is float32 little-endian, directly
    comparable to stored rows via cosine similarity in queries.semantic_search.
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

    Mirrors embed_all() but reads from the OKF bundle (not symbols table).
    Each concept = one chunk (title + description + body). Docs are <2KB.
    """
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
            # existing rowid, unlike INSERT OR REPLACE (which deletes +
            # reinserts and can assign a NEW rowid). Mirrors the symbol
            # embeddings path so a vec0 ANN index (keyed on rowid) stays
            # correctly aligned across re-embeds. See the symbol-embeddings
            # upsert above for the same discipline.
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
