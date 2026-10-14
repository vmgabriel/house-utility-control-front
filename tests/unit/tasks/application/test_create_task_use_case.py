"""Tests for `CreateTaskUseCase`.

Every test here passes a frozen clock rather than calling `date.today()`, for two
reasons that are really one: a test that reads the real date passes today and
fails tomorrow, and a rule phrased in terms of "today" is exactly the kind whose
boundary cases (`today`, `today - 1`, `today + 1`) are the ones worth asserting
and the ones a real date makes impossible to pin down.

The assertions are on the port call, not just the outcome: a use case that
rejected the input but had already called `create_task` would be a bug worth
catching, and "did we make a request?" is the only place that shows up.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from src.tasks.application.use_cases.create_task import CreateTaskUseCase
from src.tasks.domain.entities import Task
from src.tasks.domain.exceptions import InvalidTaskError, InvalidTasksInputError
from src.tasks.domain.value_objects import GoalId, Importance, Priority

#: The "today" every test in this module pretends it is. Any frozen value works;
#: a fixed date rather than a relative one makes a failing test's message
#: unambiguous.
TODAY = date(2026, 10, 14)


class FrozenClock:
    """A `Clock` that always answers the same day."""

    def __init__(self, today: date = TODAY) -> None:
        self._today = today

    def today(self) -> date:
        return self._today


class RecordingTasksClient:
    """A port stand-in that records what it was asked to create.

    Hand-written rather than an `AsyncMock` so that a test which asserts "the
    input was rejected" can also assert "and nothing was sent", which
    `AsyncMock` makes easy to forget and awkward to express.
    """

    def __init__(self) -> None:
        self.created: list[Task] = []

    async def create_task(self, access_token: str, task: Task) -> Task:
        self.created.append(task)
        return task

    @property
    def call_count(self) -> int:
        return len(self.created)


def build_use_case(clock: FrozenClock | None = None) -> tuple:
    client = RecordingTasksClient()
    return CreateTaskUseCase(tasks_client=client, clock=clock or FrozenClock()), client


class TestDueDateValidation:
    """`due_date` may not be in the past. Today is allowed."""

    @pytest.mark.asyncio
    async def test_a_due_date_in_the_past_is_rejected(self):
        use_case, client = build_use_case()

        with pytest.raises(InvalidTasksInputError) as caught:
            await use_case.execute(
                "token", name="Write the report", due_date=date(2026, 10, 13)
            )

        assert "past" in str(caught.value).lower()
        assert client.call_count == 0, "a rejected task must not reach the backend"

    @pytest.mark.asyncio
    async def test_a_due_date_exactly_today_is_accepted(self):
        """The boundary, and the case most likely to be broken by an off-by-one.

        A `>=` comparison rejects today while its error message claims to be
        about the past, which is the bug this assertion exists to catch. "Do it
        before I go home" is also the most common due date in practice.
        """
        use_case, client = build_use_case()

        task = await use_case.execute("token", name="Call the bank", due_date=TODAY)

        assert task.due_date == TODAY
        assert client.call_count == 1

    @pytest.mark.asyncio
    async def test_a_future_due_date_is_accepted(self):
        """Planning ahead is the point of a backlog."""
        use_case, client = build_use_case()

        task = await use_case.execute(
            "token", name="Renew the contract", due_date=date(2026, 12, 31)
        )

        assert task.due_date == date(2026, 12, 31)
        assert client.call_count == 1

    @pytest.mark.asyncio
    async def test_the_boundary_follows_the_injected_clock(self):
        """A date that is "in the past" for one clock is fine for an earlier one.

        Proves the rule reads the clock rather than the system date, which is
        what makes it testable at all -- and what would break silently if someone
        "simplified" `self.clock.today()` back to `date.today()`.
        """
        earlier_use_case, earlier_client = build_use_case(
            FrozenClock(date(2026, 10, 13))
        )
        later_use_case, later_client = build_use_case(FrozenClock(date(2026, 10, 20)))

        await earlier_use_case.execute("token", name="Thing", due_date=TODAY)

        with pytest.raises(InvalidTasksInputError):
            await later_use_case.execute("token", name="Thing", due_date=TODAY)

        assert earlier_client.call_count == 1
        assert later_client.call_count == 0

    @pytest.mark.asyncio
    async def test_an_omitted_due_date_is_accepted(self):
        """A task with no deadline is legal, and is the default in the form."""
        use_case, client = build_use_case()

        task = await use_case.execute("token", name="Someday")

        assert task.due_date is None
        assert client.call_count == 1

    @pytest.mark.asyncio
    async def test_a_datetime_is_rejected(self):
        """`isinstance(x, date)` is True for `datetime`, and it must not be.

        A datetime reaching the entity would lose its time component on the way
        to a template and render as a plain date, so the rejection is about
        catching the wrong type at the boundary rather than about dates.
        """
        use_case, client = build_use_case()

        with pytest.raises(InvalidTasksInputError):
            await use_case.execute(
                "token",
                name="Thing",
                due_date=datetime(2026, 10, 15, 18, 30, tzinfo=UTC),
            )

        assert client.call_count == 0


class TestOtherInputValidation:
    """The rest of the form's rules, for the same reason.

    Each asserts the port was not called, which is the property that actually
    matters: a use case that rejects *after* sending would surface a DRF 400 to
    the user instead of a message naming the field.
    """

    @pytest.mark.asyncio
    async def test_a_blank_name_is_rejected(self):
        use_case, client = build_use_case()

        with pytest.raises(InvalidTasksInputError):
            await use_case.execute("token", name="   ")

        assert client.call_count == 0

    @pytest.mark.asyncio
    async def test_a_negative_estimate_is_rejected(self):
        use_case, client = build_use_case()

        with pytest.raises(InvalidTasksInputError):
            await use_case.execute("token", name="Thing", estimated_hours="-1")

        assert client.call_count == 0

    @pytest.mark.asyncio
    async def test_an_unparseable_estimate_is_rejected(self):
        use_case, client = build_use_case()

        with pytest.raises(InvalidTasksInputError):
            await use_case.execute("token", name="Thing", estimated_hours="soon")

        assert client.call_count == 0

    @pytest.mark.asyncio
    async def test_an_estimate_beyond_the_daily_budget_is_rejected(self):
        """A 500-hour estimate is a typo, not a plan."""
        use_case, client = build_use_case()

        with pytest.raises(InvalidTasksInputError):
            await use_case.execute("token", name="Thing", estimated_hours="500")

        assert client.call_count == 0

    @pytest.mark.asyncio
    async def test_a_zero_estimate_means_not_estimated(self):
        """An empty `<input>` posts `""`, which means "no estimate", not "no time".

        Rendering a `Duration(0)` would show "0h" on the task row, claiming the
        user made an estimate of zero effort.
        """
        use_case, _ = build_use_case()

        for raw in ("", "   ", "0", 0):
            task = await use_case.execute("token", name="Thing", estimated_hours=raw)
            assert task.estimated_hours is None, raw

    @pytest.mark.asyncio
    async def test_a_non_priority_importance_is_rejected(self):
        """The wire's `importance` is a choice, and a bare int is not one.

        The frontend's 1-5 scale has no wire field, so a caller passing one is
        either confused about the contract or tampering -- both deserve a
        message rather than a coerced `Priority.MEDIUM`.
        """
        use_case, client = build_use_case()

        with pytest.raises(InvalidTasksInputError):
            await use_case.execute("token", name="Thing", priority=7)

        assert client.call_count == 0

    @pytest.mark.asyncio
    async def test_the_dot_value_is_derived_from_the_priority(self):
        """Never accepted as input: the backend could not store it."""
        use_case, _ = build_use_case()

        for priority, expected in (
            (Priority.LOW, 1),
            (Priority.MEDIUM, 3),
            (Priority.HIGH, 5),
        ):
            task = await use_case.execute("token", name="Thing", priority=priority)
            assert int(task.importance) == expected, priority

    @pytest.mark.asyncio
    async def test_a_string_goal_id_is_rejected_rather_than_coerced(self):
        """A wrong goal id means the template and the domain have drifted.

        Coercing would attach the task to whichever goal the string parsed as,
        which is a worse outcome than refusing.
        """
        use_case, client = build_use_case()

        with pytest.raises(InvalidTasksInputError):
            await use_case.execute("token", name="Thing", goal_id="not-a-uuid")

        assert client.call_count == 0

    @pytest.mark.asyncio
    async def test_a_goal_id_that_is_not_a_real_goal_is_the_backends_problem(self):
        """A well-formed id is the client's business; whether it exists is not.

        The use case forwards it and lets the backend answer, so a stale goal in
        a still-open form produces the backend's own message rather than a
        client-side guess.
        """
        use_case, client = build_use_case()
        goal = GoalId("22222222-2222-2222-2222-222222222222")

        task = await use_case.execute("token", name="Thing", goal_id=goal)

        assert task.goal_id == goal
        assert client.call_count == 1

    @pytest.mark.asyncio
    async def test_an_over_long_description_is_rejected_by_the_use_case(self):
        """Caught here rather than left to the entity.

        The use case owns it so the message names the field the user filled in,
        which the entity's generic one does not.
        """
        use_case, client = build_use_case()

        with pytest.raises(InvalidTasksInputError) as caught:
            await use_case.execute("token", name="Thing", description="x" * 5001)

        assert "description" in str(caught.value).lower()
        assert client.call_count == 0


class TestTaskForwardedToThePort:
    """What reaches the client, once the input is valid."""

    @pytest.mark.asyncio
    async def test_the_domain_normalises_the_name(self):
        use_case, _ = build_use_case()

        task = await use_case.execute("token", name="  Buy   milk  ")

        assert task.name == "Buy milk"

    @pytest.mark.asyncio
    async def test_the_description_keeps_its_markdown_intact(self):
        """Whitespace collapsing is wrong for markdown and must not happen here.

        A description is a `TextField` whose interior newlines and indentation
        are meaningful; collapsing them would reflow every paragraph in the
        rendered markdown.
        """
        use_case, _ = build_use_case()
        markdown = "## Heading\n\n- one\n- two\n\n    indented code"

        task = await use_case.execute("token", name="Thing", description=markdown)

        assert task.description == markdown

    @pytest.mark.asyncio
    async def test_the_task_starts_as_todo_and_unassessed(self):
        """Status and the LLM flags are facts about work nobody has done yet.

        Letting the create path set them would let a form claim a task was
        evaluated by the AI before anyone asked.
        """
        use_case, _ = build_use_case()

        task = await use_case.execute("token", name="Thing")

        assert task.status.value == "todo"
        assert task.is_checked_by_llm is False
        assert task.llm_evaluation_failed is False

    @pytest.mark.asyncio
    async def test_valid_input_is_passed_through_unchanged(self):
        use_case, client = build_use_case()

        await use_case.execute(
            "token",
            name="Ship v2",
            description="With **markdown**",
            priority=Priority.HIGH,
            estimated_hours=Decimal("2.5"),
            due_date=date(2026, 11, 1),
        )

        sent = client.created[0]
        assert sent.name == "Ship v2"
        assert sent.description == "With **markdown**"
        assert sent.priority is Priority.HIGH
        # Derived from the level, since nothing else could set it.
        assert sent.importance == Importance(5)
        assert sent.estimated_hours is not None
        assert sent.estimated_hours.amount == Decimal("2.5")
        assert sent.due_date == date(2026, 11, 1)


class TestValidationDoesNotMaskDomainErrors:
    """The line between "the user typed something odd" and "this is a bug"."""

    def test_the_domain_owns_the_invariants_the_use_case_does_not_check(self):
        """A `Task` cannot be constructed in a state the use case forbids anyway.

        Asserted directly on the domain rather than through the use case, because
        the point is that the entity does not depend on the use case having run
        first. If someone loosens the use case, this still holds.
        """
        with pytest.raises(InvalidTaskError):
            Task.create(name="   ")

        with pytest.raises(InvalidTaskError):
            Task.create(name="Thing", importance=Importance(0))
