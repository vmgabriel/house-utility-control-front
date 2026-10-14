"""List the caller's finished tasks.

**This is not a seven-day window, and the use case is where that has to be
honest.** The brief behind this asked for "completed tasks, last 7 days", and
the backend cannot answer that question:

* `TaskModel` has no `completed_at` column. It has `created_at` and
  `updated_at`, and nothing else.
* `TaskSerializer` exposes neither as a completion time either.
* `Task.mark_as_done` sets `status` and bumps `updated_at`, but so does every
  other transition -- renaming, `promote_priority`, `link_to_goal` all touch it
  too. So `updated_at` is "last touched", not "finished", and filtering on it
  would put a two-year-old task completed yesterday at the top of a "recently
  completed" list, while hiding a task finished eight days ago that happened to
  be edited today.

Substituting `updated_at` here would look like the requested feature and be
wrong in both directions, so the use case asks for the truth it can have --
every `done` task -- and leaves the window to a backend change. The adapter
documents the same gap on the wire side, and `CompletedTasksViewModel` renders
the ordering without claiming it is recency.
"""

from __future__ import annotations

from src.tasks.application.ports import TasksApiClient
from src.tasks.domain.entities import Task


class GetCompletedTasksUseCase:
    """Every task the authenticated user has finished.

    The order is the backend's: `-created_at, -id`, which is *not* completion
    order. There is no field on the wire that would make it one.
    """

    def __init__(self, *, tasks_client: TasksApiClient) -> None:
        self._tasks = tasks_client

    async def execute(self, access_token: str) -> list[Task]:
        return await self._tasks.get_completed_tasks(access_token)


__all__ = ["GetCompletedTasksUseCase"]
