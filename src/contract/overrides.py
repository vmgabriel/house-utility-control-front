"""Documented divergences between the OpenAPI schema and the live DRF API.

drf-spectacular derives `required`/nullability from the serializer field
declarations, but several DRF viewsets serialise `null` for a field the schema
declares non-nullable. The generated `types.py` therefore disagrees with the
running backend, which is why it is a verification artifact only and the
hand-written pydantic schemas are the runtime source of truth.

Each entry below records the schema's claim and the behaviour observed against
a freshly registered user with zero transactions. These are the exact fields
where trusting `types.py` at runtime would raise.

`tests/integration/test_contract_overrides.py` asserts that the runtime
schemas keep these fields optional, so the divergence cannot silently grow.

Format: field name -> what the schema claims, and what the API really does.
"""

from __future__ import annotations

from typing import NamedTuple


class Override(NamedTuple):
    """One schema-vs-runtime divergence.

    Attributes:
        schema_claim: How the generated dataclass types the field.
        observed: What the live API actually returns, and when.
        runtime_type: The optional annotation the hand-written schema uses.
    """

    schema_claim: str
    observed: str
    runtime_type: str


#: `GET /api/v1/dashboard/overview/` returns all three periods as explicit
#: null for a user with no transactions in the window:
#:   {"as_of_date": "2026-10-05", "today": null, "this_week": null,
#:    "this_month": null}
#: `types.py` declares them as required, non-nullable `DashboardSummary`.
#: Consuming that would AttributeError on `None.period` in
#: `src/infrastructure/api/mappers.py` and surface as a 500 on the dashboard.
DASHBOARD_OVERVIEW_OPTIONAL_FIELDS: dict[str, Override] = {
    "today": Override(
        schema_claim="today: DashboardSummary",
        observed="null when the user has no transactions today",
        runtime_type="DRFDashboardSummaryResponse | None = None",
    ),
    "this_week": Override(
        schema_claim="this_week: DashboardSummary",
        observed="null when the user has no transactions this week",
        runtime_type="DRFDashboardSummaryResponse | None = None",
    ),
    "this_month": Override(
        schema_claim="this_month: DashboardSummary",
        observed="null when the user has no transactions this month",
        runtime_type="DRFDashboardSummaryResponse | None = None",
    ),
}

#: `GET /api/v1/profile/me/` returns both fields as null for a user who has
#: never set them:
#:   {..., "avatar_url": null, "bio": null, ...}
#: `types.py` declares them as required, non-nullable `str`.
PROFILE_OPTIONAL_FIELDS: dict[str, Override] = {
    "avatar_url": Override(
        schema_claim="avatar_url: str",
        observed="null until the user uploads an avatar",
        runtime_type="str | None = None",
    ),
    "bio": Override(
        schema_claim="bio: str",
        observed="null until the user writes a bio",
        runtime_type="str | None = None",
    ),
}

#: Every divergence, keyed by OpenAPI schema name, for reporting.
OVERRIDES_BY_SCHEMA: dict[str, dict[str, Override]] = {
    "DashboardOverview": DASHBOARD_OVERVIEW_OPTIONAL_FIELDS,
    "Profile": PROFILE_OPTIONAL_FIELDS,
}


def describe() -> str:
    """Render all known divergences as a human-readable report."""
    lines = []
    for schema, fields in OVERRIDES_BY_SCHEMA.items():
        lines.append(f"{schema}:")
        for name, override in fields.items():
            lines.append(f"  {name}")
            lines.append(f"    schema:   {override.schema_claim}")
            lines.append(f"    observed: {override.observed}")
            lines.append(f"    runtime:  {override.runtime_type}")
    return "\n".join(lines)
