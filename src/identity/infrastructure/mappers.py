"""Mappers from DRF schemas to identity domain entities."""

from src.identity.domain.entities import User
from src.identity.domain.value_objects import UserId

from .schemas import DRFUserResponse


def map_user_response(user_data: DRFUserResponse) -> User:
    return User(
        id=UserId(user_data.id),
        email=user_data.email,
        name=user_data.name,
        plan=user_data.plan,
        is_active=user_data.is_active,
        is_staff=user_data.is_staff,
    )
