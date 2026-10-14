"""Apply a partial update to one task.

Two things happen here that do not happen for the create path.

**The input is not a `Task`.** `CreateTaskUseCase` takes loose strings because
every field has to be supplied; an edit supplies *some* of them, and which ones
is the whole meaning of the request. `TaskEdits` carries that distinction, and
this use case's job is to turn form values into one -- rejecting a field the
backend will not accept, before spending a round trip on a 400.

**Nothing is validated against the stored task.** The BFF does not hold the task
being edited; the caller's form is its only copy, and a stale form editing a task
somebody else changed is the backend's problem to resolve, not ours to
second-guess. Fetching it first would double the requests on every save and
still not make the result atomic.
"""

from __future__ import annotations

from datetime import date

from src.tasks.application.ports import TasksApiClient
from src.tasks.domain.entities import Task
from src.tasks.domain.value_objects import (
    UNSET,
    Duration,
    Priority,
    TaskEdits,
    TaskId,
    UnsetOr,
)


class UpdateTaskUseCase:
    """Edit a task's name, description, due date, importance, or estimate.

    Deliberately does **not** cover the goal or the status, because the backend
    does not: `UpdateTaskSerializer` accepts neither, and offering a field here
    that is dropped on the way to DRF would be a form that reports a change it
    never made.
    """

    def __init__(self, *, tasks_client: TasksApiClient) -> None:
        self._tasks = tasks_client

    async def execute(
        self,
        access_token: str,
        task_id: TaskId,
        *,
        name: UnsetOr[str] = UNSET,
        description: UnsetOr[str | None] = UNSET,
        due_date: UnsetOr[date | None] = UNSET,
        priority: UnsetOr[Priority] = UNSET,
        estimated_hours: UnsetOr[Duration | None] = UNSET,
    ) -> Task:
        """Apply a validated edit and return the task as the backend stored it.

        Every parameter defaults to `UNSET`, not to `None`, and the difference
        is the whole contract of this method:

        * omitted -> the key is absent from the request -> PATCH's
          ``partial=True`` leaves the stored column alone;
        * ``None`` -> the key is present and explicit -> the column is cleared.

        A `None` default here would have made `execute(token, id, name="X")`
        silently wipe the description, the due date, and the estimate, because
        each would arrive as "clear this". That is not a signature anyone would
        read correctly, so the view converts a blank form field into `None` and
        an absent one into `UNSET`, explicitly, at the point where the form's
        meaning is known.

        An edit with nothing set is rejected before the request: an empty payload
        is what `UpdateTaskSerializer.validate` answers with a 400 (*"Provide
        name, description, due_date, importance, or estimated_hours."*).
        """
        edits = TaskEdits(
            name=name,
            description=description,
            due_date=due_date,
            priority=priority,
            estimated_hours=estimated_hours,
        )
        return await self._tasks.update_task(access_token, task_id, edits)


__all__ = ["UpdateTaskUseCase"]
