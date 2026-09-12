not applicable — no open questions at Stage 0

Gate rationale (orchestrator decision, 2026-09-13): every technical choice in this
spec is closed by internal evidence — exclusion via the existing cairn.json scanner
layer (graph/scanner.py Layer C), incremental embedding via the existing
`embeddings.embed_symbols` seam (unwired, defined), multivector via the existing
`--multivector`/`embeddings_mv` machinery (cli/embed.py, FR-005 of retrieval-quality-v2),
stats surfacing via graph/stats.py. No library, algorithm, or protocol unknown remains.
Manufacturing questions to justify a researcher spawn is this gate's named failure mode.
