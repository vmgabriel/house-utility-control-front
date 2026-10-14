"""Integration tests for the three new tasks features.

Same seam as `test_tasks_routes.py` -- only the DRF HTTP boundary is mocked by
`respx`, so the adapter's URL construction, status handling, and payload shapes
are all under test together with the routing and CSRF around them.

Every request body here is copied from `src/apps/tasks/interfaces/serializers.py`
in the backend repository, and every endpoint from
`src/apps/tasks/interfaces/urls.py`. Three of them did not exist when this
context was first written and their shapes are not the obvious ones:

* ``POST /daily-plans/generate/`` answers **202** with a queued body and **no
  plan**, because the work goes to Celery.
* ``POST /daily-plans/<uuid:pk>/add-task/`` is keyed on a **plan UUID**, not a
  date, and answers **409** for a task that is already scheduled, for a full day,
  and for a finished task.
* There is no ``/goals/<id>/tasks/``. A goal's tasks come from the task
  collection with ``?goal_id=``, and the goal itself from the goal detail route.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from uuid import uuid4

import httpx
import pytest

from src.interfaces.web.app import create_app
from src.shared.auth.jwt_cookie_manager import ACCESS_COOKIE, REFRESH_COOKIE
from src.tasks.application.wiring import build_tasks_use_cases
from tests.integration.rentals.page_html import main_content

BASE = "http://localhost:8000/api/v1"
TODAY = date(2026, 10, 5)
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)

ACCESS_TOKEN = "tasks-access-token"
REFRESH_TOKEN = "tasks-refresh-token"

TASKS_URL = f"{BASE}/tasks/tasks/"
GOALS_URL = f"{BASE}/tasks/goals/"
DAILY_PLANS_URL = f"{BASE}/tasks/daily-plans/"
GENERATE_URL = f"{BASE}/tasks/daily-plans/generate/"

CSRF_MARKER = 'name="csrf_token" value="'


def task_payload(**overrides) -> dict:
    """One `TaskSerializer` payload, verbatim from the backend's serializer."""
    payload = {
        "id": str(uuid4()),
        "goal_id": None,
        "name": "Draft the quarterly report",
        "description": "## Sections",
        "due_date": None,
        "importance": "medium",
        "estimated_hours": "2.00",
        "status": "todo",
        "is_overwhelmed": False,
        "is_checked_by_llm": False,
        "llm_evaluation_failed": False,
        # `TaskSerializer` declares it read-only and `allow_null`, so it is on
        # every payload and is `null` while the task is open.
        "completed_at": None,
        "created_at": "2026-10-01T09:00:00Z",
        "updated_at": "2026-10-01T09:00:00Z",
    }
    payload.update(overrides)
    return payload


def goal_payload(**overrides) -> dict:
    """One `GoalSerializer` payload."""
    payload = {
        "id": str(uuid4()),
        "name": "Ship the thing",
        "description": "## Scope",
        "due_date": None,
        "status": "active",
        "task_count": 2,
    }
    payload.update(overrides)
    return payload


def daily_plan_payload(**overrides) -> dict:
    """One `DailyPlanSerializer` payload."""
    payload = {
        "id": str(uuid4()),
        "date": TODAY.isoformat(),
        "tasks": [],
        "total_hours": "0.00",
        "is_full": False,
        "generated_by_llm": False,
        "created_at": "2026-10-05T06:00:00Z",
    }
    payload.update(overrides)
    return payload


class FrozenClock:
    def now(self) -> datetime:
        return NOW

    def today(self) -> date:
        return TODAY


@pytest.fixture
def app():
    application = create_app()
    application.clock = FrozenClock()
    application.config.update(TESTING=True)
    build_tasks_use_cases(
        application.extensions["tasks_api_client"], clock=application.clock
    ).attach_to(application)
    return application


@pytest.fixture
def authed_client(app):
    with app.test_client() as test_client:
        test_client.set_cookie(ACCESS_COOKIE, ACCESS_TOKEN)
        test_client.set_cookie(REFRESH_COOKIE, REFRESH_TOKEN)
        yield test_client


def _csrf_from(html: str) -> str:
    start = html.find(CSRF_MARKER)
    if start == -1:
        return ""
    start += len(CSRF_MARKER)
    return html[start : html.find('"', start)]


def task_id_of(html: str) -> str:
    """The first task id rendered on a page. Takes the raw body; callers may
    already have sliced it to `<main>`."""
    return re.search(r'data-task-id="([^"]+)"', html).group(1)


def _stub_daily_page(respx_mock, plans, tasks=()):
    """The two reads the daily page makes, plus the health probe on the layout."""
    respx_mock.get(DAILY_PLANS_URL).mock(
        return_value=httpx.Response(200, json=list(plans))
    )
    respx_mock.get(TASKS_URL).mock(return_value=httpx.Response(200, json=list(tasks)))


# --------------------------------------------------------------------------- #
# 1. Manual daily plan generation
# --------------------------------------------------------------------------- #
class TestGenerateDailyPlanButton:
    def test_the_button_appears_on_an_unplanned_today(self, respx_mock, authed_client):
        with respx_mock:
            _stub_daily_page(respx_mock, plans=[])
            body = authed_client.get("/tasks/daily/").data.decode()

        page = main_content(body)
        assert 'data-testid="generate-daily-plan-button"' in page
        assert 'action="/tasks/daily/generate/"' in page

    def test_it_disappears_once_the_day_has_a_plan(self, respx_mock, authed_client):
        """The button is an answer to "this day is empty", not a toolbar action.

        Offering it on a planned day would queue work that replaces a plan the
        user is looking at and would have to think about.
        """
        plan = daily_plan_payload()
        with respx_mock:
            _stub_daily_page(
                respx_mock,
                plans=[plan],
                tasks=[task_payload(id=plan["id"], status="todo")],
            )
            body = authed_client.get("/tasks/daily/").data.decode()

        assert 'data-testid="generate-daily-plan-button"' not in main_content(body)

    def test_it_is_not_offered_for_a_day_the_user_is_only_browsing(
        self, respx_mock, authed_client
    ):
        """Past and future dates get the stepper, not a queue button.

        "Plan my day" is what an empty *today* means; offering it for a past
        date would queue a plan nobody will ever read.
        """
        with respx_mock:
            _stub_daily_page(respx_mock, plans=[])
            body = authed_client.get("/tasks/daily/?date=2026-09-01").data.decode()

        assert 'data-testid="generate-daily-plan-button"' not in main_content(body)

    def test_the_page_still_has_exactly_one_alpine_scope(
        self, respx_mock, authed_client
    ):
        """The generation form borrows the page's root state.

        A `x-data` on the form would be a second root inside one page body, which
        is AGENTS.md rule 5's failure mode and the one assertion that catches it.
        """
        with respx_mock:
            _stub_daily_page(respx_mock, plans=[])
            body = authed_client.get("/tasks/daily/").data.decode()

        assert main_content(body).count("x-data=") == 1


class TestGenerateDailyPlanRequest:
    def test_it_posts_the_date_and_the_csrf_token(self, respx_mock, authed_client):
        """The date in the body is the day the page was showing.

        A page rendered for today and submitted tomorrow must plan the day the
        user was looking at, so the date travels with the form rather than being
        recomputed from the clock on submit.
        """
        with respx_mock:
            _stub_daily_page(respx_mock, plans=[])
            page = authed_client.get("/tasks/daily/").data.decode()
            token = _csrf_from(page)

            route = respx_mock.post(GENERATE_URL).mock(
                return_value=httpx.Response(
                    202,
                    json={
                        "detail": "Daily plan generation queued.",
                        "date": TODAY.isoformat(),
                        "queued": True,
                    },
                )
            )
            response = authed_client.post(
                "/tasks/daily/generate/",
                data={"csrf_token": token, "date": TODAY.isoformat()},
            )

        assert route.called
        assert json.loads(route.calls.last.request.read()) == {
            "date": TODAY.isoformat()
        }
        assert response.status_code == 302

    def test_it_says_queued_rather_than_generated(self, respx_mock, authed_client):
        """The endpoint answers 202 and the work is done by a worker afterwards.

        "Your plan is ready" would be a claim the response does not support, and
        the user would discover it when no plan appears.
        """
        with respx_mock:
            _stub_daily_page(respx_mock, plans=[])
            page = authed_client.get("/tasks/daily/").data.decode()
            token = _csrf_from(page)

            respx_mock.post(GENERATE_URL).mock(
                return_value=httpx.Response(
                    202,
                    json={
                        "detail": "Daily plan generation queued.",
                        "date": TODAY.isoformat(),
                        "queued": True,
                    },
                )
            )
            response = authed_client.post(
                "/tasks/daily/generate/",
                data={"csrf_token": token, "date": TODAY.isoformat()},
                follow_redirects=True,
            )

        body = response.data.decode()
        assert "queued" in body.lower()
        assert "your day has been planned" not in body.lower()

    def test_a_bad_csrf_token_queues_nothing(self, respx_mock, authed_client):
        with respx_mock:
            _stub_daily_page(respx_mock, plans=[])
            route = respx_mock.post(GENERATE_URL).mock(
                return_value=httpx.Response(
                    202, json={"detail": "", "date": "", "queued": True}
                )
            )
            authed_client.post("/tasks/daily/generate/", data={"csrf_token": "wrong"})

        assert not route.called

    def test_an_unreachable_provider_is_a_flash_not_a_503_page(
        self, respx_mock, authed_client
    ):
        """The AI is optional; a 503 page would deny the user their daily view."""
        with respx_mock:
            _stub_daily_page(respx_mock, plans=[])
            page = authed_client.get("/tasks/daily/").data.decode()
            token = _csrf_from(page)

            respx_mock.post(GENERATE_URL).mock(
                return_value=httpx.Response(
                    503, json={"detail": "The LLM provider is unreachable."}
                )
            )
            response = authed_client.post(
                "/tasks/daily/generate/",
                data={"csrf_token": token, "date": TODAY.isoformat()},
                follow_redirects=True,
            )

        assert response.status_code == 200
        assert "unavailable" in response.data.decode().lower()

    def test_it_is_post_only(self, respx_mock, authed_client):
        with respx_mock:
            route = respx_mock.post(GENERATE_URL).mock(
                return_value=httpx.Response(
                    202, json={"detail": "", "date": "", "queued": True}
                )
            )
            response = authed_client.get("/tasks/daily/generate/")

        assert response.status_code == 405
        assert not route.called


# --------------------------------------------------------------------------- #
# 2. Add a backlog task to today's plan
# --------------------------------------------------------------------------- #
class TestAddTaskToPlanButton:
    def test_every_open_row_carries_one(self, respx_mock, authed_client):
        with respx_mock:
            respx_mock.get(TASKS_URL, params={"status": "done"}).mock(
                return_value=httpx.Response(200, json=[])
            )
            respx_mock.get(TASKS_URL).mock(
                return_value=httpx.Response(200, json=[task_payload()])
            )
            respx_mock.get(GOALS_URL).mock(return_value=httpx.Response(200, json=[]))
            body = authed_client.get("/tasks/").data.decode()

        page = main_content(body)
        assert page.count('data-testid="add-to-plan-button"') == 1
        assert f'action="/tasks/{task_id_of(body)}/add-to-plan/"' in page

    def test_a_finished_row_does_not(self, respx_mock, authed_client):
        """The backend answers 409 `TaskAlreadyDone` for one.

        A button that is always there and always fails teaches the user that the
        page's buttons are unreliable.
        """
        with respx_mock:
            respx_mock.get(TASKS_URL, params={"status": "done"}).mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        task_payload(
                            status="done",
                            completed_at="2026-10-04T18:00:00Z",
                            estimated_hours="0.00",
                        )
                    ],
                )
            )
            respx_mock.get(TASKS_URL).mock(return_value=httpx.Response(200, json=[]))
            respx_mock.get(GOALS_URL).mock(return_value=httpx.Response(200, json=[]))
            body = authed_client.get("/tasks/").data.decode()

        page = main_content(body)
        # The completed section renders its own row, which carries Delete only.
        assert page.count('data-testid="add-to-plan-button"') == 0

    def test_the_post_carries_a_csrf_token(self, respx_mock, authed_client):
        with respx_mock:
            respx_mock.get(TASKS_URL, params={"status": "done"}).mock(
                return_value=httpx.Response(200, json=[])
            )
            respx_mock.get(TASKS_URL).mock(
                return_value=httpx.Response(200, json=[task_payload()])
            )
            respx_mock.get(GOALS_URL).mock(return_value=httpx.Response(200, json=[]))
            body = authed_client.get("/tasks/").data.decode()

        form = re.search(
            r"<form[^>]*data-testid=\"add-to-plan-form\".*?</form>",
            main_content(body),
            re.DOTALL,
        ).group(0)
        assert CSRF_MARKER in form


class TestAddTaskToPlanRequest:
    def _post_it(self, respx_mock, authed_client, plans, expected_status=200):
        respx_mock.get(TASKS_URL, params={"status": "done"}).mock(
            return_value=httpx.Response(200, json=[])
        )
        respx_mock.get(TASKS_URL).mock(
            return_value=httpx.Response(200, json=[task_payload()])
        )
        respx_mock.get(GOALS_URL).mock(return_value=httpx.Response(200, json=[]))
        page = authed_client.get("/tasks/").data.decode()
        token = _csrf_from(page)
        task_id = re.search(r'data-task-id="([^"]+)"', main_content(page)).group(1)

        respx_mock.get(DAILY_PLANS_URL).mock(
            return_value=httpx.Response(200, json=list(plans))
        )
        add_task = respx_mock.post(
            f"{DAILY_PLANS_URL}{plans[0]['id']}/add-task/"
            if plans
            else f"{DAILY_PLANS_URL}{{plan}}/add-task/"
        ).mock(
            return_value=httpx.Response(
                expected_status,
                json=(
                    daily_plan_payload(total_hours="2.00")
                    if expected_status == 200
                    else {"detail": "This task is already on the plan."}
                ),
            )
        )
        if plans:
            respx_mock.post(DAILY_PLANS_URL).mock(
                return_value=httpx.Response(
                    201, json=daily_plan_payload(id=str(uuid4()))
                )
            )
        response = authed_client.post(
            f"/tasks/{task_id}/add-to-plan/", data={"csrf_token": token}
        )
        return add_task, response

    def test_it_posts_the_task_id_to_the_plans_add_task_action(
        self, respx_mock, authed_client
    ):
        """The body is the single key `DailyPlanTaskSerializer` declares."""
        plan = daily_plan_payload()
        with respx_mock:
            add_task, response = self._post_it(respx_mock, authed_client, [plan])

        assert add_task.called
        body = add_task.calls.last.request.read().decode()
        assert '"task_id"' in body
        assert response.status_code == 302

    def test_it_opens_the_day_when_it_has_no_plan_yet(self, respx_mock, authed_client):
        """`add-task` is keyed on a plan UUID, so an unplanned day has none.

        `GetOrCreateDailyPlan` is the backend's own answer, and the alternative
        -- hiding the button -- would leave it missing on most days.
        """
        created_id = str(uuid4())
        with respx_mock:
            respx_mock.get(DAILY_PLANS_URL).mock(
                return_value=httpx.Response(200, json=[])
            )
            respx_mock.get(TASKS_URL, params={"status": "done"}).mock(
                return_value=httpx.Response(200, json=[])
            )
            respx_mock.get(TASKS_URL).mock(
                return_value=httpx.Response(200, json=[task_payload()])
            )
            respx_mock.get(GOALS_URL).mock(return_value=httpx.Response(200, json=[]))
            page = authed_client.get("/tasks/").data.decode()
            token = _csrf_from(page)
            task_id = re.search(r'data-task-id="([^"]+)"', main_content(page)).group(1)

            create = respx_mock.post(DAILY_PLANS_URL).mock(
                return_value=httpx.Response(201, json=daily_plan_payload(id=created_id))
            )
            add_task = respx_mock.post(f"{DAILY_PLANS_URL}{created_id}/add-task/").mock(
                return_value=httpx.Response(
                    200, json=daily_plan_payload(total_hours="2.00")
                )
            )
            authed_client.post(
                f"/tasks/{task_id}/add-to-plan/", data={"csrf_token": token}
            )

        assert create.called
        assert json.loads(create.calls.last.request.read()) == {
            "date": TODAY.isoformat()
        }
        assert add_task.called

    def test_a_409_shows_the_backends_own_reason(self, respx_mock, authed_client):
        """Already scheduled, day full, and already done need three different
        reactions from the user, and only the backend knows which one applies.

        Its `detail` is carried through rather than replaced by a message of this
        BFF's choosing.
        """
        plan = daily_plan_payload()
        with respx_mock:
            _, response = self._post_it(
                respx_mock,
                authed_client,
                [plan],
                expected_status=409,
            )

        assert response.status_code == 302
        with respx_mock:
            _stub_daily_page(respx_mock, plans=[plan])
            flashed = authed_client.get(
                response.headers["Location"], follow_redirects=True
            ).data.decode()

        assert "already on the plan" in flashed

    def test_it_is_post_only(self, respx_mock, authed_client):
        with respx_mock:
            response = authed_client.get(f"/tasks/{uuid4()}/add-to-plan/")

        assert response.status_code == 405


# --------------------------------------------------------------------------- #
# 3. Goal detail and its progress bar
# --------------------------------------------------------------------------- #
class TestGoalDetailPage:
    def _stub(self, respx_mock, *, goal, tasks):
        respx_mock.get(GOALS_URL).mock(return_value=httpx.Response(200, json=[goal]))
        respx_mock.get(f"{GOALS_URL}{goal['id']}/").mock(
            return_value=httpx.Response(200, json=goal)
        )
        respx_mock.get(TASKS_URL, params={"goal_id": goal["id"]}).mock(
            return_value=httpx.Response(200, json=list(tasks))
        )

    def test_the_goal_and_its_tasks_are_rendered(self, respx_mock, authed_client):
        goal = goal_payload()
        with respx_mock:
            self._stub(
                respx_mock,
                goal=goal,
                tasks=[
                    task_payload(goal_id=goal["id"], estimated_hours="3.00"),
                    task_payload(
                        name="Finished bit",
                        goal_id=goal["id"],
                        estimated_hours="1.00",
                        status="done",
                        completed_at="2026-10-04T18:00:00Z",
                    ),
                ],
            )
            body = authed_client.get(f"/tasks/goals/{goal['id']}/").data.decode()

        page = main_content(body)
        assert "Ship the thing" in page
        assert page.count('data-testid="goal-task-row"') == 2
        assert page.count('data-testid="goal-task-completed-badge"') == 1

    def test_the_task_read_is_narrowed_by_goal_and_not_by_status(
        self, respx_mock, authed_client
    ):
        """`?goal_id=` only.

        A `status` filter here would hide the finished tasks that are both half
        the list and the whole numerator of the progress bar.
        """
        goal = goal_payload()
        with respx_mock:
            self._stub(respx_mock, goal=goal, tasks=[task_payload()])
            authed_client.get(f"/tasks/goals/{goal['id']}/")
            # Collected inside the context: `respx_mock.calls` is cleared when
            # the router stops, so reading it afterwards yields an empty list and
            # every assertion over it passes vacuously.
            calls = list(respx_mock.calls)

        by_goal = [
            call
            for call in calls
            if call.request.url.path.endswith("/tasks/tasks/")
            and call.request.url.params.get("goal_id")
        ]
        assert by_goal, "no ?goal_id= read was made"
        assert dict(by_goal[0].request.url.params) == {"goal_id": goal["id"]}

    def test_progress_is_the_share_of_estimated_hours_that_are_done(
        self, respx_mock, authed_client
    ):
        goal = goal_payload()
        with respx_mock:
            self._stub(
                respx_mock,
                goal=goal,
                tasks=[
                    task_payload(
                        goal_id=goal["id"],
                        estimated_hours="3.00",
                        status="done",
                        completed_at="2026-10-04T18:00:00Z",
                    ),
                    task_payload(goal_id=goal["id"], estimated_hours="1.00"),
                ],
            )
            body = authed_client.get(f"/tasks/goals/{goal['id']}/").data.decode()

        page = main_content(body)
        assert 'data-testid="goal-progress-percentage"' in page
        assert "75%" in page
        assert "3h of 4h" in page

    def test_a_goal_with_no_tasks_says_it_is_unmeasured(
        self, respx_mock, authed_client
    ):
        """ "0h of 0h complete" reads as a measurement of zero progress.

        A goal with no tasks filed against it has not been measured, and the two
        are different facts the page words differently.
        """
        goal = goal_payload(task_count=0)
        with respx_mock:
            self._stub(respx_mock, goal=goal, tasks=[])
            body = authed_client.get(f"/tasks/goals/{goal['id']}/").data.decode()

        page = main_content(body)
        assert 'data-testid="goal-progress-unmeasured"' in page
        assert 'data-testid="goal-detail-empty"' in page

    def test_a_missing_goal_redirects_with_a_flash(self, respx_mock, authed_client):
        """Not an empty page under a heading the user never had.

        The backend answers 404 for a missing goal and for somebody else's
        alike, and the page must not become a way to probe for other people's
        goal ids.
        """
        goal = goal_payload()
        with respx_mock:
            respx_mock.get(f"{GOALS_URL}{goal['id']}/").mock(
                return_value=httpx.Response(404, json={"detail": "Not found."})
            )
            response = authed_client.get(f"/tasks/goals/{goal['id']}/")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/tasks/goals/")

    def test_the_page_has_exactly_one_alpine_scope(self, respx_mock, authed_client):
        goal = goal_payload()
        with respx_mock:
            self._stub(respx_mock, goal=goal, tasks=[task_payload()])
            body = authed_client.get(f"/tasks/goals/{goal['id']}/").data.decode()

        assert main_content(body).count("x-data=") == 1


class TestGoalCardIsALink:
    def test_a_card_links_to_that_goal_s_own_page(self, respx_mock, authed_client):
        goal = goal_payload()
        with respx_mock:
            respx_mock.get(GOALS_URL).mock(
                return_value=httpx.Response(200, json=[goal])
            )
            body = authed_client.get("/tasks/goals/").data.decode()

        card = re.search(
            r"<a[^>]*data-testid=\"goal-card\"[^>]*>", main_content(body)
        ).group(0)
        assert f'href="/tasks/goals/{goal["id"]}/"' in card

    def test_the_card_is_a_link_rather_than_a_javascript_navigation(
        self, respx_mock, authed_client
    ):
        """A card built with `@click` would need the goal id inside the page's
        `x-data` expression, and user text in a JavaScript expression is exactly
        what `tests/integration/web/test_security.py` refuses."""
        goal = goal_payload()
        with respx_mock:
            respx_mock.get(GOALS_URL).mock(
                return_value=httpx.Response(200, json=[goal])
            )
            body = authed_client.get("/tasks/goals/").data.decode()

        card = re.search(
            r"<a[^>]*data-testid=\"goal-card\"[^>]*>", main_content(body)
        ).group(0)
        assert "@click" not in card
