"""Mapper tests for the tasks context, against the backend's real wire shapes.

Every payload here is copied from the serializers in the backend repository
(`src/apps/tasks/interfaces/serializers.py`), not from what the BFF would like
the API to send. That distinction is the point of the file.

Two of these tests exist because a schema written from assumption rather than
from the serializer shipped and then failed against the live API:

* `test_llm_health_maps_the_ok_status` -- the schema required an ``available``
  boolean the API never sends, and because the navbar polls health on every page
  load, that one field took down every page in the app with a 500.
* `test_daily_plan_reads_the_nested_task_list` -- the schema expected a flat
  ``task_ids`` array; the API nests full task objects, so every day mapped to an
  empty plan and the daily page rendered blank with no error to find.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.tasks.domain.exceptions import InvalidTasksInputError
from src.tasks.domain.value_objects import GoalStatus, Priority, TaskStatus
from src.tasks.infrastructure.mappers import (
    map_daily_plan,
    map_goal,
    map_llm_health,
    map_task,
    task_edits_to_payload,
    task_to_payload,
)
from src.tasks.infrastructure.schemas import (
    DRFDailyPlanResponse,
    DRFGoalResponse,
    DRFLlmHealthResponse,
    DRFTaskResponse,
)

# Verbatim from `TaskSerializer`. `estimated_hours` is a string because DRF's
# `COERCE_DECIMAL_TO_STRING` is on by default.
TASK_PAYLOAD = {
    "id": "11111111-1111-1111-1111-111111111111",
    "goal_id": "22222222-2222-2222-2222-222222222222",
    "name": "Draft the quarterly report",
    "description": "## Sections\n\n- revenue\n- costs",
    "due_date": "2026-11-01",
    "importance": "high",
    "estimated_hours": "9.00",
    "status": "todo",
    "is_overwhelmed": True,
    "is_checked_by_llm": False,
    "llm_evaluation_failed": False,
    "created_at": "2026-10-14T09:00:00Z",
    "updated_at": "2026-10-14T09:00:00Z",
}


class TestTaskMapping:
    def test_maps_every_field_of_a_real_payload(self):
        task = map_task(DRFTaskResponse.model_validate(TASK_PAYLOAD))

        assert str(task.id) == TASK_PAYLOAD["id"]
        assert task.name == "Draft the quarterly report"
        assert str(task.goal_id) == TASK_PAYLOAD["goal_id"]
        assert task.due_date == date(2026, 11, 1)
        # The wire's `importance` is the priority level.
        assert task.priority is Priority.HIGH
        assert task.status is TaskStatus.TODO
        assert task.estimated_hours is not None
        assert task.estimated_hours.amount == 9
        # ...and the 1-5 dots are derived from it, not read from the wire.
        assert int(task.importance) == 5

    def test_derives_the_dots_from_the_priority_level(self):
        for wire_value, expected in (("low", 1), ("medium", 3), ("high", 5)):
            payload = {**TASK_PAYLOAD, "importance": wire_value}
            task = map_task(DRFTaskResponse.model_validate(payload))
            assert int(task.importance) == expected, wire_value

    def test_carries_the_backend_overwhelmed_verdict(self):
        """`False` and "absent" must stay distinguishable -- see the entity."""
        assert map_task(DRFTaskResponse.model_validate(TASK_PAYLOAD)).is_overwhelmed(4)

    def test_a_false_verdict_is_kept_and_not_recomputed(self):
        """A small task the backend explicitly cleared stays unflagged.

        Without this the local threshold would re-flag it, which is exactly the
        "the badge flickers between pages" bug the precedence rule exists to
        prevent.
        """
        payload = {**TASK_PAYLOAD, "estimated_hours": "0.50", "is_overwhelmed": False}
        task = map_task(DRFTaskResponse.model_validate(payload))

        assert task.server_is_overwhelmed is False
        assert task.is_overwhelmed(0.1) is False

    def test_an_absent_verdict_falls_back_to_the_local_threshold(self):
        payload = {**TASK_PAYLOAD, "estimated_hours": "9.00"}
        payload.pop("is_overwhelmed")
        task = map_task(DRFTaskResponse.model_validate(payload))

        assert task.server_is_overwhelmed is None
        assert task.is_overwhelmed(4) is True

    def test_a_null_goal_maps_to_none(self):
        """`goal_id` is a nullable FK; most backlog items are not filed."""
        payload = {**TASK_PAYLOAD, "goal_id": None}
        assert map_task(DRFTaskResponse.model_validate(payload)).goal_id is None

    def test_a_null_estimate_means_not_estimated(self):
        """Absent locally, so the verdict comes from the backend's flag."""
        payload = {
            **TASK_PAYLOAD,
            "estimated_hours": None,
            "is_overwhelmed": False,
        }
        task = map_task(DRFTaskResponse.model_validate(payload))

        assert task.estimated_hours is None
        assert task.is_overwhelmed(1) is False

    def test_a_null_estimate_with_no_verdict_is_never_overwhelmed(self):
        """The local fallback has nothing to compare, so the answer is no."""
        payload = {**TASK_PAYLOAD, "estimated_hours": None}
        payload.pop("is_overwhelmed")
        task = map_task(DRFTaskResponse.model_validate(payload))

        assert task.server_is_overwhelmed is None
        assert task.is_overwhelmed(1) is False

    def test_an_unknown_importance_level_is_reported_not_coerced(self):
        """Defaulting would render a wrong badge with no signal that it moved."""
        payload = {**TASK_PAYLOAD, "importance": "urgent"}
        with pytest.raises(InvalidTasksInputError) as caught:
            map_task(DRFTaskResponse.model_validate(payload))

        assert "urgent" in str(caught.value)

    def test_a_datetime_in_the_due_date_field_is_rejected(self):
        """`fromisoformat` would accept it and silently drop the time."""
        payload = {**TASK_PAYLOAD, "due_date": "2026-11-01T18:00:00Z"}
        with pytest.raises(InvalidTasksInputError):
            map_task(DRFTaskResponse.model_validate(payload))


class TestLlmHealthMapping:
    """The contract: `status`, never `available`."""

    def test_the_ok_shape_the_backend_sends(self):
        """`GET .../llm-health/` -> 200 `{"status": "ok", "model": "qwen2.5:3b"}`."""
        health = map_llm_health(
            DRFLlmHealthResponse.model_validate({"status": "ok", "model": "qwen2.5:3b"})
        )

        assert health.available is True
        assert health.unknown is False
        assert health.status_glyph == "🟢"
        # The model name is the most useful thing to say to a user about to ask
        # the assistant something.
        assert health.detail == "qwen2.5:3b"

    def test_the_unavailable_shape_the_backend_sends(self):
        """503 `{"status": "unavailable", "model": null, "error": "..."}`."""
        health = map_llm_health(
            DRFLlmHealthResponse.model_validate(
                {
                    "status": "unavailable",
                    "model": None,
                    "error": "connection refused",
                }
            )
        )

        assert health.available is False
        assert health.unknown is False
        assert health.status_glyph == "🔴"
        assert health.detail == "connection refused"

    def test_an_unrecognised_status_is_unknown_rather_than_guessed(self):
        """Both available defaults -- up and down -- would be a lie."""
        health_error = pytest.raises(
            InvalidTasksInputError,
            map_llm_health,
            DRFLlmHealthResponse.model_validate({"status": "warming_up"}),
        )
        assert health_error is not None

    def test_status_is_matched_case_insensitively(self):
        """Cheap leniency, because it is free and the wire is lowercase today."""
        health = map_llm_health(
            DRFLlmHealthResponse.model_validate({"status": "OK", "model": "x"})
        )
        assert health.available is True


class TestGoalMapping:
    def test_maps_the_serialized_shape_including_task_count(self):
        goal = map_goal(
            DRFGoalResponse.model_validate(
                {
                    "id": "22222222-2222-2222-2222-222222222222",
                    "name": "Ship v2",
                    "description": "",
                    "due_date": None,
                    "status": "active",
                    "task_count": 4,
                    "created_at": "2026-10-14T09:00:00Z",
                    "updated_at": "2026-10-14T09:00:00Z",
                }
            )
        )

        assert goal.name == "Ship v2"
        assert goal.status is GoalStatus.ACTIVE
        assert goal.task_count == 4
        # An empty-string description is `None`, never "" -- it would render as
        # an empty markdown block.
        assert goal.description is None

    def test_an_archived_goal_maps(self):
        """The third status is `archived`, not `cancelled`.

        An earlier schema modelled it as `cancelled`, and since the mapper
        reports unknown members rather than coercing them, every archived goal
        would have raised.
        """
        goal = map_goal(
            DRFGoalResponse.model_validate(
                {
                    "id": "22222222-2222-2222-2222-222222222222",
                    "name": "Old plan",
                    "status": "archived",
                }
            )
        )

        assert goal.status is GoalStatus.ARCHIVED

    def test_a_missing_task_count_defaults_to_zero(self):
        goal = map_goal(
            DRFGoalResponse.model_validate(
                {"id": "22222222-2222-2222-2222-222222222222", "name": "X"}
            )
        )
        assert goal.task_count == 0


class TestDailyPlanMapping:
    """The plan nests full task objects; it does not send a flat id array."""

    def test_reads_the_nested_task_list(self):
        plan = map_daily_plan(
            DRFDailyPlanResponse.model_validate(
                {
                    "id": "44444444-4444-4444-4444-444444444444",
                    "date": "2026-10-14",
                    "tasks": [TASK_PAYLOAD],
                    "total_hours": "9.00",
                    "is_full": False,
                    "generated_by_llm": True,
                    "created_at": "2026-10-14T09:00:00Z",
                }
            )
        )

        assert plan.plan_date == date(2026, 10, 14)
        assert [str(task_id) for task_id in plan.task_ids] == [TASK_PAYLOAD["id"]]
        assert plan.total_hours is not None
        assert plan.total_hours.amount == 9
        assert plan.generated_by_llm is True
        assert plan.is_empty is False

    def test_a_day_with_no_tasks_maps_to_empty_not_broken(self):
        plan = map_daily_plan(
            DRFDailyPlanResponse.model_validate(
                {
                    "id": "44444444-4444-4444-4444-444444444444",
                    "date": "2026-10-14",
                    "tasks": [],
                    "total_hours": "0.00",
                    "is_full": False,
                    "generated_by_llm": False,
                }
            )
        )

        assert plan.is_empty is True
        assert plan.task_ids == []

    def test_the_total_is_mapped_not_recomputed(self):
        """The backend's sum is authoritative, even when it looks wrong.

        Recomputing it here would need every task re-fetched, and would be free
        to disagree with the number already stored and shown.
        """
        plan = map_daily_plan(
            DRFDailyPlanResponse.model_validate(
                {
                    "id": "44444444-4444-4444-4444-444444444444",
                    "date": "2026-10-14",
                    "tasks": [TASK_PAYLOAD],  # 9.00h
                    "total_hours": "3.00",  # does not match; still mapped
                }
            )
        )

        assert plan.total_hours is not None
        assert plan.total_hours.amount == 3


class TestCreateTaskPayload:
    def test_sends_only_fields_the_create_serializer_accepts(self):
        """`CreateTaskSerializer` declares exactly these five inputs."""
        from src.tasks.domain.entities import Task
        from src.tasks.domain.value_objects import Duration, GoalId

        task = Task.create(
            name="Ship it",
            goal_id=GoalId("22222222-2222-2222-2222-222222222222"),
            description="With **markdown**",
            due_date=date(2026, 11, 1),
            estimated_hours=Duration(1.5),
        )

        payload = task_to_payload(task)

        assert payload == {
            "name": "Ship it",
            # `importance` is the priority level: the serializer's choice field,
            # not the frontend's 1-5 number.
            "importance": "medium",
            "description": "With **markdown**",
            "goal_id": "22222222-2222-2222-2222-222222222222",
            "due_date": "2026-11-01",
            # `Decimal("1.5")` stringifies without the trailing zero. DRF's
            # DecimalField parses either, so this is exact rather than pretty.
            "estimated_hours": "1.5",
        }

    def test_an_unestimated_task_still_sends_the_required_hours(self):
        """`CreateTaskSerializer` declares `estimated_hours` as required.

        Omitting it earns a 400 on a task the user considers complete, so the
        backend's own zero is sent instead.
        """
        from src.tasks.domain.entities import Task

        payload = task_to_payload(Task.create(name="Bare"))

        assert set(payload) == {"name", "importance", "estimated_hours"}
        assert payload["estimated_hours"] == "0"
        assert "goal_id" not in payload
        assert "description" not in payload

    def test_the_priority_select_reaches_the_importance_field(self):
        """The one field whose name differs from the domain's."""
        from src.tasks.domain.entities import Task

        payload = task_to_payload(Task.create(name="Urgent", priority=Priority.HIGH))

        assert payload["importance"] == "high"

    def test_never_sends_a_server_computed_field(self):
        """`status` and the LLM flags are derived; sending them asserts a
        verdict the client did not compute."""
        from src.tasks.domain.entities import Task

        payload = task_to_payload(Task.create(name="Bare"))

        assert "status" not in payload
        assert "is_overwhelmed" not in payload
        assert "is_checked_by_llm" not in payload


class TestUpdateTaskPayload:
    """`UpdateTaskSerializer` semantics, in reverse.

    Every assertion here is against a field the backend's serializer actually
    declares. The shape that matters is the *absence*: PATCH is applied with
    ``partial=True``, so an absent key means "leave this alone" and only a
    present one changes the column. A payload built from the whole entity --
    which is what `task_to_payload` has to do, because create requires every
    field -- would clear everything the caller did not mention.
    """

    def test_sends_only_the_fields_that_were_set(self):
        from src.tasks.domain.value_objects import TaskEdits

        payload = task_edits_to_payload(TaskEdits(name="Renamed"))

        assert payload == {"name": "Renamed"}

    def test_never_sends_the_goal_or_the_status(self):
        """Neither is on `UpdateTaskSerializer`. The goal moves through
        `POST /goals/<id>/link-task/` and the status through the dedicated
        mark-doing / mark-done actions; sending either would be a field the
        backend discards, so a form offering it would report a change it never
        made."""
        from src.tasks.domain.value_objects import TaskEdits

        payload = task_edits_to_payload(
            TaskEdits(name="Renamed", priority=Priority.HIGH)
        )

        assert "goal_id" not in payload
        assert "status" not in payload

    def test_the_priority_reaches_the_importance_field(self):
        """The same rename as create: one wire field, called "importance" by the
        backend, carrying a low/medium/high choice."""
        from src.tasks.domain.value_objects import TaskEdits

        payload = task_edits_to_payload(TaskEdits(priority=Priority.HIGH))

        assert payload["importance"] == "high"

    def test_a_cleared_description_is_an_empty_string_not_null(self):
        """`UpdateTaskSerializer` declares `description` with `allow_blank=True`,
        and the model column is non-null with a blank default. Sending `null`
        would be rejected."""
        from src.tasks.domain.value_objects import TaskEdits

        payload = task_edits_to_payload(TaskEdits(description=None))

        assert payload["description"] == ""

    def test_a_cleared_due_date_is_an_explicit_null(self):
        """`DateField(allow_null=True)` is explicit here, so this is the one field
        where clearing is spelled `null` and genuinely clears the column."""
        from src.tasks.domain.value_objects import TaskEdits

        payload = task_edits_to_payload(TaskEdits(due_date=None))

        assert "due_date" in payload
        assert payload["due_date"] is None

    def test_a_set_due_date_is_iso_formatted(self):
        from src.tasks.domain.value_objects import TaskEdits

        payload = task_edits_to_payload(TaskEdits(due_date=date(2026, 11, 1)))

        assert payload["due_date"] == "2026-11-01"

    def test_a_cleared_estimate_sends_the_models_zero_not_null(self):
        """`DecimalField` is declared without `allow_null`, so `null` is a 400.
        `0` is the model's own representation of "no effort given", and is what
        `Duration.zero()` means."""
        from src.tasks.domain.value_objects import TaskEdits

        payload = task_edits_to_payload(TaskEdits(estimated_hours=None))

        assert payload["estimated_hours"] == "0"

    def test_a_set_estimate_is_a_decimal_string(self):
        """DRF's `COERCE_DECIMAL_TO_STRING` means hours arrive as strings; they
        leave as strings for the same reason."""
        from src.tasks.domain.value_objects import Duration, TaskEdits

        payload = task_edits_to_payload(TaskEdits(estimated_hours=Duration(2.5)))

        assert payload["estimated_hours"] == "2.5"

    def test_a_full_form_sends_exactly_the_five_updatable_fields(self):
        """What the edit modal posts on every submit."""
        from src.tasks.domain.value_objects import Duration, TaskEdits

        payload = task_edits_to_payload(
            TaskEdits(
                name="Renamed",
                description="**bold**",
                due_date=date(2026, 11, 1),
                priority=Priority.LOW,
                estimated_hours=Duration(1),
            )
        )

        assert set(payload) == {
            "name",
            "description",
            "due_date",
            "importance",
            "estimated_hours",
        }
