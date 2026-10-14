"""Tests for the task mutation use cases.

These assert on the port call, not just the outcome: a use case that rejected the
input but had already issued the request would be a bug worth catching, and
"did we call the backend?" is the only place it shows up.

The client is hand-written rather than an `AsyncMock` so that a test asserting
"the port received this" reads as a fact about the contract rather than as an
assertion about mock call syntax.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.tasks.application.use_cases.delete_task import DeleteTaskUseCase
from src.tasks.application.use_cases.get_completed_tasks import (
    GetCompletedTasksUseCase,
)
from src.tasks.application.use_cases.update_task import UpdateTaskUseCase
from src.tasks.domain.entities import Task
from src.tasks.domain.exceptions import InvalidTaskError, TaskNotFoundError
from src.tasks.domain.value_objects import Duration, Priority, TaskId

TASK_ID = TaskId("11111111-1111-1111-1111-111111111111")
TOKEN = "access-token"


class RecordingTasksClient:
    """A port stand-in that records what it was asked to do."""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.update_calls: list[tuple[str, TaskId, object]] = []
        self.delete_calls: list[tuple[str, TaskId]] = []
        self.completed_calls: list[str] = []

    async def update_task(self, access_token, task_id, edits):
        self.update_calls.append((access_token, task_id, edits))
        if self.error is not None:
            raise self.error
        return self.result

    async def delete_task(self, access_token, task_id):
        self.delete_calls.append((access_token, task_id))
        if self.error is not None:
            raise self.error

    async def get_completed_tasks(self, access_token):
        self.completed_calls.append(access_token)
        if self.error is not None:
            raise self.error
        return self.result


def _task(**overrides) -> Task:
    payload = {"name": "Ship it", "id": TASK_ID}
    payload.update(overrides)
    return Task(**payload)


class TestUpdateTaskUseCase:
    async def test_passes_the_edits_straight_to_the_port(self):
        client = RecordingTasksClient(result=_task())
        use_case = UpdateTaskUseCase(tasks_client=client)

        await use_case.execute(
            TOKEN,
            TASK_ID,
            name="Renamed",
            description="**bold**",
            due_date=date(2026, 11, 1),
            priority=Priority.HIGH,
            estimated_hours=Duration(2.5),
        )

        _token, task_id, edits = client.update_calls[0]
        assert task_id is TASK_ID
        assert edits.name == "Renamed"
        assert edits.description == "**bold**"
        assert edits.due_date == date(2026, 11, 1)
        assert edits.priority is Priority.HIGH
        assert edits.estimated_hours == Duration(2.5)

    async def test_a_cleared_field_is_sent_rather_than_omitted(self):
        """The distinction the whole object exists for. Omitting these would
        make a cleared due date impossible to express."""
        client = RecordingTasksClient(result=_task())
        use_case = UpdateTaskUseCase(tasks_client=client)

        await use_case.execute(
            TOKEN,
            TASK_ID,
            name="Renamed",
            description=None,
            due_date=None,
            estimated_hours=None,
        )

        _token, _task_id, edits = client.update_calls[0]
        assert edits.description is None
        assert edits.due_date is None
        assert edits.estimated_hours is None

    async def test_an_empty_edit_never_reaches_the_port(self):
        client = RecordingTasksClient(result=_task())
        use_case = UpdateTaskUseCase(tasks_client=client)

        with pytest.raises(InvalidTaskError, match="Provide name"):
            await use_case.execute(TOKEN, TASK_ID)

        assert client.update_calls == []

    async def test_a_blank_name_never_reaches_the_port(self):
        client = RecordingTasksClient(result=_task())
        use_case = UpdateTaskUseCase(tasks_client=client)

        with pytest.raises(InvalidTaskError, match="cannot be empty"):
            await use_case.execute(TOKEN, TASK_ID, name="   ", description="x")

        assert client.update_calls == []

    async def test_returns_the_task_the_backend_sent_back(self):
        """Not the pre-update entity: `is_overwhelmed` is recomputed server-side
        against the task's new estimate."""
        updated = _task(name="Renamed")
        use_case = UpdateTaskUseCase(tasks_client=RecordingTasksClient(result=updated))

        result = await use_case.execute(TOKEN, TASK_ID, name="Renamed")

        assert result is updated

    async def test_lets_a_not_found_error_through_unsoftened(self):
        """The view words it; the use case does not guess at a message."""
        client = RecordingTasksClient(error=TaskNotFoundError("gone"))
        use_case = UpdateTaskUseCase(tasks_client=client)

        with pytest.raises(TaskNotFoundError):
            await use_case.execute(TOKEN, TASK_ID, name="Renamed")


class TestDeleteTaskUseCase:
    async def test_passes_the_id_through(self):
        client = RecordingTasksClient()
        use_case = DeleteTaskUseCase(tasks_client=client)

        assert await use_case.execute(TOKEN, TASK_ID) is None
        assert client.delete_calls == [(TOKEN, TASK_ID)]

    async def test_a_404_is_not_swallowed_into_a_success(self):
        """ "It was already gone" and "I deleted it" are different facts, and the
        view has to be able to say which happened."""
        client = RecordingTasksClient(error=TaskNotFoundError("gone"))
        use_case = DeleteTaskUseCase(tasks_client=client)

        with pytest.raises(TaskNotFoundError):
            await use_case.execute(TOKEN, TASK_ID)


class TestGetCompletedTasksUseCase:
    async def test_asks_the_port_for_the_finished_set(self):
        finished = [_task(status=_done())]
        client = RecordingTasksClient(result=finished)
        use_case = GetCompletedTasksUseCase(tasks_client=client)

        result = await use_case.execute(TOKEN)

        assert result == finished
        assert client.completed_calls == [TOKEN]

    async def test_does_not_filter_or_order_locally(self):
        """The window and the ordering are the backend's, because it is the only
        side that could know when a task was finished. Filtering here would mean
        dropping rows on a field that does not exist -- see the module docstring
        on `completed_at`."""
        rows = [_task(), _task(), _task()]
        use_case = GetCompletedTasksUseCase(
            tasks_client=RecordingTasksClient(result=rows)
        )

        assert len(await use_case.execute(TOKEN)) == 3


def _done():
    from src.tasks.domain.value_objects import TaskStatus

    return TaskStatus.DONE
