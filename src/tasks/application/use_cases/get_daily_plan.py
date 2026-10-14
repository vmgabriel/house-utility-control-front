"""Fetch the daily plan for a date."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.shared.domain.ports.clock import Clock
from src.tasks.application.ports import TasksApiClient
from src.tasks.domain.entities import DailyPlan
from src.tasks.domain.exceptions import InvalidTasksInputError


@dataclass(frozen=True, slots=True)
class GetDailyPlanUseCase:
    """Fetch the plan for `plan_date`, defaulting to today.

    An absent plan is `None`, not an error: most days have no plan until the
    user asks the LLM to build one, and "nothing planned" is a page to render
    rather than a 404. The view decides what an empty day looks like.

    Past and future dates are both allowed. Planning tomorrow is the main use
    case, and blocking it here would make the page useless the evening before --
    the one moment a user plans tomorrow. What *is* blocked is a date before
    the epoch of the product, which is always a malformed query parameter.
    """

    tasks_client: TasksApiClient
    clock: Clock

    async def execute(
        self, access_token: str, plan_date: date | None = None
    ) -> DailyPlan | None:
        if plan_date is not None and type(plan_date) is not date:
            raise InvalidTasksInputError("plan_date must be a date.")
        target = plan_date if plan_date is not None else self.clock.today()
        if target.year < 1970:
            raise InvalidTasksInputError("plan_date is implausibly far in the past.")
        return await self.tasks_client.get_daily_plan(access_token, target)
