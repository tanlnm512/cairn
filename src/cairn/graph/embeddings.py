"""Semantic embeddings for the symbol corpus: build, store, and query dense vector representations of symbols so agents can find code by meaning."""
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

from .embed_backends import (
    CallableEmbeddingBackend,
    register_embedding_backend,
    resolve_effective_backend,
    resolve_embedding_backend,
)
from .schema import note_contention, rebuild_term_df

# Model identity is stored per row so producer swaps invalidate stale embeddings.

DEFAULT_LOCAL_MODEL = "BAAI/bge-m3"
HASH_MODEL = "hash-256-v1"  # deterministic fallback; tests + offline smoke
DEFAULT_DIM = 256           # dimensionality of the hash fallback embedder


_CORPUS_MODEL_ENV = {
    "knowledge": "CAIRN_EMBED_KNOWLEDGE_MODEL",
    "memory": "CAIRN_EMBED_MEMORY_MODEL",
}


def current_model(corpus: str = "code") -> str:
    """Return the backend stamp that scopes embedding staleness and vec0 tables."""
    return resolve_embedding_backend(_effective_backend()).model(corpus)


def _model_hash(_corpus: str) -> str:
    return HASH_MODEL


def _model_openai(_corpus: str) -> str:
    return os.environ.get("CAIRN_EMBED_OPENAI_MODEL", "text-embedding-3-small")


def _model_server(_corpus: str) -> str:
    override = _config_or_env("CAIRN_EMBED_MODEL_STAMP")
    if override:
        return override
    with _BACKEND_CACHE_LOCK:
        session_stamp = _SESSION_STAMP_OVERRIDE
    if session_stamp:
        return session_stamp
    # urlsplit keeps the bracket pair on IPv6 netlocs ([::1]:8000); the
    # stamp -- and every vec0 table name derived from it -- drops them.
    netloc = urlsplit(_server_base_url()).netloc
    netloc = netloc.replace("[", "").replace("]", "")
    return f"server/{netloc}/{_server_model()}"


def _model_local(corpus: str) -> str:
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
    """Return whether the configured embedding backend can currently serve requests."""
    backend = resolve_embedding_backend(backend_name())
    available = backend.available()
    fallback = backend.fallback_name
    if not available and fallback is not None:
        with _BACKEND_CACHE_LOCK:
            _EFFECTIVE_BACKEND_CACHE["effective"] = fallback
        return True
    return available


def _available_openai() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


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


# Every chunk variant retains the identity floor enforced by tests.
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
    """Return one symbol's chunk using the selected ablation variant."""
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
    # File: line itself always stays (identity floor). A/B/C never take this
    # branch, so their output is unaffected by this flag.
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
    """Return declaration lines keyed by symbol id, omitting unreadable symbols."""
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


# Multi-vector kinds track staleness independently of CHUNK_VARIANTS.
MV_KINDS = ("name", "docstring")


def name_text_for_symbol(row: sqlite3.Row, signature: Optional[str] = None) -> str:
    """Return the name-only multi-vector text for a symbol."""
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
    """Return a symbol's stripped docstring, or an empty string."""
    doc = row["docstring"] if "docstring" in row.keys() else None
    return (doc or "").strip()


def mv_text_for_kind(
    row: sqlite3.Row, vector_kind: str, signature: Optional[str] = None
) -> str:
    """Return one multi-vector text for a symbol and kind, raising ValueError otherwise."""
    if vector_kind == "name":
        return name_text_for_symbol(row, signature=signature)
    if vector_kind == "docstring":
        return docstring_text_for_symbol(row)
    raise ValueError(f"unknown vector_kind: {vector_kind!r}")


def _config_or_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Resolve an embedding knob from environment, config file, then default."""
    from ..paths import get_config_value

    env = (os.environ.get(name) or "").strip()
    if env:
        return env
    file_val = get_config_value(name)
    if isinstance(file_val, str) and file_val.strip():
        return file_val.strip()
    return default


def backend_name() -> str:
    return (_config_or_env("CAIRN_EMBED_BACKEND") or "local").strip().lower()


# The server family: omlx/ollama are preset aliases of the same 'server' arm
# (OpenAI-compatible /v1 endpoint); only the default base URL differs.
SERVER_FAMILY = frozenset({"server", "omlx", "ollama"})

_SERVER_PRESET_BASE_URL = {
    "omlx": "http://127.0.0.1:8000/v1",
    "ollama": "http://127.0.0.1:11434/v1",
}


# Guard lazy model loading and single-entry eviction across threads.
_MODEL_CACHE: dict = {}
_MODEL_CACHE_LOCK = threading.Lock()

# Cache for the effective backend after fallback resolution. Set once per
# process by embeddings_available(). None until the first resolution stamps
# it; every later value is a backend name.
_EFFECTIVE_BACKEND_CACHE: dict[str, Optional[str]] = {"effective": None}

# Cached availability-probe verdict for the server family, stamped
# by _server_probe_available(); invalidated by reset_backend_cache(). None
# until the first probe answers.
_SERVER_PROBE_CACHE: dict[str, Optional[bool]] = {"available": None}

# Guard check-then-stamp resolution and every session-override read.
_BACKEND_CACHE_LOCK = threading.Lock()

# The probe timeout is fixed at 2 s: a down server must fail the
# availability gate fast, independent of CAIRN_EMBED_TIMEOUT (which governs
# embed requests). Tests inject a shorter value via this module attribute.
_PROBE_TIMEOUT_S = 2.0

# Alias-gate verdicts keyed by the CAIRN_EMBED_MODEL_STAMP value: the
# parity check costs up to 16 embeds, so it runs once per process per stamp.
_ALIAS_GATE_CACHE: dict = {}
_ALIAS_GATE_LOCK = threading.Lock()

_SESSION_STAMP_OVERRIDE: Optional[str] = None
_SESSION_SERVER_MODEL: Optional[str] = None
_SESSION_BACKEND_OVERRIDE: Optional[str] = None


def reset_backend_cache() -> None:
    """Clear backend caches, probes, aliases, and session adoptions."""
    with _BACKEND_CACHE_LOCK:
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
    """Parity-verify stamped server rows before any embedding write."""
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
    """Return the effective backend after fallback and session adoption."""
    cached: Optional[str] = _EFFECTIVE_BACKEND_CACHE["effective"]
    if cached is not None:
        return cached
    resolved = resolve_effective_backend(backend_name())
    with _BACKEND_CACHE_LOCK:
        cached = _EFFECTIVE_BACKEND_CACHE["effective"]
        if cached is None:
            cached = resolved
            _EFFECTIVE_BACKEND_CACHE["effective"] = cached
    return cached


def _server_base_url() -> str:
    """Return the active server base URL or raise RuntimeError at resolution time."""
    configured = _config_or_env("CAIRN_EMBED_BASE_URL") or ""
    if configured:
        return configured
    preset = _SERVER_PRESET_BASE_URL.get(backend_name())
    if preset:
        return preset
    raise RuntimeError(
        "CAIRN_EMBED_BACKEND=server requires CAIRN_EMBED_BASE_URL "
        "(an OpenAI-compatible base URL ending in /v1); "
        "or use the 'omlx'/'ollama' presets"
    )


def _server_model() -> str:
    """Return the adopted or configured server model id."""
    with _BACKEND_CACHE_LOCK:
        adopted = _SESSION_SERVER_MODEL
    if adopted:
        return adopted
    env = _config_or_env("CAIRN_EMBED_SERVER_MODEL")
    if env:
        return env
    return "bge-m3"


def _server_probe_available() -> bool:
    """Return the process-cached availability verdict for the configured server model."""
    verdict: Optional[bool] = _SERVER_PROBE_CACHE["available"]
    if verdict is None:
        probed = _run_server_probe()
        with _BACKEND_CACHE_LOCK:
            verdict = _SERVER_PROBE_CACHE["available"]
            if verdict is None:
                verdict = probed
                _SERVER_PROBE_CACHE["available"] = verdict
    return verdict


def _run_server_probe() -> bool:
    """Probe the server model endpoint once, returning False on any failure."""
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
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT_S) as resp:  # nosec B310 -- base is config-controlled
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
    """Return whether local embeddings silently degraded to the hash backend."""
    return _effective_backend() == "hash" and backend_name() == "local"


# Process-global guard so the one-time warning fires at most once per process.
_HASH_FALLBACK_WARNED: bool = False


def warn_hash_fallback_once(logger, context: str = "") -> None:
    """Warn once per process when hash embeddings are implicit."""
    global _HASH_FALLBACK_WARNED
    if not _HASH_FALLBACK_WARNED and is_hash_fallback():
        # Durable telemetry event; the WARNING below keeps the human detail.
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
        from huggingface_hub import _CACHED_NO_EXIST, try_to_load_from_cache
    except ImportError:
        return False

    m_name = model_name or current_model()
    result = try_to_load_from_cache(m_name, "config.json")
    return result is not None and result is not _CACHED_NO_EXIST


def download_model(model_name: Optional[str] = None) -> bool:
    """Download model weights into the shared HuggingFace cache when absent."""
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
    """Run a subprocess with live output and return its combined output."""
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
    """Return an installer command pinned to the running interpreter."""
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
    """Return a child PYTHONPATH matching in-process shared-library order."""
    from ..paths import SHARED_LIB, shared_lib_path

    dirs = [shared_lib_path()]
    if not os.environ.get("CAIRN_LIB"):
        dirs.append(SHARED_LIB)
    parts = [str(d) for d in dirs]
    if os.environ.get("PYTHONPATH"):
        parts.append(os.environ["PYTHONPATH"])
    return os.pathsep.join(parts)


def _verify_install(lib_dir) -> None:
    """Import the semantic stack in a subprocess and print any failure output."""
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
    """Return whether sentence-transformers is usable, optionally installing and repairing it."""
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
            # pip --target cannot repair a broken install; rebuild it from empty.
            print(
                f"Install verification failed; wiping {lib_dir} and "
                "reinstalling once from scratch..."
            )
            shutil.rmtree(lib_dir, ignore_errors=True)
            lib_dir.mkdir(parents=True, exist_ok=True)
            _run_install_with_progress(cmd, lib_dir)
            _verify_install(lib_dir)  # a second failure is terminal
        reset_backend_cache()
        with _BACKEND_CACHE_LOCK:
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
    """Load and cache the local sentence-transformers model under lock."""
    m_name = model_name or current_model()
    key = ("local", m_name)
    model = _MODEL_CACHE.get(key)
    if model is None:
        with _MODEL_CACHE_LOCK:
            model = _MODEL_CACHE.get(key)
            if model is None:
                from sentence_transformers import SentenceTransformer

                trust = os.environ.get("CAIRN_EMBED_TRUST_REMOTE_CODE") == "1"
                # Values stay object-typed: the dict mixes a bool flag with
                # nested model_kwargs dicts for SentenceTransformer(**kwargs).
                kwargs: dict[str, object] = {"trust_remote_code": trust}
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
    # Multi-vector rows carry the same model stamp as their base row;
    # a model swap orphans them identically, so they purge with it. The table
    # is created unconditionally by SCHEMA_SQL, so no try/except is needed.
    c4 = cur.execute("DELETE FROM embeddings_mv WHERE model != ?", (target_model,)).rowcount

    # Escape '_' so only one model's vec_ and vecmv_ tables match.
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
    if not texts:
        return [], 0
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
    with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 -- fixed https URL
        raw = resp.read()
    body = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = None
    # A 200 body is untrusted: validate the envelope shape, count, and
    # dimensionality before the write path consumes the blobs.
    data = parsed.get("data") if isinstance(parsed, dict) else None
    if not isinstance(data, list) or not all(
        isinstance(entry, dict)
        and isinstance(entry.get("index"), int)
        and isinstance(entry.get("embedding"), list)
        for entry in data
    ):
        raise RuntimeError(
            "OpenAI embeddings returned malformed response: " f"{body[:120]}"
        )
    data.sort(key=lambda d: d["index"])  # preserve input order
    if len(data) != len(texts):
        raise RuntimeError(
            f"OpenAI embeddings returned {len(data)} vectors "
            f"for {len(texts)} inputs"
        )
    dim = len(data[0]["embedding"])
    for entry in data:
        if len(entry["embedding"]) != dim:
            raise RuntimeError(
                f"mixed-dimension batch: expected {dim}-dim vectors, "
                f"got {len(entry['embedding'])}"
            )
    blobs = [_floats_to_blob(entry["embedding"]) for entry in data]
    return blobs, dim


def _embed_server(texts: Sequence[str]) -> Tuple[List[bytes], int]:
    """Embed texts through the configured server with bounded retries and validation."""
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
    if timeout_raw:
        try:
            timeout = float(timeout_raw)
        except ValueError:
            raise RuntimeError(
                f"invalid CAIRN_EMBED_TIMEOUT: {timeout_raw!r}"
            ) from None
        if not math.isfinite(timeout) or timeout <= 0:
            raise RuntimeError(f"invalid CAIRN_EMBED_TIMEOUT: {timeout_raw!r}")
    else:
        timeout = 30.0
    batch_raw = _config_or_env("CAIRN_EMBED_SERVER_BATCH")
    if batch_raw:
        try:
            batch = int(batch_raw)
        except ValueError:
            raise RuntimeError(
                f"invalid CAIRN_EMBED_SERVER_BATCH: {batch_raw!r}"
            ) from None
        if batch < 1:
            raise RuntimeError(f"invalid CAIRN_EMBED_SERVER_BATCH: {batch_raw!r}")
    else:
        batch = 32
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
                with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 -- base is config-controlled
                    raw = resp.read()
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")
                if e.code == 429 or e.code >= 500:
                    last_error = f"HTTP {e.code}: {detail[:80]}"
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
        body = raw.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = None
        data = parsed.get("data") if isinstance(parsed, dict) else None
        if not isinstance(data, list) or not all(
            isinstance(entry, dict)
            and isinstance(entry.get("index"), int)
            and isinstance(entry.get("embedding"), list)
            for entry in data
        ):
            raise RuntimeError(
                "embedding server returned malformed response: "
                f"{body[:120]}"
            )
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


def _hash_vec(text: str, dim: int = DEFAULT_DIM) -> List[float]:
    """Return a deterministic unit-norm hash vector for text."""
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
    blobs, dim = resolve_embedding_backend(_effective_backend()).embed(texts)
    if len(blobs) != len(texts):
        raise RuntimeError(
            f"embedding backend returned {len(blobs)} vectors "
            f"for {len(texts)} inputs"
        )
    return blobs, dim


def _server_adapter_effective() -> str:
    with _BACKEND_CACHE_LOCK:
        return _SESSION_BACKEND_OVERRIDE or "server"


def _server_adapter_available() -> bool:
    with _BACKEND_CACHE_LOCK:
        if _SESSION_BACKEND_OVERRIDE:
            return True
    return _server_probe_available()


def _local_adapter_effective() -> str:
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return "hash"
    return "local"


def _local_adapter_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


def _register_backend_adapters() -> None:
    server_backend = CallableEmbeddingBackend(
        canonical_name="server",
        fallback_name=None,
        _effective_name=_server_adapter_effective,
        _is_available=_server_adapter_available,
        _model_for_corpus=lambda corpus: _model_server(corpus),
        _transport=lambda texts: _embed_server(texts),
    )
    register_embedding_backend(
        "hash",
        CallableEmbeddingBackend(
            canonical_name="hash",
            fallback_name=None,
            _effective_name=lambda: "hash",
            _is_available=lambda: True,
            _model_for_corpus=lambda corpus: _model_hash(corpus),
            _transport=lambda texts: _embed_hash(texts),
        ),
    )
    register_embedding_backend(
        "openai",
        CallableEmbeddingBackend(
            canonical_name="openai",
            fallback_name=None,
            _effective_name=lambda: "openai",
            _is_available=lambda: _available_openai(),
            _model_for_corpus=lambda corpus: _model_openai(corpus),
            _transport=lambda texts: _embed_openai(texts),
        ),
    )
    register_embedding_backend("server", server_backend)
    register_embedding_backend("omlx", server_backend)
    register_embedding_backend("ollama", server_backend)
    register_embedding_backend(
        "local",
        CallableEmbeddingBackend(
            canonical_name="local",
            fallback_name="hash",
            _effective_name=_local_adapter_effective,
            _is_available=_local_adapter_available,
            _model_for_corpus=lambda corpus: _model_local(corpus),
            _transport=lambda texts: _embed_local(texts),
        ),
    )


_register_backend_adapters()


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
    """Refresh stale multi-vector rows and delete vanished docstring rows."""
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
            # comment in embed_all): preserves rowids so the vecmv
            # ANN sync can key on them like the vec0 tables do.
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


def _purge_embedding_rows(
    conn: sqlite3.Connection, where_sql: str, params: tuple = ()
) -> int:
    """Delete matching embeddings and vec0 entries without committing."""
    from .ann_index import ann_backend_enabled, delete_index_rows

    doomed: dict = {}
    if ann_backend_enabled():
        for r in conn.execute(
            f"SELECT model, rowid FROM embeddings WHERE {where_sql}", params
        ).fetchall():
            doomed.setdefault(r[0], []).append(r[1])

    cur = conn.execute(f"DELETE FROM embeddings WHERE {where_sql}", params)
    for model, rowids in doomed.items():
        delete_index_rows(conn, model, rowids)
    return cur.rowcount if cur.rowcount is not None and cur.rowcount > 0 else 0


def reap_orphaned_embeddings(conn: sqlite3.Connection) -> int:
    """Delete base and multi-vector embedding rows whose symbols no longer exist."""
    reaped = _purge_embedding_rows(conn, "symbol_id NOT IN (SELECT id FROM symbols)")
    mv_cur = conn.execute(
        "DELETE FROM embeddings_mv WHERE symbol_id NOT IN (SELECT id FROM symbols)"
    )
    conn.commit()
    reaped_mv = (
        mv_cur.rowcount if mv_cur.rowcount is not None and mv_cur.rowcount > 0 else 0
    )
    return reaped + reaped_mv


def _select_stale_symbols(
    conn: sqlite3.Connection,
    model: str,
    symbol_ids: Optional[Sequence[str]] = None,
    variant: Optional[str] = None,
) -> tuple:
    """Return fetched symbol rows, stale chunks, and declaration lines."""
    if symbol_ids is None:
        where = "WHERE s.kind IS NOT NULL"
        params: tuple = (model,)
    else:
        ids = [sid for sid in symbol_ids if sid]
        if not ids:
            return [], [], {}
        placeholders = ",".join("?" for _ in ids)
        where = f"WHERE s.kind IS NOT NULL AND s.id IN ({placeholders})"
        params = (model, *ids)
    rows = conn.execute(
        f"""SELECT s.id, s.name, s.qualified_name, s.kind, s.docstring,
                   s.line_start, s.parameters, s.return_type,
                   s.parent_scope, s.imports_summary, s.body,
                   f.path AS file_path, f.repo_id AS repo,
                   e.content_hash AS existing_hash
            FROM symbols s
            JOIN files f ON s.file_id = f.id
            LEFT JOIN embeddings e ON e.symbol_id = s.id AND e.model = ?
            {where}
            ORDER BY s.id""",
        params,
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
    return rows, stale_rows, signatures


def embed_all(
    conn: sqlite3.Connection,
    batch_size: int = 64,
    limit: Optional[int] = None,
    progress=None,
    reap_orphans: bool = True,
    variant: Optional[str] = None,
    multivector: bool = True,
) -> dict:
    """Embed stale symbols and refresh multi-vector, orphan, and term-df state."""
    _alias_preflight(conn)
    model = current_model()
    all_rows, stale_rows, signatures = _select_stale_symbols(
        conn, model, variant=variant
    )

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
            # ON CONFLICT preserves rowids keyed by vec0; bulk sync is rebuilt once.
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

    mv_embedded = (
        _embed_mv_kinds(conn, all_rows, signatures, model, batch_size, limit, progress)
        if multivector
        else None
    )

    # The DF table's refresh rides the embed pass, so a `cairn embed`
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
    """Embed selected symbols and synchronously sync their ANN rows."""
    _alias_preflight(conn)
    model = current_model()
    ids = [sid for sid in symbol_ids if sid]
    if not ids:
        return {"model": model, "embedded": 0, "skipped": 0, "ann_synced": 0}

    rows, stale_rows, _signatures = _select_stale_symbols(
        conn, model, symbol_ids=ids, variant=variant
    )

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
    """Embed one natural-language query with the active backend."""
    blobs, dim = _embed([text])
    return blobs[0], dim


def embed_count(conn: sqlite3.Connection) -> int:
    """Number of embeddings stored under the current model."""
    r = conn.execute(
        "SELECT COUNT(*) AS c FROM embeddings WHERE model = ?", (current_model(),)
    ).fetchone()
    return r["c"] if r else 0


def embed_knowledge(conn, bundle, batch_size=64, progress=None):
    """Embed knowledge concepts missing under the current model."""
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


# Memory concept ids move across tiers; rows follow or are reaped.

_CHUNK_SPLIT_RE = re.compile(r"\n(?=Why:|How to apply:)")
MAX_MEMORY_CHUNKS = 5


def chunk_memory_body(concept) -> List[str]:
    """Split a titled memory body into bounded embeddable chunks."""
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
    """Embed selected memory concepts in one batch and replace their rows."""
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
    """Backfill memory concepts missing embeddings under the current model."""
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
    """Return a user hint for unembedded memories, or an empty string."""
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
    """Move every model's memory embedding rows to a new concept id."""
    cur = conn.execute(
        "UPDATE memory_embeddings SET doc_id = ? WHERE doc_id = ?",
        (new_id, old_id),
    )
    return cur.rowcount if cur.rowcount is not None and cur.rowcount > 0 else 0


def reap_orphaned_memory_embeddings(conn: sqlite3.Connection, bundle) -> int:
    """Delete memory embeddings whose concepts no longer resolve."""
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
