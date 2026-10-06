"""Integration tests for the identity context's DRF <-> domain mapper."""

from src.identity.infrastructure.mappers import map_user_response
from src.identity.infrastructure.schemas import (
    DRFTokenResponse,
    DRFUserResponse,
)


class TestMappingToDomain:
    def test_map_user_response(self):
        user = map_user_response(
            DRFUserResponse(
                id="u1", email="a@b.com", name="Ana", plan="pro", is_active=False
            )
        )
        assert (user.id, user.email, user.name, user.plan) == (
            "u1",
            "a@b.com",
            "Ana",
            "pro",
        )
        assert user.is_active is False

    def test_user_is_active_defaults_to_true(self):
        user = map_user_response(
            DRFUserResponse(id="u1", email="a@b.com", name="Ana", plan="free")
        )
        assert user.is_active is True

    def test_display_name_falls_back_to_full_name(self):
        # The backend sends `full_name`; the domain field is `name`. Accepting
        # both keeps the domain neutral and the backend authoritative.
        user = map_user_response(
            DRFUserResponse(id="u1", email="a@b.com", full_name="Ana", plan="pro")
        )
        assert user.name == "Ana"


class TestSchemaTolerance:
    def test_unknown_drf_fields_are_ignored(self):
        # DRF is free to add fields; the adapter must not break.
        schema = DRFTokenResponse.model_validate(
            {"access": "a", "refresh": "r", "jti": "abc", "expires_in": 3600}
        )
        assert (schema.access, schema.refresh) == ("a", "r")
