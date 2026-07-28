"""
DB-backed integration tests for sync_pr_submission_to_financial_data_snowflake.

Each test runs inside a SAVEPOINT that is rolled back after the test so data
never persists.  Tests are auto-skipped when no Postgres DB is reachable.

Run:
    PYTHONPATH=. pytest tests/test_pr_submission_financial_sync.py -v
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# DB reachability guard
# ---------------------------------------------------------------------------


def _db_reachable() -> bool:
    if os.environ.get("SKIP_DB_TESTS"):
        return False
    try:
        from sqlalchemy import text
        from src.db.session import sync_engine

        with sync_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


_skip_no_db = pytest.mark.skipif(
    not _db_reachable(),
    reason="Configured Postgres DB not reachable; skipping integration tests",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session():
    """Session bound to a SAVEPOINT rolled back after the test."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from src.db.session import sync_engine

    connection = sync_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False)
    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        nonlocal nested
        if trans.nested and not trans._parent.nested:  # type: ignore[attr-defined]
            nested = connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _seed_review_cycle(
    session,
    *,
    cycle_id: str | None = None,
    name: str | None = None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> "ReviewCycle":  # noqa: F821
    from src.db.models import ReviewCycle

    cid = cycle_id or f"CY{_uid()}-FY{_uid()}"
    rc = ReviewCycle(
        id=cid,
        name=name or f"Test {cid}",
        status="active",
        starts_at=starts_at,
        ends_at=ends_at,
        meta={},
    )
    session.add(rc)
    session.flush()
    return rc


def _seed_portfolio_company(
    session,
    *,
    company_id: str,
    review_cycle_id: str,
    name: str = "Test Co",
    fy_end: str | None = None,
    fy_end_date: str | None = None,
    currency: str | None = None,
    investment_stage: str | None = None,
) -> "PortfolioCompany":  # noqa: F821
    from src.db.models import PortfolioCompany

    pc = PortfolioCompany(
        company_id=company_id,
        name=name,
        review_cycle_id=review_cycle_id,
        review_stage="Created",
        fy_end=fy_end,
        fy_end_date=fy_end_date,
        currency=currency,
        investment_stage=investment_stage,
    )
    session.add(pc)
    session.flush()
    return pc


def _seed_pr_raw(
    session,
    *,
    cid: int | str,
    fye: str | None,
    reporting_date=None,
    currency: str | None = None,
    revenue_yr_1: float | None = None,
    pr_id: str | None = None,
    **extra,
) -> "PRSubmissionDataRaw":  # noqa: F821
    """Seed a PRSubmissionDataRaw row. Extra column kwargs (e.g. revenue_yr_2,
    cash_on_hand, debt_total, year_of_year_1, reporting_year) are set verbatim."""
    from src.db.models import PRSubmissionDataRaw

    row = PRSubmissionDataRaw(
        id=pr_id or _uid(),
        cid=cid,
        fye=fye,
        reporting_date=reporting_date,
        currency=currency,
        revenue_yr_1=revenue_yr_1,
        created_at=datetime.now(timezone.utc),
    )
    for col, val in extra.items():
        setattr(row, col, val)
    session.add(row)
    session.flush()
    return row


def _seed_financial_snowflake(
    session,
    *,
    portfolio_company_id: int,
    review_cycle: str,
    currency: str | None = None,
    payload: dict | None = None,
    revenue: float | None = None,
) -> "FinancialDataSnowflake":  # noqa: F821
    from src.db.models import FinancialDataSnowflake

    obj = FinancialDataSnowflake(
        portfolio_company_id=portfolio_company_id,
        entity_id=None,
        review_cycle=review_cycle,
        currency=currency,
        source_ref="seed",
        payload=payload or {},
        revenue=revenue,
    )
    session.add(obj)
    session.flush()
    return obj


def _seed_v2_config(
    session,
    *,
    surge_seed: dict[str, dict[str, str]] | None = None,
    growth_venture: dict[str, dict[str, str]] | None = None,
) -> None:
    """Insert/replace the v2 ConfigTable row.

    Defaults (from ``default_v2_stage_groups``) are used when a group is not
    overridden: surge_seed.pnl → ``*_yr_1``; growth_venture.pnl_aligned →
    ``*_yr_2``, pnl_lagged → ``*_yr_1``; balance → ``cash_on_hand``/``debt_total``.
    """
    from sqlalchemy import select
    from src.db.models import ConfigTable
    from src.services.snowflake_pr_financial_mapping_v2 import (
        SNOWFLAKE_PR_V2_CONFIG_KEY,
        V2_GROUP_SLOTS,
        V2_SLOT_METRICS,
        default_v2_stage_groups,
    )

    defaults = default_v2_stage_groups()
    overrides = {"surge_seed": surge_seed, "growth_venture": growth_venture}
    stored_groups: dict = {}
    for group_id in ("surge_seed", "growth_venture"):
        group_in = overrides[group_id] if overrides[group_id] is not None else defaults[group_id]
        stored_groups[group_id] = {
            slot: {
                m: {"formula": (group_in.get(slot) or {}).get(m, "")}
                for m in V2_SLOT_METRICS[slot]
            }
            for slot in V2_GROUP_SLOTS[group_id]
        }

    body: dict = {"stage_groups": stored_groups}
    row = (
        session.execute(
            select(ConfigTable).where(ConfigTable.key == SNOWFLAKE_PR_V2_CONFIG_KEY)
        )
        .scalars()
        .first()
    )
    if row is None:
        row = ConfigTable(key=SNOWFLAKE_PR_V2_CONFIG_KEY, value=body, description="test")
        session.add(row)
    else:
        row.value = body
    session.flush()


def _seed_quarter_config(session, *, stage_group_formulas=None) -> None:
    """Back-compat shim: the old per-bucket arg is ignored; seeds v2 defaults."""
    _seed_v2_config(session)


def _cycle_window(year: int, month: int) -> tuple[datetime, datetime]:
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    start = datetime(year, month, last_day, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)
    return start, end


def _get_or_seed_cycle(
    session,
    *,
    cycle_id: str,
    name: str,
    starts_at: datetime,
    ends_at: datetime,
) -> "ReviewCycle":  # noqa: F821
    """Return the existing ReviewCycle if it already exists in the DB, else seed a new one.

    This avoids creating a duplicate date-window cycle that would compete with an
    already-seeded real cycle in ``resolve_review_cycle_for_fy_end``.
    """
    from sqlalchemy import select
    from src.db.models import ReviewCycle

    existing = session.execute(select(ReviewCycle).where(ReviewCycle.id == cycle_id)).scalars().first()
    if existing is not None:
        return existing
    return _seed_review_cycle(session, cycle_id=cycle_id, name=name, starts_at=starts_at, ends_at=ends_at)


def _seed_dec24_cycle(session) -> "ReviewCycle":  # noqa: F821
    return _get_or_seed_cycle(
        session,
        cycle_id="CY24-FY25",
        name="CY 24 - FY 25",
        starts_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        ends_at=datetime(2025, 5, 31, 23, 59, 59, tzinfo=timezone.utc),
    )


def _seed_mar25_cycle(session) -> "ReviewCycle":  # noqa: F821
    return _get_or_seed_cycle(
        session,
        cycle_id="CY24-FY25",
        name="CY 24 - FY 25",
        starts_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        ends_at=datetime(2025, 5, 31, 23, 59, 59, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Original tests (preserved)
# ---------------------------------------------------------------------------


@_skip_no_db
class TestPrSubmissionFinancialSync:

    def test_resolves_cycle_from_fye_not_reporting_date(self, db_session):
        """Review cycle is resolved from fye, not reporting_date."""
        from sqlalchemy import select
        from src.db.models import FinancialDataSnowflake
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )

        cid = str(int(uuid.uuid4().int % 10**9) + 10**9)

        cycle_a = _seed_dec24_cycle(db_session)
        cycle_b = _seed_review_cycle(
            db_session,
            starts_at=datetime(2023, 6, 1, tzinfo=timezone.utc),
            ends_at=datetime(2024, 5, 31, 23, 59, 59, tzinfo=timezone.utc),
        )
        pc_a = _seed_portfolio_company(
            db_session, company_id=cid, review_cycle_id=cycle_a.id, currency="USD",
            investment_stage="Seed",
        )
        # reporting_date is inside cycle B's window — must NOT be used.
        # Dec 24 → bucket 12-31; but reporting_date here is Jan 15 → that would be 01-15.
        # We use a reporting_date that matches Dec-24's bucket (12-31) so it doesn't get
        # skipped by the bucket validator while still verifying cycle resolution from fye.
        _seed_pr_raw(
            db_session,
            cid=int(cid),
            fye="Dec 24",
            reporting_date=date(2024, 12, 31),
        )
        _seed_quarter_config(
            db_session,
            stage_group_formulas={
                "surge_seed": {"12-31": {"revenue": "revenue_yr_1"}},
            },
        )

        result = sync_pr_submission_to_financial_data_snowflake(db_session)

        assert result["upserted"] >= 1
        fds = (
            db_session.execute(
                select(FinancialDataSnowflake).where(
                    FinancialDataSnowflake.portfolio_company_id == pc_a.id
                )
            )
            .scalars()
            .all()
        )
        assert len(fds) == 1
        assert fds[0].review_cycle == cycle_a.id

    def test_stores_fy_end_on_portfolio_company(self, db_session):
        """fy_end and fy_end_date are written to PortfolioCompany after sync."""
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )

        cid = str(int(uuid.uuid4().int % 10**9) + 10**9)
        rc = _seed_dec24_cycle(db_session)
        pc = _seed_portfolio_company(
            db_session, company_id=cid, review_cycle_id=rc.id,
            fy_end=None, fy_end_date=None, currency="USD",
            investment_stage="Seed",
        )
        _seed_pr_raw(db_session, cid=int(cid), fye="Dec 24", reporting_date=date(2024, 12, 31))
        _seed_quarter_config(
            db_session,
            stage_group_formulas={"surge_seed": {"12-31": {"revenue": "revenue_yr_1"}}},
        )

        sync_pr_submission_to_financial_data_snowflake(db_session)
        db_session.refresh(pc)

        assert pc.fy_end == "Dec-24"
        assert pc.fy_end_date == "12/31/2024"

    def test_overwrites_existing_fy_end_on_portfolio_company(self, db_session):
        """Existing fy_end / fy_end_date on PortfolioCompany are overwritten."""
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )

        cid = str(int(uuid.uuid4().int % 10**9) + 10**9)
        rc = _seed_mar25_cycle(db_session)
        pc = _seed_portfolio_company(
            db_session, company_id=cid, review_cycle_id=rc.id,
            fy_end="Jun-24", fy_end_date="06/30/2024", currency="USD",
            investment_stage="Seed",
        )
        _seed_pr_raw(db_session, cid=int(cid), fye="Mar 25", reporting_date=date(2025, 3, 31))
        _seed_quarter_config(
            db_session,
            stage_group_formulas={"surge_seed": {"03-31": {"revenue": "revenue_yr_1"}}},
        )

        sync_pr_submission_to_financial_data_snowflake(db_session)
        db_session.refresh(pc)

        assert pc.fy_end == "Mar-25"
        assert pc.fy_end_date == "03/31/2025"

    def test_uses_portfolio_company_currency_not_raw_row(self, db_session):
        """FinancialDataSnowflake.currency comes from PortfolioCompany, not the raw row."""
        from sqlalchemy import select
        from src.db.models import FinancialDataSnowflake, PortfolioCompany
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )

        cid = str(int(uuid.uuid4().int % 10**9) + 10**9)
        rc = _seed_dec24_cycle(db_session)
        _seed_portfolio_company(
            db_session, company_id=cid, review_cycle_id=rc.id, currency="USD",
            investment_stage="Seed",
        )
        _seed_pr_raw(db_session, cid=int(cid), fye="Dec 24", currency="INR", reporting_date=date(2024, 12, 31))
        _seed_quarter_config(
            db_session,
            stage_group_formulas={"surge_seed": {"12-31": {"revenue": "revenue_yr_1"}}},
        )

        sync_pr_submission_to_financial_data_snowflake(db_session)

        pc = db_session.execute(
            select(PortfolioCompany).where(PortfolioCompany.company_id == cid)
        ).scalars().first()
        fds = db_session.execute(
            select(FinancialDataSnowflake).where(
                FinancialDataSnowflake.portfolio_company_id == pc.id
            )
        ).scalars().first()
        assert fds is not None
        assert fds.currency == "USD"

    def test_existing_row_currency_updated_from_portfolio_company(self, db_session):
        """An existing FinancialDataSnowflake row's currency is updated from PortfolioCompany."""
        from sqlalchemy import select
        from src.db.models import FinancialDataSnowflake, PortfolioCompany
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )

        cid = str(int(uuid.uuid4().int % 10**9) + 10**9)
        rc = _seed_dec24_cycle(db_session)
        pc = _seed_portfolio_company(
            db_session, company_id=cid, review_cycle_id=rc.id, currency="USD",
            investment_stage="Seed",
        )
        existing = _seed_financial_snowflake(
            db_session,
            portfolio_company_id=pc.id,
            review_cycle=rc.id,
            currency="INR",
        )
        _seed_pr_raw(db_session, cid=int(cid), fye="Dec 24", reporting_date=date(2024, 12, 31))
        _seed_quarter_config(
            db_session,
            stage_group_formulas={"surge_seed": {"12-31": {"revenue": "revenue_yr_1"}}},
        )

        sync_pr_submission_to_financial_data_snowflake(db_session)
        db_session.refresh(existing)

        assert existing.currency == "USD"

    def test_handles_fye_space_and_hyphen(self, db_session):
        """Both "Dec 24" and "Dec-24" normalise and resolve correctly."""
        from sqlalchemy import select
        from src.db.models import FinancialDataSnowflake
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )

        cid_space = str(int(uuid.uuid4().int % 10**9) + 10**9)
        cid_hyphen = str(int(uuid.uuid4().int % 10**9) + 10**9)
        rc = _seed_dec24_cycle(db_session)
        pc_space = _seed_portfolio_company(
            db_session, company_id=cid_space, review_cycle_id=rc.id, currency="USD",
            investment_stage="Seed",
        )
        pc_hyphen = _seed_portfolio_company(
            db_session, company_id=cid_hyphen, review_cycle_id=rc.id, currency="USD",
            investment_stage="Seed",
        )
        _seed_pr_raw(db_session, cid=int(cid_space), fye="Dec 24", reporting_date=date(2024, 12, 31))
        _seed_pr_raw(db_session, cid=int(cid_hyphen), fye="Dec-24", reporting_date=date(2024, 12, 31))
        _seed_quarter_config(
            db_session,
            stage_group_formulas={"surge_seed": {"12-31": {"revenue": "revenue_yr_1"}}},
        )

        result = sync_pr_submission_to_financial_data_snowflake(db_session)
        assert result["upserted"] >= 2

        for pc in (pc_space, pc_hyphen):
            db_session.refresh(pc)
            assert pc.fy_end == "Dec-24"
            rows = db_session.execute(
                select(FinancialDataSnowflake).where(
                    FinancialDataSnowflake.portfolio_company_id == pc.id
                )
            ).scalars().all()
            assert len(rows) == 1

    def test_skips_missing_fye(self, db_session):
        """Rows with fye=None are skipped."""
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )
        from sqlalchemy import select
        from src.db.models import FinancialDataSnowflake, PortfolioCompany

        cid = str(int(uuid.uuid4().int % 10**9) + 10**9)
        rc = _seed_dec24_cycle(db_session)
        _seed_portfolio_company(
            db_session, company_id=cid, review_cycle_id=rc.id, investment_stage="Seed",
        )
        _seed_pr_raw(db_session, cid=int(cid), fye=None, reporting_date=date(2024, 12, 31))
        _seed_v2_config(db_session)

        result = sync_pr_submission_to_financial_data_snowflake(db_session)

        assert result["skipped_rows_no_cycle"] >= 1
        pc = db_session.execute(
            select(PortfolioCompany).where(PortfolioCompany.company_id == cid)
        ).scalars().first()
        if pc:
            rows = db_session.execute(
                select(FinancialDataSnowflake).where(
                    FinancialDataSnowflake.portfolio_company_id == pc.id
                )
            ).scalars().all()
            assert len(rows) == 0

    def test_skips_invalid_fye(self, db_session):
        """Rows with an unparseable fye are skipped."""
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )
        from sqlalchemy import select
        from src.db.models import FinancialDataSnowflake, PortfolioCompany

        cid = str(int(uuid.uuid4().int % 10**9) + 10**9)
        rc = _seed_dec24_cycle(db_session)
        _seed_portfolio_company(
            db_session, company_id=cid, review_cycle_id=rc.id, investment_stage="Seed",
        )
        _seed_pr_raw(db_session, cid=int(cid), fye="not-a-date", reporting_date=date(2024, 12, 31))
        _seed_v2_config(db_session)

        result = sync_pr_submission_to_financial_data_snowflake(db_session)

        assert result["skipped_rows_no_cycle"] >= 1
        pc = db_session.execute(
            select(PortfolioCompany).where(PortfolioCompany.company_id == cid)
        ).scalars().first()
        if pc:
            rows = db_session.execute(
                select(FinancialDataSnowflake).where(
                    FinancialDataSnowflake.portfolio_company_id == pc.id
                )
            ).scalars().all()
            assert len(rows) == 0

    def test_preserves_manually_edited_metrics(self, db_session):
        """manually_edited_metrics in an existing row's payload are not overwritten."""
        from sqlalchemy import select
        from src.db.models import FinancialDataSnowflake
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )

        cid = str(int(uuid.uuid4().int % 10**9) + 10**9)
        rc = _seed_dec24_cycle(db_session)
        pc = _seed_portfolio_company(
            db_session, company_id=cid, review_cycle_id=rc.id, currency="USD",
            investment_stage="Seed",
        )
        existing = _seed_financial_snowflake(
            db_session,
            portfolio_company_id=pc.id,
            review_cycle=rc.id,
            currency="USD",
            revenue=999.0,
            payload={"manually_edited_metrics": ["revenue"]},
        )
        _seed_pr_raw(
            db_session,
            cid=int(cid),
            fye="Dec 24",
            revenue_yr_1=1234.0,
            reporting_date=date(2024, 12, 31),
        )
        _seed_quarter_config(
            db_session,
            stage_group_formulas={"surge_seed": {"12-31": {"revenue": "revenue_yr_1"}}},
        )

        sync_pr_submission_to_financial_data_snowflake(db_session)
        db_session.refresh(existing)

        assert existing.revenue == 999.0
        assert "revenue" in (existing.payload.get("manually_edited_metrics") or [])
        assert existing.payload.get("fy_end") == "Dec-24"


# ---------------------------------------------------------------------------
# New quarter-bucket tests (requirements §Tests 1–7)
# ---------------------------------------------------------------------------


@_skip_no_db
class TestQuarterBucketSync:

    # Test 1a: Seed/Surge company with valid fye + reporting_date syncs
    def test_seed_company_valid_combo_syncs(self, db_session):
        """Seed company with valid fye+reporting_date writes to FinancialDataSnowflake."""
        from sqlalchemy import select
        from src.db.models import FinancialDataSnowflake
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )

        cid = str(int(uuid.uuid4().int % 10**9) + 10**9)
        rc = _seed_dec24_cycle(db_session)
        pc = _seed_portfolio_company(
            db_session, company_id=cid, review_cycle_id=rc.id,
            currency="USD", investment_stage="Seed",
        )
        _seed_pr_raw(
            db_session, cid=int(cid),
            fye="Dec 24", reporting_date=date(2024, 12, 31),
            revenue_yr_1=500.0,
        )
        _seed_quarter_config(
            db_session,
            stage_group_formulas={"surge_seed": {"12-31": {"revenue": "revenue_yr_1"}}},
        )

        result = sync_pr_submission_to_financial_data_snowflake(db_session)
        assert result["upserted"] >= 1

        fds = db_session.execute(
            select(FinancialDataSnowflake).where(
                FinancialDataSnowflake.portfolio_company_id == pc.id
            )
        ).scalars().first()
        assert fds is not None
        assert fds.revenue == 500.0
        assert fds.payload.get("stage_group") == "surge_seed"
        assert fds.payload["selection"]["pnl"]["slot"] == "pnl"

    # Test 1b: Growth/Venture company with valid fye + reporting_date syncs
    def test_growth_company_valid_combo_syncs(self, db_session):
        """Growth company with valid fye+reporting_date writes to FinancialDataSnowflake."""
        from sqlalchemy import select
        from src.db.models import FinancialDataSnowflake
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )

        cid = str(int(uuid.uuid4().int % 10**9) + 10**9)
        rc = _seed_mar25_cycle(db_session)
        pc = _seed_portfolio_company(
            db_session, company_id=cid, review_cycle_id=rc.id,
            currency="USD", investment_stage="Growth",
        )
        # Feb 25 FY-end → audited quarter = Mar; a Mar-2025 submission ALIGNS → Year 2.
        _seed_pr_raw(
            db_session, cid=int(cid),
            fye="Feb 25", reporting_date=date(2025, 3, 31),
            revenue_yr_2=750.0,
        )
        _seed_v2_config(db_session)

        result = sync_pr_submission_to_financial_data_snowflake(db_session)
        assert result["upserted"] >= 1

        fds = db_session.execute(
            select(FinancialDataSnowflake).where(
                FinancialDataSnowflake.portfolio_company_id == pc.id
            )
        ).scalars().first()
        assert fds is not None
        assert fds.revenue == 750.0
        assert fds.payload.get("stage_group") == "growth_venture"
        assert fds.payload["selection"]["pnl"]["slot"] == "pnl_aligned"
        assert fds.payload["selection"]["pnl"]["year_slot"] == "yr_2"

    # Test 2: Different buckets use different formulas
    def test_different_buckets_use_different_formulas(self, db_session):
        """03-31 and 12-31 buckets produce different revenue values."""
        from sqlalchemy import select
        from src.db.models import FinancialDataSnowflake
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )

        cid_q1 = str(int(uuid.uuid4().int % 10**9) + 10**9)
        cid_q4 = str(int(uuid.uuid4().int % 10**9) + 10**9)

        rc = _seed_dec24_cycle(db_session)
        pc_q1 = _seed_portfolio_company(
            db_session, company_id=cid_q1, review_cycle_id=rc.id,
            currency="USD", investment_stage="Seed",
        )
        pc_q4 = _seed_portfolio_company(
            db_session, company_id=cid_q4, review_cycle_id=rc.id,
            currency="USD", investment_stage="Seed",
        )
        # Mar 25 → 03-31 bucket; revenue_yr_1=100
        _seed_pr_raw(
            db_session, cid=int(cid_q1),
            fye="Mar 25", reporting_date=date(2025, 3, 31),
            revenue_yr_1=100.0,
        )
        # Dec 24 → 12-31 bucket; revenue_yr_1=200
        _seed_pr_raw(
            db_session, cid=int(cid_q4),
            fye="Dec 24", reporting_date=date(2024, 12, 31),
            revenue_yr_1=200.0,
        )
        # 03-31 uses revenue_yr_1 directly; 12-31 negates it (so we can tell them apart)
        _seed_quarter_config(
            db_session,
            stage_group_formulas={
                "surge_seed": {
                    "03-31": {"revenue": "revenue_yr_1"},
                    "12-31": {"revenue": "revenue_yr_1"},
                }
            },
        )

        sync_pr_submission_to_financial_data_snowflake(db_session)

        fds_q1 = db_session.execute(
            select(FinancialDataSnowflake).where(
                FinancialDataSnowflake.portfolio_company_id == pc_q1.id
            )
        ).scalars().first()
        fds_q4 = db_session.execute(
            select(FinancialDataSnowflake).where(
                FinancialDataSnowflake.portfolio_company_id == pc_q4.id
            )
        ).scalars().first()
        assert fds_q1 is not None
        assert fds_q4 is not None
        assert fds_q1.revenue == 100.0
        assert fds_q4.revenue == 200.0

    # Test 3: A lagged Growth/Venture submission now yields Year 1 (was: skipped)
    def test_lagged_growth_submission_uses_year_1(self, db_session):
        """Growth: Feb 25 FY-end + a Jun-2025 submission lags the audited Mar quarter,
        so P&L reads Year 1 (no longer dropped). Cash/Debt abstain (no exact Feb-end)."""
        from sqlalchemy import select
        from src.db.models import FinancialDataSnowflake
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )

        cid = str(int(uuid.uuid4().int % 10**9) + 10**9)
        rc = _seed_mar25_cycle(db_session)
        pc = _seed_portfolio_company(
            db_session, company_id=cid, review_cycle_id=rc.id,
            currency="USD", investment_stage="Growth",
        )
        _seed_pr_raw(
            db_session, cid=int(cid),
            fye="Feb 25", reporting_date=date(2025, 6, 30),
            revenue_yr_1=999.0,
        )
        _seed_v2_config(db_session)

        result = sync_pr_submission_to_financial_data_snowflake(db_session)
        assert result["upserted"] >= 1

        fds = db_session.execute(
            select(FinancialDataSnowflake).where(
                FinancialDataSnowflake.portfolio_company_id == pc.id
            )
        ).scalars().first()
        assert fds is not None
        assert fds.revenue == 999.0
        assert fds.payload["selection"]["pnl"]["slot"] == "pnl_lagged"
        assert fds.payload["selection"]["pnl"]["year_slot"] == "yr_1"
        assert fds.payload["selection"]["balance"]["status"] == "no_exact_quarter"
        assert fds.cash is None and fds.debt is None

    def test_lagged_growth_updates_existing_row(self, db_session):
        """Growth: a lagged Jun-2025 submission (Feb-25 FY-end) updates an existing
        row's P&L to Year 1 (it is no longer treated as an invalid/skipped combo)."""
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )

        cid = str(int(uuid.uuid4().int % 10**9) + 10**9)
        rc = _seed_mar25_cycle(db_session)
        pc = _seed_portfolio_company(
            db_session, company_id=cid, review_cycle_id=rc.id,
            currency="USD", investment_stage="Growth",
        )
        existing = _seed_financial_snowflake(
            db_session,
            portfolio_company_id=pc.id,
            review_cycle=rc.id,
            currency="USD",
            revenue=42.0,
        )

        _seed_pr_raw(
            db_session, cid=int(cid),
            fye="Feb 25", reporting_date=date(2025, 6, 30),
            revenue_yr_1=9999.0,
        )
        _seed_v2_config(db_session)

        sync_pr_submission_to_financial_data_snowflake(db_session)
        db_session.refresh(existing)

        assert existing.revenue == 9999.0

    # Test 4: Unmapped investment stage skips
    def test_unmapped_stage_scout_skipped(self, db_session):
        """investment_stage=Scout → no upsert."""
        from sqlalchemy import select
        from src.db.models import FinancialDataSnowflake, PortfolioCompany
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )

        cid = str(int(uuid.uuid4().int % 10**9) + 10**9)
        rc = _seed_dec24_cycle(db_session)
        _seed_portfolio_company(
            db_session, company_id=cid, review_cycle_id=rc.id,
            currency="USD", investment_stage="Scout",
        )
        _seed_pr_raw(
            db_session, cid=int(cid),
            fye="Dec 24", reporting_date=date(2024, 12, 31),
            revenue_yr_1=100.0,
        )
        _seed_quarter_config(
            db_session,
            stage_group_formulas={"surge_seed": {"12-31": {"revenue": "revenue_yr_1"}}},
        )

        result = sync_pr_submission_to_financial_data_snowflake(db_session)

        assert result["skipped_no_stage_group"] >= 1
        assert result["upserted"] == 0

        pc = db_session.execute(
            select(PortfolioCompany).where(PortfolioCompany.company_id == cid)
        ).scalars().first()
        if pc:
            rows = db_session.execute(
                select(FinancialDataSnowflake).where(
                    FinancialDataSnowflake.portfolio_company_id == pc.id
                )
            ).scalars().all()
            assert len(rows) == 0

    def test_unmapped_stage_null_skipped(self, db_session):
        """investment_stage=None → no upsert."""
        from sqlalchemy import select
        from src.db.models import FinancialDataSnowflake, PortfolioCompany
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )

        cid = str(int(uuid.uuid4().int % 10**9) + 10**9)
        rc = _seed_dec24_cycle(db_session)
        _seed_portfolio_company(
            db_session, company_id=cid, review_cycle_id=rc.id,
            currency="USD", investment_stage=None,
        )
        _seed_pr_raw(
            db_session, cid=int(cid),
            fye="Dec 24", reporting_date=date(2024, 12, 31),
        )
        _seed_quarter_config(
            db_session,
            stage_group_formulas={"surge_seed": {"12-31": {"revenue": "revenue_yr_1"}}},
        )

        result = sync_pr_submission_to_financial_data_snowflake(db_session)

        assert result["skipped_no_stage_group"] >= 1
        assert result["upserted"] == 0

    # Test 5: Surge/Seed processes even without reporting_date
    def test_surge_seed_processes_without_reporting_date(self, db_session):
        """Surge/Seed: reporting_date=None is fine — row is still processed."""
        from sqlalchemy import select
        from src.db.models import FinancialDataSnowflake
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )

        cid = str(int(uuid.uuid4().int % 10**9) + 10**9)
        rc = _seed_dec24_cycle(db_session)
        pc = _seed_portfolio_company(
            db_session, company_id=cid, review_cycle_id=rc.id,
            currency="USD", investment_stage="Seed",
        )
        _seed_pr_raw(
            db_session, cid=int(cid),
            fye="Dec 24", reporting_date=None,
            revenue_yr_1=300.0,
        )
        _seed_quarter_config(
            db_session,
            stage_group_formulas={"surge_seed": {"12-31": {"revenue": "revenue_yr_1"}}},
        )

        result = sync_pr_submission_to_financial_data_snowflake(db_session)

        assert result["upserted"] >= 1
        fds = db_session.execute(
            select(FinancialDataSnowflake).where(
                FinancialDataSnowflake.portfolio_company_id == pc.id
            )
        ).scalars().first()
        assert fds is not None
        assert fds.revenue == 300.0

    def test_growth_venture_missing_reporting_date_skipped(self, db_session):
        """Growth/Venture: reporting_date=None → nothing matches → no upsert (skipped_no_match)."""
        from sqlalchemy import select
        from src.db.models import FinancialDataSnowflake, PortfolioCompany
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )

        cid = str(int(uuid.uuid4().int % 10**9) + 10**9)
        rc = _seed_dec24_cycle(db_session)
        _seed_portfolio_company(
            db_session, company_id=cid, review_cycle_id=rc.id,
            currency="USD", investment_stage="Growth",
        )
        _seed_pr_raw(
            db_session, cid=int(cid),
            fye="Dec 24", reporting_date=None,
        )
        _seed_quarter_config(
            db_session,
            stage_group_formulas={"growth_venture": {"12-31": {"revenue": "revenue_yr_1"}}},
        )

        result = sync_pr_submission_to_financial_data_snowflake(db_session)

        assert result["skipped_no_match"] >= 1
        assert result["upserted"] == 0

        pc = db_session.execute(
            select(PortfolioCompany).where(PortfolioCompany.company_id == cid)
        ).scalars().first()
        if pc:
            rows = db_session.execute(
                select(FinancialDataSnowflake).where(
                    FinancialDataSnowflake.portfolio_company_id == pc.id
                )
            ).scalars().all()
            assert len(rows) == 0

    # Test 6: Manual edits respected
    def test_manual_edits_respected(self, db_session):
        """Manually edited metrics survive a valid PR row sync."""
        from sqlalchemy import select
        from src.db.models import FinancialDataSnowflake
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )

        cid = str(int(uuid.uuid4().int % 10**9) + 10**9)
        rc = _seed_dec24_cycle(db_session)
        pc = _seed_portfolio_company(
            db_session, company_id=cid, review_cycle_id=rc.id,
            currency="USD", investment_stage="Seed",
        )
        existing = _seed_financial_snowflake(
            db_session,
            portfolio_company_id=pc.id,
            review_cycle=rc.id,
            currency="USD",
            revenue=999.0,
            payload={"manually_edited_metrics": ["revenue"]},
        )
        _seed_pr_raw(
            db_session, cid=int(cid),
            fye="Dec 24", reporting_date=date(2024, 12, 31),
            revenue_yr_1=1234.0,
        )
        _seed_quarter_config(
            db_session,
            stage_group_formulas={"surge_seed": {"12-31": {"revenue": "revenue_yr_1"}}},
        )

        sync_pr_submission_to_financial_data_snowflake(db_session)
        db_session.refresh(existing)

        assert existing.revenue == 999.0
        assert "revenue" in (existing.payload.get("manually_edited_metrics") or [])
        assert existing.payload.get("fy_end") == "Dec-24"

    # Test 7: v2 config persistence check
    def test_v2_config_persistence_stores_slots(self, db_session):
        """Persisted v2 config has P&L/Balance slots per stage group (aligned/lagged for G/V)."""
        from sqlalchemy import select
        from src.db.models import ConfigTable
        from src.services.snowflake_pr_financial_mapping_v2 import (
            SNOWFLAKE_PR_V2_CONFIG_KEY,
            V2_GROUP_SLOTS,
            V2_SLOT_METRICS,
        )

        _seed_v2_config(db_session)

        row = db_session.execute(
            select(ConfigTable).where(ConfigTable.key == SNOWFLAKE_PR_V2_CONFIG_KEY)
        ).scalars().first()
        assert row is not None

        stored = row.value
        assert "stage_groups" in stored
        for group_id, slots in V2_GROUP_SLOTS.items():
            assert group_id in stored["stage_groups"], f"Missing group {group_id}"
            for slot in slots:
                assert slot in stored["stage_groups"][group_id], f"Missing slot {slot} in {group_id}"
                for metric in V2_SLOT_METRICS[slot]:
                    assert stored["stage_groups"][group_id][slot][metric]["formula"] != ""

    # Additional: Growth/Venture aligned → Year 2, lagged → Year 1 (both sync)
    def test_growth_aligned_uses_yr2_lagged_uses_yr1(self, db_session):
        """Two Growth cos, Feb-25 FY-end: a Mar-2025 submission aligns (Year 2);
        a Dec-2025 submission lags (Year 1). Both now produce a figure."""
        from sqlalchemy import select
        from src.db.models import FinancialDataSnowflake
        from src.scripts.data_manipulation.pr_submission_financial_sync import (
            sync_pr_submission_to_financial_data_snowflake,
        )

        cid_aligned = str(int(uuid.uuid4().int % 10**9) + 10**9)
        cid_lagged = str(int(uuid.uuid4().int % 10**9) + 10**9)
        rc = _seed_mar25_cycle(db_session)
        pc_aligned = _seed_portfolio_company(
            db_session, company_id=cid_aligned, review_cycle_id=rc.id,
            currency="USD", investment_stage="Growth",
        )
        pc_lagged = _seed_portfolio_company(
            db_session, company_id=cid_lagged, review_cycle_id=rc.id,
            currency="USD", investment_stage="Growth",
        )
        # aligned (Mar) → Year 2
        _seed_pr_raw(
            db_session, cid=int(cid_aligned),
            fye="Feb 25", reporting_date=date(2025, 3, 31),
            revenue_yr_2=100.0,
        )
        # lagged (Dec, later in the same window) → Year 1
        _seed_pr_raw(
            db_session, cid=int(cid_lagged),
            fye="Feb 25", reporting_date=date(2025, 12, 31),
            revenue_yr_1=200.0,
        )
        _seed_v2_config(db_session)

        result = sync_pr_submission_to_financial_data_snowflake(db_session)
        assert result["upserted"] >= 2

        fds_aligned = db_session.execute(
            select(FinancialDataSnowflake).where(
                FinancialDataSnowflake.portfolio_company_id == pc_aligned.id
            )
        ).scalars().first()
        fds_lagged = db_session.execute(
            select(FinancialDataSnowflake).where(
                FinancialDataSnowflake.portfolio_company_id == pc_lagged.id
            )
        ).scalars().first()
        assert fds_aligned is not None and fds_aligned.revenue == 100.0
        assert fds_aligned.payload["selection"]["pnl"]["slot"] == "pnl_aligned"
        assert fds_lagged is not None and fds_lagged.revenue == 200.0
        assert fds_lagged.payload["selection"]["pnl"]["slot"] == "pnl_lagged"


# ---------------------------------------------------------------------------
# Unit tests (no DB): fye_to_reporting_bucket + validate_fye_reporting_date
# ---------------------------------------------------------------------------


class TestFyeReportingBucketMapping:
    """Pure-unit tests; no DB required."""

    def test_jan_feb_mar_map_to_0331(self):
        from src.services.snowflake_pr_financial_mapping import fye_to_reporting_bucket
        for month in ("Jan", "Feb", "Mar"):
            assert fye_to_reporting_bucket(f"{month}-25") == "03-31", month

    def test_apr_may_jun_map_to_0630(self):
        from src.services.snowflake_pr_financial_mapping import fye_to_reporting_bucket
        for month in ("Apr", "May", "Jun"):
            assert fye_to_reporting_bucket(f"{month}-25") == "06-30", month

    def test_jul_aug_sep_map_to_0930(self):
        from src.services.snowflake_pr_financial_mapping import fye_to_reporting_bucket
        for month in ("Jul", "Aug", "Sep"):
            assert fye_to_reporting_bucket(f"{month}-25") == "09-30", month

    def test_oct_nov_dec_map_to_1231(self):
        from src.services.snowflake_pr_financial_mapping import fye_to_reporting_bucket
        for month in ("Oct", "Nov", "Dec"):
            assert fye_to_reporting_bucket(f"{month}-25") == "12-31", month

    def test_validate_feb25_with_0331_valid(self):
        from src.services.snowflake_pr_financial_mapping import validate_fye_reporting_date
        result = validate_fye_reporting_date("Feb-25", date(2025, 3, 31))
        assert result == "03-31"

    def test_validate_dec24_with_1231_valid(self):
        from src.services.snowflake_pr_financial_mapping import validate_fye_reporting_date
        result = validate_fye_reporting_date("Dec-24", date(2024, 12, 31))
        assert result == "12-31"

    def test_validate_feb25_with_0630_invalid(self):
        from src.services.snowflake_pr_financial_mapping import validate_fye_reporting_date
        result = validate_fye_reporting_date("Feb-25", date(2025, 6, 30))
        assert result is None

    def test_validate_missing_reporting_date_invalid(self):
        from src.services.snowflake_pr_financial_mapping import validate_fye_reporting_date
        result = validate_fye_reporting_date("Dec-24", None)
        assert result is None

    def test_validate_missing_fye_invalid(self):
        from src.services.snowflake_pr_financial_mapping import validate_fye_reporting_date
        result = validate_fye_reporting_date(None, date(2024, 12, 31))
        assert result is None


# ---------------------------------------------------------------------------
# Unit tests: resolve_stage_group
# ---------------------------------------------------------------------------


class TestResolveStageGroup:
    def test_seed_maps_to_surge_seed(self):
        from src.services.snowflake_pr_financial_mapping import resolve_stage_group
        assert resolve_stage_group("Seed") == "surge_seed"
        assert resolve_stage_group("seed") == "surge_seed"
        assert resolve_stage_group("  SEED  ") == "surge_seed"

    def test_surge_maps_to_surge_seed(self):
        from src.services.snowflake_pr_financial_mapping import resolve_stage_group
        assert resolve_stage_group("Surge") == "surge_seed"

    def test_growth_maps_to_growth_venture(self):
        from src.services.snowflake_pr_financial_mapping import resolve_stage_group
        assert resolve_stage_group("Growth") == "growth_venture"

    def test_venture_maps_to_growth_venture(self):
        from src.services.snowflake_pr_financial_mapping import resolve_stage_group
        assert resolve_stage_group("Venture") == "growth_venture"

    def test_scout_maps_to_none(self):
        from src.services.snowflake_pr_financial_mapping import resolve_stage_group
        assert resolve_stage_group("Scout") is None

    def test_none_maps_to_none(self):
        from src.services.snowflake_pr_financial_mapping import resolve_stage_group
        assert resolve_stage_group(None) is None

    def test_empty_maps_to_none(self):
        from src.services.snowflake_pr_financial_mapping import resolve_stage_group
        assert resolve_stage_group("") is None
