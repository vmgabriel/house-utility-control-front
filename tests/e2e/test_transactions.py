"""E2E tests for transaction operations.

Exercises the list, the create modal and delete against the fake DRF backend,
including the server-side validation that the browser's own ``required``/``min``
attributes and Alpine's disabled submit button would otherwise make
unreachable.
"""

from datetime import date, timedelta

from playwright.sync_api import Page, expect

from tests.e2e.drf_stub import (
    DEFAULT_ACCESS_TOKEN,
    StubResponse,
    default_transaction,
)
from tests.e2e.support import (
    expect_path,
    form_csrf_token,
    mock_drf_refresh,
    mock_drf_transaction_create,
    mock_drf_transaction_delete,
    mock_drf_transactions_list,
    mock_drf_transactions_list_status,
    submit_form,
    transaction_row,
)

#: The token the stub hands out when the browser asks for a refresh.
ROTATED_ACCESS = "rotated-access-token"

GROCERIES = {
    "id": "tx-1",
    "type": "expense",
    "amount": "50.00",
    "description": "Groceries",
    "category": "Food",
    "date": "2026-09-26",
}
SALARY = {
    "id": "tx-2",
    "type": "income",
    "amount": "100.00",
    "description": "Salary",
    "category": "Salary",
    "date": "2026-09-25",
}


def open_create_modal(page: Page) -> None:
    page.get_by_role("button", name="New Transaction").click()


def fill_create_form(
    page: Page,
    *,
    transaction_type: str = "expense",
    amount: str = "100.00",
    description: str = "New transaction",
    category: str = "Food",
) -> None:
    page.get_by_label("Type").select_option(transaction_type)
    page.get_by_label("Amount").fill(amount)
    page.get_by_label("Description").fill(description)
    page.get_by_label("Category").fill(category)


def delete_row(page: Page, description: str) -> None:
    transaction_row(page, description).get_by_role("button", name="Delete").click()


def access_token_cookie(page: Page) -> str:
    for cookie in page.context.cookies():
        if cookie["name"] == "bt_access_token":
            return cookie["value"]
    raise AssertionError("no access token cookie is set")


def accept_dialogs(page: Page) -> list[str]:
    """Accept every dialog, recording the messages so they can be asserted on."""
    seen: list[str] = []

    def handle(dialog) -> None:
        seen.append(dialog.message)
        dialog.accept()

    page.on("dialog", handle)
    return seen


class TestTransactionList:
    def test_transactions_page_redirects_when_not_authenticated(self, page: Page):
        page.goto("/transactions/")
        expect_path(page, "/auth/login")

    def test_transactions_page_loads_when_authenticated(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf, [])
        page.goto("/transactions/")

        expect(page.locator("h1")).to_have_text("Transactions")
        expect(page.get_by_role("button", name="New Transaction")).to_be_visible()

    def test_transactions_page_displays_transactions(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf, [GROCERIES, SALARY])
        page.goto("/transactions/")

        # Expenses carry a minus and income a plus; the sign is what the user
        # actually reads, so it is asserted per row.
        expect(
            transaction_row(page, "Groceries").get_by_text("-$50.00")
        ).to_be_visible()
        expect(
            transaction_row(page, "Groceries").get_by_text(
                "Expense • Food • Sep 26, 2026"
            )
        ).to_be_visible()
        expect(transaction_row(page, "Salary").get_by_text("+$100.00")).to_be_visible()
        expect(
            transaction_row(page, "Salary").get_by_text(
                "Income • Salary • Sep 25, 2026"
            )
        ).to_be_visible()

    def test_investments_and_savings_render_without_a_sign(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(
            mock_drf,
            [
                {
                    "id": "tx-3",
                    "type": "investment",
                    "amount": "250.00",
                    "description": "Index fund",
                    "category": "Investment",
                },
                {
                    "id": "tx-4",
                    "type": "savings",
                    "amount": "75.00",
                    "description": "Emergency fund",
                    "category": "Savings",
                },
            ],
        )
        page.goto("/transactions/")

        expect(
            transaction_row(page, "Index fund").get_by_text("$250.00")
        ).to_be_visible()
        expect(
            transaction_row(page, "Emergency fund").get_by_text("$75.00")
        ).to_be_visible()

    def test_transactions_page_shows_empty_state(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf, [])
        page.goto("/transactions/")

        expect(page.get_by_text("No transactions yet")).to_be_visible()
        expect(page.get_by_text("Create your first transaction")).to_be_visible()

    def test_list_forwards_pagination_to_drf(self, page: Page, authed: Page, mock_drf):
        # 21 rows at page_size 20 puts exactly one row on page 2, so the
        # forwarded page number is visible in the UI and not just in the stub.
        mock_drf.seed_transactions(
            *[
                default_transaction(
                    transaction_id=f"tx-{index}",
                    description=f"Item {index}",
                    amount="1.00",
                )
                for index in range(1, 22)
            ]
        )
        page.goto("/transactions/?page=2")

        calls = mock_drf.calls("GET", "/transactions/")
        assert len(calls) == 1
        assert calls[0].query == {"page": "2", "page_size": "20"}
        expect(page.get_by_text("Page 2")).to_be_visible()
        expect(transaction_row(page, "Item 21")).to_be_visible()
        expect(transaction_row(page, "Item 1")).to_have_count(0)

    def test_list_survives_a_failing_backend(self, page: Page, authed: Page, mock_drf):
        mock_drf_transactions_list(mock_drf, [GROCERIES])
        mock_drf_transactions_list_status(mock_drf, 500)
        page.goto("/transactions/")

        # Degrade to the empty state plus a message rather than a 500 page.
        expect(page.get_by_text("Failed to load transactions.")).to_be_visible()
        expect(page.get_by_text("No transactions yet")).to_be_visible()

    def test_list_refreshes_an_expired_token_and_retries(
        self, page: Page, expired: Page, mock_drf
    ):
        """The 401 -> refresh -> single retry path, end to end."""
        mock_drf_transactions_list(mock_drf, [GROCERIES])
        # The browser's token is rejected at the auth layer, so the frontend
        # sees a 401, refreshes, and retries with the rotated token.
        mock_drf.require_token(ROTATED_ACCESS)
        mock_drf_refresh(mock_drf, access=ROTATED_ACCESS)

        page.goto("/transactions/")

        expect(transaction_row(page, "Groceries")).to_be_visible()
        assert mock_drf.calls("POST", "/users/auth/refresh/") != []
        # The rotated pair was persisted, and the retry used it.
        assert access_token_cookie(page) == ROTATED_ACCESS
        retried = mock_drf.calls("GET", "/transactions/")[-1]
        assert retried.bearer_token == ROTATED_ACCESS


class TestCreateModal:
    def test_modal_is_hidden_until_the_button_is_clicked(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf, [])
        page.goto("/transactions/")

        expect(page.get_by_role("heading", name="Create Transaction")).to_be_hidden()

        open_create_modal(page)
        expect(page.get_by_role("heading", name="Create Transaction")).to_be_visible()

    def test_modal_exposes_every_field(self, page: Page, authed: Page, mock_drf):
        mock_drf_transactions_list(mock_drf, [])
        page.goto("/transactions/")
        open_create_modal(page)

        expect(page.get_by_label("Type")).to_be_visible()
        expect(page.get_by_label("Amount")).to_be_visible()
        expect(page.get_by_label("Description")).to_be_visible()
        expect(page.get_by_label("Category")).to_be_visible()
        expect(page.get_by_label("Date")).to_be_visible()
        # The API requires a category, so it is prefilled with the domain
        # default rather than left blank.
        expect(page.get_by_label("Category")).to_have_value("General")

    def test_modal_offers_every_transaction_type(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf, [])
        page.goto("/transactions/")
        open_create_modal(page)

        values = (
            page.get_by_label("Type")
            .locator("option")
            .evaluate_all("options => options.map(o => o.value)")
        )
        assert values == ["income", "expense", "investment", "savings"]

    def test_modal_suggests_categories(self, page: Page, authed: Page, mock_drf):
        mock_drf_transactions_list(mock_drf, [])
        page.goto("/transactions/")
        open_create_modal(page)

        suggestions = page.locator("#category-options option").evaluate_all(
            "options => options.map(o => o.value)"
        )
        assert suggestions == [
            "General",
            "Food",
            "Transport",
            "Salary",
            "Housing",
            "Other",
        ]

    def test_modal_includes_a_csrf_token(self, page: Page, authed: Page, mock_drf):
        mock_drf_transactions_list(mock_drf, [])
        page.goto("/transactions/")
        open_create_modal(page)

        assert form_csrf_token(page)

    def test_modal_closes_on_cancel(self, page: Page, authed: Page, mock_drf):
        mock_drf_transactions_list(mock_drf, [])
        page.goto("/transactions/")
        open_create_modal(page)

        page.get_by_role("button", name="Cancel").click()
        expect(page.get_by_role("heading", name="Create Transaction")).to_be_hidden()

    def test_modal_closes_on_escape(self, page: Page, authed: Page, mock_drf):
        mock_drf_transactions_list(mock_drf, [])
        page.goto("/transactions/")
        open_create_modal(page)

        page.keyboard.press("Escape")
        expect(page.get_by_role("heading", name="Create Transaction")).to_be_hidden()

    def test_submit_is_disabled_until_the_form_is_valid(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf, [])
        page.goto("/transactions/")
        open_create_modal(page)

        submit = page.get_by_role("button", name="Create", exact=True)
        expect(submit).to_be_disabled()

        fill_create_form(page, amount="10.00", description="Coffee")
        expect(submit).to_be_enabled()

    def test_zero_amount_is_flagged_before_submission(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf, [])
        page.goto("/transactions/")
        open_create_modal(page)

        page.get_by_label("Amount").fill("0")
        page.get_by_label("Amount").blur()

        expect(page.get_by_text("Enter an amount greater than 0.")).to_be_visible()
        expect(page.get_by_role("button", name="Create", exact=True)).to_be_disabled()


class TestCreate:
    def test_create_with_valid_data_succeeds(self, page: Page, authed: Page, mock_drf):
        mock_drf_transactions_list(mock_drf, [])
        mock_drf_transaction_create(mock_drf, transaction_id="tx-new-123")
        page.goto("/transactions/")

        open_create_modal(page)
        fill_create_form(
            page,
            transaction_type="expense",
            amount="42.50",
            description="Groceries run",
            category="Food",
        )
        page.get_by_role("button", name="Create", exact=True).click()

        expect_path(page, "/transactions/")
        expect(page.get_by_text("Transaction created successfully!")).to_be_visible()
        expect(transaction_row(page, "Groceries run")).to_be_visible()

    def test_create_sends_the_drf_payload(self, page: Page, authed: Page, mock_drf):
        mock_drf_transactions_list(mock_drf, [])
        mock_drf_transaction_create(mock_drf)
        page.goto("/transactions/")

        open_create_modal(page)
        fill_create_form(
            page,
            transaction_type="income",
            amount="42.50",
            description="Freelance",
            category="Salary",
        )
        page.get_by_role("button", name="Create", exact=True).click()
        expect_path(page, "/transactions/")

        calls = mock_drf.calls("POST", "/transactions/")
        assert len(calls) == 1
        # The backend names the type `transaction_type` and requires a category.
        assert calls[0].json == {
            "transaction_type": "income",
            "amount": "42.50",
            "category": "Salary",
            "description": "Freelance",
            "date": date.today().isoformat(),
        }
        assert calls[0].bearer_token == DEFAULT_ACCESS_TOKEN

    def test_create_defaults_a_blank_category(self, page: Page, authed: Page, mock_drf):
        """The API rejects a blank category, so the view substitutes the default."""
        mock_drf_transactions_list(mock_drf, [])
        mock_drf_transaction_create(mock_drf)
        page.goto("/transactions/")

        html = submit_form(
            page,
            "/transactions/create",
            {
                "type": "expense",
                "amount": "12.00",
                "description": "Blank category",
                "category": "",
                "date": date.today().isoformat(),
            },
        )

        assert "Transaction created successfully!" in html
        assert mock_drf.calls("POST", "/transactions/")[0].json["category"] == "General"

    def test_create_rejects_a_non_positive_amount_on_the_server(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf, [])
        mock_drf_transaction_create(mock_drf)
        page.goto("/transactions/")

        html = submit_form(
            page,
            "/transactions/create",
            {
                "type": "expense",
                "amount": "0",
                "description": "Free sample",
                "category": "General",
                "date": date.today().isoformat(),
            },
        )

        assert "Transaction amount must be positive" in html
        assert mock_drf.calls("POST", "/transactions/") == []

    def test_create_rejects_a_future_date_on_the_server(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf, [])
        mock_drf_transaction_create(mock_drf)
        page.goto("/transactions/")

        html = submit_form(
            page,
            "/transactions/create",
            {
                "type": "expense",
                "amount": "10.00",
                "description": "Tomorrow",
                "category": "General",
                "date": (date.today() + timedelta(days=1)).isoformat(),
            },
        )

        assert "Transaction date cannot be in the future" in html
        assert mock_drf.calls("POST", "/transactions/") == []

    def test_create_rejects_a_malformed_amount(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf, [])
        mock_drf_transaction_create(mock_drf)
        page.goto("/transactions/")

        html = submit_form(
            page,
            "/transactions/create",
            {
                "type": "expense",
                "amount": "not-a-number",
                "description": "Broken",
                "category": "General",
                "date": date.today().isoformat(),
            },
        )

        assert "Invalid transaction data" in html
        assert mock_drf.calls("POST", "/transactions/") == []

    def test_create_rejects_an_unknown_transaction_type(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf, [])
        mock_drf_transaction_create(mock_drf)
        page.goto("/transactions/")

        html = submit_form(
            page,
            "/transactions/create",
            {
                "type": "donation",
                "amount": "10.00",
                "description": "Mystery",
                "category": "General",
                "date": date.today().isoformat(),
            },
        )

        assert "Invalid transaction data" in html
        assert mock_drf.calls("POST", "/transactions/") == []

    def test_create_reports_a_backend_rejection(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf, [])
        mock_drf_transaction_create(mock_drf, success=False)
        page.goto("/transactions/")

        open_create_modal(page)
        fill_create_form(page)
        page.get_by_role("button", name="Create", exact=True).click()

        expect_path(page, "/transactions/")
        expect(page.get_by_text("Validation failed")).to_be_visible()
        # A rejected create must never also claim success, and the backend's
        # own field errors must not be echoed back verbatim.
        expect(page.get_by_text("Transaction created successfully!")).to_have_count(0)
        expect(page.get_by_text("Enter a valid amount.")).to_have_count(0)
        expect(page.get_by_text("No transactions yet")).to_be_visible()

    def test_create_reports_an_unreadable_backend_response(
        self, page: Page, authed: Page, mock_drf
    ):
        """A payload the frontend cannot parse must not look like a success."""
        mock_drf_transactions_list(mock_drf, [])
        # The shape the schema requires is missing, so validation explodes
        # without a DomainException to catch.
        mock_drf.on_call(
            "POST", "/transactions/", lambda _r: StubResponse(201, {"oops": True})
        )
        page.goto("/transactions/")

        open_create_modal(page)
        fill_create_form(page)
        page.get_by_role("button", name="Create", exact=True).click()

        expect(page.get_by_text("Failed to create transaction.")).to_be_visible()
        expect(page.get_by_text("Transaction created successfully!")).to_have_count(0)

    def test_create_requires_authentication(self, page: Page):
        response = page.request.post(
            "/transactions/create",
            form={
                "type": "expense",
                "amount": "10.00",
                "description": "x",
                "category": "General",
                "date": date.today().isoformat(),
            },
        )
        assert response.url.endswith("/auth/login")


class TestDelete:
    def test_delete_with_confirmation_removes_the_row(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf, [GROCERIES, SALARY])
        mock_drf_transaction_delete(mock_drf, "tx-1")
        page.goto("/transactions/")

        accept_dialogs(page)
        delete_row(page, "Groceries")

        expect(page.get_by_text("Transaction deleted successfully!")).to_be_visible()
        expect(transaction_row(page, "Groceries")).to_have_count(0)
        expect(transaction_row(page, "Salary")).to_be_visible()
        assert mock_drf.calls("DELETE", "/transactions/tx-1/") != []

    def test_delete_asks_for_confirmation_first(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf, [GROCERIES])
        mock_drf_transaction_delete(mock_drf, "tx-1")
        page.goto("/transactions/")

        messages = accept_dialogs(page)
        delete_row(page, "Groceries")

        expect(page.get_by_text("Transaction deleted successfully!")).to_be_visible()
        assert len(messages) == 1
        assert "delete this transaction" in messages[0]

    def test_declining_the_confirmation_keeps_the_row(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf, [GROCERIES])
        mock_drf_transaction_delete(mock_drf, "tx-1")
        page.goto("/transactions/")

        page.on("dialog", lambda dialog: dialog.dismiss())
        delete_row(page, "Groceries")

        expect(transaction_row(page, "Groceries")).to_be_visible()
        assert mock_drf.calls("DELETE", "/transactions/tx-1/") == []

    def test_delete_reports_a_missing_transaction(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf, [GROCERIES])
        mock_drf_transaction_delete(mock_drf, "tx-1", success=False)
        page.goto("/transactions/")

        accept_dialogs(page)
        delete_row(page, "Groceries")

        expect(page.get_by_text("Failed to delete transaction.")).to_be_visible()

    def test_delete_rejects_a_forged_csrf_token(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf, [GROCERIES])
        mock_drf_transaction_delete(mock_drf, "tx-1")
        page.goto("/transactions/")

        accept_dialogs(page)
        page.eval_on_selector(
            "form[action$='/delete'] input[name='csrf_token']",
            "(el, token) => { el.value = token }",
            "forged:0:" + "0" * 64,
        )
        delete_row(page, "Groceries")

        expect(page.get_by_text("Invalid or expired CSRF token.")).to_be_visible()
        assert mock_drf.calls("DELETE", "/transactions/tx-1/") == []
        expect(transaction_row(page, "Groceries")).to_be_visible()

    def test_delete_requires_authentication(self, page: Page, authed: Page, mock_drf):
        mock_drf_transactions_list(mock_drf, [GROCERIES])
        page.goto("/transactions/")

        accept_dialogs(page)
        page.context.clear_cookies()
        delete_row(page, "Groceries")

        expect_path(page, "/auth/login")
        assert mock_drf.calls("DELETE", "/transactions/tx-1/") == []
