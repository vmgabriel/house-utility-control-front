"""Fetch the user's goals."""

from dataclasses import dataclass

from src.tasks.application.ports import TasksApiClient
from src.tasks.domain.entities import Goal


@dataclass(frozen=True, slots=True)
class GetGoalsUseCase:
    """List the authenticated user's goals.

    Thin for the same reason as :class:`GetBacklogUseCase`: the backend owns the
    list, its membership, and its order.
    """

    tasks_client: TasksApiClient

    async def execute(self, access_token: str) -> list[Goal]:
        return await self.tasks_client.get_goals(access_token)
