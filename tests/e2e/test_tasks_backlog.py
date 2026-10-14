"""E2E tests for the tasks backlog's row actions and completed-tasks section.

These drive a real browser against the in-process `StubDRFServer`, so they cover
what an `httpx`-mocked integration test cannot: that Alpine actually populates
the edit modal from a row's data attributes, that the modal's form submits to
the URL the row implies, and that a badge's `href` is a page that exists.

The DRF boundary is stubbed rather than `page.route()`d, per the module note in
`drf_stub.py`: every DRF call is made by Flask, never by the browser, so a
Playwright route would mock nothing while the suite appeared to pass.

Nothing here exercises a "last 7 days" window or a Reopen action. Neither is
expressible against the current backend -- there is no `completed_at` on
`TaskModel` or `TaskSerializer`, and `Task.mark_as_doing` refuses a finished task
with `TaskAlreadyDone`. `TestAbsentBackendFeatures` records that as an absence
so the gap is visible and breaks loudly if the backend gains the capability.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.e2e.drf_stub import StubResponse
from tests.e2e.support import expect_path

#: A real `TaskSerializer` payload. `goal_id` is a UUID because
#: `TaskSerializer` declares it as a `UUIDField`, and the BFF wraps it in a
#: validating `GoalId`.
TASK_ID = "11111111-1111-1111-1111-111111111111"
GOAL_ID = "22222222-2222-2222-2222-222222222222"

OPEN_TASK = {
    "id": TASK_ID,
    "goal_id": GOAL_ID,
    "name": "Draft the quarterly report",
    "description": "## Sections",
    "due_date": "2026-11-01",
    "importance": "high",
    "estimated_hours": "2.50",
    "status": "todo",
    "is_overwhelmed": False,
    "is_checked_by_llm": False,
    "llm_evaluation_failed": False,
    "created_at": "2026-10-01T09:00:00Z",
    "updated_at": "2026-10-01T09:00:00Z",
}

DONE_TASK = {
    **OPEN_TASK,
    "id": "33333333-3333-3333-3333-333333333333",
    "goal_id": None,
    "name": "Shipped the release",
    "status": "done",
    "estimated_hours": "0.00",
}


def seed(mock_drf, tasks: list[dict], goals: list[dict] | None = None) -> None:
    """Put rows on the stub for the backlog, completed, and goals reads.

    Both task lists come from one collection endpoint: the backlog reads it bare
    and the completed section reads it with `?status=done`, so seeding the same
    rows for both is what makes the two sections show different subsets of one
    dataset.
    """
    mock_drf.seed_tasks(*tasks, goals=goals or [])


def task_row(page: Page, name: str):
    return page.locator('[data-testid="task-row"]', has_text=name)


class TestEditModal:
    def test_the_edit_button_opens_a_modal_prefilled_from_the_row(
        self, page: Page, authed: Page, mock_drf
    ):
        seed(mock_drf, [OPEN_TASK])
        page.goto("/tasks/")

        task_row(page, "Draft the quarterly report").get_by_role(
            "button", name="Edit"
        ).click()

        modal = page.locator('[data-testid="edit-task-form"]')
        expect(modal).to_be_visible()
        expect(modal.locator('input[name="name"]')).to_have_value(
            "Draft the quarterly report"
        )
        expect(modal.locator('textarea[name="description"]')).to_have_value(
            "## Sections"
        )
        expect(modal.locator('input[name="due_date"]')).to_have_value("2026-11-01")
        expect(modal.locator('select[name="priority"]')).to_have_value("high")
        # A bare number: the input is `type="number"` and the browser would
        # reject a prefilled "2.5h" outright. Two decimals, not the label's
        # "2.5h" and not the normalized form "1E+1".
        expect(modal.locator('input[name="estimated_hours"]')).to_have_value("2.50")

    def test_the_modal_is_hidden_until_a_row_is_pressed(
        self, page: Page, authed: Page, mock_drf
    ):
        seed(mock_drf, [OPEN_TASK])
        page.goto("/tasks/")

        expect(page.locator('[data-testid="edit-task-form"]')).not_to_be_visible()

    def test_pressing_a_second_row_replaces_the_first_rows_values(
        self, page: Page, authed: Page, mock_drf
    ):
        """One modal serves every row, so stale state would show the previous
        task's name -- and saving would then rename the wrong task."""
        second = {
            **OPEN_TASK,
            "id": "44444444-4444-4444-4444-444444444444",
            "name": "Buy milk",
            "description": "Semi skimmed",
            "importance": "low",
            "estimated_hours": "0.25",
        }
        seed(mock_drf, [OPEN_TASK, second])
        page.goto("/tasks/")

        task_row(page, "Draft the quarterly report").get_by_role(
            "button", name="Edit"
        ).click()
        task_row(page, "Buy milk").get_by_role("button", name="Edit").click()

        modal = page.locator('[data-testid="edit-task-form"]')
        expect(modal.locator('input[name="name"]')).to_have_value("Buy milk")
        expect(modal.locator('textarea[name="description"]')).to_have_value(
            "Semi skimmed"
        )
        expect(modal.locator('select[name="priority"]')).to_have_value("low")

    def test_escape_closes_the_modal(self, page: Page, authed: Page, mock_drf):
        seed(mock_drf, [OPEN_TASK])
        page.goto("/tasks/")

        task_row(page, "Draft the quarterly report").get_by_role(
            "button", name="Edit"
        ).click()
        expect(page.locator('[data-testid="edit-task-form"]')).to_be_visible()

        page.keyboard.press("Escape")
        expect(page.locator('[data-testid="edit-task-form"]')).not_to_be_visible()

    def test_a_task_name_containing_quotes_is_handled_safely(
        self, page: Page, authed: Page, mock_drf
    ):
        """The values travel as HTML attributes read back through `$el.dataset`,
        not interpolated into an `x-data` expression. A name built to break out
        of a JS string literal must therefore arrive as literal text."""
        hostile = {
            **OPEN_TASK,
            "name": "Robert'); alert(1); //",
            "description": "'; fetch('/x'); //",
        }
        seed(mock_drf, [hostile])
        page.goto("/tasks/")

        task_row(page, "alert(1)").get_by_role("button", name="Edit").click()

        expect(
            page.locator('[data-testid="edit-task-form"] input[name="name"]')
        ).to_have_value("Robert'); alert(1); //")
        # Nothing ran: a dialog would still be open waiting for an answer.
        expect(page.locator('[data-testid="edit-task-form"]')).to_be_visible()

    def test_saving_sends_a_patch_to_the_row_it_opened(
        self, page: Page, authed: Page, mock_drf
    ):
        seed(mock_drf, [OPEN_TASK])
        page.goto("/tasks/")

        task_row(page, "Draft the quarterly report").get_by_role(
            "button", name="Edit"
        ).click()
        modal = page.locator('[data-testid="edit-task-form"]')
        modal.locator('input[name="name"]').fill("Renamed")
        modal.get_by_role("button", name="Save changes").click()

        expect(page.get_by_text("Task updated")).to_be_visible()
        patch = [
            r
            for r in mock_drf.requests
            if r.method == "PATCH" and r.path == f"/tasks/tasks/{TASK_ID}/"
        ]
        assert patch, "the edit did not PATCH the task it was opened from"
        assert patch[0].json["name"] == "Renamed"

    def test_the_patch_carries_only_the_five_updatable_fields(
        self, page: Page, authed: Page, mock_drf
    ):
        """`UpdateTaskSerializer` accepts these five and nothing else. Sending
        `goal_id` or `status` would be a field the backend discards."""
        seed(mock_drf, [OPEN_TASK])
        page.goto("/tasks/")

        task_row(page, "Draft the quarterly report").get_by_role(
            "button", name="Edit"
        ).click()
        page.locator('[data-testid="edit-task-form"]').get_by_role(
            "button", name="Save changes"
        ).click()

        patch = [
            r
            for r in mock_drf.requests
            if r.method == "PATCH" and r.path == f"/tasks/tasks/{TASK_ID}/"
        ][0]
        assert set(patch.json) == {
            "name",
            "description",
            "due_date",
            "importance",
            "estimated_hours",
        }

    def test_clearing_the_due_date_sends_an_explicit_null(
        self, page: Page, authed: Page, mock_drf
    ):
        """PATCH is applied with `partial=True`, where an absent key means "leave
        it alone". Clearing therefore has to be spelled as `null`."""
        seed(mock_drf, [OPEN_TASK])
        page.goto("/tasks/")

        task_row(page, "Draft the quarterly report").get_by_role(
            "button", name="Edit"
        ).click()
        modal = page.locator('[data-testid="edit-task-form"]')
        modal.locator('input[name="due_date"]').fill("")
        modal.get_by_role("button", name="Save changes").click()

        patch = [
            r
            for r in mock_drf.requests
            if r.method == "PATCH" and r.path == f"/tasks/tasks/{TASK_ID}/"
        ][0]
        assert "due_date" in patch.json
        assert patch.json["due_date"] is None


class TestDeleteFlow:
    def test_the_confirmation_names_the_task_before_deleting_anything(
        self, page: Page, authed: Page, mock_drf
    ):
        seed(mock_drf, [OPEN_TASK])
        page.goto("/tasks/")

        task_row(page, "Draft the quarterly report").get_by_role(
            "button", name="Delete"
        ).click()

        expect(page.get_by_test_id("delete-task-name")).to_have_text(
            "Draft the quarterly report"
        )
        # Opening the dialog must not have deleted it yet.
        assert not [r for r in mock_drf.requests if r.method == "DELETE"]

    def test_cancelling_deletes_nothing(self, page: Page, authed: Page, mock_drf):
        seed(mock_drf, [OPEN_TASK])
        page.goto("/tasks/")

        task_row(page, "Draft the quarterly report").get_by_role(
            "button", name="Delete"
        ).click()
        page.get_by_test_id("delete-task-cancel").click()

        expect(page.get_by_test_id("delete-task-name")).not_to_be_visible()
        assert not [r for r in mock_drf.requests if r.method == "DELETE"]

    def test_confirming_deletes_the_row_and_flashes(
        self, page: Page, authed: Page, mock_drf
    ):
        seed(mock_drf, [OPEN_TASK])
        page.goto("/tasks/")

        task_row(page, "Draft the quarterly report").get_by_role(
            "button", name="Delete"
        ).click()
        page.get_by_test_id("confirm-delete-submit").click()

        expect(page.get_by_text("Task deleted.")).to_be_visible()
        deletes = [r for r in mock_drf.requests if r.method == "DELETE"]
        assert [r.path for r in deletes] == [f"/tasks/tasks/{TASK_ID}/"]

    def test_a_task_that_is_already_gone_is_not_reported_as_deleted(
        self, page: Page, authed: Page, mock_drf
    ):
        """The row the user confirmed had already disappeared, which means their
        view was stale. "Task deleted" would hide that."""
        seed(mock_drf, [OPEN_TASK])
        # `"*"` rather than `"DELETE"`: `StubBackend.dispatch` resolves the task
        # detail route by looking up the wildcard key, so an override registered
        # under a concrete method would silently never be reached.
        mock_drf.on_call(
            "*",
            "/tasks/tasks/<id>/",
            lambda _r: StubResponse(404, {"detail": "Not found."}),
        )
        page.goto("/tasks/")

        task_row(page, "Draft the quarterly report").get_by_role(
            "button", name="Delete"
        ).click()
        page.get_by_test_id("confirm-delete-submit").click()

        expect(page.get_by_text("That task no longer exists.")).to_be_visible()
        expect(page.get_by_text("Task deleted.")).not_to_be_visible()


class TestDerivedFromGoalBadge:
    def test_it_appears_only_for_a_task_filed_under_a_goal(
        self, page: Page, authed: Page, mock_drf
    ):
        seed(
            mock_drf,
            [
                OPEN_TASK,
                {
                    **DONE_TASK,
                    "id": "55555555-5555-5555-5555-555555555555",
                    "goal_id": None,
                },
            ],
        )
        page.goto("/tasks/")

        expect(
            task_row(page, "Draft the quarterly report").get_by_test_id(
                "derived-from-goal-badge"
            )
        ).to_be_visible()
        expect(
            page.locator(
                '[data-testid="task-row"]', has_text="Shipped the release"
            ).get_by_test_id("derived-from-goal-badge")
        ).to_have_count(0)

    def test_clicking_it_lands_on_the_goals_page(
        self, page: Page, authed: Page, mock_drf
    ):
        """It points at `/tasks/goals/`, which exists -- not `/goals/<id>`, which
        this BFF has no route for and would 404."""
        seed(
            mock_drf,
            [OPEN_TASK],
            goals=[
                {
                    "id": GOAL_ID,
                    "name": "Ship v2",
                    "description": "",
                    "due_date": None,
                    "status": "active",
                    "task_count": 1,
                }
            ],
        )
        page.goto("/tasks/")

        page.get_by_test_id("derived-from-goal-badge").first.click()

        expect_path(page, "/tasks/goals/")
        expect(page.locator("h1")).to_have_text("Goals")


class TestCompletedTasksSection:
    def test_finished_tasks_are_listed_separately_from_the_backlog(
        self, page: Page, authed: Page, mock_drf
    ):
        seed(mock_drf, [OPEN_TASK, DONE_TASK])
        page.goto("/tasks/")

        expect(page.get_by_test_id("completed-task-row")).to_have_count(1)
        expect(
            page.get_by_test_id("completed-task-row").get_by_text("Shipped the release")
        ).to_be_visible()

    def test_a_finished_task_is_not_also_in_the_backlog(
        self, page: Page, authed: Page, mock_drf
    ):
        """The two sections have to partition the task list.

        The backend's `status` filter takes a single value, so `get_backlog`
        cannot ask for "not done" over the wire and drops `done` rows itself.
        Without that, a finished task appears twice on one page -- once as work,
        once as history -- and the counts stop meaning anything.
        """
        seed(mock_drf, [OPEN_TASK, DONE_TASK])
        page.goto("/tasks/")

        expect(page.get_by_test_id("task-row")).to_have_count(1)
        expect(page.get_by_test_id("task-row")).to_contain_text(
            "Draft the quarterly report"
        )
        # One occurrence of the finished task's name, in the history section.
        expect(page.get_by_text("Shipped the release")).to_have_count(1)

    def test_the_read_filters_on_done(self, page: Page, authed: Page, mock_drf):
        """`TASK_STATUS_CHOICES` is todo/doing/done. `completed` is a 400, which
        the stub reproduces so a wrong value fails here rather than appearing to
        work."""
        seed(mock_drf, [OPEN_TASK, DONE_TASK])
        page.goto("/tasks/")

        filtered = [
            r
            for r in mock_drf.requests
            if r.path == "/tasks/tasks/" and r.method == "GET" and r.query.get("status")
        ]
        assert [r.query["status"] for r in filtered] == ["done"]

    def test_the_heading_makes_no_claim_about_a_window(
        self, page: Page, authed: Page, mock_drf
    ):
        """The product asked for "last 7 days". There is no `completed_at` to
        window on, so the section says what it can honestly say."""
        seed(mock_drf, [OPEN_TASK, DONE_TASK])
        page.goto("/tasks/")

        section = page.locator(
            "section", has=page.get_by_test_id("completed-task-list")
        )
        expect(section).to_contain_text("Completed tasks")
        expect(section).not_to_contain_text("7 days")
        expect(section).not_to_contain_text("week")

    def test_an_empty_history_shows_a_friendly_message(
        self, page: Page, authed: Page, mock_drf
    ):
        seed(mock_drf, [OPEN_TASK])
        page.goto("/tasks/")

        expect(page.get_by_test_id("completed-empty")).to_contain_text(
            "No recently completed tasks."
        )

    def test_a_finished_task_can_be_deleted_from_the_section(
        self, page: Page, authed: Page, mock_drf
    ):
        seed(mock_drf, [OPEN_TASK, DONE_TASK])
        page.goto("/tasks/")

        page.get_by_test_id("completed-delete-button").click()
        expect(page.get_by_test_id("delete-task-name")).to_have_text(
            "Shipped the release"
        )
        page.get_by_test_id("confirm-delete-submit").click()

        expect(page.get_by_text("Task deleted.")).to_be_visible()
        assert [r.path for r in mock_drf.requests if r.method == "DELETE"] == [
            "/tasks/tasks/33333333-3333-3333-3333-333333333333/"
        ]


class TestAbsentBackendFeatures:
    """What this BFF deliberately does not offer, asserted as an absence.

    Each of these is a capability the backend does not have. If it gains one,
    these tests fail and point at the place to implement it -- which is the
    point of writing them down rather than leaving them to a code comment.
    """

    def test_there_is_no_reopen_action(self, page: Page, authed: Page, mock_drf):
        """`status` is not on `UpdateTaskSerializer`, and the only inbound
        transition, `POST /tasks/<id>/mark-doing/`, answers 409
        (`TaskAlreadyDone`) for a finished task."""
        seed(mock_drf, [OPEN_TASK, DONE_TASK])
        page.goto("/tasks/")

        section = page.locator(
            "section", has=page.get_by_test_id("completed-task-list")
        )
        expect(section).not_to_contain_text("Reopen")

    def test_no_request_tries_to_change_a_finished_task_back(
        self, page: Page, authed: Page, mock_drf
    ):
        seed(mock_drf, [OPEN_TASK, DONE_TASK])
        page.goto("/tasks/")

        # Load the section, then confirm nothing about it produces a mutation
        # other than an explicit delete.
        page.get_by_test_id("completed-delete-button").click()
        page.get_by_test_id("delete-task-cancel").click()

        assert not [
            r
            for r in mock_drf.requests
            if r.method in {"PATCH", "PUT"}
            and r.json
            and isinstance(r.json, dict)
            and "status" in r.json
        ]

    def test_there_is_no_manual_daily_plan_generation_button(
        self, page: Page, authed: Page, mock_drf
    ):
        """`GenerateDailyPlanWithLlm` is wired into the backend's DI container but
        is exposed by no URL and no view method, so there is no endpoint a button
        could post to. The use case is not written either: a port method pointing
        at a route that does not exist would be a lie the E2E suite could not
        catch and the integration suite would have to mock into existence."""
        seed(mock_drf, [OPEN_TASK])
        page.goto("/tasks/daily/")

        expect(
            page.get_by_role("button", name="Generate Daily Plan Manually")
        ).to_have_count(0)
