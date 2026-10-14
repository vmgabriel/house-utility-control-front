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
    DailyPlanNotFoundError,
    GoalNotFoundError,
    InvalidTasksInputError,
    LlmUnavailableError,
    TaskNotFoundError,
    TaskNotPlannableError,
)
from src.tasks.domain.value_objects import (
    DailyPlanId,
    GoalId,
    LlmHealth,
    TaskEdits,
    TaskId,
    TaskStatus,
)
from src.tasks.infrastructure.mappers import (
    map_daily_plan,
    map_goal,
    map_llm_health,
    map_task,
    task_edits_to_payload,
    task_to_payload,
)
from src.tasks.infrastructure.schemas import (
    DRFDailyPlanQueuedResponse,
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


def _backend_detail(exc: DRFAPIClientError, fallback: str) -> str:
    """The backend's own error message, or `fallback` if it did not send one.

    DRF's exception handler puts the message under `detail`, which the shared
    client preserves on the raised error rather than flattening into its own text.
    Reading it back is what lets a 409 say *why* the task could not be scheduled
    instead of repeating the endpoint's failure.

    The ``fallback`` is not defensive padding: a 409 raised by
    `AddTaskToDailyPlan` carries a real message today, but a proxy in front of
    DRF can replace the body with an HTML error page, and a task that cannot be
    scheduled must still produce a flash rather than `KeyError`.
    """
    body = exc.detail
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail
        # DRF field errors arrive as `{"field": ["..."]}` with no `detail` key.
        # Joined rather than indexed by field name, so an unexpected shape still
        # yields the user's text instead of a KeyError.
        messages = [
            message
            for value in body.values()
            for message in ([value] if isinstance(value, str) else value or [])
        ]
        if messages:
            return " ".join(str(message) for message in messages)
    if isinstance(body, str) and body.strip():
        return body
    return fallback


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

        **No date filtering is applied, here or afterwards.** Not because the backend
        cannot support one -- `completed_at` is on every task payload and could be
        filtered on -- but because it filters on nothing: the read is
        ``?status=done`` and the list is rendered in the backend's order, which is
        ``-created_at, -id``. A "last 7 days" window is therefore not something
        this method can start honouring by adding a parameter; it would mean
        sorting and slicing a list the backend already ordered, and presenting the
        result as the backend's own ordering when it is not. `completed_at` is
        used where a *measured* figure needs it -- goal progress -- rather than to
        quietly impose an order this page has never had.

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

    async def get_goal(self, access_token: str, goal_id: GoalId) -> Goal:
        try:
            body = await self.api_client.get_goal(access_token, str(goal_id))
        except NotFoundError as exc:
            raise GoalNotFoundError("Goal not found or not accessible.") from exc
        return map_goal(_validated(DRFGoalResponse, _as_mapping(body, "Goal"), "Goal"))

    async def get_tasks_by_goal(self, access_token: str, goal_id: GoalId) -> list[Task]:
        """Every task under `goal_id`, `done` ones included.

        Deliberately *not* filtered. :meth:`get_backlog` drops `done` because it
        renders the working set alongside a separate completed list; this renders
        a goal, whose finished tasks are part of the page and are the numerator of
        its progress bar. Applying the same rule here would make the goal detail
        page show only open work and report every goal as 0% complete.
        """
        body = await self.api_client.list_tasks_by_goal(access_token, str(goal_id))
        return [
            map_task(_validated(DRFTaskResponse, item, "Task"))
            for item in _as_list(body, "Task")
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

    async def create_daily_plan(self, access_token: str, plan_date: date) -> DailyPlan:
        """Open the plan for `plan_date`, or return the one already open.

        The backend's `GetOrCreateDailyPlan` answers 201 either way and does not
        say which happened, so neither does this. Nothing here needs to know:
        the caller wanted a plan for the date and now has one.
        """
        body = await self.api_client.create_daily_plan(
            access_token, plan_date.isoformat()
        )
        return map_daily_plan(
            _validated(
                DRFDailyPlanResponse, _as_mapping(body, "DailyPlan"), "DailyPlan"
            )
        )

    async def generate_daily_plan(self, access_token: str, plan_date: date) -> None:
        """Queue the assistant's plan for `plan_date`.

        The body is validated against :class:`DRFDailyPlanQueuedResponse` even
        though nothing is mapped from it. It is read to prove the endpoint
        answered the shape this adapter expects -- a ``202`` carrying a plan, or a
        route that has been renamed to something else, both of which would
        otherwise pass silently and leave the user told their day was planned when
        it was not. The date it echoes is the one a test asserts on.

        A ``503`` becomes `LlmUnavailableError` for the same reason the decompose
        and evaluate triggers map one: an unreachable provider must not take the
        page down, and the flash saying so is more useful than a 503 page.
        """
        try:
            body = await self.api_client.generate_daily_plan(
                access_token, plan_date.isoformat()
            )
        except DRFAPIClientError as exc:
            raise _as_llm_unavailable(exc) from exc
        _validated(
            DRFDailyPlanQueuedResponse, _as_mapping(body, "DailyPlanQueued"), "Queue"
        )

    async def add_task_to_daily_plan(
        self, access_token: str, plan_id: DailyPlanId, task_id: TaskId
    ) -> DailyPlan:
        """Schedule one task onto a day and return the reloaded plan.

        The 409 translation is the interesting part. The backend answers it for
        three real outcomes that all deserve to be shown to the user -- already
        scheduled, day full, already done -- and its `detail` says which. That
        detail is carried into `TaskNotPlannableError` verbatim rather than being
        swapped for a message of this BFF's choosing, because the three reasons
        need different advice from the user and the backend is the only thing that
        knows which one applies. Translating them into three exceptions here would
        mean pattern-matching free text, which breaks the first time the wording
        changes.

        A 404 is *not* softened the way `get_task`'s is: the missing row here is
        the plan or the task, and the user clicked a button on a page that named
        one of them, so "that task no longer exists" or "today's plan no longer
        exists" is the honest answer either way.
        """
        try:
            body = await self.api_client.add_task_to_daily_plan(
                access_token, str(plan_id), str(task_id)
            )
        except DRFAPIClientError as exc:
            if exc.status_code == 409:
                raise TaskNotPlannableError(
                    _backend_detail(exc, "The task could not be scheduled.")
                ) from exc
            if exc.status_code == 404:
                raise DailyPlanNotFoundError(
                    "Today's plan, or that task, no longer exists."
                ) from exc
            raise
        return map_daily_plan(
            _validated(
                DRFDailyPlanResponse, _as_mapping(body, "DailyPlan"), "DailyPlan"
            )
        )

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
