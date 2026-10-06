"""ViewModel for the signed-in user in templates."""

from dataclasses import dataclass

from src.identity.domain.entities import User


@dataclass
class UserViewModel:
    """ViewModel for user data in templates."""

    id: str
    email: str
    name: str
    plan: str
    plan_display: str

    @classmethod
    def from_domain(cls, user: User) -> "UserViewModel":
        plan_display = {
            "free": "Free Plan",
            "pro": "Pro Plan",
            "premium": "Premium Plan",
        }.get(user.plan, user.plan.title())

        return cls(
            id=user.id,
            email=user.email,
            name=user.name,
            plan=user.plan,
            plan_display=plan_display,
        )
