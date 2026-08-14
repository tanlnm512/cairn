"""Semantic embeddings for the symbol corpus: build, store, and query dense
vector representations of symbols so agents can find code by meaning.

Backend selection is env-var driven via ``CAIRN_EMBED_BACKEND``:
``local`` (default, sentence-transformers), ``hash`` (dep-free fallback), or
``openai`` (opt-in API). ``embeddings_available()`` reports whether a real
backend is wired so callers degrade with an install hint.
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

from .schema import note_contention

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
    """
    backend = _effective_backend()
    if backend == "hash":
        return HASH_MODEL
    if backend == "openai":
        return os.environ.get("CAIRN_EMBED_OPENAI_MODEL", "text-embedding-3-small")
    env_name = _CORPUS_MODEL_ENV.get(corpus)
    if env_name:
        corpus_model = os.environ.get(env_name)
        if corpus_model:
            return corpus_model.strip()
    return (os.environ.get("CAIRN_EMBED_LOCAL_MODEL") or DEFAULT_LOCAL_MODEL).strip()


# ---------------------------------------------------------------------------
# Availability — callers gate on this to degrade cleanly.
# ---------------------------------------------------------------------------


def embeddings_available() -> bool:
    """True iff an embedding backend can be loaded right now.

    The default 'local' backend falls back to the hash embedder when
    sentence_transformers is missing. Returns False only when openai is
    selected but OPENAI_API_KEY is missing.
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
        "Install it with: pip install 'cairn-intel[semantic]' "
        "(or set CAIRN_EMBED_BACKEND=hash for a dep-free smoke test)."
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
    """Build the embedding chunk for one symbol.

    Variants: A (kind + name + first signature line), B (A + docstring +
    parameters + return_type + full signature), C (B + body + context).
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


# ---------------------------------------------------------------------------
# Backend abstraction — local (sentence-transformers) / hash / openai.
# Each backend exposes _embed(texts) -> List[bytes] (float32 BLOBs).
# ---------------------------------------------------------------------------


def _backend_name() -> str:
    return (os.environ.get("CAIRN_EMBED_BACKEND") or "local").strip().lower()


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


def reset_backend_cache() -> None:
    """Clear the cached effective-backend resolution.

    Call this in test setup/teardown whenever CAIRN_EMBED_BACKEND is changed,
    since the cache is never invalidated mid-process.
    """
    _EFFECTIVE_BACKEND_CACHE["effective"] = None


def _effective_backend() -> str:
    """The backend actually used for embedding (after fallback resolution).

    When CAIRN_EMBED_BACKEND is unset (default 'local') but
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
    """Download model weights into the local HuggingFace cache if not present."""
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

    ``package_names`` should be the top-level package names (e.g.
    ``["sentence_transformers", "sqlite_vec"]``), **not** pip specifiers.
    """
    import sys

    for name in package_names:
        # Normalise pip specifier "sentence-transformers" -> import name.
        key = name.replace("-", "_")
        sys.modules.pop(key, None)
        to_drop = [k for k in sys.modules if k == key or k.startswith(key + ".")]
        for k in to_drop:
            sys.modules.pop(k, None)


def _run_install_with_progress(cmd: list[str], lib_dir) -> None:
    """Run a pip/uv install subprocess with a single-line progress indicator."""
    import subprocess
    import time

    from ..cli.display import progress_bar

    print(f"Installing semantic deps into {lib_dir} (one-time, ~hundreds of MB via torch)...")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # Drain stdout in the loop: pip writes progress to the combined pipe, and
    # if output exceeds the OS pipe buffer (~64 KB) pip blocks on write.
    output_lines = []
    with progress_bar("Installing semantic deps", total=None, unit="") as bar:
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


def ensure_semantic_deps(auto_install: bool = True) -> bool:
    """Ensure sentence-transformers is installed.

    If missing and ``auto_install=True``, installs the dependency into the
    shared lib directory (``~/.cairn/lib``), which survives reinstalls.
    Model-weight downloading is handled separately by ``download_model``.
    """
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        if not auto_install:
            return False

    import importlib.util
    import shutil
    import sys

    from ..paths import shared_lib_path

    lib_dir = shared_lib_path()
    packages = ["sentence-transformers", "numpy", "sqlite-vec"]
    try:
        if importlib.util.find_spec("pip") is not None:
            # pip install --target writes into the shared lib dir, which is
            # prepended to sys.path at import time (paths.py).
            lib_dir.mkdir(parents=True, exist_ok=True)
            _run_install_with_progress(
                [sys.executable, "-m", "pip", "install", "--target", str(lib_dir), *packages],
                lib_dir,
            )
        else:
            # No pip in this interpreter (uv tool env); use uv pip install.
            uv = shutil.which("uv")
            if not uv:
                raise RuntimeError(
                    "no 'pip' module in this interpreter and 'uv' not found on PATH -- "
                    "install pip or run: uv pip install --target "
                    f"{lib_dir} sentence-transformers numpy sqlite-vec"
                )
            _run_install_with_progress(
                [uv, "pip", "install", "--target", str(lib_dir), *packages],
                lib_dir,
            )
        # Verify the import resolves now from the shared lib dir.
        _clear_import_cache(packages)
        # Re-add the lib dir to sys.path in case paths.py ran before it existed.
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

    tables = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'vec_%'").fetchall()
    # Resolve the active ANN table name once so an ImportError surfaces loudly
    # rather than being swallowed per-iteration.
    from .ann_index import _table_name as ann_table_name
    for (tname,) in tables:
        if tname != ann_table_name(target_model):
            cur.execute(f"DROP TABLE IF EXISTS {tname}")

    conn.commit()
    return c1 + c2 + c3


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


def reap_orphaned_embeddings(conn: sqlite3.Connection) -> int:
    """Delete embedding rows whose symbol no longer exists.

    Returns the number of rows removed. Safe to call any time.
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
    current chunk text. Re-embeds on a model swap (model name change) or on a
    content edit (chunk hash change). Rows with ``content_hash IS NULL`` are
    treated as stale and self-heal.

    When ``reap_orphans`` is True (default), also deletes embedding rows for
    symbols that no longer exist. ``progress`` is an optional
    callable(n_done, n_total). Returns a dict summary
    {model, embedded, skipped, total, reaped}.
    """
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
            # existing rowid. The vec0 ANN index keys on embeddings.rowid, so
            # INSERT OR REPLACE (which assigns a NEW rowid) would misalign the
            # index until rebuild_index runs. New symbols still require a
            # rebuild; existing symbols stay correctly keyed.
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
