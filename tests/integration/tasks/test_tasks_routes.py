"""Integration tests for the tasks web layer.

These exercise the full request path -- Flask routing, CSRF, the use cases, the
httpx adapter, and the mappers -- with only the DRF HTTP boundary mocked by
`respx`. That is deliberate: the point is to prove the layers are wired to each
other correctly, and mocking at the socket is the only level that still tests
the adapter's URL construction, status handling, and payload shapes together.

Every request body here is copied from `src/apps/tasks/interfaces/serializers.py`
in the backend repository rather than from what the BFF would like the API to
send. Where a payload cannot be copied because the field does not exist, the
test says so instead of inventing one -- see `TestCompletedTasksHasNoWindow`.
"""

from __future__ import annotations

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

USER_ID = uuid4()
GOAL_ID = uuid4()

ACCESS_TOKEN = "tasks-access-token"
REFRESH_TOKEN = "tasks-refresh-token"

TASKS_URL = f"{BASE}/tasks/tasks/"
GOALS_URL = f"{BASE}/tasks/goals/"

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
        "created_at": "2026-10-01T09:00:00Z",
        "updated_at": "2026-10-01T09:00:00Z",
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
    """An app with a frozen clock, so `today`-dependent rendering is stable."""
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
        with test_client.session_transaction() as session:
            session["user_id"] = str(USER_ID)
            session["is_staff"] = False
        yield test_client


def _csrf_from(html: str) -> str:
    start = html.find(CSRF_MARKER)
    if start == -1:
        return ""
    start += len(CSRF_MARKER)
    return html[start : html.find('"', start)]


def _stub_backlog(respx_mock, tasks, goals=(), completed=()):
    """Route the three reads the backlog page makes.

    The completed-tasks read is `?status=done` on the *same* collection URL, so
    it is registered **first**. respx matches routes in registration order and
    the bare-URL route matches any query string, so registering the plain
    backlog route first would swallow the filtered one and quietly serve the
    backlog as the completed list -- which looks like a passing test until the
    section renders the wrong rows.
    Returns the `?status=done` route so a test can assert on the query that was
    actually sent.
    """
    done_route = respx_mock.get(TASKS_URL, params={"status": "done"}).mock(
        return_value=httpx.Response(200, json=list(completed))
    )
    respx_mock.get(TASKS_URL).mock(return_value=httpx.Response(200, json=list(tasks)))
    respx_mock.get(GOALS_URL).mock(return_value=httpx.Response(200, json=list(goals)))
    return done_route


class TestBacklogPage:
    def test_renders_edit_and_delete_actions_on_every_row(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            _stub_backlog(respx_mock, [task_payload()])
            body = authed_client.get("/tasks/").data.decode()

        page = main_content(body)
        assert page.count('data-testid="edit-button"') == 1
        assert page.count('data-testid="delete-button"') == 1

    def test_the_edit_button_carries_the_row_values_for_the_modal(
        self, respx_mock, authed_client
    ):
        """The modal is rendered once and filled from `dataset`, so the values
        have to be on the button. Asserting the exact attributes is what stops a
        refactor from quietly dropping one and producing an empty field."""
        with respx_mock:
            _stub_backlog(
                respx_mock,
                [
                    task_payload(
                        name="Buy milk",
                        description="Semi **skimmed**",
                        due_date="2026-11-01",
                        importance="high",
                        estimated_hours="2.50",
                    )
                ],
            )
            body = authed_client.get("/tasks/").data.decode()

        button = re.search(
            r"<button[^>]*data-testid=\"edit-button\"[^>]*>", body
        ).group(0)
        assert 'data-task-name="Buy milk"' in button
        assert 'data-task-description="Semi **skimmed**"' in button
        assert 'data-task-due="2026-11-01"' in button
        assert 'data-task-priority="high"' in button
        # A bare number, never "2.5h": the modal's input is `type="number"` and
        # the browser rejects a suffixed value outright.
        assert 'data-task-hours="2.50"' in button

    def test_an_unestimated_task_prefills_the_hours_input_empty(
        self, respx_mock, authed_client
    ):
        """Prefilling "0.00" here would be fine, but prefilling the *label*
        ("an em dash") would post a dash as the new estimate and silently clear
        a figure the user had set."""
        with respx_mock:
            _stub_backlog(respx_mock, [task_payload(estimated_hours="0.00")])
            body = authed_client.get("/tasks/").data.decode()

        button = re.search(
            r"<button[^>]*data-testid=\"edit-button\"[^>]*>", body
        ).group(0)
        assert 'data-task-hours=""' in button

    def test_user_text_never_reaches_a_javascript_expression(
        self, respx_mock, authed_client
    ):
        """`openEdit($el)` reads the row's values from data attributes precisely
        so that a task named `'); fetch('//evil')//` cannot become code. This
        asserts the end of that chain: the name is present, escaped, and the
        only `x-data` bodies are free of interpolation."""
        with respx_mock:
            _stub_backlog(
                respx_mock, [task_payload(name="Robert'); DROP TABLE tasks;--")]
            )
            body = authed_client.get("/tasks/").data.decode()

        assert "Robert&#39;); DROP TABLE tasks;--" in body
        for scope in re.findall(r'x-data="(.*?)"', body, re.DOTALL):
            assert "{{" not in scope, "user data was interpolated into an x-data body"

    def test_the_page_still_has_exactly_one_alpine_scope(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            _stub_backlog(respx_mock, [task_payload()])
            body = authed_client.get("/tasks/").data.decode()

        assert main_content(body).count("x-data=") == 1


class TestDerivedFromGoalBadge:
    def test_a_task_filed_under_a_goal_shows_the_badge(self, respx_mock, authed_client):
        with respx_mock:
            _stub_backlog(respx_mock, [task_payload(goal_id=str(GOAL_ID))])
            body = authed_client.get("/tasks/").data.decode()

        page = main_content(body)
        assert 'data-testid="derived-from-goal-badge"' in page
        # Whitespace-tolerant: the badge wraps across lines in the template.
        assert re.search(r">\s*Derived from Goal\s*</a>", page)

    def test_the_badge_links_to_the_goals_list_that_exists(
        self, respx_mock, authed_client
    ):
        """Not to `/goals/<id>`: this BFF has no per-goal route, so that href
        would be a dead badge pointing at a 404."""
        with respx_mock:
            _stub_backlog(respx_mock, [task_payload(goal_id=str(GOAL_ID))])
            body = authed_client.get("/tasks/").data.decode()

        badge = re.search(
            r"<a[^>]*data-testid=\"derived-from-goal-badge\"[^>]*>", body
        ).group(0)
        assert 'href="/tasks/goals/"' in badge

    def test_a_task_with_no_goal_shows_no_badge(self, respx_mock, authed_client):
        with respx_mock:
            _stub_backlog(respx_mock, [task_payload(goal_id=None)])
            body = authed_client.get("/tasks/").data.decode()

        assert 'data-testid="derived-from-goal-badge"' not in main_content(body)


class TestCompletedTasksSection:
    def test_the_read_filters_on_done_not_completed(self, respx_mock, authed_client):
        """`TASK_STATUS_CHOICES` is todo/doing/done. `completed` is a 400."""
        with respx_mock:
            done_route = _stub_backlog(respx_mock, [task_payload()])
            authed_client.get("/tasks/")

        assert done_route.called

    def test_finished_tasks_are_rendered_with_a_delete_action(
        self, respx_mock, authed_client
    ):
        done = task_payload(name="Shipped it", status="done", estimated_hours="0.00")
        with respx_mock:
            _stub_backlog(respx_mock, [task_payload()], completed=[done])
            body = authed_client.get("/tasks/").data.decode()

        page = main_content(body)
        assert "Shipped it" in page
        assert 'data-testid="completed-task-row"' in page
        assert 'data-testid="completed-delete-button"' in page

    def test_there_is_no_reopen_button(self, respx_mock, authed_client):
        """Reopening is not expressible in this API: `status` is not on
        `UpdateTaskSerializer`, and `POST /tasks/<id>/mark-doing/` answers 409
        (`TaskAlreadyDone`) for a finished task. A button that cannot work would
        advertise a feature the product does not have."""
        done = task_payload(name="Shipped it", status="done")
        with respx_mock:
            respx_mock.get(TASKS_URL, params={"status": "done"}).mock(
                return_value=httpx.Response(200, json=[done])
            )
            _stub_backlog(respx_mock, [task_payload()])
            body = authed_client.get("/tasks/").data.decode()

        page = main_content(body).lower()
        assert "reopen" not in page

    def test_an_empty_history_shows_a_friendly_message(self, respx_mock, authed_client):
        with respx_mock:
            _stub_backlog(respx_mock, [task_payload()])
            body = authed_client.get("/tasks/").data.decode()

        assert "No recently completed tasks." in main_content(body)

    def test_a_failed_read_is_not_rendered_as_an_empty_history(
        self, respx_mock, authed_client
    ):
        """ "You have finished nothing" and "we could not ask" are different
        facts. Collapsing them would let an outage look like good news."""
        with respx_mock:
            done_route = _stub_backlog(respx_mock, [task_payload()])
            # Re-stub with a failure *after* `_stub_backlog` registered the
            # filtered route, so the more specific match still wins.
            done_route.mock(return_value=httpx.Response(500, json={"detail": "boom"}))
            body = authed_client.get("/tasks/").data.decode()

        page = main_content(body)
        assert 'data-testid="completed-unavailable"' in page
        assert "No recently completed tasks." not in page


class TestCompletedTasksHasNoWindow:
    """The "last 7 days" requirement, recorded as an absence.

    The product asked for completed tasks from the last week. `TaskModel` has no
    `completed_at` column and `TaskSerializer` sends no completion timestamp, so
    there is nothing to filter on. These tests exist so that the gap is a test
    that fails loudly when the backend gains the field -- rather than a filter
    somebody quietly added on `updated_at`, which is "last touched", not "last
    finished", and is wrong in both directions.
    """

    def test_no_date_query_parameter_is_sent(self, respx_mock, authed_client):
        """The filtered read carries `status` and nothing else. A `since=`,
        `start_date=`, or `completed_after=` here would mean somebody had
        invented a window the backend cannot honour."""
        with respx_mock:
            _stub_backlog(respx_mock, [task_payload()])
            authed_client.get("/tasks/")

        for call in respx_mock.calls:
            if call.request.url.path.endswith("/tasks/tasks/"):
                params = dict(call.request.url.params)
                assert params in ({}, {"status": "done"}), params

    def test_the_completed_schema_declares_no_completion_timestamp(self):
        """A schema field the serializer does not send is the exact failure mode
        AGENTS.md rule 7 exists to prevent: it turns a missing backend feature
        into a 500 instead of a visible gap. `completed_at` is absent here
        because `TaskSerializer` does not emit it."""
        from src.tasks.infrastructure.schemas import DRFTaskResponse

        assert "completed_at" not in DRFTaskResponse.model_fields


class TestUpdateTask:
    def test_sends_a_patch_with_only_the_five_updatable_fields(
        self, respx_mock, authed_client
    ):
        task = task_payload(name="Before")
        with respx_mock:
            route = respx_mock.patch(f"{TASKS_URL}{task['id']}/").mock(
                return_value=httpx.Response(200, json=task_payload(name="After"))
            )
            _stub_backlog(respx_mock, [task])
            page = authed_client.get("/tasks/").data.decode()

            response = authed_client.post(
                f"/tasks/{task['id']}/edit/",
                data={
                    "csrf_token": _csrf_from(page),
                    "name": "After",
                    "description": "",
                    "due_date": "",
                    "priority": "high",
                    "estimated_hours": "",
                },
            )

        assert route.called
        # The edit succeeded, so the view redirected rather than re-rendering.
        assert response.status_code == 302
        body = route.calls[0].request.read().decode()
        import json

        sent = json.loads(body)
        # Exactly `UpdateTaskSerializer`'s five fields. `goal_id` and `status`
        # are absent from the serializer, so sending them would be a lie the
        # user discovers on the next page load.
        assert set(sent) == {
            "name",
            "description",
            "due_date",
            "importance",
            "estimated_hours",
        }
        assert sent["importance"] == "high"
        # A cleared due date is an explicit null, not an absent key: PATCH is
        # applied with partial=True, so absent means "leave it alone".
        assert sent["due_date"] is None
        assert sent["description"] == ""
        # `DecimalField` has no allow_null, so clearing sends the model's zero.
        assert sent["estimated_hours"] == "0"

    def test_flashes_success_and_redirects_back_to_the_backlog(
        self, respx_mock, authed_client
    ):
        task = task_payload(name="Before")
        with respx_mock:
            respx_mock.patch(f"{TASKS_URL}{task['id']}/").mock(
                return_value=httpx.Response(200, json=task_payload(name="After"))
            )
            _stub_backlog(respx_mock, [task])
            page = authed_client.get("/tasks/").data.decode()
            response = authed_client.post(
                f"/tasks/{task['id']}/edit/",
                data={
                    "csrf_token": _csrf_from(page),
                    "name": "After",
                    "description": "",
                    "due_date": "",
                    "priority": "medium",
                    "estimated_hours": "1",
                },
            )

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/tasks/")

    def test_a_404_flashes_instead_of_crashing(self, respx_mock, authed_client):
        task = task_payload()
        with respx_mock:
            respx_mock.patch(f"{TASKS_URL}{task['id']}/").mock(
                return_value=httpx.Response(404, json={"detail": "Not found."})
            )
            _stub_backlog(respx_mock, [task])
            page = authed_client.get("/tasks/").data.decode()
            response = authed_client.post(
                f"/tasks/{task['id']}/edit/",
                data={
                    "csrf_token": _csrf_from(page),
                    "name": "After",
                    "description": "",
                    "due_date": "",
                    "priority": "medium",
                    "estimated_hours": "1",
                },
                follow_redirects=True,
            )

        assert response.status_code == 200
        assert b"no longer exists" in response.data

    def test_rejects_a_blank_name_before_calling_the_backend(
        self, respx_mock, authed_client
    ):
        """`Task._clean_name` already refuses an empty name; catching it here
        saves the round trip and gives a message that names the field."""
        task = task_payload()
        with respx_mock:
            patch_route = respx_mock.patch(f"{TASKS_URL}{task['id']}/").mock(
                return_value=httpx.Response(200, json=task)
            )
            _stub_backlog(respx_mock, [task])
            page = authed_client.get("/tasks/").data.decode()
            response = authed_client.post(
                f"/tasks/{task['id']}/edit/",
                data={
                    "csrf_token": _csrf_from(page),
                    "name": "   ",
                    "description": "",
                    "due_date": "",
                    "priority": "medium",
                    "estimated_hours": "1",
                },
                follow_redirects=True,
            )

        assert not patch_route.called
        assert b"cannot be empty" in response.data

    def test_a_bad_csrf_token_changes_nothing(self, respx_mock, authed_client):
        task = task_payload()
        with respx_mock:
            patch_route = respx_mock.patch(f"{TASKS_URL}{task['id']}/").mock(
                return_value=httpx.Response(200, json=task)
            )
            _stub_backlog(respx_mock, [task])
            response = authed_client.post(
                f"/tasks/{task['id']}/edit/",
                data={"csrf_token": "forged", "name": "After"},
            )

        assert not patch_route.called
        assert response.status_code == 302


class TestDeleteTask:
    def test_sends_a_delete_and_flashes(self, respx_mock, authed_client):
        task = task_payload(name="Doomed")
        with respx_mock:
            route = respx_mock.delete(f"{TASKS_URL}{task['id']}/").mock(
                return_value=httpx.Response(204)
            )
            _stub_backlog(respx_mock, [task])
            page = authed_client.get("/tasks/").data.decode()
            response = authed_client.post(
                f"/tasks/{task['id']}/delete/",
                data={"csrf_token": _csrf_from(page)},
                follow_redirects=True,
            )

        assert route.called
        assert b"Task deleted." in response.data

    def test_a_404_is_not_reported_as_success(self, respx_mock, authed_client):
        """The row the user confirmed had already gone, which means their view
        was stale. "Task deleted" would hide that."""
        task = task_payload()
        with respx_mock:
            respx_mock.delete(f"{TASKS_URL}{task['id']}/").mock(
                return_value=httpx.Response(404, json={"detail": "Not found."})
            )
            _stub_backlog(respx_mock, [task])
            page = authed_client.get("/tasks/").data.decode()
            response = authed_client.post(
                f"/tasks/{task['id']}/delete/",
                data={"csrf_token": _csrf_from(page)},
                follow_redirects=True,
            )

        assert b"no longer exists" in response.data
        assert b"Task deleted." not in response.data

    def test_a_finished_task_can_also_be_deleted(self, respx_mock, authed_client):
        done = task_payload(name="Shipped it", status="done")
        with respx_mock:
            route = respx_mock.delete(f"{TASKS_URL}{done['id']}/").mock(
                return_value=httpx.Response(204)
            )
            respx_mock.get(TASKS_URL, params={"status": "done"}).mock(
                return_value=httpx.Response(200, json=[done])
            )
            _stub_backlog(respx_mock, [task_payload()])
            page = authed_client.get("/tasks/").data.decode()
            authed_client.post(
                f"/tasks/{done['id']}/delete/", data={"csrf_token": _csrf_from(page)}
            )

        assert route.called

    def test_is_post_only(self, respx_mock, authed_client):
        """A GET link that deletes would be triggerable by any prefetcher or
        crawler that followed it."""
        task = task_payload()
        with respx_mock:
            route = respx_mock.delete(f"{TASKS_URL}{task['id']}/").mock(
                return_value=httpx.Response(204)
            )
            response = authed_client.get(f"/tasks/{task['id']}/delete/")

        assert response.status_code == 405
        assert not route.called
