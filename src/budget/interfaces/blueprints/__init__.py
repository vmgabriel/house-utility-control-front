"""Budget Flask blueprints: dashboard and transactions."""

from .dashboard import dashboard_bp
from .transactions import transactions_bp

__all__ = ["dashboard_bp", "transactions_bp"]
