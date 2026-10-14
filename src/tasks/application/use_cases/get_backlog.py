"""Fetch the backlog of open tasks."""

from dataclasses import dataclass

from src.tasks.application.ports import TasksApiClient
from src.tasks.domain.entities import Task


@dataclass(frozen=True, slots=True)
class GetBacklogUseCase:
    """List the authenticated user's open tasks.

    There is nothing to validate and nothing to filter, and that is deliberate:
    the backend already scopes the backlog to the caller and to not-done tasks,
    so any filtering here would be a second, divergent definition of "backlog"
    that drifts the first time the backend changes its ordering.

    Ordering is the backend's, too. Sorting client-side by due date would
    silently reorder a list the backend ranked by importance, and a user who
    has learned the order would find it shuffled on every load.
    """

    tasks_client: TasksApiClient

    async def execute(self, access_token: str) -> list[Task]:
        return await self.tasks_client.get_backlog(access_token)
