"""ViewModels for User templates."""

from dataclasses import dataclass

from src.users.domain.entities import SystemUser


@dataclass
class AdminUserViewModel:
    id: str
    email: str
    full_name: str
    plan_display: str
    is_active: bool
    is_staff: bool

    @classmethod
    def from_domain(cls, user: SystemUser) -> "AdminUserViewModel":
        return cls(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            plan_display=user.plan.value.capitalize(),
            is_active=user.is_active,
            is_staff=user.is_staff,
        )
