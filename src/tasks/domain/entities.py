"""Task, Goal, and DailyPlan entities with their invariants.

Pure Python: no Flask, no httpx, no pydantic (AGENTS.md, constraint 2).

Field sets mirror the DRF output serializers in `apps.tasks.interfaces.serializers`
for the fields that exist there. Where a field is declared optional below, the
reason is given on the field itself -- those are the ones where the schema spec
and the live API are known to disagree, or where a nullable model column means
the serializer can legitimately answer `null`. Treating one of those as required
is what turns a freshly created task into a 500 inside a mapper.

Entities are mutable dataclasses with explicit behaviour methods because the
tasks rules are stateful: `Task.is_overdue`, `Task.start`, `Task.complete`,
`DailyPlan.exceeds`. Hiding them behind a `with_` style API would only move the
same rules into the application layer, which is supposed to stay thin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from src.tasks.domain.exceptions import (
    InvalidDailyPlanError,
    InvalidGoalError,
    InvalidTaskError,
)
from src.tasks.domain.value_objects import (
    HOUR_STEP,
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    MIN_IMPORTANCE,
    DailyPlanId,
    Duration,
    GoalId,
    GoalStatus,
    Importance,
    Priority,
    TaskId,
    TaskStatus,
    normalize_markdown,
    normalize_name,
)

#: Re-exported from the value objects rather than redefined. `TaskEdits` enforces
#: the same limits on the edit path and lives in `value_objects`, which this
#: module imports -- so the constants have to be defined on the other side of
#: that dependency or one of the two paths would have to guess at them.
#: `create_task.py` imports `MAX_DESCRIPTION_LENGTH` from here and is unaffected.
__all__ = [
    "MAX_DESCRIPTION_LENGTH",
    "MAX_NAME_LENGTH",
    "DEFAULT_OVERWHELM_THRESHOLD",
    "DailyPlan",
    "Goal",
    "GoalProgress",
    "Task",
]

#: A task estimated at more hours than this is flagged as overloaded by default.
#:
#: Lives here rather than in the interface layer because "overloaded" is a
#: business judgement, not a presentation one: the same number decides whether
#: the decompose action is worth offering. The 4.0 figure is the product default
#: and the use case overrides it from configuration, so a deployment can change
#: it without a code change.
DEFAULT_OVERWHELM_THRESHOLD = Decimal("4.0")


def _require_exact_date(value: object, label: str) -> None:
    """Reject anything that is not a plain `date`.

    `isinstance(x, date)` is also True for `datetime`, which would silently lose
    its time component when formatted for a template -- and a due date that reads
    as "2026-03-01" for a datetime of "2026-03-01 18:00" is a lie the user acts on.
    """
    if type(value) is not date:
        raise InvalidTaskError(f"{label} must be a date.")


def _require_exact_date_for_goal(value: object, label: str) -> None:
    if type(value) is not date:
        raise InvalidGoalError(f"{label} must be a date.")


def _require_exact_date_for_plan(value: object, label: str) -> None:
    if type(value) is not date:
        raise InvalidDailyPlanError(f"{label} must be a date.")


def _require_bool(raw: object, label: str, error) -> bool:
    if not isinstance(raw, bool):
        raise error(f"{label} must be a boolean.")
    return raw


def _require_optional_bool(raw: object, label: str, error) -> bool | None:
    if raw is None:
        return None
    if not isinstance(raw, bool):
        raise error(f"{label} must be a boolean or None.")
    return raw


@dataclass(eq=False, slots=True)
class Task:
    """A single unit of work in the backlog.

    `estimated_hours` is optional and defaults to "not estimated" rather than
    zero. The difference matters for `is_overwhelmed`: a task with no estimate
    is not overloaded, and modelling it as `0` makes the two indistinguishable
    at every call site that forgets to check `is_estimated`.

    `goal_id` is optional because the backend's `goal` foreign key is nullable
    -- the overwhelming majority of backlog items are not filed under a goal --
    and because drf-spectacular is known to over-report nullability here.

    `completed_at` is the backend's own answer to *when* the task was finished.
    It is stored rather than derived from `status` on purpose. `status == DONE`
    and `completed_at is not None` agree for every task the backend currently
    produces -- `TaskModel.mark_done` sets both together and `reopen` clears both
    -- but only the timestamp can say *when*, and it is the only field on the
    whole task payload that can. Everything that wants to know how much of a
    goal is genuinely finished (see `GoalProgress`) needs that "when", not the
    "that".
    """

    name: str
    id: TaskId | None = None
    goal_id: GoalId | None = None
    description: str | None = None
    due_date: date | None = None
    importance: Importance = field(default_factory=lambda: Importance(MIN_IMPORTANCE))
    priority: Priority = Priority.MEDIUM
    estimated_hours: Duration | None = None
    status: TaskStatus = TaskStatus.TODO
    #: When the backend finished this task, or `None` while it is still open.
    #:
    #: Optional because the column is nullable, not because the backend omits the
    #: field: `TaskSerializer` declares `completed_at` read-only and `allow_null`,
    #: so every task payload carries the key and an open task carries `null`.
    completed_at: datetime | None = None
    is_checked_by_llm: bool = False
    llm_evaluation_failed: bool = False
    #: What the backend's serializer computed for this task, or `None` when the
    #: response did not carry the field.
    #:
    #: Held as data rather than as a computed property because the backend owns
    #: that verdict: it knows the whole day the task competes for, and a BFF
    #: recomputation would be a second, silently divergent implementation of the
    #: same rule. See :meth:`is_overwhelmed` for how the two are reconciled.
    server_is_overwhelmed: bool | None = None

    def __post_init__(self) -> None:
        self.name = self._clean_name(self.name)
        if self.id is not None and not isinstance(self.id, TaskId):
            raise InvalidTaskError("Task id must be a TaskId.")
        if self.goal_id is not None and not isinstance(self.goal_id, GoalId):
            raise InvalidTaskError("Task goal must be a GoalId or None.")
        self.description = normalize_markdown(
            self.description,
            MAX_DESCRIPTION_LENGTH,
            "Task description",
            InvalidTaskError,
        )
        if self.due_date is not None:
            _require_exact_date(self.due_date, "Task due date")
        if not isinstance(self.importance, Importance):
            raise InvalidTaskError("Task importance must be an Importance.")
        if not isinstance(self.priority, Priority):
            raise InvalidTaskError("Task priority must be a Priority.")
        if self.estimated_hours is not None and not isinstance(
            self.estimated_hours, Duration
        ):
            raise InvalidTaskError("Task estimated hours must be a Duration or None.")
        if not isinstance(self.status, TaskStatus):
            raise InvalidTaskError("Task status must be a TaskStatus.")
        if self.completed_at is not None and not isinstance(
            self.completed_at, datetime
        ):
            raise InvalidTaskError("Task completed_at must be a datetime or None.")
        self.is_checked_by_llm = _require_bool(
            self.is_checked_by_llm, "Task is_checked_by_llm", InvalidTaskError
        )
        self.llm_evaluation_failed = _require_bool(
            self.llm_evaluation_failed, "Task llm_evaluation_failed", InvalidTaskError
        )
        self.server_is_overwhelmed = _require_optional_bool(
            self.server_is_overwhelmed,
            "Task server_is_overwhelmed",
            InvalidTaskError,
        )

    @classmethod
    def create(
        cls,
        *,
        name: str,
        goal_id: GoalId | None = None,
        description: str | None = None,
        due_date: date | None = None,
        importance: Importance | None = None,
        priority: Priority | None = None,
        estimated_hours: Duration | None = None,
    ) -> Task:
        """Build an unsaved task.

        `status` is left at `TODO` and the two LLM flags at `False`: both are
        facts about work the backend has not done yet, so letting a caller
        assert them at construction time would let the UI claim a task was
        evaluated by the LLM before anyone asked.
        """
        return cls(
            id=None,
            name=name,
            goal_id=goal_id,
            description=description,
            due_date=due_date,
            importance=(
                importance if importance is not None else Importance(MIN_IMPORTANCE)
            ),
            priority=priority if priority is not None else Priority.MEDIUM,
            estimated_hours=estimated_hours,
        )

    def is_overwhelmed(self, threshold: Decimal = DEFAULT_OVERWHELM_THRESHOLD) -> bool:
        """Whether this task carries more work than `threshold` hours.

        The backend's verdict wins when it sent one. It is the authority: it
        evaluates against the day the task is scheduled into, which this BFF
        does not have, and a locally recomputed value could therefore disagree
        with the flag the user was shown on a previous page load -- the same
        task flipping between ⚠️ Inabarcable and unflagged as they navigate.

        The local comparison is the fallback, used when the field was absent
        from the response (older payloads, or an endpoint that does not compute
        it). A task with no estimate is never overwhelmed; an estimate of
        exactly `threshold` hours is not either, since "more than N hours" is
        what the badge claims and 4.0h reading as exactly at the limit would
        make the threshold itself look wrong.
        """
        if self.server_is_overwhelmed is not None:
            return self.server_is_overwhelmed
        if self.estimated_hours is None or not self.estimated_hours.is_estimated:
            return False
        return self.estimated_hours.amount > threshold

    def is_completed(self) -> bool:
        """Whether the backend has recorded this task as finished.

        Keyed on `completed_at` rather than on `status`, because the timestamp is
        the fact both facts are read from and the only one of the two that says
        *when*. The two agree on every task the backend currently produces --
        `mark_done` writes both, `reopen` clears both -- so this is not a
        divergence in practice; it is a choice about which single field is the
        authority, so that :class:`GoalProgress` and this method can never end up
        reading two different definitions of "finished".
        """
        return self.completed_at is not None

    def start(self) -> None:
        """Move to `DOING`.

        A completed task cannot be reopened through this method: undoing real
        work is a decision the user makes deliberately on a detail view, not
        something a status button does on the way past.
        """
        if self.status is TaskStatus.DONE:
            raise InvalidTaskError("A completed task cannot be moved back to doing.")
        self.status = TaskStatus.DOING

    def complete(self, now: datetime | None = None) -> None:
        """Mark the task done.

        Idempotent: completing an already-completed task is a no-op rather than
        an error, because the button that triggers this is rendered on every
        row and a double-click is a realistic input, not a programming mistake.

        `now` is the completion timestamp, mirroring the backend's
        `Task.complete(now=...)`, and is left alone on a repeat call so a
        double-click cannot move the moment the work was finished. The BFF does
        not currently call this -- completion goes through
        `POST /tasks/<id>/mark-done/`, which the backend applies itself -- but
        leaving `status` and `completed_at` able to disagree would make the
        entity's two notions of "finished" inconsistent for no reason.
        """
        if self.status is TaskStatus.DONE:
            return
        self.status = TaskStatus.DONE
        if now is not None:
            self.completed_at = now

    def is_overdue(self, today: date) -> bool:
        """Whether the due date has passed and the task is not finished.

        An unfinished task due *today* is not overdue. The badge means "late",
        and colouring today's deadline red would train users to ignore the one
        signal on the page that matters.
        """
        if self.status is TaskStatus.DONE or self.due_date is None:
            return False
        _require_exact_date(today, "Today")
        return self.due_date < today

    def mark_llm_evaluated(self) -> None:
        """Record that the LLM evaluated this task successfully."""
        self.is_checked_by_llm = True
        self.llm_evaluation_failed = False

    def mark_llm_failed(self) -> None:
        """Record that the last LLM evaluation attempt failed.

        `is_checked_by_llm` is cleared rather than left set: a failed run tells
        us nothing about the task, and leaving the flag on would render an
        "evaluated" badge over a task that was never evaluated.
        """
        self.is_checked_by_llm = False
        self.llm_evaluation_failed = True

    @staticmethod
    def _clean_name(name: object) -> str:
        return normalize_name(name, MAX_NAME_LENGTH, "Task name", InvalidTaskError)


@dataclass(eq=False, slots=True)
class Goal:
    """An outcome a group of tasks serves.

    A goal has no duration and no hours of its own: its size is the sum of its
    tasks' estimates, which the backend can change at any time by editing a
    task. Storing a total here would mean the goal and its tasks could disagree.
    """

    name: str
    id: GoalId | None = None
    description: str | None = None
    due_date: date | None = None
    status: GoalStatus = GoalStatus.ACTIVE
    #: How many tasks the backend has filed under this goal.
    #:
    #: Mapped from `GoalSerializer.task_count` rather than computed here. It is a
    #: field on the wire, so deriving it would mean fetching every task to count
    #: rows the backend already counted -- and the two numbers could disagree.
    task_count: int = 0

    def __post_init__(self) -> None:
        self.name = self._clean_name(self.name)
        if self.id is not None and not isinstance(self.id, GoalId):
            raise InvalidGoalError("Goal id must be a GoalId.")
        self.description = normalize_markdown(
            self.description,
            MAX_DESCRIPTION_LENGTH,
            "Goal description",
            InvalidGoalError,
        )
        if self.due_date is not None:
            _require_exact_date_for_goal(self.due_date, "Goal due date")
        if not isinstance(self.status, GoalStatus):
            raise InvalidGoalError("Goal status must be a GoalStatus.")
        if (
            isinstance(self.task_count, bool)
            or not isinstance(self.task_count, int)
            or self.task_count < 0
        ):
            raise InvalidGoalError("Goal task count must be a non-negative integer.")

    def is_overdue(self, today: date) -> bool:
        """Whether the due date has passed and the goal is still active.

        A cancelled goal is never overdue. It was dropped on purpose, and
        warning about a deadline on something the user deliberately abandoned
        is noise, not information.
        """
        if self.status is not GoalStatus.ACTIVE or self.due_date is None:
            return False
        _require_exact_date_for_goal(today, "Today")
        return self.due_date < today

    def complete(self) -> None:
        """Mark the goal achieved. Idempotent, as on `Task.complete`."""
        self.status = GoalStatus.COMPLETED

    @staticmethod
    def _clean_name(name: object) -> str:
        return normalize_name(name, MAX_NAME_LENGTH, "Goal name", InvalidGoalError)


@dataclass(frozen=True, slots=True)
class GoalProgress:
    """How much of a goal is finished, measured in estimated hours.

    The backend sends no such figure. `GoalSerializer` carries `task_count` and
    nothing else, and `DailyPlanSerializer.total_hours` is scoped to a single
    day -- there is no goal-level hours field anywhere in the API, and no
    `completed_hours` either. So this is computed here from the tasks the goal
    owns, which is the only place the two inputs exist.

    That computation is deliberately the *simplest* one the data supports:
    completed hours are the estimates of the tasks the backend has stamped with
    `completed_at`, not the hours actually worked. The API has no time-tracking
    and no "hours spent" field, so an estimate is the finest unit of effort the
    product can honestly count, and presenting it as anything more precise would
    be a claim the data cannot support. A goal whose tasks were badly estimated
    will read as 100% complete while being nothing like finished; that is a
    property of estimating, not of this arithmetic.

    Two details that are choices rather than arithmetic:

    * **Unestimated tasks contribute zero.** A task with no estimate is not a
      zero-hour task, it is an *unmeasured* one. Counting it as zero is the only
      option that does not require inventing a number, and it is the same
      convention `Task.is_overwhelmed` uses.
    * **The percentage truncates rather than rounds.** 2/3 complete is 66.67%,
      and this reports 66. Truncation can only ever *understate* progress,
      whereas rounding could report 67% on two thirds of the work. A bar that
      quietly overshoots is worse than one that is a fraction pessimistic.
    """

    total_hours: Duration
    completed_hours: Duration
    #: 0-100, inclusive.
    percentage: int

    def __post_init__(self) -> None:
        if not isinstance(self.total_hours, Duration):
            raise InvalidGoalError("Goal progress total hours must be a Duration.")
        if not isinstance(self.completed_hours, Duration):
            raise InvalidGoalError("Goal progress completed hours must be a Duration.")
        if isinstance(self.percentage, bool) or not isinstance(self.percentage, int):
            raise InvalidGoalError("Goal progress percentage must be an integer.")
        if not 0 <= self.percentage <= 100:
            raise InvalidGoalError(
                "Goal progress percentage must be between 0 and 100."
            )
        if self.completed_hours.amount > self.total_hours.amount:
            raise InvalidGoalError(
                "Goal progress cannot exceed the goal's total estimated hours."
            )

    @classmethod
    def from_tasks(cls, tasks: list[Task]) -> GoalProgress:
        """Measure progress over `tasks`.

        A goal with no tasks, or whose tasks are all unestimated, is 0% of 0h
        rather than a division by zero: "nothing to do" is a real state a goal
        is in on the day it is created, and a page that crashed on it would be
        crashing on the most ordinary event in the product.
        """
        total = Duration.zero()
        completed = Duration.zero()
        for task in tasks:
            hours = task.estimated_hours
            if hours is None or not hours.is_estimated:
                continue
            total = total + hours
            if task.is_completed():
                completed = completed + hours
        return cls(
            total_hours=total,
            completed_hours=completed,
            percentage=_percentage(completed, total),
        )

    @property
    def remaining_hours(self) -> Duration:
        """Estimated hours still outstanding."""
        return self.total_hours - self.completed_hours

    @property
    def is_empty(self) -> bool:
        """Whether there is no measured work at all behind this progress."""
        return not self.total_hours.is_estimated


def _percentage(completed: Duration, total: Duration) -> int:
    """The share of `total` that `completed` represents, as a whole percent."""
    if not total.is_estimated:
        return 0
    return int(completed.amount / total.amount * 100)


@dataclass(eq=False, slots=True)
class DailyPlan:
    """What the user committed to on one day.

    `total_hours` is the backend's own sum of the scheduled tasks, not a number
    this BFF computed from `task_ids`. The plan carries ids, not tasks, so a
    local sum would have to re-fetch every task to add them up -- and would then
    be a second implementation of the same arithmetic, free to disagree with the
    figure already stored and displayed.
    """

    plan_date: date
    id: DailyPlanId | None = None
    task_ids: list[TaskId] = field(default_factory=list)
    total_hours: Duration | None = None
    generated_by_llm: bool = False

    def __post_init__(self) -> None:
        if self.id is not None and not isinstance(self.id, DailyPlanId):
            raise InvalidDailyPlanError("Daily plan id must be a DailyPlanId.")
        _require_exact_date_for_plan(self.plan_date, "Daily plan date")
        self.task_ids = self._clean_task_ids(self.task_ids)
        if self.total_hours is not None and not isinstance(self.total_hours, Duration):
            raise InvalidDailyPlanError("Daily plan hours must be a Duration or None.")
        self.generated_by_llm = _require_bool(
            self.generated_by_llm,
            "Daily plan generated_by_llm",
            InvalidDailyPlanError,
        )

    @property
    def is_empty(self) -> bool:
        """True when nothing was scheduled."""
        return not self.task_ids

    def exceeds(self, max_hours: Decimal) -> bool:
        """Whether the day is over-committed relative to `max_hours`.

        The comparison is `>` rather than `>=` for the same reason the
        overwhelmed badge uses it: a day planned to exactly the limit is a full
        day, which is the goal, not a warning.
        """
        if self.total_hours is None:
            return False
        return self.total_hours.amount > max_hours

    def hours_remaining(self, max_hours: Decimal) -> Decimal:
        """Hours left before the day is over-committed; negative when over.

        Signed on purpose. Clamping at zero would render "0h remaining" for an
        over-committed day, which is indistinguishable from a perfectly packed
        one -- and the bar next to it would be the only clue.
        """
        if self.total_hours is None:
            return max_hours
        return (max_hours - self.total_hours.amount).quantize(HOUR_STEP)

    def contains(self, task_id: TaskId) -> bool:
        """Whether `task_id` is scheduled on this day."""
        if not isinstance(task_id, TaskId):
            raise InvalidDailyPlanError("Daily plan membership takes a TaskId.")
        return task_id in self.task_ids

    @staticmethod
    def _clean_task_ids(task_ids: object) -> list[TaskId]:
        if task_ids is None:
            return []
        if not isinstance(task_ids, (list, tuple)):
            raise InvalidDailyPlanError("Daily plan task ids must be a sequence.")
        cleaned: list[TaskId] = []
        for raw in task_ids:
            if not isinstance(raw, TaskId):
                raise InvalidDailyPlanError("Daily plan task ids must be TaskIds.")
            # Duplicates rejected rather than silently collapsed: a plan listing
            # the same task twice would render it twice and inflate the day's
            # apparent load.
            if raw in cleaned:
                raise InvalidDailyPlanError(
                    "A daily plan cannot schedule the same task twice."
                )
            cleaned.append(raw)
        return cleaned


__all__ = [
    "MAX_DESCRIPTION_LENGTH",
    "MAX_NAME_LENGTH",
    "DEFAULT_OVERWHELM_THRESHOLD",
    "DailyPlan",
    "Goal",
    "GoalProgress",
    "Task",
]
