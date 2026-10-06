"""Pydantic schemas for the identity DRF endpoints."""

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class DRFTokenResponse(BaseModel):
    """Response from login/refresh endpoints."""

    model_config = ConfigDict(extra="allow")
    access: str
    refresh: str


class DRFUserResponse(BaseModel):
    """Response from /users/me/ endpoint."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    email: str
    # The DRF backend exposes the display name as `full_name`
    # (apps.users.interfaces.serializers.UserSerializer). `name` is accepted as
    # well so the domain field stays neutral; the backend field wins.
    name: str = Field(validation_alias=AliasChoices("full_name", "name"))
    plan: str
    is_active: bool = True
    is_staff: bool = False
    is_superuser: bool = False
