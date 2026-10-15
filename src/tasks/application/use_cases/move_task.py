"""Move one task between the board's columns.

The board's vocabulary is "move this card to Doing". The backend's is three named
operations, and this is the one place that translates between them -- a
deliberately boring piece of code whose whole job is to keep the mapping in one
file.

**What the client sends is only the target status, never the source.** That is
not an omission. Each of the three statuses has exactly one backend endpoint
that produces it, so the target alone identifies the operation; and omitting the
source means the BFF cannot be talked into running `reopen` on a task that was
never finished just because a browser said so. What the backend thinks the task's
status is, the backend decides.

**The local check is an offer, not a gate.** `Task.can_transition_to` refuses to
*offer* the two moves the backend cannot perform (`doing -> todo`, `done ->
doing`), which is what keeps the board from showing arrows that always fail. It
does not reject a move that arrives anyway: the row on screen may be stale --
moved in another tab since the page was rendered -- and in that case the backend's
409 is the correct and current answer, not the BFF's cached opinion. So the
check lives here as a fast local rejection *and* the 409 is still handled, and
the two are deliberately redundant rather than one of them being a backstop for a
bug in the other.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.tasks.application.ports import TasksApiClient
from src.tasks.domain.entities import Task
from src.tasks.domain.exceptions import InvalidTasksInputError
from src.tasks.domain.value_objects import TASK_TRANSITIONS, TaskId, TaskStatus


@dataclass(frozen=True, slots=True)
class MoveTaskUseCase:
    """Move one task to `target`, returning it as the backend stored it.

    Returns the reloaded `Task` rather than `None` for the reason
    `TasksApiClient.mark_task_done` documents: the response is the first
    authoritative view of the result. `completed_at` in particular is a
    server-set fact with no local equivalent, and a caller that echoed the
    pre-move entity would render a card in Done with no completion time -- and a
    reload would then disagree with what the user was just shown.
    """

    tasks_client: TasksApiClient

    async def execute(
        self, access_token: str, task_id: TaskId, target: TaskStatus
    ) -> Task:
        if not isinstance(task_id, TaskId):
            # Built here rather than coerced from a string, so a malformed id in
            # the URL fails with a message the user can act on instead of a 500
            # from inside the adapter's UUID parse.
            raise InvalidTasksInputError("task_id must be a TaskId.")
        if not isinstance(target, TaskStatus):
            # Rejected rather than parsed from a string: this is the one value on
            # the route that arrives from the client, and a bare
            # `TaskStatus(raw)` would raise a `ValueError` the view's
            # `except DomainException` does not catch -- turning a tampered form
            # into a 500 instead of a flash.
            raise InvalidTasksInputError("target must be a TaskStatus.")

        transition = TASK_TRANSITIONS[target]
        if transition.name == "start":
            return await self.tasks_client.mark_task_doing(access_token, task_id)
        if transition.name == "complete":
            return await self.tasks_client.mark_task_done(access_token, task_id)
        return await self.tasks_client.reopen_task(access_token, task_id)


__all__ = ["MoveTaskUseCase"]
