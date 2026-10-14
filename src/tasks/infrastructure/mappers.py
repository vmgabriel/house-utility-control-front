"""Translate raw DRF tasks JSON into domain entities.

These mappers take **already-parsed JSON** -- plain ``dict`` and ``list[dict]``
-- never an ``httpx.Response``. Keeping the transport out of the signature is
what lets the application layer stay free of httpx (AGENTS.md, constraint 2),
and it is why the same functions are usable directly in unit tests with
hand-built payloads.

Four contract details drive the implementation, all confirmed against
``apps.tasks.interfaces.serializers`` in the backend repository:

1. **Decimals arrive as strings.** DRF's ``COERCE_DECIMAL_TO_STRING`` is on by
   default, so ``estimated_hours`` is ``"2.50"``, not ``2.5``. Hours go through
   :class:`Duration`, which parses and bounds them, rather than being trusted as
   floats -- ``0.1 + 0.2`` drift inside a daily total is invisible until the
   hours bar reads 7.9999999h.

2. **List endpoints return bare arrays.** The tasks views are plain ``APIView``
   classes, so DRF's default pagination does not apply and there is no
   ``count``/``results`` envelope to unwrap.

3. **Nullable columns stay nullable.** ``goal_id``, ``description``,
   ``due_date``, and ``estimated_hours`` are all nullable in the model, so a
   ``null`` maps to ``None`` rather than being defaulted to a placeholder.

4. **The stored verdict is mapped, not recomputed.** ``is_overwhelmed`` is the
   backend's own computation and is carried into `Task.server_is_overwhelmed`
   verbatim, including the difference between ``False`` (the backend said no)
   and ``None`` (the backend did not say). See :mod:`src.tasks.domain.entities`
   for why those two must not be collapsed.

The hand-written pydantic schemas in :mod:`src.tasks.infrastructure.schemas`
are authoritative over the drf-spectacular OpenAPI schema (AGENTS.md, rule 7).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from src.tasks.domain.entities import DailyPlan, Goal, Task
from src.tasks.domain.exceptions import InvalidTasksInputError
from src.tasks.domain.value_objects import (
    DailyPlanId,
    Duration,
    GoalId,
    GoalStatus,
    Importance,
    LlmHealth,
    Priority,
    TaskId,
    TaskStatus,
)
from src.tasks.infrastructure.schemas import (
    DRFDailyPlanResponse,
    DRFGoalResponse,
    DRFLlmHealthResponse,
    DRFTaskResponse,
)


def _parse_date(raw: str | None, entity: str, field: str) -> date | None:
    """Parse a nullable ISO-8601 date (``YYYY-MM-DD``)."""
    if raw is None or raw == "":
        return None
    text = str(raw)
    # Checked before `fromisoformat`, which would raise first and report the
    # less useful "not a valid date" for what is really a datetime in a date
    # field. DRF would send one only through a hand-written serializer, and the
    # time component would then be silently dropped by `fromisoformat` instead.
    if "T" in text:
        raise InvalidTasksInputError(
            f"{entity} '{field}' must be a date, not a datetime: {raw!r}."
        )
    try:
        return date.fromisoformat(text)
    except (ValueError, TypeError) as exc:
        raise InvalidTasksInputError(
            f"{entity} '{field}' is not a valid date: {raw!r}."
        ) from exc


def _parse_hours(raw: str | None, entity: str, field: str) -> Duration | None:
    """Parse a nullable decimal hours field into a :class:`Duration`.

    ``None`` and ``""`` both mean "not estimated". An unparseable or out-of-range
    value raises rather than defaulting: a task whose estimate silently became
    ``None`` would lose the overwhelmed badge, which is the one signal on the
    backlog page that prompts the user to decompose it.
    """
    if raw is None or raw == "":
        return None
    try:
        return Duration(Decimal(str(raw)))
    except (InvalidOperation, ValueError, ArithmeticError) as exc:
        raise InvalidTasksInputError(
            f"{entity} '{field}' is not a valid number of hours: {raw!r}."
        ) from exc


def _parse_enum(raw: Any, enum_cls: Any, entity: str, field: str):
    """Parse an enum-valued wire string.

    An unknown member is a backend change, so it is reported rather than coerced
    or defaulted: defaulting would render the wrong label in the UI with no
    signal that the contract moved. The allowed values are named in the message
    because the fix is a one-line enum addition and the person reading this is
    that person.
    """
    try:
        return enum_cls(str(raw))
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_cls)
        raise InvalidTasksInputError(
            f"{entity} '{field}' value {raw!r} is not one of: {allowed}."
        ) from exc


def map_task(data: DRFTaskResponse) -> Task:
    """Build a :class:`Task` from one validated DRF task.

    `is_overwhelmed` is stored on the entity as `server_is_overwhelmed` rather
    than being applied here, so the distinction between "the backend said no"
    and "the backend said nothing" survives all the way to the badge.
    """
    return Task(
        id=TaskId(data.id),
        name=data.name,
        goal_id=GoalId(data.goal_id) if data.goal_id else None,
        description=data.description,
        due_date=_parse_date(data.due_date, "Task", "due_date"),
        importance=Importance(data.importance),
        priority=_parse_enum(data.priority, Priority, "Task", "priority"),
        estimated_hours=_parse_hours(data.estimated_hours, "Task", "estimated_hours"),
        status=_parse_enum(data.status, TaskStatus, "Task", "status"),
        is_checked_by_llm=data.is_checked_by_llm,
        llm_evaluation_failed=data.llm_evaluation_failed,
        server_is_overwhelmed=data.is_overwhelmed,
    )


def map_goal(data: DRFGoalResponse) -> Goal:
    """Build a :class:`Goal` from one validated DRF goal."""
    return Goal(
        id=GoalId(data.id),
        name=data.name,
        description=data.description,
        due_date=_parse_date(data.due_date, "Goal", "due_date"),
        status=_parse_enum(data.status, GoalStatus, "Goal", "status"),
    )


def map_daily_plan(data: DRFDailyPlanResponse) -> DailyPlan:
    """Build a :class:`DailyPlan` from one validated DRF daily plan.

    `total_hours` is mapped as sent. It is the backend's own sum of the day's
    tasks, and recomputing it here would need every task re-fetched to be
    comparable -- a second implementation of arithmetic that is already stored,
    and free to disagree with what the user was already shown.
    """
    return DailyPlan(
        id=DailyPlanId(data.id),
        plan_date=_parse_date(data.date, "DailyPlan", "date"),
        task_ids=[TaskId(raw) for raw in data.task_ids],
        total_hours=_parse_hours(data.total_hours, "DailyPlan", "total_hours"),
        generated_by_llm=data.generated_by_llm,
    )


def map_llm_health(data: DRFLlmHealthResponse) -> LlmHealth:
    """Build an :class:`LlmHealth` from the validated health probe's body.

    This mapper is only reached on a 2xx: a failed probe is raised as an error by
    the adapter and becomes `LlmHealth.indeterminate` in the use case, so the
    unknown state is never forged from a body that does not carry a verdict.
    """
    return LlmHealth(available=data.available, detail=data.detail)


def task_to_payload(task: Task) -> dict[str, Any]:
    """Serialise a :class:`Task` into the create-task request body.

    Only the fields the user supplied are sent. `status`, `is_checked_by_llm`,
    `llm_evaluation_failed`, and the backend's own `is_overwhelmed` are all
    derived server-side, so sending them would either be rejected as unknown
    fields or -- worse -- let a client assert a verdict it did not compute.

    Nullable fields are omitted rather than sent as ``null``: a create payload
    with an explicit ``null`` goal is not the same request as one that never
    mentions the field, and the second is what a new task actually means.
    """
    payload: dict[str, Any] = {
        "name": task.name,
        "priority": task.priority.value,
        "importance": int(task.importance),
    }
    if task.description is not None:
        payload["description"] = task.description
    if task.goal_id is not None:
        payload["goal_id"] = str(task.goal_id)
    if task.due_date is not None:
        payload["due_date"] = task.due_date.isoformat()
    if task.estimated_hours is not None:
        # A string, to match DRF's decimal wire format exactly. A float would be
        # at the mercy of binary rounding on the way out.
        payload["estimated_hours"] = str(task.estimated_hours.amount)
    return payload


__all__ = [
    "map_daily_plan",
    "map_goal",
    "map_llm_health",
    "map_task",
    "task_to_payload",
]
