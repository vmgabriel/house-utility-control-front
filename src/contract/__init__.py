"""API contract types generated from the DRF OpenAPI schema.

`types.py` in this package is auto-generated (see its header). It exists to
detect drift between the BFF and the backend, not to be imported by the
runtime code path. Nothing under `src/` outside this package should import
from `src.contract.types`.
"""

from .overrides import (
    DASHBOARD_OVERVIEW_OPTIONAL_FIELDS,
    PROFILE_OPTIONAL_FIELDS,
)

__all__ = [
    "DASHBOARD_OVERVIEW_OPTIONAL_FIELDS",
    "PROFILE_OPTIONAL_FIELDS",
]
