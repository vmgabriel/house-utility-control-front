"""Delete one task.

A use case with no logic at all, which is still worth having: it is the seam
where the 404 is decided. The delete route cannot tell the difference between
"you deleted it" and "it was already gone" on its own, and the two deserve
different wording -- the first is a confirmation, the second is a correction,
because the row the user was looking at had already stopped existing.
"""

from __future__ import annotations

from src.tasks.application.ports import TasksApiClient
from src.tasks.domain.value_objects import TaskId


class DeleteTaskUseCase:
    """Remove a task and every reference to it.

    Returns nothing. The backend answers `204`, so there is no deleted
    representation to map, and returning one would mean inventing a `Task` the
    user can no longer act on.
    """

    def __init__(self, *, tasks_client: TasksApiClient) -> None:
        self._tasks = tasks_client

    async def execute(self, access_token: str, task_id: TaskId) -> None:
        await self._tasks.delete_task(access_token, task_id)


__all__ = ["DeleteTaskUseCase"]
