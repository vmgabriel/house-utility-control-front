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
from decimal import Decimal, InvalidOperation
from enum import Enum
from uuid import UUID

from src.tasks.domain.exceptions import (
    InvalidDailyPlanError,
    InvalidGoalError,
    InvalidTaskError,
)

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
    """How urgent a task is, independent of its ``importance`` score.

    ``str`` mixin rather than ``StrEnum``: the infrastructure layer serialises
    these members straight to the DRF JSON API, and AGENTS.md warns explicitly
    against letting Ruff "fix" this to ``StrEnum`` (see the ``UP042`` per-file
    ignore in ``pyproject.toml``).
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


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

    ``ACTIVE`` and ``COMPLETED`` are the two the API actually reports;
    ``CANCELLED`` is modelled because a goal the user abandons is a real state
    and mapping it to "active" would keep it in the active list forever.
    """

    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


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

    def __str__(self) -> str:
        # Trimmed, not zero-padded: "2.5h" reads better than "2.50h" and the
        # trailing zero carries no information.
        return f"{self.amount.normalize():f}h"


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


__all__ = [
    "HOUR_STEP",
    "MAX_DURATION",
    "MAX_IMPORTANCE",
    "MIN_IMPORTANCE",
    "DailyPlanId",
    "Duration",
    "GoalId",
    "GoalStatus",
    "Importance",
    "LlmHealth",
    "Priority",
    "TaskId",
    "TaskStatus",
    "UserId",
    "normalize_markdown",
]
