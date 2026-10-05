"""Regression tests for the catalogued OpenAPI-vs-runtime divergences.

`src/contract/overrides.py` records the fields that drf-spectacular declares
required and non-nullable but the live API returns as `null`. These tests assert
the runtime schemas keep treating those fields as optional, so the divergence
cannot grow unnoticed: if someone "fixes" a schema to match the generated
`src/contract/types.py`, these fail.

They also assert the generated contract is importable and self-consistent, so
`make sync-api-contract` cannot emit something that does not even parse.
"""

from dataclasses import fields, is_dataclass

import pytest

from src.contract.overrides import (
    DASHBOARD_OVERVIEW_OPTIONAL_FIELDS,
    OVERRIDES_BY_SCHEMA,
    PROFILE_OPTIONAL_FIELDS,
    describe,
)
from src.infrastructure.api.schemas import DRFDashboardOverviewResponse
from src.profile.infrastructure.schemas import DRFProfileResponse


class TestRuntimeSchemasStayOptional:
    """The hand-written schemas must remain more permissive than the schema."""

    @pytest.mark.parametrize("field_name", sorted(DASHBOARD_OVERVIEW_OPTIONAL_FIELDS))
    def test_dashboard_overview_period_fields_are_optional(self, field_name):
        payload = {
            "as_of_date": "2026-10-05",
            "today": None,
            "this_week": None,
            "this_month": None,
        }
        parsed = DRFDashboardOverviewResponse(**payload)
        assert getattr(parsed, field_name) is None

    @pytest.mark.parametrize("field_name", sorted(PROFILE_OPTIONAL_FIELDS))
    def test_profile_fields_are_optional(self, field_name):
        payload = {
            "id": "p1",
            "first_name": "Ana",
            "last_name": "Silva",
            "timezone": "UTC",
            "language": "pt-BR",
            "currency": "BRL",
            "date_format": "DD/MM/YYYY",
            "avatar_url": None,
            "bio": None,
        }
        parsed = DRFProfileResponse(**payload)
        assert getattr(parsed, field_name) is None

    def test_dashboard_overview_omitted_periods_default_to_none(self):
        """The backend may omit the period keys entirely, not just null them."""
        parsed = DRFDashboardOverviewResponse(as_of_date="2026-10-05")
        assert parsed.today is None
        assert parsed.this_week is None
        assert parsed.this_month is None


class TestOverridesCatalogue:
    def test_every_override_names_a_field_that_exists_on_the_schema(self):
        for schema_name, entries in OVERRIDES_BY_SCHEMA.items():
            known = set(_schema_field_names(schema_name))
            unknown = set(entries) - known
            assert not unknown, f"{schema_name}: unknown fields {unknown}"

    def test_catalogue_covers_the_known_divergences(self):
        assert set(DASHBOARD_OVERVIEW_OPTIONAL_FIELDS) == {
            "today",
            "this_week",
            "this_month",
        }
        assert set(PROFILE_OPTIONAL_FIELDS) == {"avatar_url", "bio"}

    def test_describe_mentions_every_schema(self):
        report = describe()
        for schema_name in OVERRIDES_BY_SCHEMA:
            assert schema_name in report


class TestGeneratedContract:
    def test_generated_contract_is_importable_dataclasses(self):
        from src.contract import types

        assert is_dataclass(types.DashboardOverview)
        assert is_dataclass(types.Profile)

    def test_generated_contract_reproduces_the_documented_divergence(self):
        """Guard the premise of the catalogue.

        If a future backend change makes these fields correctly optional in the
        schema, this test fails and prompts removing the override instead of
        leaving a stale entry behind.
        """
        from src.contract import types

        overview = {f.name: f for f in fields(types.DashboardOverview)}
        for field_name in DASHBOARD_OVERVIEW_OPTIONAL_FIELDS:
            assert not _is_optional(overview[field_name].type), (
                f"DashboardOverview.{field_name} is now optional in the schema; "
                "update src/contract/overrides.py"
            )

        profile = {f.name: f for f in fields(types.Profile)}
        for field_name in PROFILE_OPTIONAL_FIELDS:
            assert not _is_optional(profile[field_name].type), (
                f"Profile.{field_name} is now optional in the schema; "
                "update src/contract/overrides.py"
            )


def _schema_field_names(schema_name: str) -> tuple[str, ...]:
    from pydantic import BaseModel

    mapping = {
        "DashboardOverview": DRFDashboardOverviewResponse,
        "Profile": DRFProfileResponse,
    }
    model: type[BaseModel] = mapping[schema_name]
    return tuple(model.model_fields)


def _is_optional(annotation) -> bool:
    """True when `annotation` is `X | None`."""
    import types as _types
    import typing

    origin = typing.get_origin(annotation)
    if origin is _types.UnionType or origin is typing.Union:
        return type(None) in typing.get_args(annotation)
    return False
