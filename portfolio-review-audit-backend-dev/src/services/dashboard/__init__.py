"""Dashboard analytics services.

The dashboard reads live data from the database via :class:`DbDashboardProvider`.
``get_dashboard_provider`` returns a single shared instance.
"""
from __future__ import annotations

from src.services.dashboard.base import DashboardProvider
from src.services.dashboard.db_provider import DbDashboardProvider

_instance: DashboardProvider | None = None


def get_dashboard_provider() -> DashboardProvider:
    global _instance
    if _instance is None:
        _instance = DbDashboardProvider()
    return _instance


__all__ = ["get_dashboard_provider", "DashboardProvider"]
