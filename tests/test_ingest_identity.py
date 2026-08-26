"""T004 — stable identity derivation for knowledge ingestion (FR-007)."""
from cairn.knowledge.ingest.identity import DocIdentity, build_identity
from cairn.knowledge.ingest.parser import ParsedDoc, parse_source_doc
from cairn.okf.utils import slugify


def _parsed(**overrides):
    fields = {
        "title": None,
        "status": None,
        "tags": [],
        "description": None,
        "body": "",
    }
    fields.update(overrides)
    return ParsedDoc(**fields)


def test_stable_id_is_deterministic_and_move_sensitive():
    parsed = _parsed(title="Routing")
    first = build_identity("polaris", "docs/gateway.md", parsed)
    second = build_identity("polaris", "docs/gateway.md", parsed)
    moved = build_identity("polaris", "docs/arch/gateway.md", parsed)
    assert first.stable_id == second.stable_id
    assert first.slug == second.slug
    assert moved.stable_id != first.stable_id


def test_title_and_slug_carry_stable_prefix():
    parsed = _parsed(title="Gateway Routing")
    identity = build_identity("polaris", "docs/gateway.md", parsed)
    assert identity.title == f"{identity.stable_id} — Gateway Routing"
    assert identity.slug == slugify(identity.title)
    assert identity.slug.startswith(identity.stable_id)


def test_long_title_capped_so_slug_keeps_stable_prefix():
    parsed = _parsed(title="W" * 200)
    identity = build_identity("polaris", "docs/gw.md", parsed)
    assert len(identity.slug) <= 60
    assert identity.slug.startswith(identity.stable_id)
    # Stable id alone fills the slug budget -> bare stable-id title.
    deep = build_identity("a", "x" * 100, parsed)
    assert deep.title == deep.stable_id
    assert deep.slug == deep.stable_id


def test_no_title_falls_back_to_stable_id():
    identity = build_identity("polaris", "docs/gateway.md", _parsed())
    assert identity.title == identity.stable_id
    assert identity.slug == identity.stable_id


def test_cross_repo_slug_collision_gets_repo_suffix():
    parsed = _parsed(title="Gateway")
    seen: set[str] = set()
    first = build_identity("Polaris", "docs/gw.md", parsed, seen_slugs=seen)
    second = build_identity("polaris", "docs/gw.md", parsed, seen_slugs=seen)
    assert first.slug in seen
    assert second.slug in seen
    assert second.slug != first.slug
    assert second.slug.endswith(f"-{slugify('polaris')}")


def test_collision_suffix_survives_slug_cap():
    parsed = _parsed()
    seen: set[str] = set()
    long_rel = "x" * 100
    first = build_identity("beta", long_rel, parsed, seen_slugs=seen)
    second = build_identity("Beta", long_rel, parsed, seen_slugs=seen)
    assert first.slug != second.slug
    assert len(second.slug) <= 60
    assert second.slug.endswith("-beta")


def test_no_seen_slugs_means_no_suffix():
    parsed = _parsed(title="Gateway")
    solo = build_identity("Polaris", "docs/gw.md", parsed)
    twin = build_identity("polaris", "docs/gw.md", parsed)
    assert solo.slug == twin.slug


def test_tag_union_dedupes_and_keeps_source_order():
    parsed = _parsed(title="T", tags=["gateway", "spec"])
    identity = build_identity("polaris", "docs/gw.md", parsed)
    assert identity.tags == ["gateway", "spec", identity.stable_id, "polaris"]
    duped = _parsed(
        title="T", tags=[identity.stable_id, "polaris", "gateway"]
    )
    assert build_identity("polaris", "docs/gw.md", duped).tags == duped.tags


def test_affects_repos_and_modules():
    nested = build_identity(
        "polaris", "docs/decisions/0001-adr.md", _parsed()
    )
    assert nested.affects_repos == ["polaris"]
    assert nested.affects_modules == ["docs/decisions"]
    root = build_identity("polaris", "README.md", _parsed())
    assert root.affects_modules == []


def test_description_prefers_frontmatter_description():
    parsed = _parsed(title="T", description="Routes alias traffic.", body="ignored")
    identity = build_identity("polaris", "docs/gw.md", parsed)
    assert identity.description == "Routes alias traffic."
    assert identity.description != identity.title


def test_description_falls_back_to_first_meaningful_paragraph():
    body = "\n".join(
        [
            "# Gateway Routing",
            "",
            "**Status:** accepted",
            "",
            "Routes alias traffic to the nearest region.",
            "Second line of the same paragraph.",
            "",
            "Later paragraph.",
        ]
    )
    identity = build_identity("polaris", "docs/gw.md", _parsed(body=body))
    expected = (
        "Routes alias traffic to the nearest region. "
        "Second line of the same paragraph."
    )
    assert identity.description == expected
    assert identity.description != identity.title


def test_description_skips_fenced_code_blocks():
    body = "```python\nimport x\n```\n\nReal prose here."
    identity = build_identity("polaris", "docs/gw.md", _parsed(body=body))
    assert identity.description == "Real prose here."


def test_description_synthesizes_provenance_line():
    identity = build_identity(
        "polaris", "docs/gw.md", _parsed(title=None, body="# Only a heading")
    )
    assert identity.description == "Imported from polaris/docs/gw.md"
    assert identity.description != identity.title


def test_identity_from_parsed_frontmatter_source():
    text = (
        "---\n"
        "title: LLM Gateway Aliases\n"
        "status: accepted\n"
        "tags: [gateway, routing]\n"
        "description: Alias routing rules for the LLM gateway.\n"
        "---\n"
        "\n"
        "# LLM Gateway Aliases\n"
        "\n"
        "Body prose.\n"
    )
    parsed = parse_source_doc(text)
    identity = build_identity("polaris", "docs/gateway.md", parsed)
    assert isinstance(identity, DocIdentity)
    assert identity.tags[:2] == ["gateway", "routing"]
    assert identity.description == "Alias routing rules for the LLM gateway."
    assert identity.slug.startswith(identity.stable_id)
