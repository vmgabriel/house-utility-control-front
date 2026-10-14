"""Read one goal together with the tasks filed under it."""

from __future__ import annotations

from dataclasses import dataclass

from src.tasks.application.ports import TasksApiClient
from src.tasks.domain.entities import Goal, GoalProgress, Task
from src.tasks.domain.exceptions import InvalidTasksInputError
from src.tasks.domain.value_objects import GoalId


@dataclass(frozen=True, slots=True)
class GetGoalDetailUseCase:
    """Fetch a goal, its tasks, and the progress those tasks represent.

    Two reads, not one, because the backend has two endpoints and no aggregate:
    `GET /goals/<uuid:pk>/` for the goal and `GET /tasks/?goal_id=<uuid>` for
    its tasks. There is no `/goals/<id>/tasks/` and no nested representation --
    `GoalSerializer` carries a `task_count` and nothing else -- so any single-call
    version of this use case would have to invent one of them.

    The tasks read is deliberately **not** status-filtered. The page this backs
    lists the goal's finished tasks as well as its open ones, and the progress
    bar is the share of all the goal's estimated hours that are done; a
    `?status=done` read could not produce the second of those at all.

    The two requests are issued sequentially rather than concurrently. They are
    independent and could be gathered, but they are two cheap reads on a page
    that also renders markdown, and the sequential version keeps the failure
    ordering obvious: a goal that does not exist fails before its tasks are
    requested at all, instead of both requests racing to fail.
    """

    tasks_client: TasksApiClient

    async def execute(
        self, access_token: str, goal_id: GoalId
    ) -> tuple[Goal, list[Task], GoalProgress]:
        if not isinstance(goal_id, GoalId):
            # Built here rather than coerced from a string, so a malformed id in
            # the URL fails with a message the user can act on rather than a 500
            # from inside the adapter's UUID parse.
            raise InvalidTasksInputError("goal_id must be a GoalId.")
        goal = await self.tasks_client.get_goal(access_token, goal_id)
        tasks = await self.tasks_client.get_tasks_by_goal(access_token, goal_id)
        return goal, tasks, GoalProgress.from_tasks(tasks)
