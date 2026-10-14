"""Unit tests for `GoalProgress`.

The arithmetic is small; what is worth pinning down is the set of states a goal
can be in, because every one of them is a page the user can land on and a bar
that must not crash or lie on it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.tasks.domain.entities import GoalProgress, Task
from src.tasks.domain.exceptions import InvalidGoalError
from src.tasks.domain.value_objects import Duration

DONE_AT = datetime(2026, 10, 5, 14, 31, tzinfo=UTC)


def task(hours: str | None, *, completed: bool = False) -> Task:
    return Task(
        name="A task",
        estimated_hours=Duration(Decimal(hours)) if hours is not None else None,
        completed_at=DONE_AT if completed else None,
    )


class TestNoTasks:
    def test_a_goal_with_no_tasks_is_zero_percent_not_a_division_by_zero(self):
        progress = GoalProgress.from_tasks([])

        assert progress.total_hours == Duration.zero()
        assert progress.completed_hours == Duration.zero()
        assert progress.percentage == 0
        assert progress.is_empty is True

    def test_a_goal_whose_tasks_are_all_unestimated_reads_as_unmeasured(self):
        """Distinct from a goal with nothing done.

        Both are 0%, but they are different facts and the page words them
        differently: one is "no progress", the other is "nothing to measure".
        """
        progress = GoalProgress.from_tasks([task(None), task(None)])

        assert progress.total_hours == Duration.zero()
        assert progress.percentage == 0
        assert progress.is_empty is True


class TestPartlyComplete:
    def test_sums_the_estimates_of_completed_tasks(self):
        progress = GoalProgress.from_tasks(
            [task("2.00", completed=True), task("1.00"), task("3.50")]
        )

        assert progress.total_hours == Duration(Decimal("6.50"))
        assert progress.completed_hours == Duration(Decimal("2.00"))
        assert progress.remaining_hours == Duration(Decimal("4.50"))

    def test_the_percentage_truncates_rather_than_rounding(self):
        """2 of 3 hours is 66.67%, and this reports 66.

        The direction is the point. Rounding would say 67% on two thirds of the
        work; truncation can only ever understate. A bar that quietly
        overshoots is worse than one that is a fraction pessimistic.
        """
        progress = GoalProgress.from_tasks([task("2.00", completed=True), task("1.00")])

        assert progress.percentage == 66

    def test_one_of_two_equal_hours_is_exactly_fifty_percent(self):
        progress = GoalProgress.from_tasks([task("1.00", completed=True), task("1.00")])

        assert progress.percentage == 50


class TestComplete:
    def test_every_task_done_is_one_hundred_percent(self):
        progress = GoalProgress.from_tasks(
            [task("2.00", completed=True), task("5.00", completed=True)]
        )

        assert progress.percentage == 100
        assert progress.remaining_hours == Duration.zero()

    def test_a_goal_is_capped_at_one_hundred(self):
        """A `percentage` above 100 cannot be constructed at all.

        `from_tasks` cannot produce one -- completed hours are a subset of the
        total -- so the cap lives in `__post_init__`, which is where a
        hand-assembled value is caught instead of rendering a bar wider than its
        track.
        """
        with pytest.raises(InvalidGoalError):
            GoalProgress(
                total_hours=Duration(Decimal("2.00")),
                completed_hours=Duration(Decimal("2.00")),
                percentage=140,
            )


class TestUnestimatedTasksAreExcluded:
    def test_an_unestimated_task_does_not_lower_the_percentage(self):
        """Counting it as zero would make every partly-done goal look worse.

        The estimate is the denominator, and a task with no estimate contributes
        nothing to it -- so it neither helps nor hurts the ratio.
        """
        progress = GoalProgress.from_tasks(
            [task("4.00", completed=True), task(None), task("4.00")]
        )

        assert progress.total_hours == Duration(Decimal("8.00"))
        assert progress.percentage == 50


class TestInvariants:
    def test_completed_hours_cannot_exceed_the_total(self):
        with pytest.raises(InvalidGoalError):
            GoalProgress(
                total_hours=Duration(Decimal("1.00")),
                completed_hours=Duration(Decimal("2.00")),
                percentage=200,
            )

    @pytest.mark.parametrize("percentage", [-1, 101])
    def test_the_percentage_must_be_within_range(self, percentage):
        with pytest.raises(InvalidGoalError):
            GoalProgress(
                total_hours=Duration(Decimal("1.00")),
                completed_hours=Duration.zero(),
                percentage=percentage,
            )

    def test_the_hours_must_be_durations(self):
        with pytest.raises(InvalidGoalError):
            GoalProgress(
                total_hours=Decimal("1.00"),  # type: ignore[arg-type]
                completed_hours=Duration.zero(),
                percentage=0,
            )
