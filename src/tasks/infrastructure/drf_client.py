"""httpx adapter implementing the tasks `TasksApiClient` port.

The adapter does three things and nothing else:

1. Delegates the round trip to the shared :class:`DRFAPIClient`, so the base
   URL, JSON headers, Bearer token, transport-error handling, and the
   ``ServiceUnavailableError`` contract are identical to every other context.
   No second ``httpx.AsyncClient`` is created here.
2. Validates the parsed body against the hand-written pydantic schemas in
   :mod:`src.tasks.infrastructure.schemas` and hands the validated model to the
   mappers. Validation lives here rather than in the mappers so the mappers
   stay free of I/O *and* of the "what shape did DRF actually send" question.
3. Translates transport-level statuses into domain errors, so a view can catch
   ``TaskNotFoundError`` and render a flash without knowing DRF speaks in 404s.

Status handling differs by intent, and the difference matters:

* A ``404`` on the two LLM triggers is a task that no longer exists -- it was
  deleted while its row was on screen -- and becomes ``TaskNotFoundError``.
* A ``503`` from either trigger or from the health probe means the LLM provider
  is unreachable, which is a *different* problem with a *different* fix, so it
  becomes ``LlmUnavailableError`` and never a 503 page. The decompose buttons
  and the navbar dot are optional features; taking the page down for them would
  be a self-inflicted outage.
* A ``400`` is not converted into a domain error. The use cases already rejected
  the equivalent input locally, so one reaching DRF means a rule exists only
  server-side and its field-level detail is worth surfacing verbatim.
"""

from __future__ import annotations

from datetime import date

from src.shared.http.drf_client import (
    DRFAPIClient,
    DRFAPIClientError,
    NotFoundError,
)
from src.tasks.domain.entities import DailyPlan, Goal, Task
from src.tasks.domain.exceptions import (
    InvalidTasksInputError,
    LlmUnavailableError,
    TaskNotFoundError,
)
from src.tasks.domain.value_objects import LlmHealth, TaskId
from src.tasks.infrastructure.mappers import (
    map_daily_plan,
    map_goal,
    map_llm_health,
    map_task,
    task_to_payload,
)
from src.tasks.infrastructure.schemas import (
    DRFDailyPlanResponse,
    DRFGoalResponse,
    DRFLlmHealthResponse,
    DRFTaskResponse,
)


def _as_list(body: object, entity: str) -> list:
    """Assert a list endpoint really returned a list.

    A DRF list view that starts paginating would answer ``{"count": ..,
    "results": [...]}`` instead, and validating that against a schema would
    fail with a message about a missing ``id`` on the key ``"results"`` --
    accurate, and useless. The shape is checked first so the failure names the
    real cause.
    """
    if body is None:
        return []
    if isinstance(body, list):
        return body
    raise InvalidTasksInputError(
        f"{entity} endpoint did not return a list, got {type(body).__name__}."
    )


def _as_mapping(body: object, entity: str) -> dict:
    """Assert a detail endpoint really returned an object."""
    if isinstance(body, dict):
        return body
    raise InvalidTasksInputError(
        f"{entity} endpoint did not return an object, got {type(body).__name__}."
    )


def _as_llm_unavailable(exc: DRFAPIClientError) -> LlmUnavailableError:
    """Re-wrap a shared-client failure as an LLM-specific one, if it is one.

    Only ``503`` translates. Every other status -- a 400 for an already-decomposed
    task, a 500 from a provider bug -- keeps its original class, because this
    error type exists to say "the LLM is unreachable", and widening it to mean
    "anything went wrong on these two endpoints" would put that wording on
    messages that have nothing to do with reachability.
    """
    status = getattr(exc, "status_code", None)
    if status == 503:
        return LlmUnavailableError(str(exc), status=status)
    return exc  # type: ignore[return-value]


def _detail_of(detail: object) -> str | None:
    """Pull the human-readable reason out of a DRF error body.

    DRF answers an error with ``{"detail": "..."}`` or, for a serializer
    failure, a dict of field errors. Only the first shape carries a sentence
    worth showing, and the second would render as a Python dict in a tooltip.
    Anything else is dropped rather than stringified -- the tooltip is a
    nicety, and showing `{'key rejected': ['Invalid API key']}` to a user is
    worse than showing nothing.
    """
    if isinstance(detail, dict):
        message = detail.get("detail")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return None


class DrfTasksClient:
    """Tasks adapter over the shared DRF API client.

    Every method takes ``access_token`` first and positionally: the
    ``AutoRefreshingRepository`` proxy that wraps this object identifies the
    token that way, and substitutes a refreshed one on ``args[0]`` after a 401.
    """

    def __init__(self, api_client: DRFAPIClient) -> None:
        self.api_client = api_client

    # ------------------------------------------------------------------ #
    # Tasks
    # ------------------------------------------------------------------ #
    async def get_backlog(self, access_token: str) -> list[Task]:
        body = await self.api_client.list_tasks(access_token)
        return [
            map_task(DRFTaskResponse.model_validate(item))
            for item in _as_list(body, "Task")
        ]

    async def get_task(self, access_token: str, task_id: TaskId) -> Task:
        try:
            body = await self.api_client.get_task(access_token, str(task_id))
        except NotFoundError as exc:
            raise TaskNotFoundError("Task not found or not accessible.") from exc
        return map_task(DRFTaskResponse.model_validate(_as_mapping(body, "Task")))

    async def create_task(self, access_token: str, task: Task) -> Task:
        body = await self.api_client.create_task(access_token, task_to_payload(task))
        return map_task(DRFTaskResponse.model_validate(_as_mapping(body, "Task")))

    # ------------------------------------------------------------------ #
    # Goals
    # ------------------------------------------------------------------ #
    async def get_goals(self, access_token: str) -> list[Goal]:
        body = await self.api_client.list_goals(access_token)
        return [
            map_goal(DRFGoalResponse.model_validate(item))
            for item in _as_list(body, "Goal")
        ]

    # ------------------------------------------------------------------ #
    # Daily plans
    # ------------------------------------------------------------------ #
    async def get_daily_plan(
        self, access_token: str, plan_date: date
    ) -> DailyPlan | None:
        """Return the plan for `plan_date`, or `None` when the day is unplanned.

        The endpoint returns every plan, so the whole list is fetched and the
        requested day picked out here. That also gives the daily view's date
        stepper its neighbouring days from the same response.

        No plan is not an error. Most days are unplanned until the user asks the
        LLM for one, and "nothing planned" is a page to render.
        """
        body = await self.api_client.list_daily_plans(access_token)
        plans = [
            map_daily_plan(DRFDailyPlanResponse.model_validate(item))
            for item in _as_list(body, "DailyPlan")
        ]
        for plan in plans:
            if plan.plan_date == plan_date:
                return plan
        return None

    # ------------------------------------------------------------------ #
    # LLM-backed actions
    # ------------------------------------------------------------------ #
    async def trigger_evaluate(self, access_token: str, task_id: TaskId) -> None:
        """Queue the LLM evaluation, discarding the (empty) response body.

        The backend answers 202 with nothing useful in it. The verdict arrives on
        a later request as the task's own flags, so there is nothing to map and
        nothing to return.
        """
        try:
            await self.api_client.evaluate_task(access_token, str(task_id))
        except NotFoundError as exc:
            raise TaskNotFoundError("Task not found or not accessible.") from exc
        except DRFAPIClientError as exc:
            raise _as_llm_unavailable(exc) from exc

    async def trigger_decompose(self, access_token: str, task_id: TaskId) -> None:
        """Queue the LLM decomposition, discarding the (empty) response body."""
        try:
            await self.api_client.decompose_task(access_token, str(task_id))
        except NotFoundError as exc:
            raise TaskNotFoundError("Task not found or not accessible.") from exc
        except DRFAPIClientError as exc:
            raise _as_llm_unavailable(exc) from exc

    async def get_llm_health(self, access_token: str) -> LlmHealth:
        """Report provider reachability, or raise for the use case to soften.

        A ``503`` is *not* raised. Unlike every other status here, it is the
        backend answering the question: "the provider is unreachable" is the
        answer, not a failure to obtain it. Turning it into an exception would
        hand the use case a ``LlmUnavailableError`` to soften into
        ``LlmHealth.indeterminate`` and render an amber dot -- telling the user
        "we do not know" when the backend just told us, in as many words, that
        the AI is down. So it is mapped to the red state directly.

        Everything else -- a 401, a 500, a transport failure -- raises, because
        none of them carry a verdict about the provider. The use case turns those
        into the unknown state, which is the only honest reading of them.
        """
        try:
            body = await self.api_client.get_llm_health(access_token)
        except DRFAPIClientError as exc:
            if exc.status_code == 503:
                return LlmHealth(available=False, detail=_detail_of(exc.detail))
            raise LlmUnavailableError(str(exc), status=exc.status_code) from exc
        return map_llm_health(
            DRFLlmHealthResponse.model_validate(_as_mapping(body, "LlmHealth"))
        )
