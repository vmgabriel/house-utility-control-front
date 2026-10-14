"""Tests for `TaskEdits`, the partial-update value object.

The object exists because `None` cannot carry three meanings at once on the
edit path. For `description`, `due_date`, and `estimated_hours`, `None` is a
legitimate instruction -- "clear this" -- while the *absence* of a field means
"leave it alone". `UpdateTaskSerializer` is applied with ``partial=True``, so
those two are different requests to the backend and have to be different states
here.

These tests therefore say as much about what is *absent* as about what is set.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from src.tasks.domain.exceptions import InvalidTaskError
from src.tasks.domain.value_objects import (
    UNSET,
    Duration,
    Priority,
    TaskEdits,
    Unset,
)


class TestTheUnsetSentinel:
    def test_is_falsy_so_a_bare_truth_test_reads_as_is_it_set(self):
        assert not UNSET

    def test_repr_names_it_in_a_failure_message(self):
        assert repr(UNSET) == "UNSET"

    def test_survives_copying_as_the_same_singleton(self):
        """Every default argument shares one identity, so a copy that produced a
        new object would stop being `isinstance(..., Unset)`-comparable to it."""
        import copy

        assert copy.copy(UNSET) is UNSET
        assert copy.deepcopy(UNSET) is UNSET


class TestTheThreeStatesOfAField:
    def test_an_unset_field_is_absent_from_the_value(self):
        assert TaskEdits(name="Ship it").due_date is UNSET

    def test_a_set_field_carries_its_value(self):
        assert (
            TaskEdits(name="Ship it", priority=Priority.HIGH).priority is Priority.HIGH
        )

    def test_none_is_a_value_not_an_absence(self):
        """The whole reason `Unset` exists. Without this distinction there is no
        way to clear a due date."""
        edits = TaskEdits(due_date=None)
        assert edits.due_date is None
        assert not isinstance(edits.due_date, Unset)

    def test_the_three_states_are_distinguishable_in_one_object(self):
        edits = TaskEdits(name="Ship it", due_date=None, priority=Priority.LOW)
        assert edits.name == "Ship it"
        assert edits.due_date is None
        assert edits.estimated_hours is UNSET


class TestValidation:
    def test_an_empty_edit_is_rejected(self):
        """`UpdateTaskSerializer.validate` answers an empty payload with a 400,
        so catching it here saves a pointless round trip."""
        with pytest.raises(InvalidTaskError, match="Provide name"):
            TaskEdits()

    def test_the_name_is_whitespace_collapsed(self):
        assert TaskEdits(name="  Buy   milk ").name == "Buy milk"

    def test_a_blank_name_is_rejected(self):
        with pytest.raises(InvalidTaskError, match="cannot be empty"):
            TaskEdits(name="   ")

    def test_an_over_long_name_is_rejected(self):
        with pytest.raises(InvalidTaskError, match="cannot exceed"):
            TaskEdits(name="x" * 201)

    def test_an_over_long_description_is_rejected(self):
        with pytest.raises(InvalidTaskError, match="cannot exceed"):
            TaskEdits(name="Ship it", description="x" * 5001)

    def test_a_datetime_reaching_the_due_date_is_rejected(self):
        """`isinstance(datetime, date)` is true, so this would otherwise be
        silently truncated to its date -- and a due date that reads as
        "2026-03-01" for 18:00 is a lie the user acts on."""
        with pytest.raises(InvalidTaskError, match="must be a date"):
            TaskEdits(name="Ship it", due_date=datetime(2026, 3, 1, 18, 0))

    def test_a_plain_date_is_accepted(self):
        assert TaskEdits(name="Ship it", due_date=date(2026, 3, 1)).due_date == date(
            2026, 3, 1
        )

    def test_a_non_priority_is_rejected(self):
        with pytest.raises(InvalidTaskError, match="must be a Priority"):
            TaskEdits(name="Ship it", priority="high")

    def test_a_non_duration_is_rejected(self):
        with pytest.raises(InvalidTaskError, match="must be a Duration or None"):
            TaskEdits(name="Ship it", estimated_hours=2)

    def test_interior_newlines_in_a_description_survive(self):
        """Markdown is whitespace-sensitive; collapsing runs would destroy every
        paragraph break and silently reflow the rendered text."""
        description = "## Sections\n\n- revenue\n- costs"
        assert (
            TaskEdits(name="Ship it", description=description).description
            == description
        )

    def test_a_blank_description_becomes_none(self):
        """`None`, not `"   "`: an all-whitespace description renders as an empty
        markdown block in the template."""
        assert TaskEdits(name="Ship it", description="   ").description is None


class TestIntrospection:
    def test_is_empty_is_false_once_anything_is_set(self):
        assert not TaskEdits(estimated_hours=None).is_empty

    def test_touch_name_reports_the_instruction_not_the_value(self):
        """A task named "Buy milk" edited without a rename still carries a name.
        Whether the *caller sent one* is a different question, and it is the one
        the payload builder asks -- which is why the mapper tests
        `isinstance(edits.name, Unset)` rather than testing for a truthy value.
        """
        assert isinstance(TaskEdits(name="Buy milk").name, str)
        assert isinstance(TaskEdits(priority=Priority.HIGH).name, Unset)

    def test_it_is_hashable_and_comparable(self):
        """Frozen dataclass, so an edit can be asserted on directly in a test
        rather than field by field."""
        assert TaskEdits(name="Ship it") == TaskEdits(name="Ship it")
        assert TaskEdits(name="Ship it") != TaskEdits(name="Ship that")

    def test_a_duration_is_accepted_as_the_hours_value(self):
        assert TaskEdits(
            name="Ship it", estimated_hours=Duration(1.5)
        ).estimated_hours == Duration(1.5)
