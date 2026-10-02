"""ViewModels for Profile templates."""

from dataclasses import dataclass

from src.profile.domain.entities import UserProfile


@dataclass
class ProfileViewModel:
    first_name: str
    last_name: str
    full_name_display: str
    timezone: str
    avatar_url: str | None
    bio: str | None
    language: str
    currency: str
    date_format: str

    @classmethod
    def from_domain(cls, entity: UserProfile) -> "ProfileViewModel":
        return cls(
            first_name=entity.first_name,
            last_name=entity.last_name,
            full_name_display=f"{entity.first_name} {entity.last_name}".strip(),
            timezone=entity.timezone,
            avatar_url=entity.avatar_url,
            bio=entity.bio,
            language=entity.language,
            currency=entity.currency,
            date_format=entity.date_format,
        )
