"""System-clock implementation of the shared `Clock` port."""

from datetime import UTC, date, datetime

from src.shared.domain.ports.clock import Clock


class SystemClock(Clock):
    """Provide timezone-aware UTC timestamps from the system clock.

    This is the production implementation. Tests inject a frozen clock instead,
    which is how the rentals use cases assert the "not in the future" rules
    without depending on when they run.
    """

    def now(self) -> datetime:
        """Return the current UTC datetime, always timezone-aware."""
        return datetime.now(UTC)

    def today(self) -> date:
        """Return the current UTC date."""
        return datetime.now(UTC).date()
