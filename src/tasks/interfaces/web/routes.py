"""Tasks Flask blueprint: the backlog, goals, and the daily plan.

Views here are deliberately thin: read a value from the form, hand it to a use
case, and render ViewModels. No business rule lives in this module.

Every interactive element lives inside the single top-level ``x-data`` scope of
its template (AGENTS.md, "Alpine.js Scope Strictness"). A button placed outside
that scope would silently do nothing. The one exception is the navbar's LLM
health dot, which is rendered from `base.html` and owns its own scope -- the same
arrangement as the account dropdown that is already there.

**Two kinds of request reach the same routes.** A form post navigates and the
server redirects back (progressive enhancement: the page works without
JavaScript). The decompose button uses ``fetch`` against the same POST, sends
``X-Requested-With: fetch``, and gets a JSON body instead of a redirect. That
dual contract is deliberate -- see `_wants_json` -- and it is why both paths run
the exact same use case rather than two near-duplicates.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from src.shared.auth.csrf import CSRFTokenManager
from src.shared.auth.jwt_cookie_manager import JWTCookieManager
from src.shared.domain.exceptions import DomainException
from src.shared.http.drf_client import ServiceUnavailableError
from src.tasks.domain.entities import DEFAULT_OVERWHELM_THRESHOLD
from src.tasks.domain.exceptions import (
    DailyPlanNotFoundError,
    GoalNotFoundError,
    LlmUnavailableError,
    TaskNotFoundError,
    TaskNotPlannableError,
    TaskTransitionRejected,
)
from src.tasks.domain.value_objects import (
    Duration,
    GoalId,
    LlmHealth,
    Priority,
    TaskId,
    TaskStatus,
)
from src.tasks.interfaces.viewmodels import (
    KANBAN_COLUMN_LABELS,
    PRIORITY_LABELS,
    BacklogViewModel,
    CompletedTasksViewModel,
    DailyPlanViewModel,
    EmptyDailyPlanViewModel,
    GoalDetailViewModel,
    GoalsViewModel,
    KanbanViewModel,
    LlmHealthViewModel,
)

tasks_bp = Blueprint("tasks", __name__, url_prefix="/tasks")

#: Hours a single day is expected to hold. A product default, configurable per
#: deployment, so it lives in app config rather than in the view.
DEFAULT_MAX_DAILY_HOURS = Decimal("8.0")

#: Header the browser's `fetch` cannot be made to set on a cross-origin request,
#: which is what makes it a usable signal that *this* request came from our own
#: JavaScript rather than from a navigation. Paired with `same-origin`, so a
#: cross-site form cannot forge it.
FETCH_HEADER = "X-Requested-With"
FETCH_HEADER_VALUE = "fetch"


def _access_token() -> str | None:
    """Read the JWT from its HttpOnly cookie."""
    cookie_manager: JWTCookieManager = current_app.cookie_manager
    return cookie_manager.get_access_token(request)


def _require_csrf() -> bool:
    """Validate the token from the form body or the request header.

    The header is accepted because the decompose button posts with `fetch` and
    reads its token from a data attribute rather than a form. Both are covered:
    accepting either alone would leave one of the two callers unprotected, and
    accepting a token from neither would make the routes unprotected.
    """
    csrf_manager: CSRFTokenManager = current_app.csrf_manager
    token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    return csrf_manager.validate_token(token)


def _wants_json() -> bool:
    """Whether this request came from `fetch` and should get JSON back.

    Not a query parameter: a caller who can set the query can also set the
    header, so making it one would not add a check, only a way to get it wrong.
    """
    return request.headers.get(FETCH_HEADER, "").strip().lower() == FETCH_HEADER_VALUE


def _overwhelm_threshold() -> Decimal:
    """Read the configured estimate threshold, falling back to the product default.

    Read through `current_app` so a deployment (or a test) can change it without
    touching the domain. A malformed value falls back rather than raising: the
    threshold decides whether a badge appears, and a bad configuration value
    should not take the whole backlog page down with it.
    """
    raw = current_app.config.get("TASKS_OVERWHELM_THRESHOLD")
    if raw is None or str(raw).strip() == "":
        return DEFAULT_OVERWHELM_THRESHOLD
    try:
        threshold = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, ArithmeticError):
        current_app.logger.warning(
            "Ignoring invalid TASKS_OVERWHELM_THRESHOLD=%r; using the default.",
            raw,
        )
        return DEFAULT_OVERWHELM_THRESHOLD
    if threshold <= 0:
        current_app.logger.warning(
            "Ignoring non-positive TASKS_OVERWHELM_THRESHOLD=%r; using the default.",
            raw,
        )
        return DEFAULT_OVERWHELM_THRESHOLD
    return threshold


def _max_daily_hours() -> Decimal:
    """Read the configured daily hour budget, falling back to the default."""
    raw = current_app.config.get("TASKS_MAX_DAILY_HOURS")
    if raw is None or str(raw).strip() == "":
        return DEFAULT_MAX_DAILY_HOURS
    try:
        hours = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, ArithmeticError):
        return DEFAULT_MAX_DAILY_HOURS
    return hours if hours > 0 else DEFAULT_MAX_DAILY_HOURS


def _parse_date(raw: str | None, field: str) -> date | None:
    """Parse an optional ISO date from a query or form field.

    A malformed date is `None` rather than an error: the daily view's date input
    is a navigation control, and a hand-edited query string should show today
    rather than a stack trace.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        current_app.logger.warning("Ignoring unparseable %s=%r", field, raw)
        return None


def _parse_priority(raw: str | None) -> Priority:
    """Parse the priority form field.

    A `<select>` is client-controlled, so an unexpected member has to become an
    error the view can flash. Constructing the enum directly would raise a bare
    `ValueError`, which the views' `except DomainException` would not catch --
    turning a tampered form into a 500 instead of a message.
    """
    text = (raw or "").strip() or Priority.MEDIUM.value
    try:
        return Priority(text)
    except ValueError as exc:
        allowed = ", ".join(label for label in PRIORITY_LABELS.values())
        raise DomainException(f"Priority must be one of: {allowed}.") from exc


def _parse_hours(raw: str | None) -> Duration | None:
    """Parse the estimated-hours form field, mapping a blank to "no estimate".

    An empty input is `None` rather than an error, and on the edit path `None`
    is a *value*: it means "this task has no estimate" and is sent as an explicit
    ``0.00`` rather than as an absent key, which PATCH's ``partial=True`` would
    read as "leave it alone". See `task_edits_to_payload`.

    A non-numeric value raises. A negative one does not need its own branch --
    `Duration` already refuses it with a message naming the problem, and that
    `InvalidTaskError` is a `DomainException`, so the view flashes it verbatim.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return Duration(Decimal(text))
    except (InvalidOperation, ArithmeticError, ValueError) as exc:
        raise DomainException(
            "Estimated hours must be a positive number of hours."
        ) from exc


def _text_field(raw: str | None) -> str:
    """Read a text form field as `""` when it was left blank.

    `request.form.get` already answers `None` for an absent key, which on the
    edit path means "don't touch this column" -- so a blank input has to be
    turned into an empty string explicitly, or clearing a description would
    silently leave it in place.
    """
    return (raw or "").strip()


def _parse_task_id(raw: str) -> TaskId:
    """Parse a task id from a URL segment."""
    return TaskId(raw)


def _parse_goal_id(raw: str | None) -> GoalId | None:
    """Parse an optional goal id from a form field."""
    text = (raw or "").strip()
    if not text:
        return None
    return GoalId(text)


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
@tasks_bp.route("/")
async def backlog():
    """The backlog: every open task, plus the goals to file new ones under."""
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))

    today = current_app.clock.today()
    threshold = _overwhelm_threshold()

    # Three independent reads. `return_exceptions=True` for the two optional
    # ones: the goal list only populates a filter and a modal's field, and the
    # finished-task list is a secondary section below the fold -- a failure in
    # either must cost the user that panel, not the page. The backlog is not
    # optional, so its failure propagates to the 503 handler.
    tasks, goals, completed = await asyncio.gather(
        current_app.get_backlog_use_case.execute(access_token),
        current_app.get_goals_use_case.execute(access_token),
        current_app.get_completed_tasks_use_case.execute(access_token),
        return_exceptions=True,
    )
    if isinstance(tasks, BaseException):
        raise tasks
    goal_list: list = [] if isinstance(goals, BaseException) else list(goals)
    if isinstance(goals, BaseException):
        current_app.logger.warning(
            "Goals unavailable for the backlog page: %s", type(goals).__name__
        )
    completed_list: list = (
        [] if isinstance(completed, BaseException) else list(completed)
    )
    if isinstance(completed, BaseException):
        current_app.logger.warning(
            "Completed tasks unavailable for the backlog page: %s",
            type(completed).__name__,
        )

    return render_template(
        "tasks/backlog.html",
        backlog=BacklogViewModel.build(
            tasks, today=today, goals=goal_list, threshold=threshold
        ),
        completed=CompletedTasksViewModel.build(
            completed_list, today=today, threshold=threshold
        ),
        # True when the completed section is hidden because the read failed,
        # rather than because the user has finished nothing. The two warrant
        # different words: an empty list is good news, a failed read is a bug
        # the user would otherwise read as "you finished nothing today".
        completed_unavailable=isinstance(completed, BaseException),
        priorities=[
            (member.value, PRIORITY_LABELS[member.name]) for member in Priority
        ],
        today=today.isoformat(),
    )


@tasks_bp.route("/goals/")
async def goals():
    """The goal list, with the count of tasks filed under each.

    ``GoalSerializer`` carries ``task_count``, so the count is read from the
    payload rather than recomputed from the backlog. An earlier version fetched
    the whole backlog and joined on ``goal_id`` here; that was a second
    definition of the same number, free to disagree with the one the backend
    already stored, and it cost an extra full-backlog request on every visit to
    this page.
    """
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))

    today = current_app.clock.today()
    goal_list = await current_app.get_goals_use_case.execute(access_token)

    return render_template(
        "tasks/goals.html",
        goals=GoalsViewModel.build(goal_list, today=today),
        today=today.isoformat(),
    )


@tasks_bp.route("/goals/<uuid:goal_id>/")
async def goal_detail(goal_id):
    """One goal, its tasks, and how far through it the user is.

    Reached by clicking a card on `/tasks/goals/`. The card list carries a
    `task_count` but no hours, and no progress figure exists anywhere in the
    backend, so the bar is computed from the same task list the page renders --
    one read feeding both, which is what keeps the bar from reporting a number
    the rows beneath it contradict.

    A goal that does not exist, or is not the caller's, answers the same 404
    flash as the delete routes: the backend returns 404 for both cases and the
    page must not become a way to probe for other people's goal ids.
    """
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))

    today = current_app.clock.today()
    try:
        goal, tasks, progress = await current_app.get_goal_detail_use_case.execute(
            access_token, GoalId(str(goal_id))
        )
    except GoalNotFoundError:
        flash("That goal no longer exists.", "error")
        return redirect(url_for("tasks.goals"))

    return render_template(
        "tasks/goal_detail.html",
        detail=GoalDetailViewModel.build(
            goal, tasks, progress, today=today, threshold=_overwhelm_threshold()
        ),
        today=today.isoformat(),
    )


@tasks_bp.route("/daily/")
async def daily():
    """The plan for one day, with that day's tasks resolved from the backlog.

    The plan carries task *ids*; the rows need names, estimates, and statuses.
    Those come from the backlog, already fetched in the same request, so the day
    costs two reads rather than one read plus a request per task.
    """
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))

    today = current_app.clock.today()
    requested = _parse_date(request.args.get("date"), "date")
    target = requested if requested is not None else today
    max_hours = _max_daily_hours()

    # Three reads, because the board needs two disjoint sets of tasks and the
    # backlog read is not one of them.
    #
    # `get_backlog` deliberately drops `done` (see the adapter), and the board's
    # Done column is *made of* `done` tasks -- so the backlog alone would render
    # a permanently empty third column while the hours bar claimed hours were
    # spent on it. The completed read is therefore not an extra nicety here, it
    # is what makes the board correct.
    #
    # `return_exceptions=True` on the completed read alone: the backlog and the
    # plan are the page, so their failures propagate to the 503 handler, but a
    # Done column that could not be read is a degraded board rather than a
    # reason to deny the user the two columns that did load.
    plan, backlog_tasks, completed_tasks = await asyncio.gather(
        current_app.get_daily_plan_use_case.execute(access_token, target),
        current_app.get_backlog_use_case.execute(access_token),
        current_app.get_completed_tasks_use_case.execute(access_token),
        return_exceptions=True,
    )
    if isinstance(plan, BaseException):
        raise plan
    if isinstance(backlog_tasks, BaseException):
        raise backlog_tasks
    completed_list: list = (
        [] if isinstance(completed_tasks, BaseException) else list(completed_tasks)
    )
    completed_unavailable = isinstance(completed_tasks, BaseException)
    if completed_unavailable:
        current_app.logger.warning(
            "Completed tasks unavailable for the daily board: %s",
            type(completed_tasks).__name__,
        )

    if plan is None:
        return render_template(
            "tasks/daily.html",
            plan=None,
            empty=EmptyDailyPlanViewModel.for_date(target, max_hours=max_hours),
            # An empty board rather than no board, so the template's `if plan is
            # none` branch is the only thing deciding between the two layouts --
            # a third shape (a board absent from the context) is the kind of
            # thing that renders as a blank page in one branch and a crash in
            # the other.
            board=None,
            missing_task_count=0,
            completed_unavailable=False,
            max_hours=max_hours,
            today=today.isoformat(),
            selected_date=target.isoformat(),
            # The generation button is offered for *today only*. The endpoint
            # accepts any date, but "plan my day" is what a user reaching an
            # empty day means; offering it for a past date would queue a plan
            # nobody will ever read, and the empty state for a past day is more
            # usefully explained by the stepper above it.
            can_generate=target == today,
        )

    # Ordered by the plan's own `task_ids`, not by the backlog's ordering: the
    # sequence the LLM produced is the sequence the day was meant to be worked
    # in, and re-sorting it by the backlog's priority would discard that.
    #
    # Resolved against the backlog *and* the completed list, because a card's
    # column is its status and the two reads between them cover all three. The
    # board then regroups them, which is the whole point of the page.
    by_id = {
        str(task.id): task
        for task in [*backlog_tasks, *completed_list]
        if task.id is not None
    }
    ordered = [
        by_id[str(task_id)] for task_id in plan.task_ids if str(task_id) in by_id
    ]
    # A task on the plan that neither read returns -- deleted since the plan was
    # built -- is dropped rather than rendered as a blank card. Counted so the
    # hours bar and the cards visibly disagree by exactly that many rows rather
    # than silently.
    missing = len(plan.task_ids) - len(ordered)

    return render_template(
        "tasks/daily.html",
        plan=DailyPlanViewModel.from_domain(plan, max_hours=max_hours),
        empty=None,
        board=KanbanViewModel.build(
            ordered, today=today, threshold=_overwhelm_threshold()
        ),
        missing_task_count=missing,
        completed_unavailable=completed_unavailable,
        max_hours=max_hours,
        today=today.isoformat(),
        selected_date=target.isoformat(),
        # A planned day has nothing to generate: the button lives in the empty
        # state, so this is False on every render that has a plan.
        can_generate=False,
    )


# --------------------------------------------------------------------------- #
# Mutations
# --------------------------------------------------------------------------- #
@tasks_bp.route("/", methods=["POST"])
async def create_task():
    """Create a task from the backlog's modal form.

    Reached by a plain form post, so the response is always a redirect: the
    browser lands back on the backlog with a flash. There is no JSON variant,
    because a task is created from a form and nothing in the UI posts it with
    `fetch`.
    """
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))
    if not _require_csrf():
        flash("Invalid CSRF token.", "error")
        return redirect(url_for("tasks.backlog"))

    try:
        task = await current_app.create_task_use_case.execute(
            access_token,
            name=request.form.get("name", ""),
            description=request.form.get("description"),
            goal_id=_parse_goal_id(request.form.get("goal_id")),
            due_date=_parse_date(request.form.get("due_date"), "due_date"),
            # The form posts this as `priority`; it becomes the backend's
            # `importance`. See `task_to_payload`.
            priority=_parse_priority(request.form.get("priority")),
            estimated_hours=request.form.get("estimated_hours"),
        )
    except DomainException as exc:
        flash(str(exc), "error")
    else:
        flash(f"Task created: {task.name}.", "success")
    return redirect(url_for("tasks.backlog"))


@tasks_bp.route("/<uuid:task_id>/edit/", methods=["POST"])
async def update_task(task_id):
    """Apply an edit to a task.

    A plain form post that redirects, like `create_task`: the modal's form is a
    real `<form>` with an action, so the page works without JavaScript and the
    redirect is what puts the user back on a freshly rendered backlog.

    All five fields the backend's `UpdateTaskSerializer` accepts are sent on
    every submit, including empty ones. That is not sloppiness -- it is how a
    cleared due date or a removed description reaches the backend: PATCH is
    applied with ``partial=True``, so an *absent* key means "leave it alone",
    and only an explicit value (or `null`) changes anything. A form that omitted
    blank fields would make those three impossible to clear.

    ``goal_id`` is deliberately absent from this form. The backend's update
    serializer does not accept it -- filing a task under a goal is
    ``POST /goals/{id}/link-task/`` -- so a goal field here would render a
    success message for a change that was silently discarded.
    """
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))
    if not _require_csrf():
        flash("Invalid CSRF token.", "error")
        return redirect(url_for("tasks.backlog"))

    try:
        task = await current_app.update_task_use_case.execute(
            access_token,
            _parse_task_id(str(task_id)),
            # The form always posts all five fields, so every one of them is
            # passed explicitly -- including the blank ones, which are
            # instructions to clear rather than fields to leave alone. See
            # `UpdateTaskUseCase.execute` for why the distinction is not
            # something this call site may infer.
            name=_text_field(request.form.get("name")),
            description=_text_field(request.form.get("description")),
            due_date=_parse_date(request.form.get("due_date"), "due_date"),
            priority=_parse_priority(request.form.get("priority")),
            estimated_hours=_parse_hours(request.form.get("estimated_hours")),
        )
    except TaskNotFoundError:
        flash("That task no longer exists.", "error")
    except DomainException as exc:
        flash(str(exc), "error")
    else:
        flash(f"Task updated: {task.name}.", "success")
    return redirect(_back_target())


@tasks_bp.route("/<uuid:task_id>/delete/", methods=["POST"])
async def delete_task(task_id):
    """Delete a task after the confirmation modal.

    POST-only and CSRF-protected, like every other mutation here, despite being
    a destructive action with an idempotent effect -- a GET link that deletes
    would be triggerable by any prefetch or crawler that followed it.
    """
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))
    if not _require_csrf():
        flash("Invalid CSRF token.", "error")
        return redirect(url_for("tasks.backlog"))

    try:
        await current_app.delete_task_use_case.execute(
            access_token, _parse_task_id(str(task_id))
        )
    except TaskNotFoundError:
        # Worded differently from the success case on purpose: the row the user
        # confirmed was already gone, which means their view was stale, and
        # telling them "Task deleted" would hide that.
        flash("That task no longer exists.", "error")
    except DomainException as exc:
        flash(str(exc), "error")
    else:
        flash("Task deleted.", "success")
    return redirect(_back_target())


def _parse_status(raw: str | None) -> TaskStatus:
    """Parse the target status from the board's move control.

    Raising a `DomainException` rather than letting `TaskStatus(raw)` raise a
    bare `ValueError` is the whole point: the view's `except DomainException`
    catches the first and turns it into a flash, and does not catch the second --
    so a tampered or stale board posting `status=in-progress` would otherwise be
    a 500 instead of a message.
    """
    text = (raw or "").strip()
    try:
        return TaskStatus(text)
    except ValueError as exc:
        allowed = ", ".join(KANBAN_COLUMN_LABELS[member] for member in TaskStatus)
        raise DomainException(
            f"A task can only be moved to one of: {allowed}."
        ) from exc


@tasks_bp.route("/<uuid:task_id>/move/", methods=["POST"])
async def move_task(task_id):
    """Move one card between the board's columns.

    Two callers, one code path, same arrangement as the evaluate/decompose
    triggers: a form post from the card's move control gets a flash and a
    redirect, and the drag-and-drop handler's `fetch` gets JSON so it can revert
    the card without a page load.

    **A failure always answers 4xx, never 200-with-a-flag**, and the drag code
    relies on that: it decides whether to put the card back by whether the
    response was ok. A 200 carrying `{"ok": false}` would leave that decision to
    a field the server and client both have to agree on the meaning of, and the
    failure mode of getting it wrong is a card that looks moved and is not.

    The 409 is passed through as 409 rather than flattened to 400, because the
    backend's refusals are board-specific facts -- a finished task cannot go back
    to Doing, a task that is not finished cannot be reopened -- and the client
    shows the message rather than deciding what it meant.
    """
    access_token = _access_token()
    if not access_token:
        if _wants_json():
            return jsonify({"ok": False, "message": "Not signed in."}), 401
        return redirect(url_for("auth.login"))
    if not _require_csrf():
        if _wants_json():
            return jsonify({"ok": False, "message": "Invalid CSRF token."}), 400
        flash("Invalid CSRF token.", "error")
        return redirect(url_for("tasks.daily"))

    #: Set alongside `message` so the failure is reported with a status that
    #: matches its cause, rather than by inspecting the exception afterwards.
    failure_status = 400
    try:
        task = await current_app.move_task_use_case.execute(
            access_token,
            _parse_task_id(str(task_id)),
            _parse_status(request.form.get("status")),
        )
    except TaskNotFoundError:
        message, failure_status = "That task no longer exists.", 404
    except TaskTransitionRejected as exc:
        # The backend's own wording, verbatim. Two copies of a transition rule
        # exist (this BFF's and the backend's) and a stale board can disagree
        # with either, so the authoritative answer is the one the backend just
        # gave -- not a message invented here that would claim a cause we cannot
        # know.
        message, failure_status = str(exc), 409
    except DomainException as exc:
        message = str(exc)
    else:
        message = f"{task.name} moved to {KANBAN_COLUMN_LABELS[task.status]}."
        if _wants_json():
            return (
                jsonify(
                    {
                        "ok": True,
                        "message": message,
                        # Echoed back so the drag handler can reconcile the card
                        # it optimistically moved with what the backend decided.
                        # On a rejection this is absent, and the handler reverts.
                        "status": task.status.value,
                        "completed_at": (
                            task.completed_at.isoformat()
                            if task.completed_at is not None
                            else None
                        ),
                    }
                ),
                200,
            )
        flash(message, "success")
        return redirect(_back_target())

    if _wants_json():
        return jsonify({"ok": False, "message": message}), failure_status
    flash(message, "error")
    return redirect(_back_target())


@tasks_bp.route("/daily/generate/", methods=["POST"])
async def generate_daily_plan():
    """Queue the assistant's plan for one day.

    Rendered only in the daily page's empty state and only for today (see the
    `can_generate` flag in `daily`), so `date` arrives from the form rather than
    being recomputed here. It is still read from the form and not taken from the
    clock, because the two are not the same thing: a page rendered for today and
    submitted tomorrow must plan the day the user was looking at.

    The flash says "queued". That is not hedging -- the backend answers 202 and
    hands the work to Celery, and the plan appears on a later load if the worker
    succeeds at all. Telling the user their day has been planned would be a claim
    this request cannot support.
    """
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))
    if not _require_csrf():
        flash("Invalid CSRF token.", "error")
        return redirect(url_for("tasks.daily"))

    target = _parse_date(request.form.get("date"), "date")
    if target is None:
        target = current_app.clock.today()

    try:
        await current_app.generate_daily_plan_use_case.execute(access_token, target)
    except LlmUnavailableError:
        # Deliberately not re-raised, for the same reason as the decompose
        # triggers: the AI is optional, and a 503 page would deny the user the
        # daily view they came from.
        flash("The AI service is unavailable right now. Try again later.", "error")
    except DomainException as exc:
        flash(str(exc), "error")
    else:
        flash(
            "Daily plan queued. The plan appears here once the AI finishes.",
            "success",
        )
    return redirect(url_for("tasks.daily", date=target.isoformat()))


@tasks_bp.route("/<uuid:task_id>/add-to-plan/", methods=["POST"])
async def add_task_to_daily_plan(task_id):
    """Schedule one backlog task onto today's plan.

    The button is on every backlog row, so this route is reached with the whole
    backlog in front of the user rather than from a day they are looking at.
    Which is why the day's total is repeated in the flash: the operation
    succeeded, but the consequence the user cannot see -- how much room is left
    -- is exactly the thing a row action should tell them.
    """
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))
    if not _require_csrf():
        flash("Invalid CSRF token.", "error")
        return redirect(url_for("tasks.backlog"))

    try:
        plan = await current_app.add_task_to_daily_plan_use_case.execute(
            access_token, _parse_task_id(str(task_id))
        )
    except TaskNotPlannableError as exc:
        # The backend's own wording, not a generic one: it distinguishes "already
        # on the plan", "the day is full", and "that task is done", and those
        # need three different reactions from the user.
        flash(str(exc), "error")
    except DailyPlanNotFoundError:
        flash("Today's plan could not be opened.", "error")
    except TaskNotFoundError:
        flash("That task no longer exists.", "error")
    except DomainException as exc:
        flash(str(exc), "error")
    else:
        # `total_hours` is the backend's own sum for the reloaded day and is
        # `None` only on a plan that carries no hour figures at all, in which
        # case the sentence says so rather than printing "None".
        planned = f"{plan.total_hours} planned" if plan.total_hours else "scheduled"
        flash(
            f"Added to today's plan ({planned}). See it on the daily plan.",
            "success",
        )
    return redirect(_back_target())


async def _run_llm_action(
    action: str, task_id: TaskId, success_message: str, referrer: str
):
    """Run one LLM trigger and answer in whichever form the caller asked for.

    Shared by the evaluate and decompose routes because they differ only in the
    use case and the wording. Two response shapes, one code path:

    * a form post gets a flash and a redirect back to `referrer`;
    * a `fetch` from the decompose button gets ``{"ok": true}`` so the button
      can stop spinning without a page load.

    The success message is worded as "queued", never as "done". The backend
    accepts the request and returns immediately; saying the decomposition
    finished would be a lie the user discovers when no subtasks have appeared.
    """
    try:
        if action == "evaluate":
            await current_app.trigger_llm_evaluation_use_case.execute(
                _access_token() or "", task_id
            )
        else:
            await current_app.trigger_llm_decomposition_use_case.execute(
                _access_token() or "", task_id
            )
    except TaskNotFoundError:
        message, ok = "That task no longer exists.", False
    except LlmUnavailableError:
        # Not re-raised: the AI feature is optional, and a 503 page would deny
        # the user access to the backlog they came from. The decompose button is
        # also disabled while the health dot is red, so this is the rare race
        # where the provider went down between the two requests.
        message, ok = "The AI service is unavailable right now. Try again later.", False
    except DomainException as exc:
        message, ok = str(exc), False
    else:
        message, ok = success_message, True

    if _wants_json():
        return jsonify({"ok": ok, "message": message}), 200 if ok else 503
    if ok:
        flash(message, "success")
    else:
        flash(message, "error")
    return redirect(referrer)


def _back_target() -> str:
    """Where a mutation returns the user to.

    Prefers the page they came from, but only ever a path from this module's own
    allow-list. The raw `Referer` is never redirected to wholesale: it is an
    attacker-controlled header, and echoing an arbitrary one into a `Location`
    is an open redirect that turns a successful POST into a convincing phishing
    link. Even same-host, a path outside the list could be an unrelated route
    that renders something confusing right after a deceptively-timed flash.
    """
    referrer = request.referrer or ""
    if referrer:
        parsed = urlparse(referrer)
        if parsed.netloc == urlparse(request.host_url).netloc:
            if parsed.path.rstrip("/") in ("/tasks", "/tasks/daily", "/tasks/goals"):
                return referrer
    return url_for("tasks.backlog")


@tasks_bp.route("/<uuid:task_id>/evaluate/", methods=["POST"])
async def evaluate_task(task_id):
    """Queue the LLM evaluation for one task."""
    if not _access_token():
        return redirect(url_for("auth.login"))
    if not _require_csrf():
        flash("Invalid CSRF token.", "error")
        return redirect(url_for("tasks.backlog"))
    return await _run_llm_action(
        "evaluate",
        _parse_task_id(str(task_id)),
        "Evaluation queued. The verdict appears here once the AI finishes.",
        _back_target(),
    )


@tasks_bp.route("/<uuid:task_id>/decompose/", methods=["POST"])
async def decompose_task(task_id):
    """Queue the LLM decomposition of one task into subtasks."""
    if not _access_token():
        return redirect(url_for("auth.login"))
    if not _require_csrf():
        flash("Invalid CSRF token.", "error")
        return redirect(url_for("tasks.backlog"))
    return await _run_llm_action(
        "decompose",
        _parse_task_id(str(task_id)),
        "Decomposition queued. The subtasks appear here once the AI finishes.",
        _back_target(),
    )


@tasks_bp.route("/llm-health/")
async def llm_health():
    """Report LLM reachability for the navbar dot.

    A plain JSON endpoint rather than a template, because it is called by
    `fetch` from the layout on every page.

    It never fails. A 500 here would put an error in the console of every page
    in the app for a decoration, so every outcome maps onto the three states
    `LlmHealth` models: available, unavailable, or unknown. A 401 is answered
    with the unknown state rather than a challenge, for the same reason -- the
    page itself will redirect to login on its own reads, and the dot should not
    be what tells the user anything.
    """
    access_token = _access_token()
    if not access_token:
        return jsonify(
            {"available": False, "unknown": True, "detail": "Not signed in."}
        )

    try:
        health = await current_app.get_llm_health_use_case.execute(access_token)
    except ServiceUnavailableError:
        # The shared client raises this for transport failures; the use case
        # does not catch it because it is not a DomainException subclass of the
        # kind it is willing to soften.
        health = LlmHealth.indeterminate("The tasks service is unreachable.")
    except DomainException:
        health = LlmHealth.indeterminate("The health check did not complete.")

    return jsonify(
        {
            "available": health.available,
            "unknown": health.unknown,
            "detail": health.detail or "",
            # The glyph and label are computed server-side by the same
            # ViewModel the navbar would use, so the dot and its tooltip cannot
            # disagree about what the three states are called.
            "glyph": LlmHealthViewModel.from_domain(health).glyph,
            "label": LlmHealthViewModel.from_domain(health).label,
        }
    )
