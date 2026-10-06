"""Bounded context: authentication and the current session.

Owns login, logout, token refresh and the `User` entity behind them. Distinct
from `src.users`, which owns staff administration and registration.

Depends on `src.shared` only. Notably it does *not* import `src.budget`: the
login view redirects to `dashboard.index` by endpoint name, which couples the
two contexts only through the URL map that `src/interfaces/web/app.py` builds.
"""
