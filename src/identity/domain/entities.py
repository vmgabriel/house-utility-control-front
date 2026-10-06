"""Identity domain entity: the user behind the current session.

Not to be confused with `rentals`' owner or with `src.users`' `SystemUser`,
which models an administrative row rather than a signed-in principal.
"""

from dataclasses import dataclass

from .value_objects import UserId


@dataclass(frozen=True, slots=True)
class User:
    """User entity."""

    id: UserId
    email: str
    name: str
    plan: str  # "free", "pro", or "premium"
    is_active: bool = True
    is_staff: bool = False
