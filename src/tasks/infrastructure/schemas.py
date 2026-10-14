"""Hand-written pydantic schemas for the DRF tasks API.

**These are authoritative, not the drf-spectacular OpenAPI schema** (AGENTS.md,
rule 7). The schema spec for this backend declares several fields required and
non-nullable that the live API returns as ``null``; validating against it raises
inside a mapper and surfaces as a 500 on a page the user just created data on.

The divergence this context inherits:

* ``goal_id`` is declared non-nullable by the spec. The model column is a plain
  nullable ``ForeignKey`` and most backlog items are not filed under a goal, so
  the live API answers ``null``. Hence ``str | None``.
* ``description``, ``due_date``, and ``estimated_hours`` are nullable columns,
  hence optional here.
* ``is_overwhelmed`` is a computed serializer field that is **absent** from some
  endpoints and present in others. Hence ``bool | None`` rather than a default:
  ``None`` has to survive to the entity, because it is the one value that means
  "the backend did not say" and lets `Task.is_overwhelmed` fall back to the
  local threshold comparison. Defaulting it to ``False`` would silently
  downgrade "unknown" into "fine", which is the exact badge the user relies on.

**Two fields this context needs and the API does not have.** Recorded here
rather than invented, because a schema that declares a field the serializer does
not send is exactly the failure mode rule 7 exists to prevent -- it would turn a
missing backend feature into a 500 rather than a visible gap:

* ``completed_at``. ``TaskModel`` has no such column and ``TaskSerializer``
  exposes no completion timestamp. There is consequently **no seven-day window
  on completed tasks**: ``created_at`` and ``updated_at`` are both "when the row
  last changed", not "when it was finished", and every status transition and
  every content edit moves ``updated_at``. See ``GetCompletedTasksUseCase``.
* ``goal_id`` is present but **read-only**: it is absent from
  ``UpdateTaskSerializer``, and filing a task under a goal goes through
  ``POST /goals/{id}/link-task/`` instead.

``extra="allow"`` everywhere: the backend adds fields without a BFF release, and
an unknown field must not fail the page.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DRFTaskResponse(BaseModel):
    """One task, as returned by the task list, detail, and create endpoints."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    #: Null for a task that is not filed under a goal -- see the module docstring.
    goal_id: str | None = None
    #: `TaskSerializer` declares ``description`` with ``allow_blank=True``, so
    #: the API sends `""` rather than omitting it.
    description: str = ""
    due_date: str | None = None
    #: **A `low`/`medium`/`high` choice, not a number.** `TaskSerializer`
    #: declares `importance = ChoiceField(choices=PRIORITY_CHOICES)` and the
    #: backend's own `Task.importance` is typed `Priority`.
    #:
    #: There is no separate wire field called `priority` -- "importance" *is* the
    #: priority level, and an earlier version of this schema declared it as an
    #: integer 1-5 scale that the API has never sent. Every task therefore failed
    #: to validate with `Input should be a valid integer, unable to parse string
    #: as an integer`.
    importance: str = "medium"
    status: str = "todo"
    #: DRF serialises decimals as strings by default, so this is ``"2.50"``.
    estimated_hours: str | None = None
    is_checked_by_llm: bool = False
    llm_evaluation_failed: bool = False
    #: The backend's own verdict. ``None`` when the field was not in the payload.
    is_overwhelmed: bool | None = None


class DRFGoalResponse(BaseModel):
    """One goal.

    ``task_count`` is present on ``GoalSerializer``, so the goals page reads it
    directly rather than joining the backlog to compute it. Kept as ``int | None``
    because it is absent from any goal payload produced by a path that does not go
    through that serializer.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    description: str | None = None
    due_date: str | None = None
    status: str = "active"
    task_count: int | None = None


class DRFDailyPlanResponse(BaseModel):
    """One day's plan.

    ``tasks`` is the **nested** list of full task objects, confirmed against
    ``DailyPlanSerializer`` in the backend repository -- it declares
    ``tasks = TaskSerializer(many=True, read_only=True)``. An earlier version of
    this schema expected a flat ``task_ids`` array, which the API never sends;
    every day therefore mapped to an empty task list and the daily page rendered
    an empty plan with no error anywhere.

    Typed as ``list[DRFTaskResponse]``, so a nested task is validated by the same
    schema a standalone one is. Validating only the ids and pulling the rest from
    the backlog would let a malformed nested task pass silently here and fail
    later, in a different request, with a less obvious message.

    ``model_rebuild()`` at the bottom resolves the forward reference; the class
    is defined after this one, and pydantic needs it resolved to build.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    date: str
    tasks: list[DRFTaskResponse] = []
    #: The backend's own sum of the scheduled tasks; mapped, never recomputed.
    total_hours: str | None = None
    #: Whether the backend considers the day full. Display only; the BFF does
    #: not act on it, so it is parsed for completeness rather than used.
    is_full: bool = False
    generated_by_llm: bool = False


#: Resolves `DRFDailyPlanResponse.tasks -> list[DRFTaskResponse]`, which is a
#: forward reference because the daily-plan class is declared first.
DRFDailyPlanResponse.model_rebuild()


class DRFLlmHealthResponse(BaseModel):
    """The LLM health probe's body.

    Confirmed against ``LlmHealthSerializer`` and ``TaskViewSet.llm_health`` in
    the backend repository. Note that this one schema covers **both** statuses,
    because the 503 carries this shape too rather than DRF's usual
    ``{"detail": ...}``:

    * reachable      -> ``200 {"status": "ok", "model": "qwen2.5:3b"}``
    * unreachable    -> ``503 {"status": "unavailable", "model": null, "error": ...}``

    There is **no ``available`` boolean**. An earlier version of this schema
    invented one, and the first real request against a live backend failed with
    a pydantic ``Field required`` on ``available`` -- a 500 in the navbar of
    every page, because the navbar polls this endpoint on every page load. The
    enum below is the actual wire contract; ``status`` is typed as ``str``
    rather than an enum on purpose, so an unrecognised value is *reported* by
    the mapper instead of being rejected here as a schema error.

    ``error`` is free text from the provider and is treated as untrusted -- it
    reaches a tooltip, never markup.
    """

    model_config = ConfigDict(extra="allow")

    status: str
    model: str | None = None
    error: str | None = None


__all__ = [
    "DRFDailyPlanResponse",
    "DRFGoalResponse",
    "DRFLlmHealthResponse",
    "DRFTaskResponse",
]
