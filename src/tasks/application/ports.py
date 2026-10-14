"""Application-layer ports for the tasks context.

A port describes what the tasks use cases *need* from the outside world, in
terms of domain objects only. The concrete adapter (an httpx client speaking the
DRF tasks API) lives in ``src/tasks/infrastructure`` and is injected in the
composition root, so the use cases stay framework-free per AGENTS.md.

Three conventions hold across every method:

**The access token is the first positional argument.** This is a hard
requirement, not a style choice. ``AutoRefreshingRepository`` -- the proxy that
refreshes an expired JWT and retries once on ``401`` -- identifies the token by
position and substitutes the new one into ``args[0]``. A port that put a
``TaskId`` first would have a refreshed JWT silently written into that slot.
Every tasks adapter is wrapped in that proxy, so ``DRF_API_BASE_URL``, JWT
handling, and refresh all come for free.

**Ownership is never passed.** The backend derives the caller from the JWT on
every tasks endpoint, and no tasks view accepts an owner or user query
parameter. Sending one would be ignored, so it is not part of the contract here
either.

**Ports speak domain, never wire.** ``create_task`` takes a :class:`Task` and
returns a :class:`Task`, not a dict and not a `TaskSerializer` payload. The
adapter owns the field-name mapping; a use case that had to know the serializer's
shape would put DRF's contract in the inner layers.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from src.tasks.domain.entities import DailyPlan, Goal, Task
from src.tasks.domain.value_objects import LlmHealth, TaskEdits, TaskId


class TasksApiClient(Protocol):
    """Outbound port for the DRF tasks API."""

    async def get_backlog(self, access_token: str) -> list[Task]:
        """List every open task, most urgent first.

        "Open" means not-``done``. It is applied by the adapter rather than by
        the endpoint, because the backend's `status` filter takes a single value
        from ``todo``/``doing``/``done`` and so cannot express "everything that
        is not done". A done task is still reachable by id, and appears in the
        completed list instead.
        """
        ...

    async def get_task(self, access_token: str, task_id: TaskId) -> Task:
        """Fetch a single task."""
        ...

    async def get_goals(self, access_token: str) -> list[Goal]:
        """List the authenticated user's goals."""
        ...

    async def get_daily_plan(
        self, access_token: str, plan_date: date
    ) -> DailyPlan | None:
        """Fetch the plan for `plan_date`, or `None` when none exists.

        `None` is a real answer, not a failure: most days have no plan until the
        user asks the LLM for one, and an empty day is a page to render rather
        than a 404.
        """
        ...

    async def create_task(self, access_token: str, task: Task) -> Task:
        """Create a task owned by the authenticated user.

        Takes the domain entity, not loose keyword arguments: `Task.create`
        already applied the name, description, and hours invariants, and
        re-validating the same fields as arguments here would give the caller two
        ways to build the same request.
        """
        ...

    async def update_task(
        self, access_token: str, task_id: TaskId, edits: TaskEdits
    ) -> Task:
        """Apply a partial update and return the task as the backend stored it.

        The return value is not a convenience. The backend echoes the whole
        `TaskSerializer` from the entity it just reloaded, which is the only
        authoritative view of the result: `is_overwhelmed` in particular is
        recomputed server-side against the task's new estimate, so returning the
        pre-update entity would show the user a badge that no longer matches
        what a reload would render.
        """
        ...

    async def delete_task(self, access_token: str, task_id: TaskId) -> None:
        """Delete one task.

        Returns nothing because the backend answers `204 No Content`; there is
        no deleted representation to map.
        """
        ...

    async def get_completed_tasks(self, access_token: str) -> list[Task]:
        """List the caller's tasks whose status is ``done``.

        A separate method rather than a `status` argument on
        :meth:`get_backlog` because the two endpoints answer different
        questions: the backlog is the working set, this is the finished set, and
        they are filtered and ordered differently server-side.

        **The ordering is the backend's, and it is not by completion time.** The
        backend sorts on `-created_at, -id` and the task payload carries no
        completion timestamp at all (see :meth:`Task.completed_at` on the
        adapter), so this list runs from most-recently-*created* to oldest, and
        a task created long ago and finished yesterday appears near the bottom.
        Every caller that renders this list says so to the user rather than
        presenting the order as recency.
        """
        ...

    async def trigger_evaluate(self, access_token: str, task_id: TaskId) -> None:
        """Ask the backend to run the LLM evaluation for one task.

        The backend queues this and returns; the result arrives on a later
        request as ``is_checked_by_llm`` / ``llm_evaluation_failed`` on the task.
        Nothing is returned for the same reason -- the POST that starts the work
        has no result to hand back.
        """
        ...

    async def trigger_decompose(self, access_token: str, task_id: TaskId) -> None:
        """Ask the backend to split one task into subtasks with the LLM.

        Asynchronous and fire-and-forget like :meth:`trigger_evaluate`; the
        subtasks appear in the backlog on a later load.
        """
        ...

    async def get_llm_health(self, access_token: str) -> LlmHealth:
        """Report whether the LLM provider is reachable.

        Used by the navbar indicator. It is a port method rather than a direct
        client call from the view so the indicator's failure mode stays a
        `LlmUnavailableError` the view knows how to render as a red dot, rather
        than an unhandled exception in the browser's fetch.
        """
        ...
