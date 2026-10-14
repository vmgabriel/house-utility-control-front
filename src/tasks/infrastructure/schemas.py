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
    description: str | None = None
    #: Null for a task that is not filed under a goal -- see the module docstring.
    goal_id: str | None = None
    due_date: str | None = None
    importance: int = 1
    priority: str = "medium"
    status: str = "todo"
    #: DRF serialises decimals as strings by default, so this is ``"2.50"``.
    estimated_hours: str | None = None
    is_checked_by_llm: bool = False
    llm_evaluation_failed: bool = False
    #: The backend's own verdict. ``None`` when the field was not in the payload.
    is_overwhelmed: bool | None = None


class DRFGoalResponse(BaseModel):
    """One goal."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    description: str | None = None
    due_date: str | None = None
    status: str = "active"


class DRFDailyPlanResponse(BaseModel):
    """One day's plan.

    ``task_ids`` rather than nested task objects: the endpoint returns the ids
    the day is built from, and the BFF joins them against the backlog it already
    fetched. If the backend ever starts nesting the tasks, the extra fields are
    kept by ``extra="allow"`` and the join simply becomes redundant rather than
    breaking.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    date: str
    task_ids: list[str] = []
    #: The backend's own sum of the scheduled tasks; mapped, never recomputed.
    total_hours: str | None = None
    generated_by_llm: bool = False


class DRFLlmHealthResponse(BaseModel):
    """The LLM health probe's body.

    ``available`` is required: a health check that cannot answer is an error
    status, not a body, and is translated into `LlmHealth.indeterminate` before
    it ever reaches here. ``detail`` is free text from the provider and is
    treated as untrusted -- it is shown in a tooltip, never as markup.
    """

    model_config = ConfigDict(extra="allow")

    available: bool
    detail: str | None = None


__all__ = [
    "DRFDailyPlanResponse",
    "DRFGoalResponse",
    "DRFLlmHealthResponse",
    "DRFTaskResponse",
]
