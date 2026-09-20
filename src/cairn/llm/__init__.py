"""LLM integration: agent-decoupled task queue and client interface."""
from cairn.llm.client import get_client
from cairn.llm.tasks import Task, create_task

__all__ = ["get_client", "create_task", "Task"]
