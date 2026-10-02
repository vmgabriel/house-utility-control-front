"""Profile domain entities."""

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class UserProfile:
    id: str
    first_name: str
    last_name: str
    timezone: str  # IANA timezone, e.g., "America/Bogota"
    language: str  # ISO 639-1, e.g., "es"
    currency: str  # ISO 4217, e.g., "USD"
    date_format: str  # "YYYY-MM-DD", "DD/MM/YYYY", "MM/DD/YYYY"
    avatar_url: str | None
    bio: str | None

    def clear_avatar(self) -> "UserProfile":
        # Backend expects "" to clear
        return replace(self, avatar_url="")

    def clear_bio(self) -> "UserProfile":
        return replace(self, bio="")
