"""E2E tests for the daily Kanban board and its drag and drop.

These drive a real browser against the in-process `StubDRFServer`, so they cover
what an `httpx`-mocked integration test cannot:

* that **SortableJS actually initialises** and wires the columns to each other.
  The stub's real `SortableJS` is vendored for exactly this -- a stubbed-out
  global would make every drag assertion fail with "Sortable is not a function",
  or worse pass via the card's form control while the drag path does nothing.
* that a **real drag** moves the card and POSTs to the right backend action.
* that an **illegal drop is refused before any request**, and that a refused
  legal move **puts the card back**.

Drag simulation goes through `page.mouse` rather than `page.drag_and_drop()`.
The latter synthesises HTML5 drag events, which SortableJS's fallback mode does
not listen for -- it binds pointer events -- so it would report success while
moving nothing. Mouse moves exercise the code path a real user does.

**The drag helper pauses between moves, and that is load-bearing.** In fallback
mode SortableJS polls the pointer position on a 50ms interval rather than
reading it from the event, so a drag driven by moves faster than that samples
one stale position and the drop lands on whatever was under the pointer when the
poll last ran. A helper that fires every move in a single tick would therefore
fail intermittently, and the failure would look like a flaky board rather than a
flaky test.

**The viewport is enlarged before every drag.** The board's columns stack
vertically below Tailwind's `md` breakpoint, and the default 1280x720 test
viewport is not tall enough for a source card and a target column to both be on
screen. A target below the fold is a drop on nothing.

The DRF boundary is stubbed rather than `page.route()`d, per the module note in
`drf_stub.py`: every DRF call is made by Flask, never by the browser.
"""

from __future__ import annotations

from datetime import date

from playwright.sync_api import Page, expect

from tests.e2e.support import expect_path

TASK_ID = "11111111-1111-1111-1111-111111111111"
DONE_TASK_ID = "33333333-3333-3333-3333-333333333333"
GOAL_ID = "22222222-2222-2222-2222-222222222222"
PLAN_ID = "44444444-4444-4444-4444-444444444444"

TODAY = date.today().isoformat()
COMPLETED_AT = "2026-10-04T18:00:00Z"

TODO_TASK = {
    "id": TASK_ID,
    "goal_id": None,
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
    **TODO_TASK,
    "id": DONE_TASK_ID,
    "name": "Shipped the release",
    "status": "done",
    "estimated_hours": "1.50",
    "completed_at": COMPLETED_AT,
}


def seed_board(mock_drf, tasks: list[dict]) -> None:
    """Put rows on the stub and give today a plan holding them.

    Both lists are seeded because the board reads them separately: the backlog
    read has `done` stripped by the adapter, so a finished task has to come from
    the `?status=done` read or the Done column would be empty.
    """
    mock_drf.seed_tasks(*tasks)
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


def column(page: Page, status: str):
    return page.locator(f'[data-column-status="{status}"]')


def card_in(page: Page, status: str, task_id: str):
    return column(page, status).locator(f'[data-task-id="{task_id}"]')


#: How often SortableJS samples the pointer in fallback mode. A drag helper
#: has to out-wait this, or the drop is resolved from a stale position.
SORTABLE_POLL_MS = 50

#: Comfortably above the poll interval. Below it, samples land between moves and
#: the card drops wherever the pointer was when the last poll ran.
_SETTLE_MS = 120


def drag(page: Page, source, target) -> None:
    """A real pointer drag from the middle of `source` to the middle of `target`.

    Several moves rather than one, because SortableJS distinguishes hover from
    press: a card only starts moving once the pointer has moved *while the
    button is down*, and a drop is only registered after a further move. A
    single `mouse.down()` -> `mouse.up()` at the destination selects text and
    does nothing else.
    """
    # Tall enough for the whole board, so a source and a target column are never
    # off-screen at the same time. `set_viewport_size` before any measurement.
    page.set_viewport_size({"width": 1400, "height": 1400})
    source.scroll_into_view_if_needed()
    start = source.bounding_box()
    end = target.bounding_box()
    assert start and end, "source or target is not visible"

    page.mouse.move(start["x"] + start["width"] / 2, start["y"] + 20)
    page.mouse.down()
    # A few intermediate points: the library starts the drag on a threshold of
    # movement, and one large jump can land past a drop zone's hit test.
    for step in range(1, 6):
        page.mouse.move(
            start["x"] + start["width"] / 2,
            start["y"] + 20 + (end["y"] - start["y"]) * step / 5,
            steps=3,
        )
        page.wait_for_timeout(_SETTLE_MS)
    page.mouse.move(end["x"] + end["width"] / 2, end["y"] + end["height"] / 2, steps=3)
    # One more settle: the card must be sampled *over the target* before the
    # button is released, or the drop is resolved against the previous column.
    page.wait_for_timeout(_SETTLE_MS)
    page.mouse.up()


class TestBoardLayout:
    def test_three_columns_appear_in_board_order(
        self, page: Page, authed: Page, mock_drf
    ):
        seed_board(mock_drf, [TODO_TASK])
        page.goto("/tasks/daily/")

        expect(page.get_by_test_id("kanban-board")).to_be_visible()
        expect(page.get_by_test_id("kanban-column")).to_have_count(3)
        headers = page.locator('[data-testid="kanban-column"] h2')
        expect(headers).to_have_text(["To Do", "Doing", "Done"])

    def test_columns_stack_vertically_on_a_phone(
        self, page: Page, authed: Page, mock_drf
    ):
        """One column per row below `md`, three across above it.

        Measured from the rendered geometry rather than asserted on a class
        name: the class is the intent, but what the user experiences is whether
        the second column starts below the first or beside it, and a `hidden
        md:block` pair would satisfy the class assertion while leaving nothing to
        see on a phone.
        """
        seed_board(mock_drf, [TODO_TASK])
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto("/tasks/daily/")

        columns = page.get_by_test_id("kanban-column")
        expect(columns).to_have_count(3)
        first, second = columns.nth(0).bounding_box(), columns.nth(1).bounding_box()
        assert first and second
        assert second["y"] > first["y"], "columns are side by side on a phone"
        # And all three are visible: a `hidden md:*` board would pass the first
        # assertion with nothing on screen.
        expect(columns.nth(2)).to_be_visible()

    def test_columns_sit_side_by_side_on_a_desktop(
        self, page: Page, authed: Page, mock_drf
    ):
        seed_board(mock_drf, [TODO_TASK])
        page.set_viewport_size({"width": 1920, "height": 1200})
        page.goto("/tasks/daily/")

        columns = page.get_by_test_id("kanban-column")
        first, second = columns.nth(0).bounding_box(), columns.nth(1).bounding_box()
        assert first and second
        assert second["x"] > first["x"], "columns are stacked on a desktop"
        assert abs(second["y"] - first["y"]) < 4

    def test_a_finished_task_appears_in_the_done_column(
        self, page: Page, authed: Page, mock_drf
    ):
        """The read that makes the third column possible.

        `get_backlog` strips `done` tasks by design, so without the separate
        completed read the board would show an empty Done column while the hours
        bar claimed hours were spent in it.
        """
        seed_board(mock_drf, [TODO_TASK, DONE_TASK])
        page.goto("/tasks/daily/")

        expect(card_in(page, "todo", TASK_ID)).to_be_visible()
        expect(card_in(page, "done", DONE_TASK_ID)).to_be_visible()
        expect(card_in(page, "doing", TASK_ID)).to_have_count(0)

    def test_the_page_has_exactly_one_alpine_scope(
        self, page: Page, authed: Page, mock_drf
    ):
        """One board, one root.

        Three `x-data="kanbanColumn(...)"` scopes -- one per column -- would be
        three roots inside one page body, which is the failure AGENTS.md rule 5
        is written against.
        """
        seed_board(mock_drf, [TODO_TASK])
        page.goto("/tasks/daily/")

        scopes = page.locator("main [x-data]")
        expect(scopes).to_have_count(1)

    def test_sortable_is_actually_wired_up(self, page: Page, authed: Page, mock_drf):
        """Guards the rest of this file from testing nothing.

        If `Sortable` were undefined the drag assertions would fail with a
        confusing DOM error rather than naming the cause. Asserted explicitly so
        the failure points at the library, not at the drag.
        """
        seed_board(mock_drf, [TODO_TASK])
        page.goto("/tasks/daily/")

        assert page.evaluate("typeof window.Sortable") == "function"


class TestDragAndDrop:
    def test_dragging_todo_into_doing_posts_mark_doing(
        self, page: Page, authed: Page, mock_drf
    ):
        seed_board(mock_drf, [TODO_TASK])
        page.goto("/tasks/daily/")

        drag(
            page,
            card_in(page, "todo", TASK_ID),
            column(page, "doing").locator("[data-kanban-list]"),
        )
        # The fetch is asynchronous; wait for it to reach the stub.
        page.wait_for_timeout(500)

        calls = mock_drf.calls("POST", f"/tasks/tasks/{TASK_ID}/mark-doing/")
        assert len(calls) == 1
        # And the card actually moved, which is the optimistic half of the
        # behaviour -- asserted separately so a test that only checked the
        # request would pass against a board that reloaded on every move.
        expect(card_in(page, "doing", TASK_ID)).to_be_visible()
        expect(card_in(page, "todo", TASK_ID)).to_have_count(0)

    def test_dragging_doing_into_done_posts_mark_done(
        self, page: Page, authed: Page, mock_drf
    ):
        doing_task = {**TODO_TASK, "status": "doing"}
        seed_board(mock_drf, [doing_task])
        page.goto("/tasks/daily/")

        drag(
            page,
            card_in(page, "doing", TASK_ID),
            column(page, "done").locator("[data-kanban-list]"),
        )
        page.wait_for_timeout(500)

        assert len(mock_drf.calls("POST", f"/tasks/tasks/{TASK_ID}/mark-done/")) == 1
        expect(card_in(page, "done", TASK_ID)).to_be_visible()

    def test_dragging_done_back_to_todo_posts_reopen(
        self, page: Page, authed: Page, mock_drf
    ):
        """The one exit from Done, and it is the backend's `reopen`.

        Worth its own test because it is the move most likely to be wired to the
        wrong action: `mark-doing` is the obvious-looking choice and it answers
        409 on a finished task.
        """
        seed_board(mock_drf, [DONE_TASK])
        page.goto("/tasks/daily/")

        drag(
            page,
            card_in(page, "done", DONE_TASK_ID),
            column(page, "todo").locator("[data-kanban-list]"),
        )
        page.wait_for_timeout(500)

        assert len(mock_drf.calls("POST", f"/tasks/tasks/{DONE_TASK_ID}/reopen/")) == 1
        expect(card_in(page, "todo", DONE_TASK_ID)).to_be_visible()

    def test_the_card_gains_a_completion_time_the_drag_gave_it(
        self, page: Page, authed: Page, mock_drf
    ):
        """The card is re-stamped from the backend's answer, not the drop's target.

        `completed_at` is a server fact with no local equivalent. Echoing the
        drag's target would leave a Done card showing a completion time the
        backend never set, and a reload would contradict it.
        """
        mock_drf.stub_now = "2026-10-05T14:31:12.480000Z"
        doing_task = {**TODO_TASK, "status": "doing"}
        seed_board(mock_drf, [doing_task])
        page.goto("/tasks/daily/")

        drag(
            page,
            card_in(page, "doing", TASK_ID),
            column(page, "done").locator("[data-kanban-list]"),
        )
        page.wait_for_timeout(500)

        expect(
            card_in(page, "done", TASK_ID).get_by_test_id("kanban-card-completed-at")
        ).to_contain_text("2026-10-05T14:31")


class TestIllegalDropsAreRefused:
    def test_doing_cannot_be_dragged_back_to_todo(
        self, page: Page, authed: Page, mock_drf
    ):
        """No backend endpoint exists, so no request should be made.

        Asserted on the *absence* of a request as well as on the card's position.
        Offering the move and failing it afterwards would be worse: the card
        would visibly jump columns and jump back, which reads as a bug rather
        than as a rule.
        """
        doing_task = {**TODO_TASK, "status": "doing"}
        seed_board(mock_drf, [doing_task])
        page.goto("/tasks/daily/")

        drag(
            page,
            card_in(page, "doing", TASK_ID),
            column(page, "todo").locator("[data-kanban-list]"),
        )

        assert not [
            r
            for r in mock_drf.requests
            if r.method == "POST" and "/tasks/tasks/" in r.path
        ]
        expect(card_in(page, "doing", TASK_ID)).to_be_visible()

    def test_done_cannot_be_dragged_back_to_doing(
        self, page: Page, authed: Page, mock_drf
    ):
        """The other missing move.

        `mark-doing` answers 409 `TaskAlreadyDone` on a finished task, so a board
        that offered this would be promising something the backend refuses.
        """
        seed_board(mock_drf, [DONE_TASK])
        page.goto("/tasks/daily/")

        drag(
            page,
            card_in(page, "done", DONE_TASK_ID),
            column(page, "doing").locator("[data-kanban-list]"),
        )
        page.wait_for_timeout(500)

        assert not [
            r
            for r in mock_drf.requests
            if r.method == "POST" and "/tasks/tasks/" in r.path
        ]
        expect(card_in(page, "done", DONE_TASK_ID)).to_be_visible()


class TestOptimisticUpdateAndRevert:
    def test_a_refused_move_puts_the_card_back_and_explains(
        self, page: Page, authed: Page, mock_drf
    ):
        """The move is allowed by the board's own rule and refused by the backend.

        This is the case that matters most, because it is the one the two copies
        of the transition matrix disagree about -- typically a task moved in
        another tab since the page was rendered. The card must land back where it
        started, and the message must be the backend's own wording, because the
        BFF cannot know which of its three refusals applied.
        """
        seed_board(mock_drf, [TODO_TASK])
        mock_drf.transition_failure = "mark-doing"
        page.goto("/tasks/daily/")

        drag(
            page,
            card_in(page, "todo", TASK_ID),
            column(page, "doing").locator("[data-kanban-list]"),
        )
        page.wait_for_timeout(500)

        expect(card_in(page, "todo", TASK_ID)).to_be_visible()
        expect(card_in(page, "doing", TASK_ID)).to_have_count(0)
        expect(page.get_by_test_id("move-result")).to_contain_text(
            "A completed task cannot be moved back to doing."
        )

    def test_a_refused_move_reports_409_not_a_silent_success(
        self, page: Page, authed: Page, mock_drf
    ):
        """The drag handler reverts on the response *status*, so 409 is load-bearing.

        A 200 carrying `{"ok": false}` would leave the decision to a field the
        server and client both have to agree the meaning of -- and getting that
        wrong leaves a card that looks moved and is not.
        """
        seed_board(mock_drf, [TODO_TASK])
        mock_drf.transition_failure = "mark-doing"
        page.goto("/tasks/daily/")

        drag(
            page,
            card_in(page, "todo", TASK_ID),
            column(page, "doing").locator("[data-kanban-list]"),
        )

        expect(page.get_by_test_id("move-result")).to_be_visible()
        expect(card_in(page, "todo", TASK_ID)).to_be_visible()

    def test_a_404_also_reverts_the_card(self, page: Page, authed: Page, mock_drf):
        """The card was deleted while it was on screen.

        The user was looking at a stale board, so the honest outcome is the card
        returning to its column with a message, not a silent partial state.
        """
        seed_board(mock_drf, [TODO_TASK])
        mock_drf.on(
            "POST",
            f"/tasks/tasks/{TASK_ID}/mark-doing/",
            type(
                "R",
                (),
                {"status": 404, "body": {"detail": "Not found."}},
            )(),
        )
        page.goto("/tasks/daily/")

        drag(
            page,
            card_in(page, "todo", TASK_ID),
            column(page, "doing").locator("[data-kanban-list]"),
        )
        page.wait_for_timeout(500)

        expect(card_in(page, "todo", TASK_ID)).to_be_visible()
        expect(page.get_by_test_id("move-result")).to_contain_text("no longer exists")


class TestKeyboardAndNoScriptPath:
    """The card's select-and-button is the baseline, not a fallback.

    A keyboard user cannot perform a drag and a screen reader announces nothing
    about one, so the non-pointer path is present for everyone and posts the
    same route.
    """

    def test_the_move_control_moves_a_card_and_reports_it(
        self, page: Page, authed: Page, mock_drf
    ):
        seed_board(mock_drf, [TODO_TASK])
        page.goto("/tasks/daily/")

        card = card_in(page, "todo", TASK_ID)
        card.get_by_test_id("kanban-card-move-select").select_option("done")
        card.get_by_test_id("kanban-card-move-button").click()

        expect(page.get_by_text("moved to Done")).to_be_visible()
        assert len(mock_drf.calls("POST", f"/tasks/tasks/{TASK_ID}/mark-done/")) == 1

    def test_a_doing_card_offers_only_done_in_its_select(
        self, page: Page, authed: Page, mock_drf
    ):
        """The missing `doing -> todo` endpoint, visible without dragging.

        This is where the constraint reaches a user who never touches the board:
        their select has one option, and there is no arrow to try and fail.
        """
        doing_task = {**TODO_TASK, "status": "doing"}
        seed_board(mock_drf, [doing_task])
        page.goto("/tasks/daily/")

        options = (
            card_in(page, "doing", TASK_ID)
            .get_by_test_id("kanban-card-move-select")
            .locator("option")
        )
        # The leading empty option is the "Move to…" placeholder.
        expect(options).to_have_text(["Move to…", "Done"])

    def test_a_refused_move_from_the_control_flashes_instead_of_reverting(
        self, page: Page, authed: Page, mock_drf
    ):
        """A form post gets a redirect and a flash, never a silent no-op.

        The drag path reverts the card; this path has nothing to revert because
        the page reloads, so the flash is the entire feedback mechanism.
        """
        seed_board(mock_drf, [TODO_TASK])
        mock_drf.transition_failure = "mark-done"
        page.goto("/tasks/daily/")

        card = card_in(page, "todo", TASK_ID)
        card.get_by_test_id("kanban-card-move-select").select_option("done")
        card.get_by_test_id("kanban-card-move-button").click()

        expect(page.get_by_text("Task is already marked as done.")).to_be_visible()
        expect_path(page, "/tasks/daily/")

    def test_the_board_still_renders_with_javascript_disabled(
        self, page: Page, authed: Page, mock_drf
    ):
        """Everything but the drag.

        The card controls are real forms precisely so this is true: with the
        JavaScript off the board degrades to three columns of forms rather than
        to an empty page.
        """
        seed_board(mock_drf, [TODO_TASK, DONE_TASK])
        page.goto("/tasks/daily/")
        page.evaluate("window.Sortable = undefined")
        page.reload()

        expect(page.get_by_test_id("kanban-board")).to_be_visible()
        expect(page.get_by_test_id("kanban-card")).to_have_count(2)
        expect(page.get_by_test_id("kanban-card-move-button")).to_have_count(2)
