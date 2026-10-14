"""Unit tests for the daily-plan write use cases.

Both are thin, and that is the point: what they contain is not arithmetic but
**sequencing**, and sequencing is exactly what a unit test with a fake client is
good at pinning down. The order of the calls is load-bearing -- `add-task` is
keyed on a plan *id*, so resolving the day and opening it if it is missing is
the whole use case -- and an integration test could only observe it indirectly
through what the fake HTTP server was asked for.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.tasks.application.use_cases.daily_plans import (
    AddTaskToTodayPlanUseCase,
    GenerateDailyPlanUseCase,
)
from src.tasks.domain.entities import DailyPlan
from src.tasks.domain.exceptions import (
    DailyPlanNotFoundError,
    InvalidTasksInputError,
)
from src.tasks.domain.value_objects import DailyPlanId, Duration, TaskId

TODAY = date(2026, 10, 5)
PLAN_ID = DailyPlanId(uuid4())
TASK_ID = TaskId(uuid4())


class FrozenClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 5, 12, 0, tzinfo=UTC)

    def today(self) -> date:
        return TODAY


def a_plan(plan_date: date = TODAY) -> DailyPlan:
    return DailyPlan(
        id=PLAN_ID,
        plan_date=plan_date,
        task_ids=[],
        total_hours=Duration(Decimal("3.00")),
    )


class TestGenerateDailyPlan:
    async def test_queues_the_date_the_caller_asked_for(self):
        """Not the server's today.

        The daily page can be showing any date, and the button has to plan the day
        the user is looking at -- sending the current date instead would quietly
        re-plan today from a page about tomorrow.
        """
        client = AsyncMock()
        target = date(2026, 12, 24)

        await GenerateDailyPlanUseCase(tasks_client=client).execute("token", target)

        client.generate_daily_plan.assert_awaited_once_with("token", target)

    async def test_returns_nothing_because_no_plan_exists_yet(self):
        """The endpoint answers 202 and hands the work to Celery.

        A returned `DailyPlan` here would have to be invented: the plan is
        written by a worker after this request is answered, and may never be.
        """
        client = AsyncMock()

        result = await GenerateDailyPlanUseCase(tasks_client=client).execute(
            "token", TODAY
        )

        assert result is None

    async def test_rejects_a_datetime_where_a_date_belongs(self):
        """A `datetime` would be formatted and sent as a date, losing its time.

        The one that matters here is not the time -- the backend reads a `date`
        -- but the silent coercion: `datetime` is a subclass of `date`, so an
        `isinstance` check would pass it through.
        """
        client = AsyncMock()
        use_case = GenerateDailyPlanUseCase(tasks_client=client)

        with pytest.raises(InvalidTasksInputError):
            await use_case.execute("token", FrozenClock().now())

        client.generate_daily_plan.assert_not_awaited()


class TestAddTaskToTodayPlan:
    async def test_uses_the_existing_plan_when_the_day_has_one(self):
        client = AsyncMock()
        client.get_daily_plan.return_value = a_plan()

        await AddTaskToTodayPlanUseCase(
            tasks_client=client, clock=FrozenClock()
        ).execute("token", TASK_ID)

        client.create_daily_plan.assert_not_awaited()
        client.add_task_to_daily_plan.assert_awaited_once_with(
            "token", PLAN_ID, TASK_ID
        )

    async def test_opens_the_day_when_it_has_no_plan(self):
        """Otherwise the button would be dead on most days.

        `add-task` is keyed on a plan UUID, so there is nothing to post to for an
        unplanned day -- and plans only exist once the scheduler or a manual
        request has run. `GetOrCreateDailyPlan` is the backend's own answer to
        exactly this.
        """
        client = AsyncMock()
        client.get_daily_plan.return_value = None
        client.create_daily_plan.return_value = a_plan()

        await AddTaskToTodayPlanUseCase(
            tasks_client=client, clock=FrozenClock()
        ).execute("token", TASK_ID)

        client.create_daily_plan.assert_awaited_once_with("token", TODAY)
        client.add_task_to_daily_plan.assert_awaited_once_with(
            "token", PLAN_ID, TASK_ID
        )

    async def test_looks_up_today_rather_than_a_hardcoded_date(self):
        client = AsyncMock()
        client.get_daily_plan.return_value = a_plan()

        await AddTaskToTodayPlanUseCase(
            tasks_client=client, clock=FrozenClock()
        ).execute("token", TASK_ID)

        client.get_daily_plan.assert_awaited_once_with("token", TODAY)

    async def test_returns_the_reloaded_plan_so_the_view_can_report_the_total(self):
        """The backend echoes the whole `DailyPlanSerializer`.

        That is what lets the flash quote the day's new total without a second
        read, and it is why this is the adapter's return value rather than the
        pre-add plan: only the reloaded one includes the task just scheduled.
        """
        client = AsyncMock()
        reloaded = a_plan()
        client.get_daily_plan.return_value = a_plan()
        client.add_task_to_daily_plan.return_value = reloaded

        result = await AddTaskToTodayPlanUseCase(
            tasks_client=client, clock=FrozenClock()
        ).execute("token", TASK_ID)

        assert result is reloaded

    async def test_rejects_a_malformed_task_id_before_any_request(self):
        client = AsyncMock()
        use_case = AddTaskToTodayPlanUseCase(tasks_client=client, clock=FrozenClock())

        with pytest.raises(InvalidTasksInputError):
            await use_case.execute("token", "not-a-task-id")

        client.get_daily_plan.assert_not_awaited()

    async def test_a_plan_with_no_id_fails_rather_than_posting_to_none(self):
        """Unreachable through the real adapter, which cannot map one.

        Without the check, the next line would build a request against `None` and
        the resulting 404 would say "plan not found" about a plan this BFF had
        just read successfully -- a message about the wrong thing entirely.
        """
        client = AsyncMock()
        client.get_daily_plan.return_value = DailyPlan(plan_date=TODAY, id=None)
        use_case = AddTaskToTodayPlanUseCase(tasks_client=client, clock=FrozenClock())

        with pytest.raises(DailyPlanNotFoundError):
            await use_case.execute("token", TASK_ID)

        client.add_task_to_daily_plan.assert_not_awaited()
