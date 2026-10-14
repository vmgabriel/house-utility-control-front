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
    TaskEdits,
    TaskId,
    TaskStatus,
    Unset,
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

    The wire field ``importance`` carries a `low`/`medium`/`high` choice and
    maps onto the domain's :attr:`Task.priority` -- the backend has one such
    field, and "importance" is its name. `Task.importance`, the integer 1-5
    scale, is a **frontend-only** concept with no wire representation, so it is
    derived here rather than read: see `_derived_importance`.

    `is_overwhelmed` is stored on the entity as `server_is_overwhelmed` rather
    than being applied here, so the distinction between "the backend said no"
    and "the backend said nothing" survives all the way to the badge.
    """
    priority = _parse_enum(data.importance, Priority, "Task", "importance")
    return Task(
        id=TaskId(data.id),
        name=data.name,
        goal_id=GoalId(data.goal_id) if data.goal_id else None,
        description=data.description,
        due_date=_parse_date(data.due_date, "Task", "due_date"),
        priority=priority,
        importance=_derived_importance(priority),
        estimated_hours=_parse_hours(data.estimated_hours, "Task", "estimated_hours"),
        status=_parse_enum(data.status, TaskStatus, "Task", "status"),
        is_checked_by_llm=data.is_checked_by_llm,
        llm_evaluation_failed=data.llm_evaluation_failed,
        server_is_overwhelmed=data.is_overwhelmed,
    )


def _derived_importance(priority: Priority) -> Importance:
    """The 1-5 importance implied by a backend priority level.

    Delegated to `Priority.importance`, which owns the mapping. It lived here
    first and would have drifted from the create path, which needs the same
    answer for a task it has just built.
    """
    return priority.importance


def map_goal(data: DRFGoalResponse) -> Goal:
    """Build a :class:`Goal` from one validated DRF goal.

    `task_count` is mapped straight from the payload. ``GoalSerializer`` computes
    it server-side, and re-deriving it here would mean fetching the backlog to
    count rows the backend already counted.
    """
    return Goal(
        id=GoalId(data.id),
        name=data.name,
        description=data.description,
        due_date=_parse_date(data.due_date, "Goal", "due_date"),
        status=_parse_enum(data.status, GoalStatus, "Goal", "status"),
        task_count=data.task_count or 0,
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
        task_ids=[TaskId(task.id) for task in data.tasks],
        total_hours=_parse_hours(data.total_hours, "DailyPlan", "total_hours"),
        generated_by_llm=data.generated_by_llm,
    )


#: The backend's ``LLM_STATUS_*`` values (``serializers.py``). Listed here
#: rather than imported -- they are wire constants, and the whole point of this
#: table is that it is the place a wire change becomes visible.
LLM_STATUS_OK = "ok"
LLM_STATUS_UNAVAILABLE = "unavailable"


def map_llm_health(data: DRFLlmHealthResponse) -> LlmHealth:
    """Build an :class:`LlmHealth` from the validated health probe's body.

    ``status`` is mapped, never guessed at. An unrecognised value raises rather
    than defaulting, because the two defaults available -- "up" and "down" --
    would both be a lie: we asked whether an assistant exists and got an answer
    we do not understand. Raising lets the adapter and the use case turn it into
    the *unknown* state, which is the only honest rendering of that.

    On success the tooltip carries the model name (``qwen2.5:3b``) rather than
    nothing. The user is looking at an indicator because they are about to ask
    for something, and which model will answer is the first thing worth telling
    them. On failure it carries the provider's own ``error`` string.
    """
    status = data.status.strip().lower()
    if status == LLM_STATUS_OK:
        return LlmHealth(available=True, detail=data.model)
    if status == LLM_STATUS_UNAVAILABLE:
        return LlmHealth(available=False, detail=data.error)
    raise InvalidTasksInputError(
        f"LLM health status {data.status!r} is not one of: "
        f"{LLM_STATUS_OK}, {LLM_STATUS_UNAVAILABLE}."
    )


def task_to_payload(task: Task) -> dict[str, Any]:
    """Serialise a :class:`Task` into the create-task request body.

    Exactly the five fields `CreateTaskSerializer` declares. The mapping is not
    name-for-name:

    * ``importance`` carries the **priority** level. The backend calls it
      "importance" and it is a `low`/`medium`/`high` choice; there is no wire
      field for the frontend's 1-5 `Task.importance`, so it is not sent. Sending
      it would be an integer where a choice is expected, which DRF answers with
      a 400 on `importance`.
    * ``estimated_hours`` is **required** by the serializer -- it is declared
      without `required=False` -- so a task with no estimate sends the task's
      zero `Duration` rather than omitting the key and earning a 400.

    Server-derived fields (`status`, `is_overwhelmed`, the LLM flags) are never
    sent: the backend computes them, and sending them would let a client assert
    a verdict it did not compute.
    """
    payload: dict[str, Any] = {
        "name": task.name,
        "importance": task.priority.value,
        # Required by CreateTaskSerializer. `Duration.zero()` for an unestimated
        # task, which is the backend's own representation of "no effort given".
        "estimated_hours": str(
            (
                task.estimated_hours
                if task.estimated_hours is not None
                else Duration.zero()
            ).amount
        ),
    }
    if task.description is not None:
        payload["description"] = task.description
    if task.goal_id is not None:
        payload["goal_id"] = str(task.goal_id)
    if task.due_date is not None:
        payload["due_date"] = task.due_date.isoformat()
    return payload


def task_edits_to_payload(edits: TaskEdits) -> dict[str, Any]:
    """Serialise a :class:`TaskEdits` into the partial-update request body.

    The counterpart to :func:`task_to_payload`, and the exact inverse problem.
    Create sends five required fields and omits the optional ones; update sends
    **only what was set**, because PATCH is applied with ``partial=True`` and an
    absent key is the backend's "leave this alone". Building the body from
    ``edits.__dict__`` or by always sending every field would silently clear
    whichever fields the caller did not mention.

    Three translations are worth spelling out, because each is a place where the
    naive mapping produces a 400 or a silent no-op:

    * ``description`` -> ``""``. ``UpdateTaskSerializer`` declares it with
      ``allow_blank=True``, and "no description" is an empty string on the
      model column rather than ``null``. Sending ``null`` would be rejected.
    * ``due_date`` -> ``null`` when cleared. ``DateField(allow_null=True)`` is
      explicit here, so this is the one field where clearing is spelled as
      ``null`` and genuinely clears the column.
    * ``estimated_hours`` -> ``"0.00"`` when cleared. ``DecimalField`` is
      declared without ``allow_null``, so ``null`` is a 400; ``0`` is the
      model's own representation of "no effort given", and `Duration.zero()` is
      what `Task.estimated_hours` already uses for an unestimated task.

    ``priority`` goes out as ``importance``, the same rename as on create: one
    wire field, called "importance" by the backend, carrying a
    low/medium/high choice.
    """
    payload: dict[str, Any] = {}
    if not isinstance(edits.name, Unset):
        payload["name"] = edits.name
    if not isinstance(edits.description, Unset):
        payload["description"] = edits.description or ""
    if not isinstance(edits.due_date, Unset):
        payload["due_date"] = edits.due_date.isoformat() if edits.due_date else None
    if not isinstance(edits.priority, Unset):
        payload["importance"] = edits.priority.value
    if not isinstance(edits.estimated_hours, Unset):
        payload["estimated_hours"] = str(
            (
                edits.estimated_hours
                if edits.estimated_hours is not None
                else Duration.zero()
            ).amount
        )
    return payload


__all__ = [
    "map_daily_plan",
    "map_goal",
    "map_llm_health",
    "map_task",
    "task_edits_to_payload",
    "task_to_payload",
]
