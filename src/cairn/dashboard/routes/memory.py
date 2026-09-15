"""Agent-memory and task-queue routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

from . import DashboardContext


def register(routes: list[Any], context: DashboardContext) -> None:
    from starlette.routing import Route

    from ..data import get_recent_memories, get_task_queue
    from ..app import MEMORY_TYPES, TASK_STATUSES

    db_path = context.db_path
    knowledge_dir = context.knowledge_dir
    render = context.render
    resolve_selection = context.resolve_selection
    templates = context.templates
    is_hx_request = context.is_hx_request

    def memory(request: Request) -> Response:
        # Type filter, read like every other view's param: absent or blank
        # means no filter; ``type`` outside MEMORY_TYPES falls back to no
        # filter (silent fallback, matching the tasks/wiki filters).
        memory_type = request.query_params.get("type", "all").strip() or "all"
        if memory_type not in MEMORY_TYPES:
            memory_type = "all"
        _, selected_knowledge, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        entries = get_recent_memories(
            selected_knowledge,
            memory_type=None if memory_type == "all" else memory_type,
        )
        return render(
            request,
            "memory.html",
            {
                "memories": entries,
                "memory_types": MEMORY_TYPES,
                "memory_type": memory_type,
                "store_key": store_key,
            },
        )

    def tasks(request: Request) -> Response:
        status = request.query_params.get("status", "all").strip() or "all"
        if status not in TASK_STATUSES:
            status = "all"
        _, selected_knowledge, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        entries = get_task_queue(
            selected_knowledge, status=None if status == "all" else status
        )
        context = {
            "tasks": entries,
            "statuses": TASK_STATUSES,
            "status": status,
            "store_key": store_key,
        }
        if is_hx_request(request):
            # htmx fragment: the status select's results region only.
            return templates.TemplateResponse(
                request, "tasks_results.html", context
            )
        return render(request, "tasks.html", context)

    routes.extend(
        [
            Route("/memory", memory, name="memory"),
            Route("/tasks", tasks, name="tasks"),
        ]
    )
