"""Regression tests for the procedural workflow layer (2026-07-24).

codegraph's answer to the gap flagged when comparing against LeanKG's
procedural ontology (`kg_trace_workflow`, live-watched ontology YAML).
Deliberately implemented as a plain knowledge doc (`doc_type="workflow"`)
rather than a new watched/synced store -- see src/knowledge/workflow.py's
module docstring for the full rationale. These tests exist to pin down:
  - a workflow inherits doc_status lifecycle + archived-filtering for free
    from the existing knowledge layer, with no extra plumbing
  - trace_workflow resolves by title, slug, or full concept_id
  - trace_workflow does NOT filter on doc_status (unlike search_knowledge)
"""
from __future__ import annotations

import tempfile

import pytest

from codegraph.knowledge.store import get_document, update_status
from codegraph.knowledge.workflow import (
    add_workflow,
    list_workflows,
    render_steps_body,
    trace_workflow,
)
from codegraph.okf.bundle import OKFBundle


@pytest.fixture
def bundle():
    with tempfile.TemporaryDirectory() as tmp:
        yield OKFBundle(tmp)


SAMPLE_STEPS = [
    {"name": "Cut branch", "description": "Branch off main", "symbol": "deploy_hotfix", "file": "src/cli.py"},
    {"name": "Run tests"},
    {"name": "Merge", "description": "Merge to main"},
]


class TestRenderStepsBody:
    def test_includes_title_and_all_steps_in_order(self):
        body = render_steps_body("Deploy Hotfix", SAMPLE_STEPS)
        assert "# Deploy Hotfix" in body
        assert "1. **Cut branch**" in body
        assert "2. **Run tests**" in body
        assert "3. **Merge**" in body
        # Order matters -- step 1 text must appear before step 2's.
        assert body.index("Cut branch") < body.index("Run tests") < body.index("Merge")

    def test_optional_fields_omitted_when_absent(self):
        body = render_steps_body("Simple", [{"name": "Only step"}])
        assert "symbol:" not in body
        assert "file:" not in body

    def test_unnamed_step_gets_positional_fallback_name(self):
        body = render_steps_body("Untitled Steps", [{"description": "no name given"}])
        assert "**Step 1**" in body


class TestAddWorkflow:
    def test_empty_steps_raises(self, bundle):
        with pytest.raises(ValueError):
            add_workflow(bundle, "Empty", steps=[])

    def test_returns_expected_concept_id_shape(self, bundle):
        cid = add_workflow(bundle, "Deploy Hotfix", steps=SAMPLE_STEPS)
        assert cid == "knowledge/workflow/deploy-hotfix"

    def test_steps_round_trip_through_frontmatter(self, bundle):
        cid = add_workflow(bundle, "Deploy Hotfix", steps=SAMPLE_STEPS)
        concept = get_document(bundle, cid)
        assert concept.extensions["steps"] == SAMPLE_STEPS

    def test_defaults_to_active_status(self, bundle):
        cid = add_workflow(bundle, "Deploy Hotfix", steps=SAMPLE_STEPS)
        concept = get_document(bundle, cid)
        assert concept.extensions["doc_status"] == "active"

    def test_tags_and_affects_are_stored(self, bundle):
        cid = add_workflow(
            bundle, "Deploy Hotfix", steps=SAMPLE_STEPS,
            tags=["deploy", "hotfix"], affects_modules=["cli"], affects_repos=["codegraph"],
        )
        concept = get_document(bundle, cid)
        assert concept.tags == ["deploy", "hotfix"]
        assert concept.extensions["affects_modules"] == ["cli"]
        assert concept.extensions["affects_repos"] == ["codegraph"]

    def test_non_workflow_doc_unaffected_by_steps_param(self, bundle):
        """A plain add_document() call with no steps= must not gain a
        'steps' key in its extensions -- see store.py's guard comment."""
        from codegraph.knowledge.store import add_document
        cid = add_document(bundle, "Refund Policy", "body", "business-rule")
        concept = get_document(bundle, cid)
        assert "steps" not in concept.extensions


class TestTraceWorkflow:
    def test_trace_by_exact_concept_id(self, bundle):
        cid = add_workflow(bundle, "Deploy Hotfix", steps=SAMPLE_STEPS)
        result = trace_workflow(bundle, cid)
        assert result is not None
        assert result["title"] == "Deploy Hotfix"
        assert result["steps"] == SAMPLE_STEPS

    def test_trace_by_slug(self, bundle):
        add_workflow(bundle, "Deploy Hotfix", steps=SAMPLE_STEPS)
        result = trace_workflow(bundle, "deploy-hotfix")
        assert result is not None
        assert result["title"] == "Deploy Hotfix"

    def test_trace_by_title_case_insensitive(self, bundle):
        add_workflow(bundle, "Deploy Hotfix", steps=SAMPLE_STEPS)
        result = trace_workflow(bundle, "DEPLOY hotfix")
        assert result is not None
        assert result["title"] == "Deploy Hotfix"

    def test_trace_missing_returns_none(self, bundle):
        assert trace_workflow(bundle, "Nonexistent Workflow") is None

    def test_steps_returned_in_original_order(self, bundle):
        add_workflow(bundle, "Deploy Hotfix", steps=SAMPLE_STEPS)
        result = trace_workflow(bundle, "Deploy Hotfix")
        names = [s["name"] for s in result["steps"]]
        assert names == ["Cut branch", "Run tests", "Merge"]

    def test_trace_does_not_filter_archived_unlike_search(self, bundle):
        """trace_workflow is a targeted lookup by name/id the caller already
        knows about -- it must still resolve an archived workflow (with
        doc_status surfaced for the caller to act on), unlike
        search_knowledge's default archived-exclusion."""
        cid = add_workflow(bundle, "Deploy Hotfix", steps=SAMPLE_STEPS)
        update_status(bundle, cid, "archived")
        result = trace_workflow(bundle, "Deploy Hotfix")
        assert result is not None
        assert result["doc_status"] == "archived"
        assert result["steps"] == SAMPLE_STEPS


class TestListWorkflows:
    def test_lists_only_workflow_doc_type(self, bundle):
        from codegraph.knowledge.store import add_document
        add_workflow(bundle, "Deploy Hotfix", steps=SAMPLE_STEPS)
        add_document(bundle, "Refund Policy", "body", "business-rule")

        workflows = list_workflows(bundle)
        assert len(workflows) == 1
        assert workflows[0].title == "Deploy Hotfix"

    def test_status_filter_passthrough(self, bundle):
        cid = add_workflow(bundle, "Deploy Hotfix", steps=SAMPLE_STEPS)
        update_status(bundle, cid, "archived")
        add_workflow(bundle, "Incident Response", steps=[{"name": "Page oncall"}])

        active_only = list_workflows(bundle, status="active")
        assert len(active_only) == 1
        assert active_only[0].title == "Incident Response"
