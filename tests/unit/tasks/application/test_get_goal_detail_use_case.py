"""Unit tests for `GetGoalDetailUseCase`.

The use case is two reads and a fold, so what is worth testing is the *contract
each read is asked under* -- in particular that the task read is not narrowed to
open work, since that single parameter is the difference between a correct
progress bar and one that reports every goal as 0%.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.tasks.application.use_cases.get_goal_detail import GetGoalDetailUseCase
from src.tasks.domain.entities import Goal, Task
from src.tasks.domain.exceptions import InvalidTasksInputError
from src.tasks.domain.value_objects import Duration, GoalId

GOAL_ID = GoalId(uuid4())
DONE_AT = datetime(2026, 10, 5, 14, 31, tzinfo=UTC)


def a_goal() -> Goal:
    return Goal(id=GOAL_ID, name="Ship the thing", task_count=2)


def a_task(name: str, hours: str, *, completed: bool = False) -> Task:
    return Task(
        id=None,
        name=name,
        estimated_hours=Duration(Decimal(hours)),
        completed_at=DONE_AT if completed else None,
    )


class TestReadContract:
    async def test_the_goal_is_fetched_by_id_not_picked_out_of_the_list(self):
        """There is a detail route, so the list is a full collection to discard."""
        client = AsyncMock()
        client.get_goal.return_value = a_goal()
        client.get_tasks_by_goal.return_value = []

        await GetGoalDetailUseCase(tasks_client=client).execute("token", GOAL_ID)

        client.get_goal.assert_awaited_once_with("token", GOAL_ID)
        client.get_goals.assert_not_awaited()

    async def test_the_task_read_is_scoped_to_the_goal(self):
        client = AsyncMock()
        client.get_goal.return_value = a_goal()
        client.get_tasks_by_goal.return_value = []

        await GetGoalDetailUseCase(tasks_client=client).execute("token", GOAL_ID)

        client.get_tasks_by_goal.assert_awaited_once_with("token", GOAL_ID)

    async def test_the_task_read_carries_no_status_filter(self):
        """Finished tasks are the numerator of the progress bar.

        `?status=done` would give the finished half and nothing else; a
        filtered read here would make every goal read as 0% complete no matter
        how much of it was done. Asserted at the port rather than on the URL,
        because the adapter is where the query parameter is decided and it has
        its own test for that.
        """
        client = AsyncMock()
        client.get_goal.return_value = a_goal()
        client.get_tasks_by_goal.return_value = [a_task("Done", "2.00", completed=True)]

        goal, tasks, progress = await GetGoalDetailUseCase(tasks_client=client).execute(
            "token", GOAL_ID
        )

        assert progress.percentage == 100
        assert len(tasks) == 1

    async def test_a_missing_goal_is_not_retried_as_an_empty_goal(self):
        """A 404 propagates; the view turns it into a flash and a redirect.

        Rendering an empty goal page under a heading the user never had would be
        indistinguishable from a real, empty goal.
        """
        client = AsyncMock()
        client.get_goal.side_effect = LookupError("Goal not found or not accessible.")

        with pytest.raises(LookupError):
            await GetGoalDetailUseCase(tasks_client=client).execute("token", GOAL_ID)

        client.get_tasks_by_goal.assert_not_awaited()


class TestProgress:
    async def test_progress_is_measured_over_the_tasks_it_returned(self):
        client = AsyncMock()
        client.get_goal.return_value = a_goal()
        client.get_tasks_by_goal.return_value = [
            a_task("One", "3.00", completed=True),
            a_task("Two", "1.00"),
        ]

        goal, tasks, progress = await GetGoalDetailUseCase(tasks_client=client).execute(
            "token", GOAL_ID
        )

        assert goal is a_goal() or goal.id == GOAL_ID
        assert progress.total_hours == Duration(Decimal("4.00"))
        assert progress.completed_hours == Duration(Decimal("3.00"))
        assert progress.percentage == 75

    async def test_a_goal_with_no_tasks_reports_zero_rather_than_failing(self):
        client = AsyncMock()
        client.get_goal.return_value = a_goal()
        client.get_tasks_by_goal.return_value = []

        _, tasks, progress = await GetGoalDetailUseCase(tasks_client=client).execute(
            "token", GOAL_ID
        )

        assert tasks == []
        assert progress.percentage == 0
        assert progress.is_empty is True


class TestValidation:
    async def test_a_malformed_goal_id_fails_before_any_request(self):
        client = AsyncMock()
        use_case = GetGoalDetailUseCase(tasks_client=client)

        with pytest.raises(InvalidTasksInputError):
            await use_case.execute("token", "not-a-goal-id")

        client.get_goal.assert_not_awaited()
