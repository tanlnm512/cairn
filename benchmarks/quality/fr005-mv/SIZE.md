# FR-005 storage accounting (T021) — the multi-vector growth factor

Derived by hand per MEASURE.md Step 3 (`ls -l` + the sweep JSON), from
the two structurally comparable scratch DBs (same t2 corpus, same
no-vec0 doctrine, same builder recipe — fr005-mv/scratch_db.py mirrors
fr003-calibration/scratch_db.py verbatim):

| DB | bytes | rows |
|---|---:|---|
| `/tmp/fr003-calibration/graph.db` (base embeddings only) | 7,215,056 | 1066 `embeddings` |
| `/tmp/fr005-mv/graph.db` (base + mv) | 13,058,048 | 1066 `embeddings` + 1240 `embeddings_mv` |

* **Growth factor: 1.8103×** (13,058,048 / 7,215,056) — inside the ≤3×
  bound (FR-005 / spec risk note; AC6).
* mv rows: 1240 = 1066 `name` kind (one per symbol) + 174 `docstring`
  kind (symbols whose docstring chunk is non-empty and not stale by its
  own `_chunk_hash`).
* In-run `_size_accounting` on the sweep (which reads the same file)
  reports `db_mb` 12.4531 — agrees with the `ls` figure (12.4516) at
  rounding.
* The all-levers-off integrity row of the mv sweep was measured against
  this LARGER DB with the lever off (mv rows present but unread): pooled
  0.4174/0.2862 with 58/58 per-query identity vs the committed T014
  baseline — db growth alone moves nothing (TC-022).
