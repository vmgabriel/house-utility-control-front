"""Integration tests for the Kanban board and its status-transition endpoint.

The seam is the same as `test_tasks_routes.py`: Flask routing, CSRF, the use
cases, the `httpx` adapter, and the mappers, with only the DRF HTTP boundary
mocked by `respx`. That matters most here, because three things are only
observable at this level at once:

* **which URL each transition posts to.** The board's correctness *is* the
  mapping -- `doing -> done` posting to `reopen` would render identically and be
  wrong -- and the URL only exists in the shared client.
* **that the three endpoints are distinct routes.** They share a path shape
  (`/tasks/<id>/<action>/`), so a stub or an adapter that matched loosely would
  let one stand in for another.
* **that a 409 becomes a 409.** The drag handler decides whether to revert the
  card from the response status alone, so this is a contract, not a detail.

Every request body is copied from `src/apps/tasks/interfaces/serializers.py` in
the backend repository. Two shapes worth naming, both verified there:

* the three transitions take **no body at all** -- `TaskViewSet.mark_doing`,
  `mark_done`, and `reopen` all declare `request=None` and read no serializer.
  A BFF sending `{"status": "doing"}` would be inventing a field no serializer
  accepts.
* each answers **200 with the whole `TaskSerializer`**, not 204, so the reload
  is available and `completed_at` comes back with it.
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

ACCESS_TOKEN = "tasks-access-token"
REFRESH_TOKEN = "tasks-refresh-token"

TASKS_URL = f"{BASE}/tasks/tasks/"
GOALS_URL = f"{BASE}/tasks/goals/"
DAILY_PLANS_URL = f"{BASE}/tasks/daily-plans/"

CSRF_MARKER = 'name="csrf_token" value="'

TASK_ID = "11111111-1111-1111-1111-111111111111"
PLAN_ID = "44444444-4444-4444-4444-444444444444"
COMPLETED_AT = "2026-10-05T14:31:12.480000Z"


def task_payload(**overrides) -> dict:
    """One `TaskSerializer` payload, verbatim from the backend's serializer."""
    payload = {
        "id": str(uuid4()),
        "goal_id": None,
        "name": "Draft the quarterly report",
        "description": "",
        "due_date": None,
        "importance": "medium",
        "estimated_hours": "2.00",
        "status": "todo",
        "is_overwhelmed": False,
        "is_checked_by_llm": False,
        "llm_evaluation_failed": False,
        "completed_at": None,
        "created_at": "2026-10-01T09:00:00Z",
        "updated_at": "2026-10-01T09:00:00Z",
    }
    payload.update(overrides)
    return payload


def daily_plan_payload(**overrides) -> dict:
    """One `DailyPlanSerializer` payload."""
    payload = {
        "id": PLAN_ID,
        "date": TODAY.isoformat(),
        "tasks": [],
        "total_hours": "4.00",
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


def _stub_daily(respx_mock, plans, open_tasks=(), done_tasks=()):
    """Route the three reads the daily board makes.

    The `?status=done` route is registered **before** the bare collection route,
    because respx matches in registration order and a bare route would swallow
    the filtered one -- serving the whole backlog as the Done column, which
    looks like a passing board that happens to be full of open work.

    Returns the `?status=done` route so a test can assert on the query actually
    sent.
    """
    done_route = respx_mock.get(TASKS_URL, params={"status": "done"}).mock(
        return_value=httpx.Response(200, json=list(done_tasks))
    )
    respx_mock.get(DAILY_PLANS_URL).mock(
        return_value=httpx.Response(200, json=list(plans))
    )
    respx_mock.get(TASKS_URL).mock(
        return_value=httpx.Response(200, json=list(open_tasks))
    )
    respx_mock.get(GOALS_URL).mock(return_value=httpx.Response(200, json=[]))
    return done_route


def _board(respx_mock, open_tasks, done_tasks=()):
    return _stub_daily(
        respx_mock,
        plans=[
            daily_plan_payload(
                tasks=[*open_tasks, *done_tasks],
            )
        ],
        open_tasks=open_tasks,
        done_tasks=done_tasks,
    )


# --------------------------------------------------------------------------- #
# Board layout
# --------------------------------------------------------------------------- #
class TestBoardLayout:
    def test_three_columns_are_rendered_in_board_order(self, respx_mock, authed_client):
        with respx_mock:
            _board(respx_mock, [task_payload(status="todo")])
            body = authed_client.get("/tasks/daily/").data.decode()

        statuses = re.findall(r'data-column-status="([^"]+)"', main_content(body))
        assert statuses == ["todo", "doing", "done"]

    def test_each_task_lands_in_the_column_matching_its_status(
        self, respx_mock, authed_client
    ):
        """The board's only organising principle, so it is asserted per status.

        A `todo` task rendering in Doing would still put three cards on the page
        and pass a count-based test.
        """
        todo = task_payload(status="todo", name="Not started")
        doing = task_payload(status="doing", name="In progress")
        done = task_payload(
            status="done",
            name="Finished",
            completed_at=COMPLETED_AT,
        )
        with respx_mock:
            _board(respx_mock, [todo, doing], done_tasks=[done])
            body = authed_client.get("/tasks/daily/").data.decode()

        page = main_content(body)
        for column_status, name in (
            ("todo", "Not started"),
            ("doing", "In progress"),
            ("done", "Finished"),
        ):
            section = re.search(
                rf'data-column-status="{column_status}".*?(?=data-column-status=|</section>)',
                page,
                re.DOTALL,
            ).group(0)
            assert name in section, f"{name} is not in the {column_status} column"

    def test_a_finished_task_on_the_plan_reaches_the_done_column(
        self, respx_mock, authed_client
    ):
        """This is why the view reads the completed list at all.

        `get_backlog` drops `done` tasks by design, so a board built on the
        backlog alone would render a permanently empty Done column while the
        hours bar claimed hours were spent in it -- and a plan with a finished
        task would silently read as one fewer task.
        """
        done = task_payload(status="done", completed_at=COMPLETED_AT)
        with respx_mock:
            _board(respx_mock, [], done_tasks=[done])
            body = authed_client.get("/tasks/daily/").data.decode()

        page = main_content(body)
        done_column = re.search(
            r'data-column-status="done".*?(?=data-column-status=|</section>)',
            page,
            re.DOTALL,
        ).group(0)
        assert done["name"] in done_column

    def test_a_completed_read_that_fails_is_reported_not_shown_as_an_empty_day(
        self, respx_mock, authed_client
    ):
        """ "You finished nothing" and "we could not ask" are different facts.

        Collapsing them lets a backend outage look like a day where nothing got
        done, which is the one conclusion the user would be most confident in
        and most wrong about.
        """
        with respx_mock:
            respx_mock.get(TASKS_URL, params={"status": "done"}).mock(
                return_value=httpx.Response(500, json={"detail": "boom"})
            )
            respx_mock.get(DAILY_PLANS_URL).mock(
                return_value=httpx.Response(
                    200, json=[daily_plan_payload(tasks=[task_payload()])]
                )
            )
            respx_mock.get(TASKS_URL).mock(
                return_value=httpx.Response(200, json=[task_payload()])
            )
            respx_mock.get(GOALS_URL).mock(return_value=httpx.Response(200, json=[]))
            body = authed_client.get("/tasks/daily/").data.decode()

        page = main_content(body)
        assert 'data-testid="board-completed-unavailable"' in page
        # And the board still renders rather than the page 500ing: two thirds of
        # it loaded.
        assert page.count('data-testid="kanban-column"') == 3

    def test_the_columns_stack_on_small_screens(self, respx_mock, authed_client):
        """One layout rule, not a breakpoint branch.

        The board's responsive story is a single `grid-template-columns` that
        goes from one column to three at 768px. Asserted on the *CSS* rather
        than on Tailwind classes because this board's geometry is load-bearing
        and cannot depend on a CDN: the columns' height is what makes them drop
        targets, so a missing utility removes the ability to move a card while
        the board still looks like a board. `min-h-[6rem]` was exactly that
        failure -- it computed to `0px` and the empty columns stopped accepting
        drops without anything looking broken.

        Also asserts all three columns stay in the DOM at every width, so a
        future `display: none` below the breakpoint is caught here rather than
        shipping a board whose columns vanish on a phone.
        """
        with respx_mock:
            _board(respx_mock, [task_payload()])
            body = authed_client.get("/tasks/daily/").data.decode()

        page = main_content(body)
        css = re.search(r"\.kanban-board\s*\{[^}]*\}", page).group(0)
        assert "grid-template-columns: 1fr" in css
        assert "repeat(3, minmax(0, 1fr))" in page
        assert "min-width: 768px" in page
        # And the columns themselves are present, not hidden at a narrow width.
        assert page.count('data-testid="kanban-column"') == 3

    def test_the_page_has_exactly_one_alpine_scope(self, respx_mock, authed_client):
        """The board has one root, not one per column.

        Three `x-data="kanbanColumn(...)"` scopes would be three roots inside one
        page body -- the failure AGENTS.md rule 5 is written against -- and
        because the card count varies, per-card scopes would scale with it too.
        """
        with respx_mock:
            _board(respx_mock, [task_payload(status="todo")])
            body = authed_client.get("/tasks/daily/").data.decode()

        assert main_content(body).count("x-data=") == 1

    def test_each_card_carries_the_status_the_drag_validator_reads(
        self, respx_mock, authed_client
    ):
        """`data-status` on the card, `data-allowed-targets` on the column.

        `onMove` compares exactly these two, so their being wrong would silently
        make every drop either always-refused or always-allowed. Asserted from
        the rendered HTML because that is the contract between the template and
        the JavaScript, and neither can be changed without the other.
        """
        with respx_mock:
            _board(respx_mock, [task_payload(id=TASK_ID, status="todo")])
            body = authed_client.get("/tasks/daily/").data.decode()

        page = main_content(body)
        card = re.search(r"<article[^>]*data-board-card[^>]*>", page).group(0)
        assert f'data-task-id="{TASK_ID}"' in card
        assert 'data-status="todo"' in card

    def test_the_doing_column_refuses_a_todo_card(self, respx_mock, authed_client):
        """`doing -> todo` has no backend endpoint.

        A `todo` card may go to Doing or Done, so the Doing column's allowed
        targets are exactly those two -- and *not* `todo`. Without the exclusion
        the board would accept the drop and then revert it, which reads as a bug
        rather than as a rule.
        """
        with respx_mock:
            _board(respx_mock, [task_payload(status="todo")])
            body = authed_client.get("/tasks/daily/").data.decode()

        page = main_content(body)
        lists = dict(
            re.findall(
                r'data-status="([^"]+)"[^>]*data-accepted-sources="([^"]*)"', page
            )
        )
        # A column lists the *sources* it takes, not the destinations its own
        # cards have. So Doing accepts `todo` (you can start a task) and refuses
        # `done` (there is no un-complete endpoint), while Done accepts both
        # `todo` and `doing` (you can finish something you never started).
        assert lists == {
            "todo": "done",
            "doing": "todo",
            "done": "todo,doing",
        }

    def test_an_empty_column_still_accepts_cards(self, respx_mock, authed_client):
        """The last card must be draggable out of its column.

        If a column's acceptance were derived from the cards inside it, emptying
        it would make it accept nothing -- and the final drag out would be the
        only one that worked. This asserts the rule is a property of the
        destination, independent of its contents.
        """
        with respx_mock:
            _stub_daily(respx_mock, plans=[daily_plan_payload()])
            body = authed_client.get("/tasks/daily/").data.decode()

        page = main_content(body)
        lists = dict(
            re.findall(
                r'data-status="([^"]+)"[^>]*data-accepted-sources="([^"]*)"', page
            )
        )
        assert lists["done"] == "todo,doing"


class TestBoardWithoutAPlan:
    def test_an_unplanned_day_has_no_board_and_no_cards(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            _stub_daily(respx_mock, plans=[])
            body = authed_client.get("/tasks/daily/").data.decode()

        page = main_content(body)
        assert 'data-testid="empty-daily-plan"' in page
        assert 'data-testid="kanban-board"' not in page

    def test_a_planned_day_with_no_tasks_still_shows_three_columns(
        self, respx_mock, authed_client
    ):
        """A board with nothing in it is still a board.

        The columns carry the day's structure, and a plan that exists but is empty
        is a real state -- the user has tasks elsewhere in the backlog and has
        not scheduled any. Hiding the board would leave no way to drag one in.
        """
        with respx_mock:
            _stub_daily(respx_mock, plans=[daily_plan_payload()])
            body = authed_client.get("/tasks/daily/").data.decode()

        page = main_content(body)
        assert page.count('data-testid="kanban-column"') == 3
        assert page.count('data-testid="kanban-column-empty"') == 3


# --------------------------------------------------------------------------- #
# The move endpoint
# --------------------------------------------------------------------------- #
class TestMoveRoutesToTheRightEndpoint:
    @pytest.mark.parametrize(
        ("target", "action"),
        [
            ("doing", "mark-doing"),
            ("done", "mark-done"),
            ("todo", "reopen"),
        ],
    )
    def test_each_status_posts_to_its_own_backend_action(
        self, respx_mock, authed_client, target, action
    ):
        """The mapping *is* the feature.

        Asserted one target at a time and by URL, because a wrong mapping
        produces a page that looks completely correct: the card lands in the
        column the user dropped it into either way.
        """
        with respx_mock:
            _board(respx_mock, [task_payload(status="todo")])
            page = authed_client.get("/tasks/daily/").data.decode()
            token = _csrf_from(page)

            route = respx_mock.post(f"{BASE}/tasks/tasks/{TASK_ID}/{action}/").mock(
                return_value=httpx.Response(
                    200, json=task_payload(status=target, id=TASK_ID)
                )
            )
            response = authed_client.post(
                f"/tasks/{TASK_ID}/move/",
                data={
                    "csrf_token": token,
                    "status": target,
                },
                headers={"X-Requested-With": "fetch"},
            )

        assert route.called, f"status={target} did not post to {action}/"
        assert response.status_code == 200

    def test_the_transition_posts_no_body(self, respx_mock, authed_client):
        """`TaskViewSet.mark_doing` declares `request=None`.

        It reads no serializer, so there is no field to send. A BFF posting
        `{"status": "doing"}` would be inventing a contract the endpoint does not
        have -- and the day the backend started *rejecting* unknown fields, it
        would become a 400 on every drag.
        """
        with respx_mock:
            _board(respx_mock, [task_payload(status="todo")])
            page = authed_client.get("/tasks/daily/").data.decode()
            token = _csrf_from(page)

            route = respx_mock.post(f"{BASE}/tasks/tasks/{TASK_ID}/mark-doing/").mock(
                return_value=httpx.Response(
                    200, json=task_payload(status="doing", id=TASK_ID)
                )
            )
            authed_client.post(
                f"/tasks/{TASK_ID}/move/",
                data={"csrf_token": token, "status": "doing"},
                headers={"X-Requested-With": "fetch"},
            )

        assert route.calls.last.request.read() == b"{}"


class TestMoveResponseContract:
    def _move(self, respx_mock, authed_client, status, action="mark-doing"):
        _board(respx_mock, [task_payload(status="todo")])
        page = authed_client.get("/tasks/daily/").data.decode()
        token = _csrf_from(page)
        route = respx_mock.post(f"{BASE}/tasks/tasks/{TASK_ID}/{action}/").mock(
            return_value=httpx.Response(
                status,
                json=task_payload(status="doing", id=TASK_ID),
            )
        )
        response = authed_client.post(
            f"/tasks/{TASK_ID}/move/",
            data={"csrf_token": token, "status": "doing"},
            headers={"X-Requested-With": "fetch"},
        )
        return route, response

    def test_success_is_200_with_the_backend_s_own_status(
        self, respx_mock, authed_client
    ):
        """The handler re-stamps the card from this, not from the drag.

        `completed_at` in particular is a server fact: echoing the drop's target
        would leave a Done card claiming a completion time the backend never set.
        """
        with respx_mock:
            _, response = self._move(respx_mock, authed_client, 200)

        body = response.get_json()
        assert response.status_code == 200
        assert body["ok"] is True
        assert body["status"] == "doing"

    def test_a_refused_transition_is_409_and_carries_the_backends_wording(
        self, respx_mock, authed_client
    ):
        """409, not 400, and not a 200 carrying a flag.

        The drag handler decides whether to put the card back from the status
        code alone, so the two failure kinds have to be distinguishable by
        status. And the message is the backend's own: two copies of a transition
        rule exist, a stale board can disagree with either, and a message
        invented here would claim a cause the BFF cannot know.
        """
        with respx_mock:
            _board(respx_mock, [task_payload(status="todo")])
            page = authed_client.get("/tasks/daily/").data.decode()
            token = _csrf_from(page)

            respx_mock.post(f"{BASE}/tasks/tasks/{TASK_ID}/mark-doing/").mock(
                return_value=httpx.Response(
                    409,
                    json={"detail": "A completed task cannot be moved back to doing."},
                )
            )
            response = authed_client.post(
                f"/tasks/{TASK_ID}/move/",
                data={"csrf_token": token, "status": "doing"},
                headers={"X-Requested-With": "fetch"},
            )

        assert response.status_code == 409
        body = response.get_json()
        assert body["ok"] is False
        assert body["message"] == ("A completed task cannot be moved back to doing.")

    def test_an_unknown_status_is_400_before_any_request(
        self, respx_mock, authed_client
    ):
        """No round trip for input the BFF can reject on its own rules.

        And it is a `DomainException`, so the view catches it and flashes -- a
        bare `ValueError` from `TaskStatus(raw)` would not be caught by
        `except DomainException` and would be a 500.
        """
        with respx_mock:
            _board(respx_mock, [task_payload(status="todo")])
            page = authed_client.get("/tasks/daily/").data.decode()
            token = _csrf_from(page)

            response = authed_client.post(
                f"/tasks/{TASK_ID}/move/",
                data={"csrf_token": token, "status": "archived"},
                headers={"X-Requested-With": "fetch"},
            )
            task_reads = [
                c
                for c in respx_mock.calls
                if c.request.url.path.endswith(f"/{TASK_ID}/mark-doing/")
            ]

        assert response.status_code == 400
        assert response.get_json()["ok"] is False
        assert not task_reads

    def test_a_missing_task_is_404(self, respx_mock, authed_client):
        """The card was on screen and no longer exists.

        Not 400 and not 200: the drag handler's revert path and the "the board is
        stale, reload" message both key off this being distinguishable.
        """
        with respx_mock:
            _board(respx_mock, [task_payload(status="todo")])
            page = authed_client.get("/tasks/daily/").data.decode()
            token = _csrf_from(page)

            respx_mock.post(f"{BASE}/tasks/tasks/{TASK_ID}/mark-doing/").mock(
                return_value=httpx.Response(404, json={"detail": "Not found."})
            )
            response = authed_client.post(
                f"/tasks/{TASK_ID}/move/",
                data={"csrf_token": token, "status": "doing"},
                headers={"X-Requested-With": "fetch"},
            )

        assert response.status_code == 404

    def test_a_bad_csrf_token_changes_nothing(self, respx_mock, authed_client):
        with respx_mock:
            _board(respx_mock, [task_payload(status="todo")])
            route = respx_mock.post(f"{BASE}/tasks/tasks/{TASK_ID}/mark-doing/").mock(
                return_value=httpx.Response(200, json=task_payload())
            )

            response = authed_client.post(
                f"/tasks/{TASK_ID}/move/",
                data={"csrf_token": "wrong", "status": "doing"},
                headers={"X-Requested-With": "fetch"},
            )

        assert not route.called
        assert response.status_code == 400

    def test_it_is_post_only(self, respx_mock, authed_client):
        """A GET that moved a card would be triggerable by any prefetch."""
        with respx_mock:
            response = authed_client.get(f"/tasks/{TASK_ID}/move/")

        assert response.status_code == 405


class TestMoveWithoutJavaScript:
    """The card's select-and-button is the baseline, not a `<noscript>` fallback.

    Drag and drop needs a pointer: a keyboard user cannot perform one, and a
    screen reader announces nothing about it. So the non-pointer path is present
    for everyone, posts the same route, and gets a flash.
    """

    def test_the_form_post_redirects_with_a_flash(self, respx_mock, authed_client):
        with respx_mock:
            _board(respx_mock, [task_payload(status="todo")])
            page = authed_client.get("/tasks/daily/").data.decode()
            token = _csrf_from(page)

            respx_mock.post(f"{BASE}/tasks/tasks/{TASK_ID}/mark-doing/").mock(
                return_value=httpx.Response(
                    200, json=task_payload(status="doing", id=TASK_ID)
                )
            )
            response = authed_client.post(
                f"/tasks/{TASK_ID}/move/",
                data={"csrf_token": token, "status": "doing"},
                follow_redirects=True,
            )

        assert response.status_code == 200
        assert "moved to Doing" in response.data.decode()

    def test_every_card_has_at_least_one_destination(self, respx_mock, authed_client):
        """Every status has somewhere to go, so no card is ever immobile.

        The matrix's two gaps (`doing -> todo`, `done -> doing`) do not leave any
        column stranded, because each status still has one legal exit: `todo` to
        Doing or Done, `doing` to Done, `done` to To Do. Asserted because it is
        what makes the card's control unconditional -- a template branch for "no
        legal moves" would be unreachable code guarding against a state the
        matrix cannot produce.
        """
        for status in ("todo", "doing", "done"):
            # A `done` task has to be seeded on the completed read: the adapter's
            # `get_backlog` drops `done` by design, so putting one there is the
            # same mistake the board would make.
            done = [task_payload(status="done", completed_at=COMPLETED_AT)]
            with respx_mock:
                if status == "done":
                    _board(respx_mock, [], done_tasks=done)
                else:
                    _board(respx_mock, [task_payload(status=status)])
                body = authed_client.get("/tasks/daily/").data.decode()

            page = main_content(body)
            assert page.count('data-testid="kanban-card-move-select"') == 1, status
            assert 'data-testid="kanban-card-no-moves"' not in page

    def test_a_doing_card_offers_only_done(self, respx_mock, authed_client):
        """The one-column-options case.

        `doing -> todo` has no endpoint, so a Doing card's select has exactly one
        option. This is where the missing transition becomes visible to a user
        who is not dragging.
        """
        with respx_mock:
            _board(respx_mock, [task_payload(status="doing")])
            body = authed_client.get("/tasks/daily/").data.decode()

        select = re.search(
            r"<select[^>]*data-testid=\"kanban-card-move-select\".*?</select>",
            main_content(body),
            re.DOTALL,
        ).group(0)
        assert re.findall(r'<option value="([^"]*)"', select) == ["", "done"]

    def test_a_todo_card_offers_doing_and_done_and_nothing_else(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            _board(respx_mock, [task_payload(status="todo")])
            body = authed_client.get("/tasks/daily/").data.decode()

        select = re.search(
            r"<select[^>]*data-testid=\"kanban-card-move-select\".*?</select>",
            main_content(body),
            re.DOTALL,
        ).group(0)
        options = re.findall(r'<option value="([^"]*)"', select)
        # The leading empty option is the "Move to…" placeholder: submitting it
        # unchanged is not a move, and the select starts on it so the user has to
        # choose a destination deliberately rather than pressing Move by reflex.
        assert options == ["", "doing", "done"]

    def test_a_done_card_offers_only_reopening(self, respx_mock, authed_client):
        with respx_mock:
            _board(
                respx_mock,
                [],
                done_tasks=[task_payload(status="done", completed_at=COMPLETED_AT)],
            )
            body = authed_client.get("/tasks/daily/").data.decode()

        select = re.search(
            r"<select[^>]*data-testid=\"kanban-card-move-select\".*?</select>",
            main_content(body),
            re.DOTALL,
        ).group(0)
        assert re.findall(r'<option value="([^"]*)"', select) == ["", "todo"]
