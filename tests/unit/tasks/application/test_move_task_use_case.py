"""Unit tests for `MoveTaskUseCase`.

The use case is a dispatch table, and a dispatch table is exactly the thing that
looks obviously right while quietly routing `done -> doing` to `reopen`. So these
assert **which port method each target status calls**, one at a time, rather than
asserting the end state -- a mock called with the wrong method still returns a
`Task`, so a coarser assertion would pass on the bug it exists to catch.

The second thing worth pinning is the *absence* of a source status in the
signature. Each of the three statuses has exactly one backend endpoint that
produces it, so the target alone names the operation, and a caller cannot talk
this use case into reopening a task that was never finished.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.tasks.application.use_cases.move_task import MoveTaskUseCase
from src.tasks.domain.exceptions import InvalidTasksInputError
from src.tasks.domain.value_objects import TaskId, TaskStatus

TASK_ID = TaskId("11111111-1111-1111-1111-111111111111")


class TestDispatch:
    async def test_target_doing_calls_mark_task_doing(self):
        client = AsyncMock()

        await MoveTaskUseCase(tasks_client=client).execute(
            "token", TASK_ID, TaskStatus.DOING
        )

        client.mark_task_doing.assert_awaited_once_with("token", TASK_ID)
        client.mark_task_done.assert_not_awaited()
        client.reopen_task.assert_not_awaited()

    async def test_target_done_calls_mark_task_done(self):
        """And never `reopen`, which is the route a wrong mapping would take."""
        client = AsyncMock()

        await MoveTaskUseCase(tasks_client=client).execute(
            "token", TASK_ID, TaskStatus.DONE
        )

        client.mark_task_done.assert_awaited_once_with("token", TASK_ID)
        client.mark_task_doing.assert_not_awaited()
        client.reopen_task.assert_not_awaited()

    async def test_target_todo_calls_reopen(self):
        client = AsyncMock()

        await MoveTaskUseCase(tasks_client=client).execute(
            "token", TASK_ID, TaskStatus.TODO
        )

        client.reopen_task.assert_awaited_once_with("token", TASK_ID)
        client.mark_task_doing.assert_not_awaited()
        client.mark_task_done.assert_not_awaited()

    async def test_exactly_one_port_method_is_awaited_per_move(self):
        """Belt and braces on the three above.

        Cheap, and it catches the failure mode where a mapping falls through to
        a second call -- e.g. trying `mark-doing` and then falling back to
        `reopen` -- which per-method assertions would each miss.
        """
        for target in TaskStatus:
            client = AsyncMock()

            await MoveTaskUseCase(tasks_client=client).execute("token", TASK_ID, target)

            awaited = [
                name
                for name in ("mark_task_doing", "mark_task_done", "reopen_task")
                if getattr(client, name).await_count
            ]
            assert awaited == [
                {
                    TaskStatus.DOING: "mark_task_doing",
                    TaskStatus.DONE: "mark_task_done",
                    TaskStatus.TODO: "reopen_task",
                }[target]
            ]


class TestReturnValue:
    async def test_the_reloaded_task_comes_back_not_none(self):
        """The backend's response is the first authoritative view of the move.

        `completed_at` has no local equivalent, and a caller echoing the
        pre-move entity would render a card in Done with no completion time --
        then a reload would contradict what the user was just shown.
        """
        client = AsyncMock()
        moved = object()
        client.mark_task_done.return_value = moved

        result = await MoveTaskUseCase(tasks_client=client).execute(
            "token", TASK_ID, TaskStatus.DONE
        )

        assert result is moved


class TestValidation:
    async def test_a_malformed_task_id_fails_before_any_request(self):
        """Not coerced here: the view's `_parse_task_id` is where that happens.

        A string reaching the port would be substituted into a URL by the
        adapter, turning a bad URL segment into a 404 that says "task not
        found" about a task the user can see.
        """
        client = AsyncMock()
        use_case = MoveTaskUseCase(tasks_client=client)

        with pytest.raises(InvalidTasksInputError):
            await use_case.execute("token", "not-a-uuid", TaskStatus.DOING)

        client.mark_task_doing.assert_not_awaited()

    @pytest.mark.parametrize("bad", ["in-progress", "TODO", "", None, 3])
    async def test_an_unknown_status_is_rejected_before_any_request(self, bad):
        """This is the one client-supplied value on the route.

        `TaskStatus(bad)` would raise a bare `ValueError`, which the view's
        `except DomainException` does not catch -- so a stale or tampered board
        posting `status=archived` would be a 500 rather than a flash.
        """
        client = AsyncMock()
        use_case = MoveTaskUseCase(tasks_client=client)

        with pytest.raises(InvalidTasksInputError):
            await use_case.execute("token", TASK_ID, bad)

        for name in ("mark_task_doing", "mark_task_done", "reopen_task"):
            assert getattr(client, name).await_count == 0

    async def test_a_case_variant_is_still_rejected(self):
        """The wire values are lower-case, and case-folding would be lenient.

        Accepting `TODO` here would mean the BFF and the backend disagreed about
        the vocabulary while both appearing to work.
        """
        client = AsyncMock()

        with pytest.raises(InvalidTasksInputError):
            await MoveTaskUseCase(tasks_client=client).execute("token", TASK_ID, "TODO")
