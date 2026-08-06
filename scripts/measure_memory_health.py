#!/usr/bin/env python3
"""Measure cairn memory-store health — evidence to decide whether the
admission-gate / consolidation-pass work is worth doing.

Answers three questions with numbers, not vibes:
  1. SCALE   — how big is the store, and how is it distributed across tiers?
  2. NOISE   — how redundant is it? (lexical overlap always; cosine when a real
               embedding model is installed — the hash fallback is flagged and skipped)
  3. HEALTH  — what shape are the memories in? (scores, freshness, supersession)

Usage:
  # auto-locate the live store (same logic as the CLI):
  python scripts/measure_memory_health.py

  # measure a specific store root (e.g. a backup, or the Trash copy with data):
  python scripts/measure_memory_health.py /Users/tan.le/.Trash/9521a7075f4ac248

  # show 20 sample near-duplicate pairs so you can eyeball the redundancy:
  python scripts/measure_memory_health.py --sample-dupes 20 <path>

This is a READ-ONLY measurement tool. It never writes to the store.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

# Allow running from a source checkout without installing.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))


def auto_locate_store() -> Path | None:
    """Mirror the CLI's default store resolution, without importing heavy deps.

    Prefers $CAIRN_STORE / $CAIRN_DB_PATH; falls back to ~/.cairn/<hash> roots.
    Returns the dir whose .knowledge/ tree actually has memory files, or None.
    """
    env = os.environ.get("CAIRN_STORE") or os.environ.get("CAIRN_DB_PATH")
    cands: list[Path] = []
    if env:
        cands.append(Path(env))
    cairn_dir = Path.home() / ".cairn"
    if cairn_dir.is_dir():
        # each workspace gets a hex-named store dir directly under ~/.cairn
        cands.extend(p for p in cairn_dir.iterdir() if p.is_dir())
    for c in cands:
        k = c / ".knowledge" if (c / ".knowledge").is_dir() else c
        if (k / "memory").is_dir():
            return k
    return None


def load_all_memories(knowledge_root: Path):
    """Use cairn's own OKFBundle so frontmatter extensions parse identically."""
    from cairn.okf.bundle import OKFBundle  # noqa: WPS433 (local import, see header)

    bundle = OKFBundle(str(knowledge_root))
    tier_counts: Counter = Counter()
    rows: list[dict] = []
    for cid in bundle.list_concepts(prefix="memory/"):
        try:
            c = bundle.read_concept(cid)
        except Exception as exc:  # noqa: BLE001 - report, don't crash
            print(f"  ! skip unreadable {cid}: {exc}", file=sys.stderr)
            continue
        ext = c.extensions or {}
        tier = ext.get("memory_tier", "?")
        tier_counts[tier] += 1
        rows.append(
            {
                "cid": cid,
                "title": c.title or "",
                "body": c.body or "",
                "tier": tier,
                "type": ext.get("memory_type", "?"),
                "score": ext.get("memory_score"),
                "is_latest": ext.get("memory_is_latest", True),
                "superseded_by": ext.get("memory_superseded_by"),
                "timestamp": c.timestamp,
            }
        )
    return bundle, tier_counts, rows


def _tokenize(text: str) -> set[str]:
    """Cheap tokenization for lexical overlap: lowercase alphanumerics, >=3 chars."""
    import re

    return {t for t in re.findall(r"[a-z0-9]{3,}", text.lower())}


def measure_lexical_redundancy(rows: list[dict]) -> dict:
    """Backend-independent redundancy via token-set overlap (Jaccard).

    Works with zero deps and gives real signal even when the embedding backend
    is the hash fallback (whose cosine scores are meaningless for this purpose).
    This mirrors the cheap lexical layer a tiered gate would run before any
    embedding lookup.
    """
    n = len(rows)
    toks = [_tokenize(f"{r['title']} {r['body']}") for r in rows]
    pairs = []
    best_per_row = [0.0] * n
    for i in range(n):
        for j in range(i + 1, n):
            a, b = toks[i], toks[j]
            union = len(a | b)
            jac = (len(a & b) / union) if union else 0.0
            pairs.append(jac)
            if jac > best_per_row[i]:
                best_per_row[i] = jac
            if jac > best_per_row[j]:
                best_per_row[j] = jac

    def above(t: float) -> int:
        return sum(1 for s in pairs if s >= t)

    return {
        "available": True,
        "pairs": len(pairs),
        "dupes_0.80": above(0.80),  # near-identical token sets
        "dupes_0.60": above(0.60),  # heavy overlap
        "dupes_0.40": above(0.40),  # related
        "pct_reject_0.80": round(100 * sum(1 for b in best_per_row if b >= 0.80) / max(n, 1), 1),
        "pct_update_0.60": round(100 * sum(1 for b in best_per_row if 0.60 <= b < 0.80) / max(n, 1), 1),
    }


def _cosine_sim_matrix(blobs: list[bytes], dim: int):
    """Pairwise cosine similarity matrix.

    Uses numpy if available (fast, vectorized); falls back to a pure-Python
    computation otherwise so the script — and the proposed gate's cheap-reject
    path — work even without the optional `[semantic]` extras installed. This
    honestly reflects the gate's own runtime constraint.
    Returns (sim_matrix, backend_name).
    """
    n = len(blobs)
    if n == 0:
        return [[0.0]], "none"
    try:
        import numpy as np

        mat = np.vstack([np.frombuffer(b, dtype="<f4") for b in blobs])
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        unit = mat / norms
        return (unit @ unit.T).tolist(), "numpy"
    except ImportError:
        import struct

        vecs = []
        for b in blobs:
            v = list(struct.unpack(f"<{dim}f", b[: dim * 4]))
            norm = sum(x * x for x in v) ** 0.5 or 1.0
            vecs.append([x / norm for x in v])
        sim = [[sum(a[k] * b[k] for k in range(dim)) for b in vecs] for a in vecs]
        return sim, "python"


def measure_redundancy(rows: list[dict]) -> dict | None:
    """Pairwise cosine redundancy, reusing cairn's embedder.

    Returns None if embeddings are unavailable (so the caller can note that the
    gate's cheap-reject path would also be unavailable in this environment).
    """
    try:
        from cairn.graph import embeddings as emb
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"import failed: {exc}"}
    if not emb.embeddings_available():
        return {"available": False, "error": emb.install_hint()}

    texts = [f"{r['title']} {r['body']}".strip() or r["cid"] for r in rows]
    try:
        blobs, dim = emb._embed(texts)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"embed failed: {exc}"}

    sim, backend = _cosine_sim_matrix(blobs, dim)
    n = len(rows)

    # upper-triangle pairs (each unordered pair once) + per-row best non-self match
    pair_sims = []
    best_per_row = [-1.0] * n
    for i in range(n):
        for j in range(i + 1, n):
            s = sim[i][j]
            pair_sims.append(s)
            if s > best_per_row[i]:
                best_per_row[i] = s
            if s > best_per_row[j]:
                best_per_row[j] = s

    def above(t: float) -> int:
        return sum(1 for s in pair_sims if s >= t)

    return {
        "available": True,
        "dim": dim,
        "backend": backend,
        "pairs": len(pair_sims),
        "dupes_0.92": above(0.92),        # gate would REJECT (near-identical)
        "dupes_0.85": above(0.85),        # gate would UPDATE/supersede
        "dupes_0.75": above(0.75),        # related (consolidation candidates)
        "pct_reject_0.92": round(100 * sum(1 for b in best_per_row if b >= 0.92) / max(n, 1), 1),
        "pct_update_0.85": round(100 * sum(1 for b in best_per_row if 0.85 <= b < 0.92) / max(n, 1), 1),
        "best_per_row": best_per_row,
        "sim": sim,  # kept for any downstream caller that wants the matrix
    }


def sample_lexical_pairs(rows: list[dict], limit: int) -> list[str]:
    """Return up to `limit` human-readable near-duplicate pairs (by Jaccard)."""
    n = len(rows)
    toks = [_tokenize(f"{r['title']} {r['body']}") for r in rows]
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            a, b = toks[i], toks[j]
            union = len(a | b)
            jac = (len(a & b) / union) if union else 0.0
            pairs.append((jac, i, j))
    pairs.sort(key=lambda t: -t[0])

    out: list[str] = []
    for jac, i, j in pairs:
        if jac < 0.20:
            break
        out.append(
            f"  J={jac:.2f}  [{rows[i]['tier']}] {rows[i]['title'][:55]}\n"
            f"          ↔ [{rows[j]['tier']}] {rows[j]['title'][:55]}"
        )
        if len(out) >= limit:
            break
    return out or ["  (no pairs above 0.20 lexical overlap)"]


def freshness(rows: list[dict]) -> dict:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    ages = []
    for r in rows:
        ts = r.get("timestamp")
        if not ts:
            continue
        try:
            ages.append((now - datetime.fromisoformat(ts)).days)
        except (ValueError, TypeError):
            continue
    if not ages:
        return {"available": False}
    ages.sort()
    return {
        "available": True,
        "p50_days": ages[len(ages) // 2],
        "p90_days": ages[int(len(ages) * 0.9)],
        "max_days": ages[-1],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("store", nargs="?", help="knowledge root (dir containing memory/). Default: auto-locate.")
    ap.add_argument("--sample-dupes", type=int, default=0, metavar="N", help="print N near-duplicate pairs to eyeball.")
    args = ap.parse_args()

    if args.store:
        knowledge_root = Path(args.store)
        if not (knowledge_root / "memory").is_dir() and (knowledge_root / ".knowledge" / "memory").is_dir():
            knowledge_root = knowledge_root / ".knowledge"
    else:
        knowledge_root = auto_locate_store()
        if knowledge_root is None:
            print("No live store found under ~/.cairn. Pass a path explicitly, e.g.:")
            print("  python scripts/measure_memory_health.py /Users/tan.le/.Trash/9521a7075f4ac248")
            return 2

    print(f"measuring: {knowledge_root}\n")
    bundle, tier_counts, rows = load_all_memories(knowledge_root)
    total = len(rows)

    # ---- 1. SCALE ----------------------------------------------------------
    print("== 1. SCALE ==")
    if total == 0:
        print("  store is EMPTY — nothing to measure. Gate/pass not justified yet.\n")
        return 0
    for tier in ("raw", "drafts", "tribal", "archived"):
        n = tier_counts.get(tier, 0)
        if n:
            print(f"  {tier:9} {n:4}  ({100*n/total:4.1f}%)")
    other = sum(v for k, v in tier_counts.items() if k not in ("raw", "drafts", "tribal", "archived"))
    if other:
        print(f"  {'other':9} {other:4}")
    print(f"  {'TOTAL':9} {total:4}\n")

    type_counts = Counter(r["type"] for r in rows)
    print("  by type: " + ", ".join(f"{t}={n}" for t, n in type_counts.most_common()) + "\n")

    # ---- 2. NOISE / redundancy --------------------------------------------
    print("== 2. NOISE — would a cheap gate help? ==")

    # Lexical measure first: backend-independent, always meaningful.
    lex = measure_lexical_redundancy(rows)
    print("  lexical (Jaccard token overlap — always meaningful):")
    print(f"    near-identical (≥0.80): {lex['dupes_0.80']:4}  ← gate would REJECT")
    print(f"    heavy overlap   (≥0.60): {lex['dupes_0.60']:4}  ← gate would UPDATE/supersede")
    print(f"    related         (≥0.40): {lex['dupes_0.40']:4}  ← consolidation candidates")
    lex_noisy = lex["pct_reject_0.80"] + lex["pct_update_0.60"]
    print(f"    of {total} memories, {lex['pct_reject_0.80']}% would be rejected, "
          f"{lex['pct_update_0.60']}% would update an existing one.")

    # Cosine measure: only meaningful with a real embedding model (not the hash fallback).
    red = measure_redundancy(rows)
    cosine_noisy = 0.0
    if red and red.get("available"):
        is_hash = red.get("backend") == "hash" or red.get("dim") == 256
        if is_hash:
            print("\n  cosine (semantic): SKIPPED — active backend is the hash fallback")
            print("    (dim=256). Hash embeddings are near-orthogonal for any distinct text,")
            print("    so cosine redundancy is ~0 by construction — not real signal.")
            print("    Install `pip install 'cairn-intel[semantic]'` for a meaningful semantic pass.")
            # Use a fake-but-honest "no usable data" entry.
            red["usable"] = False
        else:
            red["usable"] = True
            cosine_noisy = red["pct_reject_0.92"] + red["pct_update_0.85"]
            print("\n  cosine (semantic):")
            print(f"    near-identical (≥0.92): {red['dupes_0.92']:4}  ← gate would REJECT")
            print(f"    near-duplicate (≥0.85): {red['dupes_0.85']:4}  ← gate would UPDATE/supersede")
            print(f"    related        (≥0.75): {red['dupes_0.75']:4}  ← consolidation candidates")
            print(f"    of {total} memories, {red['pct_reject_0.92']}% rejected, "
                  f"{red['pct_update_0.85']}% update.")
    else:
        print(f"\n  cosine (semantic): unavailable — {red.get('error') if red else '?'}")

    # Verdict uses lexical (always available); cosine refines only when usable.
    noisy = max(lex_noisy, cosine_noisy)
    verdict = (
        "HIGH redundancy — a gate pays for itself." if noisy >= 25
        else "MODERATE — gate worth considering." if noisy >= 10
        else "LOW redundancy — gate not justified by current noise."
    )
    print(f"\n  → {verdict}\n")
    if args.sample_dupes:
        print(f"-- top {args.sample_dupes} near-duplicate pairs (by lexical overlap) --")
        for line in sample_lexical_pairs(rows, args.sample_dupes):
            print(line)
        print()

    # ---- 3. HEALTH ---------------------------------------------------------
    print("== 3. HEALTH ==")
    scores = [r["score"] for r in rows if isinstance(r["score"], (int, float))]
    if scores:
        scores.sort()
        print(f"  score: min={scores[0]:.2f}  p50={scores[len(scores)//2]:.2f}  max={scores[-1]:.2f}  (n={len(scores)})")
    n_superseded = sum(1 for r in rows if r["is_latest"] is False)
    print(f"  superseded (not latest): {n_superseded}  ({100*n_superseded/max(total,1):.1f}%)")
    fr = freshness(rows)
    if fr.get("available"):
        print(f"  age: p50={fr['p50_days']}d  p90={fr['p90_days']}d  max={fr['max_days']}d")
    print()

    print("== DECISION GUIDE ==")
    if total < 25:
        print("  Store is small (<25). The gate/pass is solving a problem you don't yet have.")
        print("  Re-run this when the store grows; the number is what matters, not the architecture.")
    else:
        # prefer the most informative usable measure
        noisy = lex_noisy
        src = "lexical"
        if red and red.get("usable"):
            noisy = max(lex_noisy, red["pct_reject_0.92"] + red["pct_update_0.85"])
            src = "lexical+cosine"
        print(f"  (using {src} redundancy signal)")
        if noisy >= 25:
            print("  Store is large AND noisy. Build the tier-1 redundancy gate first.")
        elif noisy >= 10:
            print("  Store is large with moderate noise. Gate is worth it; pass can wait.")
        else:
            print("  Store is large but clean. Gate not urgent — check recall quality instead")
            print("  (are agents re-asking things already in the store?). If yes, build the pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
