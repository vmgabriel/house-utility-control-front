"""List the caller's finished tasks.

**This is still not a seven-day window, but the reason has changed, and the old
reason is worth keeping in mind.** The brief behind this asked for "completed
tasks, last 7 days". When this was first written the backend could not answer
it:

* `TaskModel` had no `completed_at` column -- only `created_at` and
  `updated_at`, and nothing else.
* `updated_at` is "last touched", not "finished": `mark_done` bumps it, but so
  does every other transition, so a filter on it would put a two-year-old task
  completed yesterday at the top and hide one finished eight days ago that
  happened to be edited today.

**The backend has since gained `completed_at`**, read-only and nullable on
`TaskSerializer`, written by `mark_done` and cleared by `reopen`. The window is
now *expressible*. It is still not implemented here, and the reason is no longer
missing data -- it is that this list is the backend's, in the backend's
`-created_at, -id` order, and narrowing and re-sorting it in the BFF would mean
presenting the result under a heading that implies the backend did it. The
ordering this page actually has is stated on it instead.

So the choice moved from "impossible" to "not this page's job", which is a
different trade and worth stating rather than leaving the old explanation in
place. Where a *measured* completion figure is wanted -- the goal progress bar
-- `completed_at` is read directly, via `Task.is_completed`.
"""

from __future__ import annotations

from src.tasks.application.ports import TasksApiClient
from src.tasks.domain.entities import Task


class GetCompletedTasksUseCase:
    """Every task the authenticated user has finished.

    The order is the backend's: `-created_at, -id`, which is *not* completion
    order. `completed_at` would make it one and is deliberately not used here --
    see the module docstring.
    """

    def __init__(self, *, tasks_client: TasksApiClient) -> None:
        self._tasks = tasks_client

    async def execute(self, access_token: str) -> list[Task]:
        return await self._tasks.get_completed_tasks(access_token)


__all__ = ["GetCompletedTasksUseCase"]
