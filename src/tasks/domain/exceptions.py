"""Exceptions raised by the tasks domain.

Rooted on :class:`DomainException` from the shared kernel rather than on a
locally defined base, because AGENTS.md's view error handling tells views to
``except DomainException`` when they want a friendly flash. A context-local root
would not be caught by that clause, so every task validation failure would fall
through to the bare ``except Exception`` branch and the user would see
"An unexpected error occurred" for a mistyped due date.

That is also why `rentals` defines its own root: it predates the shared
`DomainException` and predates the rule being applied uniformly. New code
inherits the shared base; nothing in this context depends on the difference.
"""

from src.shared.domain.exceptions import DomainException


class InvalidTaskError(DomainException):
    """Raised when a task breaks an invariant the backend would reject."""


class InvalidGoalError(DomainException):
    """Raised when a goal breaks an invariant the backend would reject."""


class InvalidDailyPlanError(DomainException):
    """Raised when a daily plan breaks an invariant the backend would reject."""


class InvalidTasksInputError(DomainException):
    """Raised when user-supplied input fails validation before any I/O.

    Separate from the invariant errors above because the origin differs: this
    one is always a form post the BFF rejected on its own rules, before any
    request was made, so the message is safe to show verbatim.
    """


class TaskNotFoundError(DomainException):
    """Raised when a task does not exist within the requesting user's scope."""


class LlmUnavailableError(DomainException):
    """Raised when an LLM-backed action was requested but the backend is down.

    Distinct from an HTTP 503 on any other call, because the UI action it backs
    is the health indicator and the two decompose buttons: those need a distinct,
    non-retryable message rather than the generic "backend is unreachable"
    page, and the condition is self-healing (the user's next click may succeed),
    so it is not an error state worth a 500.

    Carries the upstream ``status`` when the backend actually answered, so a
    view can distinguish "the LLM provider rejected the key" (401/403) from any
    other rejection without knowing how the backend reports failure.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
