"""ViewModels for the tasks templates.

The only place task domain objects become strings, labels, and CSS classes.
Templates never touch domain objects directly, which keeps presentation out of
the inner layers and makes every rendering decision unit-testable without a
request context -- the same arrangement as `src/budget/interfaces/viewmodels.py`
and `src/rentals/interfaces/viewmodels.py`.

**Markdown is prepared here, not parsed.** The plan allows either a client-side
`marked.js` or ViewModel-side rendering; this module takes the client-side
route and exposes the raw source plus a flag saying whether there is anything to
render. Two reasons:

1. The BFF has no markdown parser and adding one (commonmark, markdown-it, or
   a hand-rolled regex) would put a second implementation of CommonMark in the
   request path for a string the browser is going to render anyway.
2. Server-rendering markdown means calling `|safe` on output built from a
   string a user typed. That is the correct place to be careful and the wrong
   place to be casual: a regex-based sanitiser missed by one tag becomes stored
   XSS in the one page that shows everyone's own text back to them.

So `description_html` is **not** provided. It would have to be either the raw
markdown (misleading name, and unsafe under `|safe`) or a client-side value the
template cannot produce. What the template gets instead is `description_markdown`
plus `has_description`, and Alpine renders the former with `marked.parse()` into
a node it does not trust -- see the `x-html` note in `tasks/backlog.html`.
`description_preview` is the one exception, and it is deliberately plain text:
it feeds a `title=` tooltip and a `text-ellipsis` row, neither of which should
carry markup at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from src.tasks.domain.entities import (
    DEFAULT_OVERWHELM_THRESHOLD,
    DailyPlan,
    Goal,
    GoalProgress,
    Task,
)
from src.tasks.domain.value_objects import (
    Duration,
    GoalStatus,
    Importance,
    LlmHealth,
    TaskStatus,
)

#: A full working day, used for the hours-vs-max-hours bar when the caller does
#: not configure one. Eight is a default of the *product*, not of the domain --
#: it is presentation, which is why it lives here and not on `DailyPlan`.
DEFAULT_MAX_DAILY_HOURS = Decimal("8.0")

PRIORITY_LABELS: dict[str, str] = {
    "LOW": "Low",
    "MEDIUM": "Medium",
    "HIGH": "High",
}

PRIORITY_STYLES: dict[str, str] = {
    "LOW": "bg-gray-100 text-gray-700",
    "MEDIUM": "bg-yellow-100 text-yellow-800",
    "HIGH": "bg-red-100 text-red-700",
}

TASK_STATUS_LABELS: dict[str, str] = {
    "TODO": "To do",
    "DOING": "In progress",
    "DONE": "Done",
}

TASK_STATUS_STYLES: dict[str, str] = {
    "TODO": "bg-gray-100 text-gray-700",
    "DOING": "bg-blue-100 text-blue-800",
    "DONE": "bg-green-100 text-green-800",
}

GOAL_STATUS_LABELS: dict[str, str] = {
    "ACTIVE": "Active",
    "COMPLETED": "Completed",
    "ARCHIVED": "Archived",
}

GOAL_STATUS_STYLES: dict[str, str] = {
    "ACTIVE": "bg-blue-100 text-blue-800",
    "COMPLETED": "bg-green-100 text-green-800",
    "ARCHIVED": "bg-gray-100 text-gray-500",
}


def _hours_label(hours: Duration | None) -> str:
    """Render an estimate for a task row.

    `None` renders as an em dash rather than "0h" or "Not estimated": the row
    is dense, and a dash reads as absent at a glance while a word costs width
    the importance badge needs.
    """
    if hours is None or not hours.is_estimated:
        return "—"
    return str(hours)


def _hours_value(hours: Duration | None) -> str:
    """Render an estimate as a bare number, for a form input.

    `Duration.__str__` appends an "h" (`"2.5h"`), which is right for a row and
    wrong for `<input type="number">` -- the browser rejects it outright, so a
    prefilled edit modal would post an empty estimate and silently clear the
    user's own figure. `format(..., "f")` and not `normalize()`, because the
    normalized form of 10.00 is "1E+1", which is also not a valid input value.
    """
    if hours is None or not hours.is_estimated:
        return ""
    return f"{hours.amount:f}"


def _readable_date(value: date | None) -> str:
    """Render an ISO date as ``YYYY-MM-DD``.

    Deliberately not locale-formatted, matching the rentals and budget
    viewmodels: the rest of the app renders ISO strings, and a mixed format here
    would be the only one in the product.
    """
    return value.isoformat() if value is not None else ""


def _preview(markdown: str | None, limit: int = 140) -> str:
    """Flatten markdown to one line for tooltips and truncated rows.

    Stripped rather than rendered, on purpose: this string ends up in a `title=`
    attribute and in `text-ellipsis` text nodes, where markup is either escaped
    into visible angle brackets or, worse, injected into a tooltip's HTML. A
    markdown-aware preview here would be a second parser for no gain.
    """
    if not markdown:
        return ""
    flattened = " ".join(markdown.split())
    if len(flattened) <= limit:
        return flattened
    return flattened[: limit - 1].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class ImportanceViewModel:
    """One of the five importance steps, as a label and a width class."""

    value: int
    label: str
    #: Tailwind width class for the dot row. A class name rather than an inline
    #: `style="width: {{ n }}%"` so the width stays in the stylesheet, where the
    #: build can see it; Tailwind purges classes it cannot find statically.
    dots_class: str

    @classmethod
    def from_domain(cls, importance: Importance) -> ImportanceViewModel:
        return cls(
            value=importance.value,
            label=f"{importance.value}/5",
            dots_class=_IMPORTANCE_DOT_CLASSES[importance.value],
        )


#: Tailwind generates these statically, so a literal per level is required --
#: `f"w-[{n * 20}%]"`, the obvious dynamic form, is purged as unused.
_IMPORTANCE_DOT_CLASSES: dict[int, str] = {
    1: "w-1/5",
    2: "w-2/5",
    3: "w-3/5",
    4: "w-4/5",
    5: "w-full",
}


@dataclass(frozen=True, slots=True)
class TaskViewModel:
    """One task, ready for a backlog row."""

    id: str
    name: str
    goal_id: str
    #: Raw markdown, for the client-side parser. Empty when there is none.
    description_markdown: str
    due_date: str
    importance: ImportanceViewModel
    priority_label: str
    priority_style: str
    status_label: str
    status_style: str
    estimated_hours_label: str
    #: Raw hours for the edit modal's `<input type="number">`.
    #:
    #: Separate from `estimated_hours_label` because that one is for display --
    #: it renders "2.5h", or an em dash when there is no estimate -- and neither
    #: is valid in a number input. Prefilling the modal with "—" would make the
    #: form submit a literal dash as the new estimate.
    estimated_hours_value: str
    #: The backend's wire value for this task's importance level, so the edit
    #: modal's `<select>` can mark the current one selected.
    #:
    #: `priority_label` ("High") is for display and does not match the value the
    #: form posts.
    priority_value: str
    is_overwhelmed: bool
    is_overdue: bool
    is_done: bool
    #: When the backend finished this task, as an ISO-8601 string, or `""`.
    #:
    #: A string rather than a formatted date because it goes into a
    #: `<time datetime="...">`, and the template has to be able to hand the
    #: machine-readable value and the human-readable one from the same field.
    completed_at: str
    #: Whether the goal detail page counts this task towards its progress bar.
    #:
    #: Reads `Task.completed_at`, which is the same fact `Task.is_completed`
    #: reads, so the row's badge and the bar can never disagree about which
    #: tasks count.
    is_completed: bool
    is_checked_by_llm: bool
    llm_evaluation_failed: bool
    description_preview: str
    #: Id of the row's Alpine root, used to open one task's detail panel without
    #: a second `x-data` scope on the page (AGENTS.md, Alpine scope strictness).
    dom_id: str

    @classmethod
    def from_domain(
        cls,
        task: Task,
        *,
        today: date,
        threshold: Decimal = DEFAULT_OVERWHELM_THRESHOLD,
    ) -> TaskViewModel:
        description = task.description or ""
        return cls(
            id=str(task.id) if task.id is not None else "",
            name=task.name,
            goal_id=str(task.goal_id) if task.goal_id is not None else "",
            description_markdown=description,
            due_date=_readable_date(task.due_date),
            importance=ImportanceViewModel.from_domain(task.importance),
            priority_label=PRIORITY_LABELS[task.priority.name],
            priority_style=PRIORITY_STYLES[task.priority.name],
            status_label=TASK_STATUS_LABELS[task.status.name],
            status_style=TASK_STATUS_STYLES[task.status.name],
            estimated_hours_label=_hours_label(task.estimated_hours),
            # Empty rather than the label: an unestimated task must prefill the
            # number input with nothing, so clearing an estimate in the modal is
            # possible at all.
            estimated_hours_value=_hours_value(task.estimated_hours),
            priority_value=task.priority.value,
            # `Task.is_overwhelmed` already prefers the backend's verdict and
            # falls back to the local comparison; the threshold only matters for
            # that fallback, which is why it is not consulted when the server
            # answered.
            is_overwhelmed=task.is_overwhelmed(threshold),
            is_overdue=task.is_overdue(today),
            is_done=task.status is TaskStatus.DONE,
            completed_at=(
                task.completed_at.isoformat() if task.completed_at is not None else ""
            ),
            is_completed=task.is_completed(),
            is_checked_by_llm=task.is_checked_by_llm,
            llm_evaluation_failed=task.llm_evaluation_failed,
            description_preview=_preview(description),
            dom_id=f"task-{task.id}",
        )

    @property
    def has_description(self) -> bool:
        """Whether there is markdown worth parsing at all.

        Guards the `marked.parse` call in the template: parsing an empty string
        is cheap, but calling it unconditionally means every row pays for a
        parse and ships a description node that renders nothing.
        """
        return bool(self.description_markdown)

    @property
    def badge_class(self) -> str:
        """The badge row's class, so overdue and plain rows differ visibly."""
        return "border-red-300 bg-red-50" if self.is_overdue else "border-gray-200"


@dataclass(frozen=True, slots=True)
class GoalViewModel:
    """One goal, ready for a goals card."""

    id: str
    name: str
    description_markdown: str
    description_preview: str
    due_date: str
    status_label: str
    status_style: str
    is_overdue: bool
    task_count: int

    @classmethod
    def from_domain(cls, goal: Goal, *, today: date) -> GoalViewModel:
        description = goal.description or ""
        return cls(
            id=str(goal.id) if goal.id is not None else "",
            name=goal.name,
            description_markdown=description,
            description_preview=_preview(description),
            due_date=_readable_date(goal.due_date),
            status_label=GOAL_STATUS_LABELS[goal.status.name],
            status_style=GOAL_STATUS_STYLES[goal.status.name],
            is_overdue=goal.is_overdue(today),
            # From the payload, not recomputed -- see `Goal.task_count`.
            task_count=goal.task_count,
        )

    @property
    def has_description(self) -> bool:
        return bool(self.description_markdown)

    @property
    def status_is_active(self) -> bool:
        return self.status_label == GOAL_STATUS_LABELS[GoalStatus.ACTIVE.name]


@dataclass(frozen=True, slots=True)
class DailyPlanViewModel:
    """One day's plan, plus the hours bar that goes with it."""

    id: str
    plan_date: str
    total_hours_label: str
    max_hours_label: str
    hours_remaining_label: str
    #: 0-100, for the bar's width. Clamped because a plan over-committed by
    #: 30h must not render a 375%-wide bar off the side of the card.
    fill_percent: int
    is_over_committed: bool
    is_empty: bool
    generated_by_llm: bool
    task_ids: tuple[str, ...]

    @classmethod
    def from_domain(
        cls,
        plan: DailyPlan,
        *,
        max_hours: Decimal = DEFAULT_MAX_DAILY_HOURS,
    ) -> DailyPlanViewModel:
        total = plan.total_hours
        total_amount = total.amount if total is not None else Decimal("0")
        remaining = (max_hours - total_amount).quantize(Decimal("0.01"))
        # Guarded before the division: a caller configuring `max_hours=0` would
        # otherwise get a ZeroDivisionError out of a presentation class.
        fill_percent = 0
        if max_hours > 0:
            fill_percent = int(min(total_amount / max_hours, 1) * 100)
        return cls(
            id=str(plan.id) if plan.id is not None else "",
            plan_date=_readable_date(plan.plan_date),
            total_hours_label=str(total) if total is not None else "0h",
            max_hours_label=f"{max_hours.normalize():f}h",
            hours_remaining_label=str(Duration(abs(remaining))),
            fill_percent=fill_percent,
            is_over_committed=plan.exceeds(max_hours),
            is_empty=plan.is_empty,
            generated_by_llm=plan.generated_by_llm,
            task_ids=tuple(str(task_id) for task_id in plan.task_ids),
        )

    @property
    def bar_class(self) -> str:
        """The bar turns red once the day is over-committed.

        Red only when over: at exactly the limit the bar is full and green,
        because a day planned to the limit is a full day, not a warning.
        """
        return "bg-red-500" if self.is_over_committed else "bg-green-500"

    @property
    def remaining_label(self) -> str:
        """Signed hours remaining, e.g. ``2.5h over``.

        The over-committed case is worded rather than shown as a negative
        number, because "-3.5h" in a bar reads as a rendering bug at a glance,
        while "3.5h over" reads as the warning it is.
        """
        if self.is_over_committed:
            return f"{self.hours_remaining_label} over"
        return f"{self.hours_remaining_label} free"


@dataclass(frozen=True, slots=True)
class EmptyDailyPlanViewModel:
    """What the daily page shows when no plan exists for the date.

    A ViewModel rather than an `if plan is None` branch in the template, so the
    template has exactly one shape to render and the empty state is covered by a
    unit test like everything else.
    """

    plan_date: str
    max_hours_label: str

    @classmethod
    def for_date(
        cls,
        plan_date: date,
        *,
        max_hours: Decimal = DEFAULT_MAX_DAILY_HOURS,
    ) -> EmptyDailyPlanViewModel:
        return cls(
            plan_date=_readable_date(plan_date),
            max_hours_label=f"{max_hours.normalize():f}h",
        )


@dataclass(frozen=True, slots=True)
class LlmHealthViewModel:
    """The navbar dot."""

    glyph: str
    label: str
    detail: str

    @classmethod
    def from_domain(cls, health: LlmHealth) -> LlmHealthViewModel:
        return cls(
            glyph=health.status_glyph,
            label=_health_label(health),
            detail=health.detail or "",
        )


def _health_label(health: LlmHealth) -> str:
    """The dot's accessible name and tooltip prefix.

    Not just the colour: a green/red dot is invisible to a screen reader and
    ambiguous to a red-green colourblind user, so the state is spelled out in
    text that the `title` and `aria-label` both use.
    """
    if health.unknown:
        return "IA: estado desconocido"
    if health.available:
        return "IA disponible"
    return "IA no disponible"


@dataclass(frozen=True, slots=True)
class GoalProgressViewModel:
    """The goal progress bar: a percentage, two hour figures, and its width.

    The percentage arrives already clamped to 0-100 from
    `GoalProgress.percentage`, so nothing here has to defend against an
    over-committed goal -- the arithmetic refuses to produce one, and this
    layer does not second-guess it.
    """

    percentage: int
    completed_label: str
    total_label: str
    remaining_label: str
    #: Whether there are no estimated hours behind this goal at all.
    #:
    #: Drives a different sentence under the bar. "0h completed of 0h" on a goal
    #: whose tasks have not been estimated reads as a measurement of zero
    #: progress; the honest reading is that nothing has been estimated yet, and
    #: the two should not be shown the same way.
    has_estimates: bool

    @classmethod
    def from_domain(cls, progress: GoalProgress) -> GoalProgressViewModel:
        return cls(
            percentage=progress.percentage,
            completed_label=str(progress.completed_hours),
            total_label=str(progress.total_hours),
            remaining_label=str(progress.remaining_hours),
            has_estimates=not progress.is_empty,
        )

    @property
    def summary(self) -> str:
        """The bar's accessible name and the sentence above it.

        One string rather than two because they must not be able to disagree: a
        screen reader that heard "50%" and a caption that said 60% would be a
        contradiction only one of the two could be checked against.
        """
        if not self.has_estimates:
            return "No tasks have an estimate yet."
        return (
            f"{self.percentage}% complete "
            f"({self.completed_label} of {self.total_label} estimated hours)"
        )


@dataclass(frozen=True, slots=True)
class GoalDetailViewModel:
    """Everything `tasks/goal_detail.html` renders.

    The goal and its tasks are kept apart from the progress rather than folded
    into one: the progress is *derived* from the task list, and rendering both
    from the same list means the bar cannot report a number the rows contradict.
    """

    goal: GoalViewModel
    tasks: tuple[TaskViewModel, ...] = ()
    progress: GoalProgressViewModel = field(
        default_factory=lambda: GoalProgressViewModel.from_domain(
            GoalProgress.from_tasks([])
        )
    )
    #: How many of `tasks` are finished, for the heading's counts.
    completed_count: int = 0

    @classmethod
    def build(
        cls,
        goal: Goal,
        tasks: list[Task],
        progress: GoalProgress,
        *,
        today: date,
        threshold: Decimal = DEFAULT_OVERWHELM_THRESHOLD,
    ) -> GoalDetailViewModel:
        task_models = tuple(
            TaskViewModel.from_domain(task, today=today, threshold=threshold)
            for task in tasks
        )
        return cls(
            goal=GoalViewModel.from_domain(goal, today=today),
            tasks=task_models,
            progress=GoalProgressViewModel.from_domain(progress),
            completed_count=sum(1 for task in task_models if task.is_completed),
        )

    @property
    def is_empty(self) -> bool:
        """Whether the goal has no tasks filed under it at all.

        Distinct from `GoalProgress.has_estimates`, and both matter: a goal with
        no tasks and a goal whose tasks are all unestimated both render an empty
        bar at 0%, but only one of them is a goal the user has not filed work
        against yet.
        """
        return not self.tasks


@dataclass(frozen=True, slots=True)
class BacklogViewModel:
    """Everything `tasks/backlog.html` renders.

    Aggregated in one object so a view passes a single value to the template and
    a unit test can assert on the whole page's model without a request context.
    """

    tasks: tuple[TaskViewModel, ...] = ()
    goals: tuple[GoalViewModel, ...] = ()
    today: str = ""
    overwhelm_threshold: Decimal = DEFAULT_OVERWHELM_THRESHOLD
    #: Number of tasks carrying the overwhelmed badge, for the summary line.
    overwhelmed_count: int = 0
    overdue_count: int = 0
    goal_options: tuple[tuple[str, str], ...] = field(default=())

    @classmethod
    def build(
        cls,
        tasks: list[Task],
        *,
        today: date,
        goals: list[Goal] | None = None,
        threshold: Decimal = DEFAULT_OVERWHELM_THRESHOLD,
    ) -> BacklogViewModel:
        task_models = tuple(
            TaskViewModel.from_domain(task, today=today, threshold=threshold)
            for task in tasks
        )
        goal_models = tuple(
            GoalViewModel.from_domain(goal, today=today) for goal in (goals or [])
        )
        return cls(
            tasks=task_models,
            goals=goal_models,
            today=_readable_date(today),
            overwhelm_threshold=threshold,
            overwhelmed_count=sum(1 for t in task_models if t.is_overwhelmed),
            overdue_count=sum(1 for t in task_models if t.is_overdue),
            goal_options=tuple(
                (str(goal.id), goal.name)
                for goal in (goals or [])
                if goal.id is not None and goal.status is GoalStatus.ACTIVE
            ),
        )

    @property
    def is_empty(self) -> bool:
        return not self.tasks


@dataclass(frozen=True, slots=True)
class CompletedTasksViewModel:
    """The finished-task section under the backlog.

    Three things this deliberately does **not** claim, and why.

    * **No date window, and no "last 7 days".** The data for one now exists --
      `completed_at` is on every task payload -- but this method does not use it.
      The list is the backend's, in the backend's order, which is `-created_at,
      -id`; a window would mean filtering and re-sorting in the BFF and then
      presenting the result as the backend's list. Where a *measured* completion
      figure is wanted -- the goal progress bar -- `completed_at` is read
      directly. Re-ordering this one is a separate change that has to be made
      deliberately rather than arriving as a side effect of adding a field.
    * **No ordering claim.** The backend sorts on `-created_at, -id`, so this
      list is newest-*created* first. The heading says "by creation date" rather
      than "most recent first", because the latter would be a promise about a
      field the response does not contain.

    Each row carries a Delete action and no Reopen one. **The backend can now
    express a reopen** -- `POST /tasks/<id>/reopen/` exists, and it returns the
    task with `completed_at` cleared -- so this is now a UI decision rather than
    an API limitation, and the wording here is deliberately accurate about that:
    the button is not missing because the request cannot be made. It is missing
    because reopening is a deliberate user decision that deserves a confirmation
    step, which no page has yet. Adding it is a small change and is recorded
    rather than half-done: a reopen button without the confirmation would sit
    one misclick away from discarding real work.
    """

    tasks: tuple[TaskViewModel, ...] = ()

    @classmethod
    def build(
        cls,
        tasks: list[Task],
        *,
        today: date,
        threshold: Decimal = DEFAULT_OVERWHELM_THRESHOLD,
    ) -> CompletedTasksViewModel:
        return cls(
            tasks=tuple(
                TaskViewModel.from_domain(task, today=today, threshold=threshold)
                for task in tasks
            )
        )

    @property
    def is_empty(self) -> bool:
        return not self.tasks


@dataclass(frozen=True, slots=True)
class GoalsViewModel:
    """Everything `tasks/goals.html` renders."""

    goals: tuple[GoalViewModel, ...] = ()
    today: str = ""

    @classmethod
    def build(cls, goals: list[Goal], *, today: date) -> GoalsViewModel:
        return cls(
            goals=tuple(GoalViewModel.from_domain(goal, today=today) for goal in goals),
            today=_readable_date(today),
        )

    @property
    def is_empty(self) -> bool:
        return not self.goals

    @property
    def active_count(self) -> int:
        return sum(1 for goal in self.goals if goal.status_is_active)


__all__ = [
    "DEFAULT_MAX_DAILY_HOURS",
    "BacklogViewModel",
    "CompletedTasksViewModel",
    "DailyPlanViewModel",
    "EmptyDailyPlanViewModel",
    "GoalDetailViewModel",
    "GoalProgressViewModel",
    "GoalViewModel",
    "GoalsViewModel",
    "ImportanceViewModel",
    "LlmHealthViewModel",
    "TaskViewModel",
]
