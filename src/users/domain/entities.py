"""User domain entities."""

from dataclasses import dataclass
from enum import Enum


class UserPlan(str, Enum):
    FREE = "free"
    PRO = "pro"
    PREMIUM = "premium"


@dataclass(frozen=True, slots=True)
class SystemUser:
    id: str
    email: str
    full_name: str
    plan: UserPlan
    is_active: bool
    is_staff: bool
