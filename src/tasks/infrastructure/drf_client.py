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
* A ``404`` on update or delete becomes ``TaskNotFoundError``, including on
  delete. Deleting something that is already gone is not a success: the user was
  looking at a row that had stopped existing, and telling them "deleted" would
  hide that their view was stale.
"""

from __future__ import annotations

from datetime import date
from logging import getLogger

from pydantic import ValidationError as PydanticValidationError

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
from src.tasks.domain.value_objects import LlmHealth, TaskEdits, TaskId, TaskStatus
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

logger = getLogger()


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


def _validated(schema: type, body: object, entity: str):
    """Validate ``body`` against a hand-written schema, or explain its failure.

    The boundary's contract is that no foreign exception escapes it -- the same
    one that keeps ``httpx.TransportError`` from reaching a use case. A raw
    ``pydantic.ValidationError`` is the other way that contract breaks, and it
    broke in production before this existed: the health schema expected an
    `available` boolean the API never sent, the navbar polled it on every page
    load, and every page in the app returned a 500 because a pydantic error is
    not a `DomainException` and so was not softened by the use case.

    The message keeps the schema name, the missing field, and the payload, because
    the fix is always "compare this schema against the serializer" and those
    three are what that comparison needs.
    """
    try:
        return schema.model_validate(body)
    except PydanticValidationError as exc:
        raise InvalidTasksInputError(
            f"The {entity} response did not match the expected contract "
            f"({schema.__name__}): {exc}. The backend's serializer and "
            f"src/tasks/infrastructure/schemas.py have diverged."
        ) from exc


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
        """List the open tasks -- everything not yet ``done``.

        Filtered here rather than by the endpoint, and the reason is that the
        backend's filter takes exactly one status:
        `TaskFilterQuerySerializer.status` is a single `ChoiceField` over
        ``todo`` / ``doing`` / ``done``, so there is no ``?status=open`` to ask
        for. `ListUserTasks` fetches the owner's rows and applies whatever
        filter it was given -- and given none, it applies none, returning
        finished tasks too.

        That was harmless while nothing else on the page showed a finished task.
        It is not any more: with a completed-tasks section on the same page, an
        unfiltered backlog lists every finished task twice, once as work and
        once as history. Dropping `done` here is what makes the two sections
        partition the task list, which is the only reading of "backlog" and
        "completed" that adds up.

        Only `done` is excluded. `todo` and `doing` are both open work, and the
        backlog renders both with a status badge.
        """
        body = await self.api_client.list_tasks(access_token)
        return [
            task
            for task in (
                map_task(_validated(DRFTaskResponse, item, "Task"))
                for item in _as_list(body, "Task")
            )
            if task.status is not TaskStatus.DONE
        ]

    async def get_task(self, access_token: str, task_id: TaskId) -> Task:
        try:
            body = await self.api_client.get_task(access_token, str(task_id))
        except NotFoundError as exc:
            raise TaskNotFoundError("Task not found or not accessible.") from exc
        return map_task(_validated(DRFTaskResponse, _as_mapping(body, "Task"), "Task"))

    async def create_task(self, access_token: str, task: Task) -> Task:
        body = await self.api_client.create_task(access_token, task_to_payload(task))
        # Logged, not printed: this is the one request whose payload is worth
        # having in the logs when a create misbehaves, and stdout is not a log.
        logger.info("payload for create task - %s", body)
        return map_task(_validated(DRFTaskResponse, _as_mapping(body, "Task"), "Task"))

    async def update_task(
        self, access_token: str, task_id: TaskId, edits: TaskEdits
    ) -> Task:
        """PATCH the task and map the task the backend sends back.

        The response is the whole `TaskSerializer`, not an ack, and that is
        deliberate: `is_overwhelmed` is recomputed server-side against the task's
        *new* estimate, so the returned entity is the first authoritative view
        of the result. Echoing the pre-update entity back to the template would
        render a badge that a reload would contradict.
        """
        try:
            body = await self.api_client.update_task(
                access_token, str(task_id), task_edits_to_payload(edits)
            )
        except NotFoundError as exc:
            raise TaskNotFoundError("Task not found or not accessible.") from exc
        return map_task(_validated(DRFTaskResponse, _as_mapping(body, "Task"), "Task"))

    async def delete_task(self, access_token: str, task_id: TaskId) -> None:
        """DELETE the task, discarding the empty body.

        A 404 becomes `TaskNotFoundError` rather than being treated as success.
        The row the user confirmed was on screen, and "it was already gone" is a
        different situation from "I deleted it" -- the view words them
        differently so the user learns which happened.
        """
        try:
            await self.api_client.delete_task(access_token, str(task_id))
        except NotFoundError as exc:
            raise TaskNotFoundError("Task not found or not accessible.") from exc

    async def get_completed_tasks(self, access_token: str) -> list[Task]:
        """List the caller's `done` tasks.

        Filtered server-side with ``?status=done`` rather than by fetching
        everything and dropping `status == done` here. Two reasons: the backlog
        endpoint and this one would otherwise be the same request under two
        names, and doing the selection in the BFF would mean fetching every open
        task on a page whose only subject is the finished ones.

        **No date filtering is applied, here or afterwards.** The backend has no
        completion timestamp to filter on -- `TaskModel` has no `completed_at`
        and `TaskSerializer` sends no completion time, so `created_at` and
        `updated_at` are both "last touched" and neither identifies when a task
        was finished. The seven-day window the product asked for is therefore not
        implementable against this API; approximating it on `updated_at` would
        put tasks edited today at the top of a "recently completed" list
        regardless of when they were done. See `GetCompletedTasksUseCase` for
        the full argument.

        `done` is the wire value, not `completed`: `TASK_STATUS_CHOICES` is
        ``todo`` / ``doing`` / ``done``.
        """
        body = await self.api_client.list_tasks_by_status(
            access_token, TaskStatus.DONE.value
        )
        return [
            map_task(_validated(DRFTaskResponse, item, "Task"))
            for item in _as_list(body, "Task")
        ]

    # ------------------------------------------------------------------ #
    # Goals
    # ------------------------------------------------------------------ #
    async def get_goals(self, access_token: str) -> list[Goal]:
        body = await self.api_client.list_goals(access_token)
        return [
            map_goal(_validated(DRFGoalResponse, item, "Goal"))
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
            map_daily_plan(_validated(DRFDailyPlanResponse, item, "DailyPlan"))
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

        A ``503`` is *not* raised, because it carries the same verdict the body
        would: ``{"status": "unavailable", "model": null, "error": "..."}`` is the
        backend answering the question, not failing to answer it. It is run
        through the same mapper as the 200 so both statuses read their meaning
        from one place -- ``status``, the field the backend actually sends --
        rather than from two code paths that could disagree.

        Everything else -- a 401, a 500, a transport failure -- raises, because
        none of them carry a verdict about the provider. The use case turns those
        into the unknown state, which is the only honest reading of them.
        """
        try:
            body = await self.api_client.get_llm_health(access_token)
        except DRFAPIClientError as exc:
            if exc.status_code != 503:
                raise LlmUnavailableError(str(exc), status=exc.status_code) from exc
            # The shared client keeps the error body in `detail` rather than
            # returning it, so it is re-read from there.
            body = exc.detail
        return map_llm_health(
            _validated(
                DRFLlmHealthResponse, _as_mapping(body, "LlmHealth"), "LlmHealth"
            )
        )
