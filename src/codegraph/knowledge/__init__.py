"""Document knowledge ingestion and semantic retrieval.

Business documents (policies, specs, design docs) stored as OKF concepts in
the .knowledge/knowledge/ subtree. Searched via lexical + semantic cosine
scan, with a metadata bridge to the code graph via affects_repos.

Public API:

    from codegraph.knowledge import add_document, search_knowledge, trace_workflow
"""
from codegraph.knowledge.search import search_knowledge
from codegraph.knowledge.store import add_document
from codegraph.knowledge.workflow import add_workflow, trace_workflow

__all__ = ["add_document", "search_knowledge", "add_workflow", "trace_workflow"]
