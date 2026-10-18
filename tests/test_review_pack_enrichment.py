"""Review pack enrichment contracts: memory, compass, and wiki sections
render in both pack formats, a reader failure degrades its section instead
of raising, and the engine imports its readers lazily at call time.
"""

from __future__ import annotations

import subprocess
import sys

from cairn.graph.blast import render_blast
from cairn.review.engine import PACK_FORMATS, enrich_pack, render_pack


def _pack(**overrides):
    """A blast-shaped pack with one ledger seed, before enrichment."""
    pack = {
        "basis": {"kind": "merge-base", "base": "main"},
        "seeds": [
            {
                "id": "s1",
                "name": "parse_date",
                "qualified_name": "ledger.parse_date",
                "kind": "function",
                "file_path": "ledger.py",
                "repo": "fixture",
                "line_start": 5,
                "line_end": 7,
                "hunks": [{"start": 5, "end": 7}],
            }
        ],
        "changed_files": [],
        "areas": [],
        "radius": [],
        "unindexed_files": [],
        "deleted_files": [],
        "truncated": False,
    }
    pack.update(overrides)
    return pack


def _raise_reader(*args, **kwargs):
    raise RuntimeError("reader down")


def test_enrichment_renders_in_both_formats():
    pack = enrich_pack(None, "unused-ws", _pack())
    # The real readers see a store without enrichment content: the sections
    # exist (absence statements), which proves the renderer path in both
    # formats; content rendering is covered by the synthetic pack below.
    for output_format in PACK_FORMATS:
        rendered = render_pack(pack, output_format)
        assert "Memories" in rendered
        assert "no matching memories" in rendered
        assert "no compass guide" in rendered
        assert "no wiki pages" in rendered


def test_enriched_sections_render_in_both_formats():
    pack = _pack()
    pack["memories"] = {
        "entries": [
            {
                "seed": "parse_date",
                "file_path": "ledger.py",
                "matches": [
                    {
                        "type": "mistake",
                        "title": "Never parse dates with regex",
                        "body": "Use the dedicated date parser.",
                    }
                ],
            }
        ],
        "error": None,
    }
    pack["compass"] = {
        "entries": [
            {"module": "ledger", "guide": "The ledger module owns date parsing.", "found": True}
        ],
        "error": None,
    }
    pack["wiki"] = {
        "entries": [
            {
                "module": "ledger",
                "pages": "# Ledger architecture\n\nPreserves the append-only invariant.",
                "found": True,
            }
        ],
        "error": None,
    }

    text = render_pack(pack, "text")
    assert "Never parse dates with regex" in text
    assert "[mistake]" in text
    assert "Use the dedicated date parser." in text
    assert "Module guides:" in text
    assert "The ledger module owns date parsing." in text
    assert "Wiki pages:" in text
    assert "Preserves the append-only invariant." in text

    markdown = render_pack(pack, "markdown")
    assert "## Memories" in markdown
    assert "**[mistake]** Never parse dates with regex" in markdown
    assert "## Module guides" in markdown
    assert "## Wiki" in markdown
    assert "Preserves the append-only invariant." in markdown


def test_render_pack_without_enrichment_matches_render_blast():
    pack = _pack()
    for output_format in PACK_FORMATS:
        assert render_pack(pack, output_format) == render_blast(pack, output_format)


def test_reader_failure_degrades_the_section(monkeypatch):
    import cairn.memory.promotion as promotion
    import cairn.mcp_server.tools_compass as tools_compass
    from cairn.okf.bundle import OKFBundle

    monkeypatch.setattr(promotion, "search_memory", _raise_reader)
    monkeypatch.setattr(tools_compass, "find_compass_concept", _raise_reader)
    monkeypatch.setattr(OKFBundle, "search", _raise_reader)

    pack = enrich_pack(None, "unused-ws", _pack())
    assert pack["memories"]["error"] == "reader down"
    assert pack["compass"]["error"] == "reader down"
    assert pack["wiki"]["error"] == "reader down"

    for output_format in PACK_FORMATS:
        rendered = render_pack(pack, output_format)
        assert "unavailable: reader down" in rendered


def test_empty_reader_results_render_as_absence(monkeypatch):
    import cairn.memory.promotion as promotion
    import cairn.mcp_server.tools_compass as tools_compass
    from cairn.okf.bundle import OKFBundle

    monkeypatch.setattr(promotion, "search_memory", lambda conn, bundle, query: [])
    monkeypatch.setattr(
        tools_compass,
        "find_compass_concept",
        lambda bundle, module: None,
    )
    monkeypatch.setattr(
        OKFBundle,
        "search",
        lambda self, query, tags=None, limit=20: [],
    )

    pack = enrich_pack(None, "unused-ws", _pack())
    assert pack["memories"]["error"] is None
    assert pack["compass"]["error"] is None
    assert pack["wiki"]["error"] is None
    assert not pack["compass"]["entries"][0]["found"]
    assert not pack["wiki"]["entries"][0]["found"]

    for output_format in PACK_FORMATS:
        rendered = render_pack(pack, output_format).lower()
        assert "no matching memories" in rendered
        assert "no compass guide" in rendered
        assert "no wiki pages" in rendered


def test_compass_hit_renders_the_concept_body(monkeypatch):
    """A compass concept renders as title + body; detection is the concept itself."""
    import cairn.mcp_server.tools_compass as tools_compass
    from types import SimpleNamespace

    concept = SimpleNamespace(title="Ledger", body="Owns date parsing.")
    monkeypatch.setattr(
        tools_compass,
        "find_compass_concept",
        lambda bundle, module: concept if module == "ledger" else None,
    )

    pack = enrich_pack(None, "unused-ws", _pack())
    entry = pack["compass"]["entries"][0]
    assert entry["found"] is True
    assert entry["guide"] == "# Ledger\n\nOwns date parsing."

    for output_format in PACK_FORMATS:
        rendered = render_pack(pack, output_format)
        assert "Owns date parsing." in rendered


def test_wiki_page_containing_miss_marker_still_renders(monkeypatch):
    """A wiki page whose body contains miss-like markers must not drop the section."""
    from types import SimpleNamespace

    from cairn.okf.bundle import OKFBundle

    page = SimpleNamespace(
        title="Ledger architecture",
        type="Wiki-Article",
        body="No wiki pages found during generation.",
    )
    monkeypatch.setattr(
        OKFBundle,
        "search",
        lambda self, query, tags=None, limit=20: [page],
    )

    pack = enrich_pack(None, "unused-ws", _pack())
    assert pack["wiki"]["error"] is None
    entry = pack["wiki"]["entries"][0]
    assert entry["found"] is True
    assert "No wiki pages found during generation." in entry["pages"]

    for output_format in PACK_FORMATS:
        rendered = render_pack(pack, output_format)
        assert "No wiki pages found during generation." in rendered


def test_enrichment_scopes_readers_to_the_pack_workspace(monkeypatch):
    """Compass and wiki readers resolve their bundle from the pack's workspace."""
    import cairn.review.engine as engine

    real_bundle = engine._workspace_bundle
    seen: list[str] = []

    def recording(workspace: str):
        seen.append(workspace)
        return real_bundle(workspace)

    monkeypatch.setattr(engine, "_workspace_bundle", recording)

    enrich_pack(None, "unused-ws", _pack())

    assert seen and set(seen) == {"unused-ws"}


def test_pack_without_seeds_renders_idle_sections():
    pack = enrich_pack(None, "unused-ws", _pack(seeds=[], areas=[]))
    assert pack["memories"]["entries"] == []
    assert pack["compass"]["entries"] == []
    assert pack["wiki"]["entries"] == []

    for output_format in PACK_FORMATS:
        rendered = render_pack(pack, output_format)
        assert "No changed symbols to key memories on." in rendered
        assert "No changed modules to look up." in rendered


def test_engine_import_pulls_no_mcp_server_or_memory_promotion():
    code = (
        "import sys\n"
        "import cairn.review, cairn.review.engine\n"
        "banned = [\n"
        "    m for m in sys.modules\n"
        "    if m == 'cairn.mcp_server' or m.startswith('cairn.mcp_server.')\n"
        "    or m == 'cairn.memory.promotion'\n"
        "]\n"
        "assert not banned, f'unexpected eager imports: {banned}'\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
