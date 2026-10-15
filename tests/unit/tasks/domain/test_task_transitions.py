"""Unit tests for the status-transition matrix.

This is the board's rulebook, so what matters is not the six cases individually
but the two gaps. A board that offers a move the backend refuses is worse than a
board with fewer arrows: the first one teaches the user that the board is lying,
and that lesson generalises to every other thing on the page.

The backend has exactly three status endpoints and each is a named decision, not
a generic write. Two of the six possible drags therefore cannot be expressed:

* ``doing -> todo``  -- `reopen` answers 409 unless the task is *finished*, and
  nothing else sets ``todo``.
* ``done -> doing``   -- `mark_as-doing` answers 409 on a finished task, and
  nothing else sets ``doing``.

The rest are asserted positively, so that a later change which "fixes" one of
the gaps by inventing a transition is caught rather than assumed harmless.
"""

from __future__ import annotations

import pytest

from src.tasks.domain.entities import KANBAN_ORDER, Task
from src.tasks.domain.exceptions import InvalidTaskError
from src.tasks.domain.value_objects import TASK_TRANSITIONS, TaskStatus

ALL_STATUSES = (TaskStatus.TODO, TaskStatus.DOING, TaskStatus.DONE)


class TestTheMatrix:
    @pytest.mark.parametrize(
        ("source", "target"),
        [
            (TaskStatus.TODO, TaskStatus.DOING),
            (TaskStatus.TODO, TaskStatus.DONE),
            (TaskStatus.DOING, TaskStatus.DONE),
            (TaskStatus.DONE, TaskStatus.TODO),
        ],
    )
    def test_the_four_moves_the_backend_permits_are_allowed(
        self, source: TaskStatus, target: TaskStatus
    ):
        assert Task.can_transition_to(source, target) is True

    @pytest.mark.parametrize(
        ("source", "target", "why"),
        [
            (
                TaskStatus.DOING,
                TaskStatus.TODO,
                "reopen answers 409 unless the task was finished",
            ),
            (
                TaskStatus.DONE,
                TaskStatus.DOING,
                "mark-as-doing answers 409 on a finished task",
            ),
        ],
    )
    def test_the_two_moves_the_backend_cannot_perform_are_refused(
        self, source: TaskStatus, target: TaskStatus, why: str
    ):
        """The whole point of the class.

        `why` is a parameter only so the failure message names the backend rule
        being protected. A test that says *this* arrow must never appear, without
        recording *what* would be broken by drawing it, is a test nobody can act
        on when it fails.
        """
        assert Task.can_transition_to(source, target) is False, why

    @pytest.mark.parametrize("status", ALL_STATUSES)
    def test_no_status_may_move_to_itself(self, status: TaskStatus):
        """A same-column drop is not a move, and must not be one.

        SortableJS fires `onEnd` for a drop inside the same column too, so
        without this a card released where it started would POST a transition and
        re-stamp its completion time.
        """
        assert Task.can_transition_to(status, status) is False

    def test_the_matrix_covers_every_ordered_pair(self):
        """Nine pairs in, nine answers out -- so a new status cannot half-work."""
        pairs = {(source, target) for source in ALL_STATUSES for target in ALL_STATUSES}
        assert len(pairs) == 9
        for source, target in pairs:
            answer = Task.can_transition_to(source, target)
            assert isinstance(answer, bool)

    @pytest.mark.parametrize("bad", ["doing", "", None, 3])
    def test_a_non_status_is_rejected_rather_than_guessed(self, bad):
        """Both arguments are checked.

        A bare `TaskStatus("doing")` would raise a `ValueError`, which the
        views' `except DomainException` does not catch -- turning a tampered
        request into a 500 instead of a flash.
        """
        with pytest.raises(InvalidTaskError):
            Task.can_transition_to(bad, TaskStatus.DONE)
        with pytest.raises(InvalidTaskError):
            Task.can_transition_to(TaskStatus.TODO, bad)


class TestTransitionLookup:
    def test_each_permitted_move_names_its_backend_operation(self):
        """`todo -> doing` is `start`; `doing -> done` is `complete`.

        Asserted against the names because the use case dispatches on them. The
        alternative -- dispatching on the target status directly -- would work
        only while the mapping stayed one-to-one, and `TASK_TRANSITIONS` is
        exactly where that assumption should be visible.
        """
        assert Task.transition_for(TaskStatus.TODO, TaskStatus.DOING).name == "start"
        assert Task.transition_for(TaskStatus.DOING, TaskStatus.DONE).name == "complete"
        assert Task.transition_for(TaskStatus.TODO, TaskStatus.DONE).name == "complete"
        assert Task.transition_for(TaskStatus.DONE, TaskStatus.TODO).name == "reopen"

    @pytest.mark.parametrize(
        ("source", "target"),
        [
            (TaskStatus.DOING, TaskStatus.TODO),
            (TaskStatus.DONE, TaskStatus.DOING),
            (TaskStatus.TODO, TaskStatus.TODO),
        ],
    )
    def test_an_impossible_move_has_no_transition_at_all(
        self, source: TaskStatus, target: TaskStatus
    ):
        assert Task.transition_for(source, target) is None

    def test_every_status_has_exactly_one_transition_that_produces_it(self):
        """`move to X` has to be unambiguous or the use case would guess.

        This is what lets the client send only a target status and never a
        source: the target alone names the operation.
        """
        targets = [transition.target for transition in TASK_TRANSITIONS.values()]
        assert sorted(targets) == sorted(ALL_STATUSES)
        assert len(targets) == len(set(targets))


class TestAllowedTargets:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (TaskStatus.TODO, ["doing", "done"]),
            # A `doing` task can go exactly one place. Both `-> todo` and the
            # self-move are excluded, which is why its card renders with no
            # usable control and the board has nothing to draw on it.
            (TaskStatus.DOING, ["done"]),
            (TaskStatus.DONE, ["todo"]),
        ],
    )
    def test_each_card_offers_exactly_the_legal_destinations(
        self, status: TaskStatus, expected: list[str]
    ):
        task = Task(name="A task")
        task.status = status

        assert [t.value for t in task.allowed_targets()] == expected

    def test_options_come_out_in_board_order_not_alphabetical(self):
        """`todo` offers Doing then Done, not Done then Doing.

        Options sorted independently of the columns would give a select whose
        order contradicts the board's own reading direction, and it would be the
        kind of detail nobody notices until the two disagree.
        """
        task = Task(name="A task")
        task.status = TaskStatus.TODO

        assert task.allowed_targets() == (
            TaskStatus.DOING,
            TaskStatus.DONE,
        )
        assert [
            status for status in KANBAN_ORDER if status in task.allowed_targets()
        ] == list(task.allowed_targets())

    def test_the_instance_helper_agrees_with_the_static_one(self):
        """Two spellings of one question, and they must not diverge.

        `can_move_to` exists so a caller holding a task cannot forget to pass
        `task.status` -- and an omission there would answer a different question
        rather than fail, which is exactly the kind of bug nothing catches.
        """
        for status in ALL_STATUSES:
            task = Task(name="A task")
            task.status = status
            for target in ALL_STATUSES:
                assert task.can_move_to(target) == Task.can_transition_to(
                    status, target
                )
