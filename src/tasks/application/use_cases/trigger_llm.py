"""Trigger the LLM's evaluate and decompose actions for one task."""

from __future__ import annotations

from dataclasses import dataclass

from src.tasks.application.ports import TasksApiClient
from src.tasks.domain.exceptions import InvalidTasksInputError
from src.tasks.domain.value_objects import TaskId


@dataclass(frozen=True, slots=True)
class TriggerLlmEvaluationUseCase:
    """Ask the backend to evaluate one task with the LLM.

    The call returns nothing usable: the backend queues the work and the verdict
    lands on a later request as ``is_checked_by_llm`` /
    ``llm_evaluation_failed`` on the task. Returning the task here would be
    misleading -- it would be the task as it was *before* the evaluation, and a
    view that rendered it would show no "evaluated" badge for a few seconds and
    then acquire one on the next page load, with nothing explaining why.

    So this use case returns `None` and the view redirects. The redirect is the
    whole feedback mechanism: the user sees the queue state on the next render,
    and if the LLM is down the flash message comes from
    :class:`LlmUnavailableError` rather than from a silently absent badge.
    """

    tasks_client: TasksApiClient

    async def execute(self, access_token: str, task_id: TaskId) -> None:
        if not isinstance(task_id, TaskId):
            # Built here rather than coerced from a string, so a malformed id in
            # the URL fails with a message the user can act on instead of a 500
            # from inside the adapter's UUID parse.
            raise InvalidTasksInputError("task_id must be a TaskId.")
        # A `TaskNotFoundError` from the port is deliberately not caught: the
        # task was deleted while its row was on screen, which is a different
        # problem from a malformed request and reads differently to the user.
        await self.tasks_client.trigger_evaluate(access_token, task_id)


@dataclass(frozen=True, slots=True)
class TriggerLlmDecompositionUseCase:
    """Ask the backend to split one task into subtasks with the LLM.

    Asynchronous and fire-and-forget, like
    :class:`TriggerLlmEvaluationUseCase`, and for the same reason: the subtasks
    do not exist when this returns.

    The one thing that is different is who may call it. Decomposing a task
    creates new tasks, so it is only offered for a task that is actually
    overloaded -- which is exactly what the UI gates the button on. The gate is
    in the template, not here: this use case cannot see the estimate threshold
    the page rendered with, and re-deriving it would be a second definition of
    "overloaded" that could disagree with the badge the user is looking at.
    """

    tasks_client: TasksApiClient

    async def execute(self, access_token: str, task_id: TaskId) -> None:
        if not isinstance(task_id, TaskId):
            raise InvalidTasksInputError("task_id must be a TaskId.")
        await self.tasks_client.trigger_decompose(access_token, task_id)
