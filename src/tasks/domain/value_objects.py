"""Immutable value objects for the tasks domain.

Pure Python: no Flask, no httpx, no DRF, no pydantic. Stays importable from the
domain and application layers under AGENTS.md's framework-free-core constraint.

Validation limits mirror the backend's `apps.tasks.domain.value_objects`. Where
a limit is an assumption rather than a mirror, the reason is stated inline, so
a mismatch found in integration is a one-line fix and not a mystery.

**Nullability is a first-class decision here, not a detail.** AGENTS.md records
that drf-spectacular declares several fields non-nullable that the live API
returns as `null`; following the spec instead of the API produced a 500 in a
mapper on a freshly registered user. The fields modelled as optional below are
the ones whose nullability the spec and the runtime are known to disagree on, or
which a plain `ModelSerializer` for a nullable model column will return as
`null`. Where a field is non-optional, that is a deliberate reading of the
contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import TypeVar, Union
from uuid import UUID

from src.tasks.domain.exceptions import (
    InvalidDailyPlanError,
    InvalidGoalError,
    InvalidTaskError,
)

#: Mirrors the backend's `MAX_NAME_LENGTH` / `MAX_DESCRIPTION_LENGTH`. They live
#: here rather than on the entities because `TaskEdits` has to enforce the same
#: limits on the edit path, and the entities import this module -- a limit
#: defined over there could not be imported from here without a cycle. The
#: entities re-export both names, so `entities.MAX_NAME_LENGTH` still resolves.
MAX_NAME_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 5000

#: Two-decimal quantum for hour estimates. Fractions of an hour are the norm
#: here ("30 minutes"), and quarters are the practical granularity, so anything
#: finer than a hundredth of an hour is a unit mistake rather than a real value.
HOUR_STEP = Decimal("0.01")

#: Matches the backend's `DecimalField(max_digits=5, decimal_places=2)`.
MAX_DURATION = Decimal("999.99")

#: Matches the backend's `PositiveIntegerField` for `importance`, bounded below.
MIN_IMPORTANCE = 1
MAX_IMPORTANCE = 5


def _to_decimal(raw: object, label: str, error: type[Exception]) -> Decimal:
    """Coerce `raw` to a finite `Decimal`.

    DRF renders decimals as JSON strings (``COERCE_DECIMAL_TO_STRING``), so the
    mappers hand us strings; accepting an existing `Decimal` or `int` as well
    keeps the value objects usable from plain Python callers and unit tests.
    """
    if isinstance(raw, Decimal):
        candidate = raw
    elif isinstance(raw, bool):
        # bool is an int subclass; True would otherwise become Decimal(1).
        raise error(f"{label} must be a decimal value, not a boolean.")
    else:
        try:
            candidate = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise error(f"{label} must be a valid decimal value.") from exc
    if not candidate.is_finite():
        raise error(f"{label} must be finite.")
    return candidate


def _require_uuid(raw: object, label: str, error: type[Exception] = ValueError) -> UUID:
    """Coerce `raw` to a `UUID`, rejecting anything else."""
    if isinstance(raw, UUID):
        return raw
    if isinstance(raw, str):
        try:
            return UUID(raw)
        except (ValueError, AttributeError, TypeError) as exc:
            raise error(f"{label} must be a valid UUID.") from exc
    raise error(f"{label} must be a UUID.")


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class TaskId:
    """Identity of a persisted task."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "value", _require_uuid(self.value, "TaskId", InvalidTaskError)
        )

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class GoalId:
    """Identity of a persisted goal."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "value", _require_uuid(self.value, "GoalId", InvalidGoalError)
        )

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class DailyPlanId:
    """Identity of a persisted daily plan."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            _require_uuid(self.value, "DailyPlanId", InvalidDailyPlanError),
        )

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class UserId:
    """Identity of the owner of a task.

    Deliberately duplicated rather than imported from `users`, `identity`, or
    `rentals`: bounded contexts never import each other, so each keeps its own
    copy of shared identity vocabulary (AGENTS.md, constraint 1).

    In practice the backend derives the owner from the access token and omits
    `user_id` from task payloads, so this is only populated by a future
    endpoint that returns it. The field exists because the entity models
    ownership, not because the current API sends it.
    """

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _require_uuid(self.value, "UserId"))

    def __str__(self) -> str:
        return str(self.value)


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #
class Priority(str, Enum):
    """How urgent a task is.

    This is the backend's ``importance`` field -- ``CreateTaskSerializer``
    declares it as a choice over low/medium/high, and the backend's own
    ``Task.importance`` is typed ``Priority``. There is no wire field called
    ``priority`` and no separate numeric one; this is the single level.

    ``str`` mixin rather than ``StrEnum``: the infrastructure layer serialises
    these members straight to the DRF JSON API, and AGENTS.md warns explicitly
    against letting Ruff "fix" this to ``StrEnum`` (see the ``UP042`` per-file
    ignore in ``pyproject.toml``).
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def importance(self) -> Importance:
        """The 1-5 dot-bar value this level renders as.

        A frontend-only rendering with no wire representation -- the backend has
        no field for it. Anchoring the ends and spreading the middle keeps the
        dots reading monotonically with the badge beside them, which is the only
        property the row depends on. It lives here rather than in the mapper
        because the create path needs it too, and two copies of one mapping is
        how the dots and the badge start disagreeing.
        """
        return _PRIORITY_IMPORTANCE[self]


class TaskStatus(str, Enum):
    """Where a task sits in its lifecycle.

    ``DONE`` is terminal. The backend never moves a task back out of it on its
    own, so a task that reopens was reopened deliberately by the user.
    """

    TODO = "todo"
    DOING = "doing"
    DONE = "done"


class GoalStatus(str, Enum):
    """Lifecycle of a goal, which outlives the tasks filed under it.

    Mirrors the backend's ``GOAL_STATUS_CHOICES`` exactly: ``active``,
    ``completed``, ``archived``. An earlier version modelled the third state as
    ``cancelled``, which the API never sends -- and since the mapper reports an
    unknown member rather than coercing it, every archived goal would have
    failed to map.
    """

    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


# --------------------------------------------------------------------------- #
# Business value objects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Duration:
    """A non-negative number of hours, at most two decimal places.

    A value object rather than a bare `Decimal` because "an estimated effort"
    and "a total of everything scheduled today" are the same measure and must
    not be interchangeable: adding a negative estimate into a daily total is the
    kind of error that renders as a plausible-looking but wrong number in the
    hours-vs-max-hours bar, which is precisely the view the overwhelmed badge
    exists to warn about.
    """

    amount: Decimal

    def __post_init__(self) -> None:
        amount = _to_decimal(self.amount, "Duration", InvalidTaskError)
        if amount < 0:
            raise InvalidTaskError("Duration cannot be negative.")
        if amount > MAX_DURATION:
            raise InvalidTaskError(f"Duration must not exceed {MAX_DURATION} hours.")
        if amount != amount.quantize(HOUR_STEP):
            raise InvalidTaskError("Duration must have at most two decimal places.")
        object.__setattr__(self, "amount", amount)

    @classmethod
    def zero(cls) -> Duration:
        """The absence of an estimate: a task may legitimately have none."""
        return cls(Decimal("0"))

    @property
    def is_estimated(self) -> bool:
        """False for the zero produced by :meth:`zero`, i.e. no real estimate."""
        return self.amount > 0

    def __add__(self, other: Duration) -> Duration:
        if not isinstance(other, Duration):
            return NotImplemented
        # Two two-decimal values can sum to a third (0.1 + 0.2); quantising here
        # is what keeps that from raising inside `sum()` over a day's tasks.
        return Duration((self.amount + other.amount).quantize(HOUR_STEP))

    def __sub__(self, other: Duration) -> Duration:
        """The remainder, which cannot be negative.

        Negative on purpose even though the class is non-negative by contract:
        "hours left to do" is the whole point of `GoalProgress.remaining_hours`,
        and a *negative* remainder would be a real answer -- a goal whose tasks
        sum to more hours than were counted against it -- rather than a bug. The
        caller that could produce it (`GoalProgress`) validates the ordering in
        its own `__post_init__`, so this raising is a backstop on hand-built
        values rather than the only guard.
        """
        if not isinstance(other, Duration):
            return NotImplemented
        return Duration((self.amount - other.amount).quantize(HOUR_STEP))

    def __str__(self) -> str:
        # Trimmed, not zero-padded: "2.5h" reads better than "2.50h" and the
        # trailing zero carries no information.
        return f"{self.amount.normalize():f}h"


#: Backend priority level -> the frontend's 1-5 importance dots. Consumed by
#: `Priority.importance`. Declared empty and populated at the bottom, because
#: `Importance` is defined after `Priority` and the property body only runs at
#: call time.
_PRIORITY_IMPORTANCE: dict[Priority, Importance] = {}  # noqa: F821


@dataclass(frozen=True, slots=True)
class TaskTransition:
    """One of the three ways a task's status can be changed.

    Named after the *operation*, not after the HTTP verb that carries it,
    because the operation is the domain's vocabulary and the route is the
    adapter's. Three of them exist and no more:

    ``START``
        Move to ``doing``. The backend calls this ``mark_as_doing``.
    ``COMPLETE``
        Move to ``done``, stamping ``completed_at``. ``mark_as_done``.
    ``REOPEN``
        Move back to ``todo``, clearing ``completed_at``. ``reopen``.

    **There is deliberately no "move back to doing" and no "unstick from
    doing".** The backend has exactly three status-changing endpoints and each
    one is a *named decision* rather than a generic assignment:

    * ``mark_as_doing`` refuses with 409 ``TaskAlreadyDone`` on a finished task,
      so ``done -> doing`` cannot be expressed at all.
    * ``reopen`` refuses with 409 ``TaskNotCompleted`` unless the task is
      finished, so ``doing -> todo`` cannot be expressed either.

    A fourth transition would have to be invented to fill the gap, and inventing
    one is worse than the gap: the board would offer a move that the backend
    then rejects, and the user would learn to distrust the board. See
    :meth:`Task.can_transition_to`, which is where that absence is decided, and
    `MoveTaskUseCase`, which is where the backend's refusal is surfaced.
    """

    name: str
    #: The status the transition lands the task in.
    target: TaskStatus

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise InvalidTaskError("Task transition name must be a non-empty string.")
        if not isinstance(self.target, TaskStatus):
            raise InvalidTaskError("Task transition target must be a TaskStatus.")

    def __str__(self) -> str:
        return self.name


#: The three transitions, keyed by the status each one produces.
#:
#: Keyed on the *target* because that is how the caller thinks about it -- a drag
#: says "put this in Doing", not "apply transition N" -- and because it is
#: unambiguous: each of these three statuses has exactly one endpoint in the
#: backend that produces it, so "move to X" names the operation on its own.
TASK_TRANSITIONS: dict[TaskStatus, TaskTransition] = {
    TaskStatus.DOING: TaskTransition(name="start", target=TaskStatus.DOING),
    TaskStatus.DONE: TaskTransition(name="complete", target=TaskStatus.DONE),
    TaskStatus.TODO: TaskTransition(name="reopen", target=TaskStatus.TODO),
}


@dataclass(frozen=True, slots=True)
class Importance:
    """A 1-5 importance score.

    Wrapped rather than left as a bare ``int`` because the bounds are a rule,
    not a convention: an unchecked integer accepts `0` and `99`, both of which
    the backend's ``PositiveIntegerField`` + upper bound rejects with a 400 the
    user cannot act on.
    """

    value: int

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int):
            raise InvalidTaskError("Importance must be an integer.")
        if not MIN_IMPORTANCE <= self.value <= MAX_IMPORTANCE:
            raise InvalidTaskError(
                f"Importance must be between {MIN_IMPORTANCE} and {MAX_IMPORTANCE}."
            )

    def __int__(self) -> int:
        return self.value

    def __str__(self) -> str:
        return str(self.value)


def normalize_name(raw: object, limit: int, label: str, error: type[Exception]) -> str:
    """Collapse whitespace in a name, rejecting an empty or over-long result.

    Shared by `Task` and `Goal` rather than duplicated as a `_clean_name` static
    method on each. Two copies of one rule is how the create path and the edit
    path end up accepting different names, and the divergence would only surface
    as a 400 from the backend on one of them.
    """
    if not isinstance(raw, str):
        raise error(f"{label} must be a string.")
    normalized = " ".join(raw.split())
    if not normalized:
        raise error(f"{label} cannot be empty.")
    if len(normalized) > limit:
        raise error(f"{label} cannot exceed {limit} characters.")
    return normalized


def normalize_markdown(raw: object, limit: int, label: str, error: type[Exception]):
    """Trim a markdown description and map an empty result to `None`.

    Markdown is whitespace-sensitive in ways plain text is not, so interior
    newlines are preserved exactly as the user typed them and only outer
    whitespace is stripped. Collapsing runs of whitespace -- correct for a task
    name -- would destroy every paragraph break and indent in a description and
    silently reflow the rendered markdown.

    An all-whitespace description is `None` rather than `"   "`, because an
    empty-string description renders as an empty markdown block in the template,
    which shows up as stray vertical whitespace in the task list.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise error(f"{label} must be a string or None.")
    normalized = raw.strip()
    if not normalized:
        return None
    if len(normalized) > limit:
        raise error(f"{label} cannot exceed {limit} characters.")
    return normalized


#: Populated after `Importance` is defined; `Priority.importance` reads it.
_PRIORITY_IMPORTANCE.update(
    {
        Priority.LOW: Importance(1),
        Priority.MEDIUM: Importance(3),
        Priority.HIGH: Importance(5),
    }
)


@dataclass(frozen=True, slots=True)
class LlmHealth:
    """Whether the LLM provider behind the backend is reachable.

    A domain value object rather than a boolean because the navbar indicator
    needs to distinguish three states, not two: reachable, unreachable, and
    unknown. "Unknown" is the honest answer when the health request itself
    failed -- a timeout, a 503, a broken network -- and collapsing that into
    "unreachable" would render a red dot that says the LLM is down when in fact
    the BFF cannot reach the backend to ask. The user reading that dot would go
    debug the wrong service.

    `detail` carries the backend's own reason when it supplied one, so the
    tooltip can distinguish a missing API key from an upstream timeout.
    """

    available: bool
    detail: str | None = None
    #: True when the health request itself could not be completed, as opposed to
    #: completing and reporting the provider as down.
    unknown: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.available, bool):
            raise InvalidTaskError("LlmHealth available must be a boolean.")
        if not isinstance(self.unknown, bool):
            raise InvalidTaskError("LlmHealth unknown must be a boolean.")
        # `slots=True` on a frozen dataclass drops the generated `__setattr__` that
        # plain frozen dataclasses keep, so normalisation has to go through
        # `object.__setattr__` explicitly -- same as every other frozen VO here.
        object.__setattr__(
            self,
            "detail",
            normalize_markdown(self.detail, 500, "LlmHealth detail", InvalidTaskError),
        )

    @classmethod
    def indeterminate(cls, detail: str | None = None) -> LlmHealth:
        """A health state the BFF could not determine."""
        return cls(available=False, detail=detail, unknown=True)

    @property
    def status_glyph(self) -> str:
        """The indicator the navbar renders.

        Amber rather than a second flavour of red: this is not "the LLM is
        down", it is "we do not know", and the two must not look alike at a
        glance in a navbar strip eight pixels tall.
        """
        if self.unknown:
            return "🟡"
        return "🟢" if self.available else "🔴"


# --------------------------------------------------------------------------- #
# Partial updates
# --------------------------------------------------------------------------- #
class Unset:
    """The absence of a value in a partial update.

    `None` cannot mean "leave this alone" here, because on every one of these
    five fields `None` is a real, distinct instruction: an empty description, a
    cleared due date, an unestimated task. Collapsing "unchanged" into `None`
    would make it impossible to clear a due date without also silently clearing
    the name.

    This mirrors the backend's own `UNSET` sentinel, which `UpdateTaskSerializer`
    depends on for exactly the same reason: a key that is absent from the request
    leaves the stored value alone, and a key that is explicitly `null` clears the
    column. Sending the distinction is not a BFF invention -- it is the contract.

    Falsy so that a bare `if edits.name:` reads as "was it set?" correctly in the
    one place that has to work generically, but every call site here still tests
    `isinstance(..., Unset)` explicitly, because `False`-ness and `is None` are
    different questions.
    """

    __slots__ = ()

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "UNSET"

    def __copy__(self) -> Unset:
        return self

    def __deepcopy__(self, memo: dict) -> Unset:
        return self


#: The singleton. Module-level so `isinstance(x, Unset)` and `x is UNSET` agree,
#: and so every default argument shares one identity.
UNSET = Unset()

#: The single "a value, or not sent" parameter type, so every `TaskEdits` field
#: says the same thing at its annotation.
T = TypeVar("T")

#: One field's state in a `TaskEdits`: either a real value, or "not sent".
#:
#: `T | None` appears in three of these fields, so the alias is what makes the
#: three-way distinction (set / cleared / untouched) readable at the annotation.
UnsetOr = Union[T, Unset]  # noqa: UP007


@dataclass(frozen=True, slots=True)
class TaskEdits:
    """A validated partial update to a task.

    Field-for-field the five keys `UpdateTaskSerializer` accepts, and nothing
    more. The two that exist in the API but are deliberately absent:

    * ``goal_id`` is read-only on `TaskSerializer` and is not on the update
      serializer. Filing a task under a goal goes through
      ``POST /goals/{id}/link-task/``, a different endpoint with different
      semantics, so an edit form that appeared to change it would send a field
      the backend ignores -- the user would see the change vanish on reload.
    * ``status`` is likewise not updatable here; it moves only through the
      dedicated ``mark-doing``/``mark-done`` actions.

    The invariant at the end is the backend's: `UpdateTaskSerializer.validate`
    rejects an empty payload with *"Provide name, description, due_date,
    importance, or estimated_hours."*, so catching it here turns a 400 into a
    flash before a pointless round trip.
    """

    name: UnsetOr[str] = UNSET
    #: `None` is a valid instruction here (clear the description), which is why
    #: this field needs `Unset` rather than defaulting to `None`.
    description: UnsetOr[str | None] = UNSET
    due_date: UnsetOr[date | None] = UNSET
    priority: UnsetOr[Priority] = UNSET
    estimated_hours: UnsetOr[Duration | None] = UNSET

    def __post_init__(self) -> None:
        if not isinstance(self.name, Unset):
            object.__setattr__(
                self,
                "name",
                normalize_name(
                    self.name, MAX_NAME_LENGTH, "Task name", InvalidTaskError
                ),
            )
        if not isinstance(self.description, Unset):
            object.__setattr__(
                self,
                "description",
                normalize_markdown(
                    self.description,
                    MAX_DESCRIPTION_LENGTH,
                    "Task description",
                    InvalidTaskError,
                ),
            )
        if not isinstance(self.due_date, Unset) and self.due_date is not None:
            # A `datetime` reaching a `DateField` would be silently truncated to
            # its date, so it is rejected for the same reason `Task` rejects one.
            if type(self.due_date) is not date:
                raise InvalidTaskError("Task due date must be a date.")
        if not isinstance(self.priority, Unset) and not isinstance(
            self.priority, Priority
        ):
            raise InvalidTaskError("Task priority must be a Priority.")
        if not isinstance(self.estimated_hours, Unset) and (
            self.estimated_hours is not None
            and not isinstance(self.estimated_hours, Duration)
        ):
            raise InvalidTaskError("Task estimated hours must be a Duration or None.")

        if self.is_empty:
            raise InvalidTaskError(
                "Provide name, description, due_date, importance, or estimated_hours."
            )

    @property
    def is_empty(self) -> bool:
        """Whether nothing at all was set -- which the backend would reject.

        Checked in `__post_init__`, so an instance that exists is never empty;
        the property is the check itself, shared by both.
        """
        return all(
            isinstance(value, Unset)
            for value in (
                self.name,
                self.description,
                self.due_date,
                self.priority,
                self.estimated_hours,
            )
        )


__all__ = [
    "HOUR_STEP",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_DURATION",
    "MAX_IMPORTANCE",
    "MAX_NAME_LENGTH",
    "MIN_IMPORTANCE",
    "TASK_TRANSITIONS",
    "UNSET",
    "DailyPlanId",
    "Duration",
    "GoalId",
    "GoalStatus",
    "Importance",
    "LlmHealth",
    "Priority",
    "TaskEdits",
    "TaskId",
    "TaskStatus",
    "TaskTransition",
    "Unset",
    "UnsetOr",
    "UserId",
    "normalize_markdown",
    "normalize_name",
]
