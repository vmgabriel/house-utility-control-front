"""E2E tests for daily-plan generation, plan scheduling, and goal progress.

These drive a real browser against the in-process `StubDRFServer`, so they cover
what an `httpx`-mocked integration test cannot: that a form actually submits to
the URL the page rendered, that a click navigates, and that the stub's answers
match the real backend's status codes closely enough that a page which looks
right here is right there too.

The DRF boundary is stubbed rather than `page.route()`d, per the module note in
`drf_stub.py`: every DRF call is made by Flask, never by the browser.

Three contract details from `src/apps/tasks/interfaces/{serializers,views}.py`
shape everything below, and the stub reproduces each of them:

* `POST /daily-plans/generate/` answers **202** with a queued body and no plan.
* `POST /daily-plans/<uuid:pk>/add-task/` is keyed on a **plan UUID**, answers
  **409** for three distinct reasons, and echoes the reloaded plan.
* A goal's tasks come from `GET /tasks/?goal_id=`, not from a nested route.

**The daily plan is dated with the real current date.** Unlike the integration
suite, nothing here freezes the clock -- the live server runs the real
`SystemClock`, and the daily page decides whether to offer the generate button
by asking whether the date it is showing *is* today. A hard-coded date would
make the button vanish on the day the suite ran, and the failure would read as
"the button is broken" rather than "the fixture is stale".
"""

from __future__ import annotations

from datetime import date

from playwright.sync_api import Page, expect

from tests.e2e.support import expect_path

GOAL_ID = "22222222-2222-2222-2222-222222222222"
PLAN_ID = "44444444-4444-4444-4444-444444444444"
TASK_ID = "11111111-1111-1111-1111-111111111111"

TODAY = date.today().isoformat()

OPEN_TASK = {
    "id": TASK_ID,
    "goal_id": GOAL_ID,
    "name": "Draft the quarterly report",
    "description": "",
    "due_date": None,
    "importance": "high",
    "estimated_hours": "2.50",
    "status": "todo",
    "is_overwhelmed": False,
    "is_checked_by_llm": False,
    "llm_evaluation_failed": False,
    "completed_at": None,
    "created_at": "2026-10-01T09:00:00Z",
    "updated_at": "2026-10-01T09:00:00Z",
}

DONE_TASK = {
    **OPEN_TASK,
    "id": "33333333-3333-3333-3333-333333333333",
    "name": "Shipped the release",
    "status": "done",
    "estimated_hours": "1.50",
    "completed_at": "2026-10-04T18:00:00Z",
}

GOAL = {
    "id": GOAL_ID,
    "name": "Ship the thing",
    "description": "## Scope",
    "due_date": None,
    "status": "active",
    "task_count": 2,
}


def seed(mock_drf, tasks: list[dict], goals: list[dict] | None = None) -> None:
    """Put rows on the stub for the backlog, completed, and goals reads."""
    mock_drf.seed_tasks(*tasks, goals=goals if goals is not None else [])


def seed_today_plan(mock_drf, tasks: list[dict]) -> None:
    """Put today's plan on the stub, holding `tasks`."""
    mock_drf.seed_daily_plans(
        {
            "id": PLAN_ID,
            "date": TODAY,
            "tasks": tasks,
            "total_hours": "0.00",
            "is_full": False,
            "generated_by_llm": False,
            "created_at": "2026-10-05T06:00:00Z",
        }
    )


def no_plans(mock_drf) -> None:
    """Make every day unplanned."""
    mock_drf.seed_daily_plans()


class TestGenerateDailyPlan:
    def test_the_button_queues_a_plan_for_an_unplanned_today(
        self, page: Page, authed: Page, mock_drf
    ):
        seed(mock_drf, [OPEN_TASK])
        no_plans(mock_drf)
        page.goto("/tasks/daily/")

        page.get_by_test_id("generate-daily-plan-button").click()

        calls = mock_drf.calls("POST", "/tasks/daily-plans/generate/")
        assert len(calls) == 1
        assert calls[0].json == {"date": TODAY}

    def test_it_says_queued_and_not_generated(self, page: Page, authed: Page, mock_drf):
        """The endpoint answers 202; the work is done by a worker afterwards.

        "Your daily plan is ready" would be a claim the response does not
        support, and the user would find out when no plan appeared.
        """
        seed(mock_drf, [OPEN_TASK])
        no_plans(mock_drf)
        page.goto("/tasks/daily/")

        page.get_by_test_id("generate-daily-plan-button").click()

        expect(page.get_by_text("Daily plan queued")).to_be_visible()
        expect(page.get_by_text("Daily plan generated")).to_have_count(0)

    def test_it_is_not_offered_once_the_day_has_a_plan(
        self, page: Page, authed: Page, mock_drf
    ):
        seed(mock_drf, [OPEN_TASK])
        seed_today_plan(mock_drf, [OPEN_TASK])
        page.goto("/tasks/daily/")

        expect(page.get_by_test_id("generate-daily-plan-button")).to_have_count(0)

    def test_an_unreachable_provider_is_a_message_and_still_a_page(
        self, page: Page, authed: Page, mock_drf
    ):
        seed(mock_drf, [OPEN_TASK])
        no_plans(mock_drf)
        mock_drf.llm_available = False
        page.goto("/tasks/daily/")

        page.get_by_test_id("generate-daily-plan-button").click()

        expect(page.get_by_text("AI service is unavailable")).to_be_visible()
        # Still the daily page, not a 503 page: the AI is optional, and taking
        # the page down would deny the user the view they came from.
        expect_path(page, "/tasks/daily/")


class TestAddTaskToTodaysPlan:
    def test_the_button_schedules_the_task_and_reports_the_new_total(
        self, page: Page, authed: Page, mock_drf
    ):
        seed(mock_drf, [OPEN_TASK])
        seed_today_plan(mock_drf, [])
        page.goto("/tasks/")

        page.get_by_test_id("add-to-plan-button").click()

        add_calls = mock_drf.calls("POST", f"/tasks/daily-plans/{PLAN_ID}/add-task/")
        assert len(add_calls) == 1
        assert add_calls[0].json == {"task_id": TASK_ID}
        expect(page.get_by_text("Added to today's plan")).to_be_visible()

    def test_the_day_is_opened_when_it_has_none_yet(
        self, page: Page, authed: Page, mock_drf
    ):
        """`add-task` is keyed on a plan UUID, so an unplanned day has none to
        post to. `GetOrCreateDailyPlan` is the backend's own answer, and the
        alternative -- hiding the button -- would leave it missing on most days.
        """
        seed(mock_drf, [OPEN_TASK])
        no_plans(mock_drf)
        page.goto("/tasks/")

        page.get_by_test_id("add-to-plan-button").click()

        creates = mock_drf.calls("POST", "/tasks/daily-plans/")
        assert len(creates) == 1
        assert creates[0].json == {"date": TODAY}
        # ...and the task then went onto the plan that create returned, whose id
        # is whatever the backend minted -- so it is matched by shape.
        add_calls = [
            r
            for r in mock_drf.requests
            if r.method == "POST" and r.path.endswith("/add-task/")
        ]
        assert len(add_calls) == 1
        assert add_calls[0].json == {"task_id": TASK_ID}

    def test_a_full_day_shows_the_backend_s_own_reason(
        self, page: Page, authed: Page, mock_drf
    ):
        """Three 409s -- already scheduled, day full, already done -- and the
        backend is the only thing that knows which one applies. Its `detail` is
        carried through rather than replaced by a message of our own.
        """
        seed(mock_drf, [OPEN_TASK])
        seed_today_plan(mock_drf, [])
        mock_drf.add_daily_plan_task_conflict = "full"
        page.goto("/tasks/")

        page.get_by_test_id("add-to-plan-button").click()

        expect(page.get_by_text("The day is already full.")).to_be_visible()

    def test_an_already_scheduled_task_says_so(
        self, page: Page, authed: Page, mock_drf
    ):
        seed(mock_drf, [OPEN_TASK])
        seed_today_plan(mock_drf, [OPEN_TASK])
        page.goto("/tasks/")

        page.get_by_test_id("add-to-plan-button").click()

        expect(page.get_by_text("This task is already on the plan.")).to_be_visible()

    def test_a_finished_task_is_not_offered_the_action(
        self, page: Page, authed: Page, mock_drf
    ):
        """The backend answers 409 `TaskAlreadyDone` for one, and a button that
        is always present but always fails is worse than no button.
        """
        seed(mock_drf, [OPEN_TASK, DONE_TASK])
        page.goto("/tasks/")

        expect(page.get_by_test_id("add-to-plan-button")).to_have_count(1)


class TestGoalDetail:
    def test_clicking_a_card_navigates_to_that_goal(
        self, page: Page, authed: Page, mock_drf
    ):
        seed(mock_drf, [OPEN_TASK, DONE_TASK], goals=[GOAL])
        page.goto("/tasks/goals/")

        page.get_by_test_id("goal-card").click()

        expect_path(page, f"/tasks/goals/{GOAL_ID}/")
        expect(page.get_by_test_id("goal-detail-name")).to_have_text("Ship the thing")

    def test_the_progress_bar_reports_the_share_of_hours_that_are_done(
        self, page: Page, authed: Page, mock_drf
    ):
        """1.5h done of a 4h goal -> 37%.

        Truncated, not rounded: a bar that overshot would be claiming more
        progress than the tasks have earned.
        """
        seed(mock_drf, [OPEN_TASK, DONE_TASK], goals=[GOAL])
        page.goto(f"/tasks/goals/{GOAL_ID}/")

        expect(page.get_by_test_id("goal-progress-percentage")).to_have_text("37%")
        expect(page.get_by_test_id("goal-progress-hours")).to_contain_text("1.5h of 4h")

    def test_the_finished_task_is_badged_and_dated(
        self, page: Page, authed: Page, mock_drf
    ):
        """The badge reads the same field the bar sums over, so the two cannot
        disagree about which tasks count.
        """
        seed(mock_drf, [OPEN_TASK, DONE_TASK], goals=[GOAL])
        page.goto(f"/tasks/goals/{GOAL_ID}/")

        expect(page.get_by_test_id("goal-task-completed-badge")).to_have_count(1)
        expect(page.get_by_test_id("goal-task-completed-at")).to_contain_text(
            "2026-10-04"
        )

    def test_a_goal_with_no_tasks_says_it_is_unmeasured(
        self, page: Page, authed: Page, mock_drf
    ):
        """ "0h of 0h complete" reads as a measurement of zero progress; a goal
        with nothing filed against it has not been measured at all.
        """
        seed(mock_drf, [], goals=[{**GOAL, "task_count": 0}])
        page.goto(f"/tasks/goals/{GOAL_ID}/")

        expect(page.get_by_test_id("goal-progress-unmeasured")).to_be_visible()
        expect(page.get_by_test_id("goal-detail-empty")).to_be_visible()

    def test_an_unknown_goal_lands_back_on_the_goal_list(
        self, page: Page, authed: Page, mock_drf
    ):
        seed(mock_drf, [], goals=[GOAL])
        page.goto("/tasks/goals/99999999-9999-9999-9999-999999999999/")

        expect_path(page, "/tasks/goals/")
        expect(page.get_by_text("That goal no longer exists.")).to_be_visible()

    def test_the_task_read_is_scoped_to_the_goal(
        self, page: Page, authed: Page, mock_drf
    ):
        """`?goal_id=` with no status filter.

        A status filter here would drop the finished tasks -- half the list and
        the whole numerator of the bar -- and the stub honours the two filters
        independently, so a client that sent one would visibly lose rows.
        """
        seed(mock_drf, [OPEN_TASK, DONE_TASK], goals=[GOAL])
        page.goto(f"/tasks/goals/{GOAL_ID}/")

        goal_reads = [
            r
            for r in mock_drf.requests
            if r.path == "/tasks/tasks/" and r.query.get("goal_id")
        ]
        assert goal_reads
        assert goal_reads[0].query == {"goal_id": GOAL_ID}
