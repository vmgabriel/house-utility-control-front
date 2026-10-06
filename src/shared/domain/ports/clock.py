"""Shared port for time abstraction.

Mirrors the backend's ``shared.domain.ports.clock`` so both sides agree on the
shape of the port. The implementation (``SystemClock``) lives in
``src/shared/infrastructure`` and is wired in the composition root
(``src/interfaces/web/app.py``).
"""

from datetime import date, datetime
from typing import Protocol


class Clock(Protocol):
    """Abstract time source for deterministic domain behaviour.

    Use cases depend on this protocol instead of calling ``datetime.now()``
    directly, so tests can inject a frozen clock and assert exact timestamps.
    """

    def now(self) -> datetime:
        """Return the current timezone-aware datetime."""
        ...

    def today(self) -> date:
        """Return the current date."""
        ...
