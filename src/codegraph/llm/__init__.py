"""LLM integration: agent-decoupled task queue and client interface.

Codegraph never calls an LLM directly. All LLM work flows through a task
queue (OKF Task concepts under .knowledge/_tasks/) that any agent with the
codegraph skill can process. Public API:

    from codegraph.llm import get_client, create_task, Task
"""
from codegraph.llm.client import get_client
from codegraph.llm.tasks import Task, create_task

__all__ = ["get_client", "create_task", "Task"]
