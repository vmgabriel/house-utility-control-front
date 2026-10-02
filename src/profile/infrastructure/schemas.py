"""Pydantic schemas for DRF Profile API."""

from pydantic import BaseModel, ConfigDict


class DRFProfileResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    first_name: str
    last_name: str
    timezone: str
    language: str
    currency: str
    date_format: str
    avatar_url: str | None = None
    bio: str | None = None
