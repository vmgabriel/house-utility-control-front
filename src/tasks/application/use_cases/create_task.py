"""Create a task in the backlog."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from src.shared.domain.ports.clock import Clock
from src.tasks.application.ports import TasksApiClient
from src.tasks.domain.entities import MAX_DESCRIPTION_LENGTH, Task
from src.tasks.domain.exceptions import InvalidTaskError, InvalidTasksInputError
from src.tasks.domain.value_objects import (
    Duration,
    GoalId,
    Priority,
)


@dataclass(frozen=True, slots=True)
class CreateTaskUseCase:
    """Validate a task form post, then create it through the port.

    Every check here is a rule the BFF can enforce without a round trip, kept
    because failing after the POST would give the user a 400 whose message
    comes from a DRF serializer they cannot see the source of. The domain does
    the structural validation (name length, description length, hours precision,
    importance bounds) by construction; what is left here is the form-shaped
    concern -- parsing loose strings and rejecting values that are meaningless
    rather than merely malformed.

    ``due_date`` may not be in the past, and today is allowed. A task is work
    still to be done, so one dated before the day it was written down was either
    a typo in the year, or work the user already let lapse and is now recording
    for the first time. Both are worth a deliberate re-entry rather than a
    backlog row that opens already flagged overdue.

    Allowing today keeps the common case working -- "do this before I go home" is
    the single most frequent due date there is, and a rule that made it invalid
    would be one people learned to work around by picking tomorrow.

    Note the consequence: with past dates rejected, a task can only become
    overdue by the clock reaching its due date, never by being created that way.
    That is why `Task.is_overdue` compares against "today" rather than being
    evaluated at creation.

    The check needs "today", so it goes through the injected `Clock` and stays
    testable.
    """

    tasks_client: TasksApiClient
    clock: Clock
    #: Highest effort a single task may claim. Not a domain limit -- it is a
    #: statement about what a working day can absorb, and a daily plan is built
    #: from tasks like this one, so a 500-hour estimate is a typo, not a plan.
    max_estimated_hours: Decimal = Decimal("24")

    async def execute(
        self,
        access_token: str,
        *,
        name: str,
        description: str | None = None,
        goal_id: GoalId | None = None,
        due_date: date | None = None,
        priority: Priority = Priority.MEDIUM,
        estimated_hours: Decimal | str | None = None,
    ) -> Task:
        """Create a task from a validated form.

        `priority` is the only importance-like input: it maps onto the backend's
        `importance` field, which is a low/medium/high choice. The 1-5
        `Task.importance` is derived from it rather than accepted, because the
        backend has no field to store a number -- accepting one would let a
        caller set a value the API silently drops.
        """
        if not isinstance(name, str) or not name.strip():
            raise InvalidTasksInputError("Task name is required.")

        if goal_id is not None and not isinstance(goal_id, GoalId):
            # Not coerced from a string: a wrong goal id means the template and
            # the domain have drifted, and guessing would attach the task to
            # whichever goal the string happened to parse as.
            raise InvalidTasksInputError("goal_id must be a GoalId or None.")

        if not isinstance(priority, Priority):
            raise InvalidTasksInputError("priority must be a Priority.")

        if due_date is not None:
            if type(due_date) is not date:
                raise InvalidTasksInputError("due_date must be a date.")
            # Strictly less-than, so today is allowed through. A `>=` here
            # would reject today and every future date while admitting every past
            # one -- the exact opposite of what the message says.
            if due_date < self.clock.today():
                raise InvalidTasksInputError("due_date cannot be in the past.")

        duration = self._parse_estimated_hours(estimated_hours)
        resolved_description = self._normalize_description(description)

        # Every invariant is satisfied by now, so `Task.create` cannot fail for a
        # reason the user could act on -- which is why nothing here needs to
        # catch `InvalidTaskError`.
        task = Task.create(
            name=name,
            goal_id=goal_id,
            description=resolved_description,
            due_date=due_date,
            importance=priority.importance,
            priority=priority,
            estimated_hours=duration,
        )
        return await self.tasks_client.create_task(access_token, task)

    def _parse_estimated_hours(
        self, estimated_hours: Decimal | str | None
    ) -> Duration | None:
        """Turn the form's hours field into a `Duration`, or `None`.

        An empty field posts `""`, which means "not estimated" rather than zero.
        So does an explicit zero: a Duration of zero would render as "0h
        estimated" on the task row, claiming the user made an estimate of no
        effort. Absent is the honest reading of both.
        """
        if estimated_hours is None:
            return None
        raw = str(estimated_hours).strip()
        if not raw:
            return None
        try:
            duration = Duration(Decimal(raw))
        except (InvalidOperation, ValueError, ArithmeticError) as exc:
            raise InvalidTasksInputError(
                "Estimated hours must be a number, such as 1.5."
            ) from exc
        except InvalidTaskError as exc:
            # Negative, or finer than a hundredth of an hour. Re-raised with the
            # form's own wording so the message names the field the user filled in.
            raise InvalidTasksInputError(
                f"Estimated hours are not acceptable: {exc}"
            ) from exc
        if not duration.is_estimated:
            return None
        if duration.amount > self.max_estimated_hours:
            raise InvalidTasksInputError(
                f"An estimate cannot exceed {self.max_estimated_hours} hours."
            )
        return duration

    @staticmethod
    def _normalize_description(description: str | None) -> str | None:
        """Trim and bound the markdown description before the domain sees it.

        Checked here rather than left to the constructor so the message reaches
        the form with the field name attached, the same reason the rest of this
        use case validates rather than delegates.
        """
        if description is None:
            return None
        if not isinstance(description, str):
            raise InvalidTasksInputError("Description must be text.")
        trimmed = description.strip()
        if not trimmed:
            return None
        if len(trimmed) > MAX_DESCRIPTION_LENGTH:
            raise InvalidTasksInputError(
                f"Description cannot exceed {MAX_DESCRIPTION_LENGTH} characters."
            )
        return trimmed
