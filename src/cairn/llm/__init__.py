"""LLM integration: agent-decoupled task queue and client interface.

Cairn never calls an LLM directly. All LLM work flows through a task
queue (OKF Task concepts under .knowledge/_tasks/) that any agent with the
cairn skill can process. Public API:

    from cairn.llm import get_client, create_task, Task
"""
from cairn.llm.client import get_client
from cairn.llm.tasks import Task, create_task

__all__ = ["get_client", "create_task", "Task"]
