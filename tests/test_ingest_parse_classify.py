"""Ingest tests: parsing + classification (disjoint tasks).

T002: Parse source documents — YAML frontmatter, status markers, body stripping.
T003: Classify parsed documents by doc type and file semantics.
"""
from __future__ import annotations

import pytest

from cairn.knowledge.ingest.classifier import classify_doc
from cairn.knowledge.ingest.parser import ParsedDoc, parse_source_doc


# --- parse (T002) ---
def test_parse_empty():
    doc = parse_source_doc("")
    assert doc.title is None
    assert doc.status is None
    assert doc.tags == []
    assert doc.description is None
    assert doc.body == ""


def test_parse_no_frontmatter():
    doc = parse_source_doc("# The title\n\nBody content.\n**Status:** Accepted\nMore body.")
    assert doc.title == "The title"
    assert doc.status == "Accepted"
    assert doc.tags == []
    assert doc.description is None
    assert doc.body == "# The title\n\nBody content.\n**Status:** Accepted\nMore body."


def test_parse_complete_frontmatter():
    text = (
        "---\n"
        "title: Test Doc\n"
        "status: Draft\n"
        "tags: [feature, urgent]\n"
        "description: A test document\n"
        "---\n"
        "# Body title\n\nBody content."
    )
    doc = parse_source_doc(text)
    assert doc.title == "Test Doc"
    assert doc.status == "Draft"
    assert doc.tags == ["feature", "urgent"]
    assert doc.description == "A test document"
    assert doc.body == "# Body title\n\nBody content."


def test_parse_frontmatter_comma_tags():
    text = (
        "---\n"
        "title: Project X\n"
        "status: Proposed\n"
        "tags: feature,experimental\n"
        "---\n"
        "Project content."
    )
    doc = parse_source_doc(text)
    assert doc.title == "Project X"
    assert doc.status == "Proposed"
    assert doc.tags == ["feature", "experimental"]


def test_parse_inline_status_frontmatter_takes_precedence():
    text = (
        "---\n"
        "status: Accepted\n"
        "---\n"
        "## Status: Draft\n"
        "Document content."
    )
    doc = parse_source_doc(text)
    assert doc.status == "Accepted"


def test_parse_status_heading_marker():
    text = (
        "## Status: Draft\n"
        "Document content."
    )
    doc = parse_source_doc(text)
    assert doc.status == "Draft"
    assert doc.body == "## Status: Draft\nDocument content."


def test_parse_status_bold_marker():
    text = (
        "**Status:** Approved\n"
        "Document content."
    )
    doc = parse_source_doc(text)
    assert doc.status == "Approved"
    assert doc.body == "**Status:** Approved\nDocument content."


def test_parse_status_bare_heading_marker():
    text = (
        "## Status\n"
        "Document content."
    )
    doc = parse_source_doc(text)
    assert doc.status == "Document content."
    assert doc.body == "## Status\nDocument content."


def test_parse_malformed_yaml():
    text = (
        "---\n"
        "title: Valid title\n"
        "broken yaml [unclosed bracket\n"
        "another value: something else\n"
        "status: Accepted\n"
        "---\n"
        "# Body title\n\nBody content."
    )
    doc = parse_source_doc(text)
    assert doc.title == "Valid title"
    assert doc.status == "Accepted"
    assert doc.tags == []
    assert doc.description is None
    assert doc.body == "# Body title\n\nBody content."


def test_parse_malformed_yaml_minimal_fallback():
    text = (
        "---\n"
        "title: Recoverable\n"
        "invalid: not_a_value\n"
        "description: value\n"
        "---\n"
        "Body content."
    )
    doc = parse_source_doc(text)
    assert doc.title == "Recoverable"
    assert doc.status is None
    assert doc.tags == []
    assert doc.description == "value"
    assert doc.body == "Body content."


def test_parse_unclosed_frontmatter():
    text = (
        "---\n"
        "title: No End Fence\n"
        "Some content that looks like frontmatter\n"
        "But no closing fence\n"
        "# Body title\n\nBody content."
    )
    doc = parse_source_doc(text)
    assert doc.title == "Body title"
    assert doc.status is None
    assert doc.tags == []
    assert doc.description is None
    assert "title: No End Fence" in doc.body


def test_parse_unicode_in_values():
    text = (
        "---\n"
        'title: "Unicode Title 🚀"\n'
        "status: 「Done」\n"
        "tags: [emoji, other]\n"
        "---\n"
        "Body."
    )
    doc = parse_source_doc(text)
    assert doc.title == "Unicode Title 🚀"
    assert doc.status == "「Done」"
    assert doc.tags == ["emoji", "other"]
    assert doc.body == "Body."


def test_parse_blank_line_between_markers():
    text = (
        "**Status:**\n"
        "\n"
        "Some content on next line"
    )
    doc = parse_source_doc(text)
    assert doc.status == "Some content on next line"
    assert doc.body == "**Status:**\n\nSome content on next line"


def test_parse_status_wrapped_lines_take_first_bare():
    text = (
        "## Status:\n"
        "Here is the actual status\n"
        "spans multiple lines"
    )
    doc = parse_source_doc(text)
    assert doc.status == "Here is the actual status"


def test_parse_status_wrapped_lines_take_first_bold():
    text = (
        "**Status:** This spans\n"
        "multiple lines\n"
        "And this continues it."
    )
    doc = parse_source_doc(text)
    assert doc.status == "This spans"


@pytest.mark.parametrize(
    "title, expected_doc_type, expected_tags",
    [
        ("ADR: …", "decision", ()),
        ("Architecture Decision Record", "decision", ()),
        ("Decision on X", "decision", ()),
        ("Feature: UI Login", "spec", ()),
        ("Spec: Data Model", "spec", ()),
        ("Proposal: New API", "spec", ()),
        ("Design: Scalability", "spec", ()),
        ("Use Case: Dashboard", "spec", ()),
        ("Use case: Analytics", "spec", ()),
        ("Component Spec: Button", "spec", ()),
        ("Runbook: Deploy", "workflow", ()),
        ("Guide: Setup", "workflow", ()),
        ("Setup Guide", "workflow", ()),
        ("Convention: Naming", "business-rule", ()),
        ("Code Standard: Lint", "business-rule", ()),
        ("Agent Instruction: Auth", "business-rule", ()),
        ("Architecture Vision", "spec", ("reference",)),
        ("Prior-art Survey", "spec", ("reference",)),
    ],
)
def test_classify_doc_type_map(title, expected_doc_type, expected_tags):

    doc = ParsedDoc(title=title)
    result = classify_doc(doc, "", include_drafts=True)
    assert result.doc_type == expected_doc_type
    assert set(result.extra_tags) == set(expected_tags)
    assert result.skip_reason is None


def test_classify_architecture_decision_record_is_decision():
    """Decision beats reference ("Architecture Decision Record")."""

    doc = ParsedDoc(title="Architecture Decision Record")
    result = classify_doc(doc, "", include_drafts=True)
    assert result.doc_type == "decision"
    assert result.extra_tags == []
    assert result.skip_reason is None


@pytest.mark.parametrize(
    "relpath, expected_doc_type",
    [
        ("docs/decisions/0001-proposal.md", "decision"),
        ("docs/adr/ADR-42.md", "decision"),
        ("docs/adrs/2024-design.md", "decision"),
        ("decisions/003-PRD.md", "decision"),
        ("0001-start.md", "decision"),  # only numbered stem
        ("12-more.md", "spec"),  # short stem
        ("specs/FEAT-012-login.md", "spec"),
        ("features/UC-031-chart.md", "spec"),
        ("guides/setup.md", "spec"),  # default
    ],
)
def test_classify_relpath_conventions(relpath, expected_doc_type):

    doc = ParsedDoc()
    result = classify_doc(doc, relpath, include_drafts=True)
    assert result.doc_type == expected_doc_type
    assert result.extra_tags == []
    assert result.skip_reason is None


def test_classify_title_layer_beats_relpath_layer():
    """Title "Runbook: …" wins over docs/decisions/ path."""

    doc = ParsedDoc(title="Runbook: Local development")
    result = classify_doc(doc, "docs/decisions/123.md", include_drafts=True)
    assert result.doc_type == "workflow"
    assert result.extra_tags == []
    assert result.skip_reason is None


def test_classify_unmatched_defaults_to_spec():
    """No signals → spec, no tags, no skip."""

    doc = ParsedDoc()
    result = classify_doc(doc, "any/path.md", include_drafts=True)
    assert result.doc_type == "spec"
    assert result.extra_tags == []
    assert result.skip_reason is None


@pytest.mark.parametrize(
    "status, include_drafts",
    [
        ("draft", False),
        ("Draft", False),  # mixed case
        ("DRAFT", False),
        ("proposed", False),
        ("review", False),
        ("superseded", False),
        ("deprecated", False),
    ],
)
def test_classify_status_gate_skips(status, include_drafts):
    """Every skip status blocks ingestion when include_drafts is off."""

    doc = ParsedDoc(status=status)
    result = classify_doc(doc, "", include_drafts=include_drafts)
    assert result.skip_reason == f"status: {status.lower()}"
    assert result.doc_type == "spec"  # computed even when skipped
    assert result.extra_tags == []


@pytest.mark.parametrize(
    "status, include_drafts",
    [
        ("accepted", True),
        ("Approved", True),  # MADR variation
        ("rejected", True),
        (None, True),
        ("", True),
    ],
)
def test_classify_ingestible_statuses_pass(status, include_drafts):
    """Status not in skip list → no skip."""

    doc = ParsedDoc(status=status)
    result = classify_doc(doc, "", include_drafts=include_drafts)
    assert result.skip_reason is None
    assert result.doc_type == "spec"
    assert result.extra_tags == []


def test_classify_include_drafts_readmits_whole_skip_family():
    """include_drafts=True re-admits every skip status, tagged `draft` (FR-005/TC-009)."""

    for status in ("draft", "proposed", "review", "superseded", "deprecated"):
        doc = ParsedDoc(status=status)
        result = classify_doc(doc, "", include_drafts=True)
        assert result.skip_reason is None
        assert result.doc_type == "spec"  # classification still runs
        assert result.extra_tags == ["draft"]

    # The draft tag merges with classifier tags: reference + draft.
    vision = ParsedDoc(status="proposed", title="Vision statement")
    result = classify_doc(vision, "", include_drafts=True)
    assert result.skip_reason is None
    assert set(result.extra_tags) == {"reference", "draft"}

    # Without the flag the whole family still skips, untagged.
    for status in ("draft", "proposed", "review", "superseded", "deprecated"):
        result = classify_doc(ParsedDoc(status=status), "", include_drafts=False)
        assert result.skip_reason == f"status: {status}"
        assert result.extra_tags == []


def test_workspace_rule_keyword_matches_whole_token_not_substring():
    """Keyword "arch" must not classify a doc titled "search overview"."""

    rules = {"arch": "decision"}
    miss = classify_doc(
        ParsedDoc(title="Search overview"), "", include_drafts=True, rules=rules
    )
    assert miss.doc_type == "spec"  # substring inside "search" does not match
    assert miss.skip_reason is None

    hit = classify_doc(
        ParsedDoc(title="System arch overview"), "", include_drafts=True, rules=rules
    )
    assert hit.doc_type == "decision"  # whole token "arch" matches


def test_workspace_rule_multi_word_keyword_matches_normalized_phrase():
    """Multi-word keywords match the whole phrase, hyphens normalized."""

    rules = {"prior art": "decision"}
    hit = classify_doc(
        ParsedDoc(title="Prior-art survey"), "", include_drafts=True, rules=rules
    )
    assert hit.doc_type == "decision"  # normalized phrase "prior art" matches

    partial = classify_doc(
        ParsedDoc(title="Prior article"), "", include_drafts=True, rules=rules
    )
    assert partial.doc_type == "spec"  # "prior art" is not a whole phrase here


def test_classify_from_parsed_source_doc():
    """Integration: parse then classify (MADR-ish frontmatter)."""

    madr_text = (
        "---\n"
        "status: approved\n"
        "title: Architecture Decision Record\n"
        "---\n"
        "This is the body."
    )
    parsed = parse_source_doc(madr_text)
    result = classify_doc(parsed, "docs/adr/0001-madr.md", include_drafts=True)
    assert result.doc_type == "decision"
    assert result.extra_tags == []
    assert result.skip_reason is None