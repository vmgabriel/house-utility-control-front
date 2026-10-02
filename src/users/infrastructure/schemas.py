"""Pydantic schemas for DRF User API."""

from pydantic import BaseModel, ConfigDict

from src.users.domain.entities import UserPlan


class DRFUserResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    email: str
    full_name: str
    plan: UserPlan
    is_active: bool
    is_staff: bool


class DRFUserListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    count: int
    results: list[DRFUserResponse]
