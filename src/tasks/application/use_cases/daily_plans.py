"""Queue an assistant plan for a day, and schedule a task onto one.

Both of these are the *write* side of the daily plan, and both exist here
rather than in the views for the same reason: each one has to answer a question
the API cannot answer in a single request.

**`AddTaskToTodayPlanUseCase` is the interesting one.** The user said "put this
task in today's plan". The endpoint they mean is
`POST /daily-plans/<uuid:pk>/add-task/`, which is keyed on a **plan UUID**, not
on a date -- so a view holding only "today" cannot call it. There is no
date-keyed add-task route, and there is no way to add a task to a day that does
not have one, because the path segment is the plan's own id.

That leaves two shapes for this use case. It could refuse when the day is
unplanned, which would make the backlog's "Add to today" button dead on most
days -- plans exist only once the nightly scheduler or a manual request has run,
so a user building tomorrow's day in the evening would very often be looking at
an unplanned date. Or it could resolve the day and open it if it is missing,
which is what it does, using the backend's own `GetOrCreateDailyPlan`. The second
is the only one where the button the product asked for works on the day the user
is actually on.

The alternative -- hiding the button unless a plan already exists -- was
rejected for the same reason: it would be a button that is usually missing, and
"usually missing" reads as broken. The cost of the second shape is that a
click can create a plan the user did not ask for, so it is called out here and
in the flash wording rather than left to be discovered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.shared.domain.ports.clock import Clock
from src.tasks.application.ports import TasksApiClient
from src.tasks.domain.entities import DailyPlan
from src.tasks.domain.exceptions import DailyPlanNotFoundError, InvalidTasksInputError
from src.tasks.domain.value_objects import TaskId


@dataclass(frozen=True, slots=True)
class GenerateDailyPlanUseCase:
    """Ask the backend to have the assistant plan a day.

    Returns nothing, and the flash this backs says "queued" rather than
    "generated", because that is what happened. The endpoint answers 202 and
    hands the work to Celery; the plan is written by a worker some seconds later,
    and may never be written at all if the provider is down. A use case that
    returned a `DailyPlan` would have to either invent one or re-read the day and
    get `None`, and both of those would put a claim in front of the user that the
    backend did not make.

    **There is no "already planned" short-circuit here**, which is worth stating
    because one was expected. The endpoint does not answer 409 for a day it has
    already planned; it re-plans. Deciding not to re-plan is therefore a product
    question about the button rather than a backend rule, and it is answered
    where the button is rendered: the daily page offers this action only for a
    day that has no plan yet. Putting the check here instead would mean every
    caller had to re-implement it, and the one caller that has the information
    already does the right thing.
    """

    tasks_client: TasksApiClient

    async def execute(self, access_token: str, plan_date: date) -> None:
        if type(plan_date) is not date:
            raise InvalidTasksInputError("plan_date must be a date.")
        await self.tasks_client.generate_daily_plan(access_token, plan_date)


@dataclass(frozen=True, slots=True)
class AddTaskToTodayPlanUseCase:
    """Schedule one task onto today's plan, opening the day if it has none.

    Two backend calls in the common case and one in the uncommon one; see the
    module docstring for why the day is resolved here at all.

    The task's own validity is not checked locally. `TaskAlreadyDone` and
    `TaskAlreadyInPlan` are the backend's verdicts on the *current* state of the
    task, which this BFF may hold a stale copy of -- the row on the backlog page
    can be finished by another tab seconds before the click lands. Re-deciding
    either one here would mean guessing at state the backend owns, and the
    409 it answers is a better message than any this could produce.
    """

    tasks_client: TasksApiClient
    clock: Clock

    async def execute(self, access_token: str, task_id: TaskId) -> DailyPlan:
        if not isinstance(task_id, TaskId):
            # Built here rather than coerced from a string, so a malformed id in
            # the URL fails with a message the user can act on instead of a 500
            # from inside the adapter's UUID parse.
            raise InvalidTasksInputError("task_id must be a TaskId.")
        today = self.clock.today()
        plan = await self.tasks_client.get_daily_plan(access_token, today)
        if plan is None:
            plan = await self.tasks_client.create_daily_plan(access_token, today)
        if plan.id is None:
            # Unreachable through the real adapter, which cannot map a plan
            # without an id (`DRFDailyPlanResponse.id` is required). It is
            # checked rather than asserted because the next line would
            # otherwise build a request against `None`, and the resulting 404
            # would say "plan not found" about a plan this BFF just read.
            raise DailyPlanNotFoundError(
                "Today's plan came back without an id, so the task cannot be scheduled."
            )
        return await self.tasks_client.add_task_to_daily_plan(
            access_token, plan.id, task_id
        )
