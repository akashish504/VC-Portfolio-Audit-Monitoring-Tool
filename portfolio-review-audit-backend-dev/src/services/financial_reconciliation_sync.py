"""Synchronous MIS helpers for batch scripts (Snowflake sync).

MIS amounts are resolved live from ``FinancialDataSnowflake`` at API read time;
this module retains a no-op entry point for callers that previously stamped recon rows.
"""

from __future__ import annotations

from sqlalchemy.orm import Session


def stamp_mis_sync_session(
    session: Session,
    *,
    portfolio_company_id: int,
    review_cycle: str,
) -> None:
    """No-op: MIS is resolved live from ``FinancialDataSnowflake`` at API read time."""
    _ = (session, portfolio_company_id, review_cycle)
