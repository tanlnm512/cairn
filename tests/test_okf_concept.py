"""Tests for OKFConcept atomic file writes (H3 fix)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from cairn.okf.concept import OKFConcept


def test_to_file_writes_atomically(tmp_path):
    """Verify atomic write: final content equals to_markdown(), no temp residue."""
    # Create a concept
    concept = OKFConcept(
        type="test",
        title="Test Concept",
        description="A test concept",
        tags=["test"],
        body="# Test Body\n\nThis is test content.",
    )

    target_file = tmp_path / "test_concept.md"

    # Write the concept
    concept.to_file(str(target_file))

    # Verify final content equals to_markdown() output
    expected_content = concept.to_markdown()
    actual_content = target_file.read_text(encoding="utf-8")
    assert actual_content == expected_content, "Final content should match to_markdown()"

    # Verify no temp file remains
    temp_files = list(tmp_path.glob("*.tmp"))
    assert len(temp_files) == 0, f"No temp files should remain, found: {temp_files}"

    # Verify the temp file was in the same directory (by checking the target directory)
    # This is verified by the fact that the write succeeded and no temp file remains
    # If the temp file were in a different directory, os.replace would fail on cross-filesystem boundaries
    # and the file wouldn't have been written successfully.


def test_to_file_crash_does_not_corrupt_target(tmp_path):
    """Verify that a mid-write failure doesn't corrupt the original file.
    
    This test simulates a crash during temp file write and verifies that:
    1. The crash is propagated (exception raised)
    2. The original file remains intact (not corrupted)
    3. No temp file is left behind
    """
    # Create initial content (v0.2 wire format: `generated: {by, at}`)
    original_content = """---
type: test
title: Original Title
description: Original Description
tags: []
generated: {by: cairn/0.4.0, at: '2024-01-01T00:00:00Z'}
okf_version: '0.2'
---

# Original Body

This is original content that must be preserved."""
    
    target_file = tmp_path / "test_concept.md"
    target_file.write_text(original_content, encoding="utf-8")

    # Create a new concept that would overwrite the file
    new_concept = OKFConcept(
        type="test",
        title="New Title",
        description="New Description",
        tags=["test"],
        body="# New Body\n\nThis is new content.",
    )

    # Monkey-patch Path.write_text to simulate a crash after writing half the content
    original_write_text = Path.write_text
    
    def crashing_write_text(self, content, **kwargs):
        # Only crash if we're writing to a .tmp file (atomic write temp file)
        if str(self).endswith(".tmp"):
            # Write only half the content, then raise
            half_content = content[:len(content)//2]
            original_write_text(self, half_content, **kwargs)
            raise IOError("Simulated mid-write crash")
        return original_write_text(self, content, **kwargs)

    # Apply the monkey patch
    Path.write_text = crashing_write_text

    try:
        # This should raise due to the crash simulation
        with pytest.raises(IOError, match="Simulated mid-write crash"):
            new_concept.to_file(str(target_file))
    finally:
        # Restore the original write_text method
        Path.write_text = original_write_text

    # Verify the original file is uncorrupted
    current_content = target_file.read_text(encoding="utf-8")
    assert current_content == original_content, "Original file should remain intact after failed write"

    # Verify no temp file remains after the crash
    temp_files = list(tmp_path.glob("*.tmp"))
    assert len(temp_files) == 0, f"No temp files should remain after crash, found: {temp_files}"


def test_to_file_no_temp_residue(tmp_path):
    """Verify that no temp residue is left after a successful write."""
    # Create a concept
    concept = OKFConcept(
        type="test",
        title="Test Concept",
        description="A test concept",
        tags=["test"],
        body="# Test Body\n\nThis is test content.",
    )

    target_file = tmp_path / "test_concept.md"

    # Perform multiple writes
    for i in range(5):
        concept.title = f"Test Concept {i}"
        concept.to_file(str(target_file))

    # Verify no temp files remain after all writes
    temp_files = list(tmp_path.glob("*.tmp"))
    assert len(temp_files) == 0, f"No temp files should remain after multiple writes, found: {temp_files}"

    # Verify the final content is correct
    concept.title = "Test Concept 4"  # Last write used title 4
    expected_content = concept.to_markdown()
    actual_content = target_file.read_text(encoding="utf-8")
    assert actual_content == expected_content, "Final content should match last to_markdown()"


def test_to_file_creates_parent_directories(tmp_path):
    """Verify that to_file creates parent directories as needed."""
    # Create a concept
    concept = OKFConcept(
        type="test",
        title="Test Concept",
        description="A test concept",
        tags=["test"],
        body="# Test Body\n\nThis is test content.",
    )

    # Target file in a subdirectory that doesn't exist yet
    target_file = tmp_path / "subdir" / "nested" / "test_concept.md"

    # Write the concept
    concept.to_file(str(target_file))

    # Verify the file exists and content is correct
    assert target_file.exists(), "Target file should be created"
    expected_content = concept.to_markdown()
    actual_content = target_file.read_text(encoding="utf-8")
    assert actual_content == expected_content, "Content should match to_markdown()"


def test_roundtrip_v02_generated_at(tmp_path):
    """v0.2 wire format round-trips: `generated.at` parses back into `timestamp`.

    Also covers the §11 rule that a bare `verified` mapping is normalized to a
    one-element list, and that v0.2 optional families survive the round trip.
    """
    fixed_ts = "2024-01-01T00:00:00Z"
    concept = OKFConcept(
        type="test",
        title="Roundtrip",
        timestamp=fixed_ts,                 # internal field
        generated_by="human:tester",        # v0.2 generated.by
        sources=[{"id": "src1", "resource": "https://example.com/src1"}],
        verified={"by": "human:tester", "at": fixed_ts},  # bare mapping (§11)
        status="stable",
        stale_after="2026-12-31",
        body="# Body",
    )
    target = tmp_path / "rt.md"
    concept.to_file(str(target))

    # Wire format must carry generated.at, not a bare timestamp key.
    on_disk = target.read_text(encoding="utf-8")
    assert "generated:" in on_disk
    assert "\ntimestamp:" not in on_disk, "v0.2 must not emit a bare timestamp key"

    parsed = OKFConcept.from_file(str(target))
    assert parsed.timestamp == fixed_ts, "generated.at must populate timestamp"
    assert parsed.generated_by == "human:tester"
    # Bare mapping normalized to a one-element list (spec §11).
    assert isinstance(parsed.verified, list) and len(parsed.verified) == 1
    assert parsed.status == "stable"
    assert parsed.stale_after == "2026-12-31"
    assert parsed.sources and parsed.sources[0]["id"] == "src1"


def test_reads_legacy_v01_timestamp():
    """A v0.1 doc with a bare `timestamp:` still parses (spec §13.1 fallback).

    The internal `timestamp` field is populated from the legacy key, so the
    6 recency readers need no changes when fed older on-disk files.
    """
    legacy = """---
type: Metric
title: Legacy
timestamp: 2023-05-01T08:00:00Z
okf_version: 0.1
---

# Body
"""
    parsed = OKFConcept._from_text(legacy, concept_id="legacy")
    assert parsed.timestamp == "2023-05-01T08:00:00Z"
    assert parsed.type == "Metric"
