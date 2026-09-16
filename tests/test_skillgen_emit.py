"""SKILL.md emit layer: ``render_skill``, ``landing_dir``, ``split_references``.

- frontmatter is a ``---``-fenced YAML block carrying exactly name +
  description, the same loadable shape the static skill package ships
- the rendered string is the exact SKILL.md bytes and is deterministic
- default landing is ``<workspace>/.agents/skills/cairn-<slug>/``
- long sections split to ``references/<slug>.md`` with a backtick-free
  pointer (the deterministic critic rejects backtick-quoted paths it
  cannot verify against the graph)
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import cairn
from cairn.skillgen.emitter import (
    REFERENCE_SPLIT_CHARS,
    landing_dir,
    render_skill,
    slugify,
    split_references,
)

STATIC_SKILL = (
    Path(cairn.__file__).parent / "agent_integration" / "skill" / "SKILL.md"
)

TRIGGER = (
    "Module context for pkg_a. Load when asked about pkg_a structure, call "
    "graphs, or blast radius before editing its files."
)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Parse a ``---``-fenced frontmatter into (mapping, body)."""
    assert text.startswith("---\n"), "SKILL.md must open with a --- fence"
    rest = text[4:]
    block, body = rest.split("\n---\n", 1)
    return yaml.safe_load(block), body


def test_frontmatter_shape_name_description_nonempty_body():
    rendered = render_skill("pkg_a", TRIGGER, [("Symbols", "zeta_hub callers")])

    assert rendered.splitlines()[0] == "---"
    fm, body = _split_frontmatter(rendered)
    assert set(fm) == {"name", "description"}
    assert fm["name"] == "pkg_a"
    assert len(fm["description"]) >= 20
    assert body.strip(), "body must be non-empty"


def test_frontmatter_matches_static_skill_shape():
    static = STATIC_SKILL.read_text(encoding="utf-8")
    static_fm, static_body = _split_frontmatter(static)
    fm, body = _split_frontmatter(
        render_skill("pkg_a", TRIGGER, [("Overview", "text")])
    )

    assert static.splitlines()[0] == "---"
    assert set(fm) == set(static_fm) == {"name", "description"}
    assert isinstance(fm["description"], str)
    assert body.strip() and static_body.strip()


def test_frontmatter_round_trips_exact_values():
    description = "trigger: load this " + "x" * 200
    fm, _ = _split_frontmatter(
        render_skill("my_mod", description, [("S", "b")])
    )
    assert fm == {"name": "my_mod", "description": description}


def test_sections_render_in_given_order():
    sections = [
        ("zebra last", "z"),
        ("apple first", "a"),
        ("middle", "m"),
    ]
    _, body = _split_frontmatter(render_skill("pkg_a", TRIGGER, sections))

    positions = [body.index(f"## {heading}") for heading, _ in sections]
    assert positions == sorted(positions)
    for heading, markdown in sections:
        assert f"## {heading}\n\n{markdown}" in body


def test_render_is_deterministic():
    sections = [("Compass", "body text"), ("Symbols", "zeta_hub")]
    assert render_skill("pkg_a", TRIGGER, sections) == render_skill(
        "pkg_a", TRIGGER, sections
    )


def test_render_rejects_empty_required_fields():
    with pytest.raises(ValueError):
        render_skill("  ", TRIGGER, [])
    with pytest.raises(ValueError):
        render_skill("pkg_a", "", [])
    with pytest.raises(ValueError):
        render_skill("pkg_a", TRIGGER, [("   ", "body")])


def test_empty_sections_still_render_nonempty_body():
    _, body = _split_frontmatter(render_skill("pkg_a", TRIGGER, []))
    assert body.strip()


def test_landing_dir_default_skills_home():
    ws = Path("/tmp/somewhere")
    landing = landing_dir(ws, "pkg_a")
    assert landing == ws / ".agents" / "skills" / "cairn-pkg_a"
    assert landing.name.startswith("cairn-")
    assert landing_dir(str(ws), "pkg_a") == landing


def test_landing_dir_rejects_non_segment_slug():
    with pytest.raises(ValueError):
        landing_dir(Path("/tmp/somewhere"), "")
    with pytest.raises(ValueError):
        landing_dir(Path("/tmp/somewhere"), "a/b")
    with pytest.raises(ValueError):
        landing_dir(Path("/tmp/somewhere"), "..")


def test_slugify_normalizes_to_filesystem_slug():
    assert slugify("pkg_a") == "pkg-a"
    assert slugify("My Module! Name") == "my-module-name"
    assert slugify("--trimmed--") == "trimmed"


def test_long_section_splits_to_references():
    sections = [
        ("Short note", "short"),
        ("Long analysis", "word " * 500),
    ]
    inline, refs = split_references(sections, max_chars=200)

    assert [heading for heading, _ in inline] == [
        "Short note",
        "Long analysis",
    ]
    assert inline[0] == ("Short note", "short")
    pointer = inline[1][1]
    assert "references/long-analysis.md" in pointer
    # The pointer must never backtick-quote the path: the critic rejects
    # backtick-quoted paths it cannot verify against the graph.
    assert "`" not in pointer

    assert len(refs) == 1
    filename, content = refs[0]
    assert filename == "references/long-analysis.md"
    assert content.startswith("# Long analysis\n")
    assert "word word" in content


def test_split_leaves_short_sections_inline_by_default():
    sections = [("Symbols", "zeta_hub, alpha_leaf")]
    inline, refs = split_references(sections)
    assert inline == sections
    assert refs == []
    assert REFERENCE_SPLIT_CHARS > 0


def test_split_reference_filename_collisions_dedupe():
    sections = [
        ("Notes!", "long body " * 100),
        ("Notes?", "other long body " * 100),
        ("Notes", "third long body " * 100),
    ]
    _, refs = split_references(sections, max_chars=100)

    assert [filename for filename, _ in refs] == [
        "references/notes.md",
        "references/notes-2.md",
        "references/notes-3.md",
    ]


def test_split_reference_content_is_self_contained():
    sections = [("Deep detail", "line one\nline two")]
    _, refs = split_references(sections, max_chars=5)
    filename, content = refs[0]
    assert filename == "references/deep-detail.md"
    assert content == "# Deep detail\n\nline one\nline two\n"


def test_split_rejects_invalid_threshold():
    with pytest.raises(ValueError):
        split_references([("H", "b")], max_chars=0)
    with pytest.raises(ValueError):
        split_references([("H", "b")], max_chars=-1)
