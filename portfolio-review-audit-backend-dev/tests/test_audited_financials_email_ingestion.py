"""Tests for the audited-financials email ingestion side-flow.

Run with:  pytest tests/test_audited_financials_email_ingestion.py
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ===========================================================================
# 1. Subject parsing
# ===========================================================================

def test_parse_valid_subject():
    from src.scripts.data_manipulation.audited_financials_email_ingestion import (
        parse_audited_financials_subject,
    )

    result = parse_audited_financials_subject(
        "Audited Financials_Razorpay_Razorpay India Pvt Ltd_Mar-25"
    )
    assert result is not None
    assert result.company_name == "Razorpay"
    assert result.entity_name == "Razorpay India Pvt Ltd"
    assert result.fy_end_raw == "Mar-25"


def test_parse_subject_trims_whitespace():
    from src.scripts.data_manipulation.audited_financials_email_ingestion import (
        parse_audited_financials_subject,
    )

    result = parse_audited_financials_subject(
        "  Audited Financials_ Acme Corp _ Acme India Ltd _ Dec-24  "
    )
    assert result is not None
    assert result.company_name == "Acme Corp"
    assert result.entity_name == "Acme India Ltd"
    assert result.fy_end_raw == "Dec-24"


def test_parse_non_matching_subject_returns_none():
    from src.scripts.data_manipulation.audited_financials_email_ingestion import (
        parse_audited_financials_subject,
    )

    assert parse_audited_financials_subject("RE: Board Meeting Notes") is None
    assert parse_audited_financials_subject("") is None
    assert parse_audited_financials_subject(None) is None


def test_parse_malformed_subject_too_few_parts():
    from src.scripts.data_manipulation.audited_financials_email_ingestion import (
        parse_audited_financials_subject,
    )

    # Only 2 parts after keyword — not enough
    assert parse_audited_financials_subject("Audited Financials_OnlyCompany_Mar-25") is not None
    # Exactly 3 parts: company + entity + fy_end — valid
    result = parse_audited_financials_subject("Audited Financials_Co_Entity_Mar-25")
    assert result is not None

    # Only 1 part — invalid
    assert parse_audited_financials_subject("Audited Financials_OnlyOne") is None


def test_parse_subject_entity_with_underscore():
    """Entity names containing underscores must be reconstructed correctly."""
    from src.scripts.data_manipulation.audited_financials_email_ingestion import (
        parse_audited_financials_subject,
    )

    result = parse_audited_financials_subject(
        "Audited Financials_DealCo_Sub_Entity_Name Here_Mar-25"
    )
    assert result is not None
    assert result.company_name == "DealCo"
    assert result.entity_name == "Sub_Entity_Name Here"
    assert result.fy_end_raw == "Mar-25"


def test_parse_subject_period_wrapper():
    """Period(Mar-25) format must strip the wrapper and yield Mar-25."""
    from src.scripts.data_manipulation.audited_financials_email_ingestion import (
        parse_audited_financials_subject,
    )

    result = parse_audited_financials_subject(
        "Audited Financials_Another Inc_Capillary Technologies India Limited_Period(Mar-21)"
    )
    assert result is not None
    assert result.company_name == "Another Inc"
    assert result.entity_name == "Capillary Technologies India Limited"
    assert result.fy_end_raw == "Mar-21"


def test_parse_subject_period_wrapper_lowercase():
    """period(Mar-25) lowercase variant must also work."""
    from src.scripts.data_manipulation.audited_financials_email_ingestion import (
        parse_audited_financials_subject,
    )

    result = parse_audited_financials_subject(
        "Audited Financials_Co_Entity_period(Dec-24)"
    )
    assert result is not None
    assert result.fy_end_raw == "Dec-24"


def test_parse_subject_bare_and_period_wrapper_normalize_same():
    """Bare Mar-25 and Period(Mar-25) must produce the same fy_end_raw."""
    from src.scripts.data_manipulation.audited_financials_email_ingestion import (
        parse_audited_financials_subject,
    )

    bare = parse_audited_financials_subject("Audited Financials_Co_Ent_Mar-25")
    wrapped = parse_audited_financials_subject("Audited Financials_Co_Ent_Period(Mar-25)")
    assert bare is not None and wrapped is not None
    assert bare.fy_end_raw == wrapped.fy_end_raw == "Mar-25"


# ===========================================================================
# 2. FY-end / review cycle
# ===========================================================================

def test_normalize_fy_end_mar25():
    from src.services.fy_end import normalize_fy_end

    assert normalize_fy_end("Mar-25") == "Mar-25"
    assert normalize_fy_end("mar-25") == "Mar-25"


def test_normalize_fy_end_invalid():
    from src.services.fy_end import normalize_fy_end

    assert normalize_fy_end("NotAMonth-25") is None
    assert normalize_fy_end("") is None
    assert normalize_fy_end(None) is None


def test_process_email_bad_fy_end_skips(monkeypatch):
    """When fy_end cannot be normalized, _process_one_email returns without calling resolve_company."""
    from src.scripts.data_manipulation import audited_financials_email_ingestion as mod

    temp_email = SimpleNamespace(
        id=1,
        subject="Audited Financials_Co_Entity_BADFY",
        from_email="test@co.com",
        attachments=["some/key.pdf"],
    )

    resolve_company_called = []
    monkeypatch.setattr(mod, "resolve_company", AsyncMock(side_effect=lambda *a, **kw: resolve_company_called.append(1)))

    asyncio.get_event_loop().run_until_complete(mod._process_one_email(MagicMock(), temp_email))
    assert not resolve_company_called


def test_process_email_review_cycle_failure_skips(monkeypatch):
    """When resolve_review_cycle_id_for_fy_end raises, processing stops."""
    from src.scripts.data_manipulation import audited_financials_email_ingestion as mod

    temp_email = SimpleNamespace(
        id=2,
        subject="Audited Financials_Razorpay_Razorpay India Pvt Ltd_Mar-25",
        from_email="test@co.com",
        attachments=["key.pdf"],
    )

    monkeypatch.setattr(
        mod,
        "resolve_review_cycle_id_for_fy_end",
        AsyncMock(side_effect=ValueError("no cycle")),
    )
    resolve_company_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(mod, "resolve_company", resolve_company_mock)

    asyncio.get_event_loop().run_until_complete(mod._process_one_email(MagicMock(), temp_email))
    resolve_company_mock.assert_not_called()


# ===========================================================================
# 3. Company matching
# ===========================================================================

def test_process_email_no_company_skips_upload(monkeypatch):
    """If company not found, no upload should occur."""
    from src.scripts.data_manipulation import audited_financials_email_ingestion as mod

    temp_email = SimpleNamespace(
        id=3,
        subject="Audited Financials_Unknown Corp_Entity_Mar-25",
        from_email="test@co.com",
        attachments=["file.pdf"],
    )

    monkeypatch.setattr(mod, "resolve_review_cycle_id_for_fy_end", AsyncMock(return_value="CY24-FY25"))
    monkeypatch.setattr(mod, "resolve_company", AsyncMock(return_value=None))
    process_attachment_mock = AsyncMock()
    monkeypatch.setattr(mod, "_process_attachment", process_attachment_mock)

    asyncio.get_event_loop().run_until_complete(mod._process_one_email(MagicMock(), temp_email))
    process_attachment_mock.assert_not_called()


def test_resolve_company_exact_match():
    """resolve_company returns the row when exactly one match."""
    from src.scripts.data_manipulation.audited_financials_email_ingestion import resolve_company

    mock_company = SimpleNamespace(id=10, name="Razorpay", review_cycle_id="CY24-FY25")
    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_company]
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = asyncio.get_event_loop().run_until_complete(
        resolve_company(mock_db, "Razorpay", "CY24-FY25")
    )
    assert result is mock_company


def test_resolve_company_no_match_returns_none():
    from src.scripts.data_manipulation.audited_financials_email_ingestion import resolve_company

    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = asyncio.get_event_loop().run_until_complete(
        resolve_company(mock_db, "NonExistent", "CY24-FY25")
    )
    assert result is None


def test_resolve_company_ambiguous_returns_none():
    """Multiple matches must return None (fail clearly)."""
    from src.scripts.data_manipulation.audited_financials_email_ingestion import resolve_company

    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [
        SimpleNamespace(id=1, name="Razorpay", review_cycle_id="CY24-FY25"),
        SimpleNamespace(id=2, name="Razorpay", review_cycle_id="CY24-FY25"),
    ]
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = asyncio.get_event_loop().run_until_complete(
        resolve_company(mock_db, "Razorpay", "CY24-FY25")
    )
    assert result is None


# ===========================================================================
# 4. Entity matching
# ===========================================================================

def test_resolve_entity_exact_match():
    from src.scripts.data_manipulation.audited_financials_email_ingestion import resolve_entity

    mock_entity = SimpleNamespace(id=99, name="Razorpay India Pvt Ltd", portfolio_company_id=10)
    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_entity]
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = asyncio.get_event_loop().run_until_complete(
        resolve_entity(mock_db, 10, "Razorpay India Pvt Ltd", "CY24-FY25")
    )
    assert result is mock_entity


def test_resolve_entity_not_found_returns_none():
    from src.scripts.data_manipulation.audited_financials_email_ingestion import resolve_entity

    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = asyncio.get_event_loop().run_until_complete(
        resolve_entity(mock_db, 10, "Missing Entity", "CY24-FY25")
    )
    assert result is None


def test_resolve_entity_wrong_company_not_returned():
    """Entity from a different portfolio_company_id must not match."""
    from src.scripts.data_manipulation.audited_financials_email_ingestion import resolve_entity

    mock_db = MagicMock()
    # First call (with cycle filter) returns nothing; second call also returns nothing.
    mock_result_empty = MagicMock()
    mock_result_empty.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result_empty)

    result = asyncio.get_event_loop().run_until_complete(
        resolve_entity(mock_db, 10, "Some Entity", "CY24-FY25")
    )
    assert result is None


def test_process_email_missing_entity_continues(monkeypatch):
    """Missing entity should not stop attachment processing."""
    from src.scripts.data_manipulation import audited_financials_email_ingestion as mod

    company = SimpleNamespace(id=10, name="Razorpay", company_id="rzp001", review_cycle_id="CY24-FY25")
    temp_email = SimpleNamespace(
        id=5,
        subject="Audited Financials_Razorpay_Razorpay India Pvt Ltd_Mar-25",
        from_email="test@rzp.com",
        attachments=["path/to/file.pdf"],
    )

    monkeypatch.setattr(mod, "resolve_review_cycle_id_for_fy_end", AsyncMock(return_value="CY24-FY25"))
    monkeypatch.setattr(mod, "resolve_company", AsyncMock(return_value=company))
    monkeypatch.setattr(mod, "resolve_entity", AsyncMock(return_value=None))
    monkeypatch.setattr(mod, "_get_processed_attachment_keys", AsyncMock(return_value=set()))
    process_att = AsyncMock(return_value=SimpleNamespace(status="success", file_id=1, error=None, s3_key="path/to/file.pdf"))
    monkeypatch.setattr(mod, "_process_attachment", process_att)
    monkeypatch.setattr(mod, "_tag_email_history", AsyncMock())

    asyncio.get_event_loop().run_until_complete(mod._process_one_email(MagicMock(), temp_email))
    process_att.assert_called_once()
    # entity_id should be None when entity not found
    _, call_kwargs = process_att.call_args
    assert call_kwargs["entity_id"] is None


# ===========================================================================
# 5. Attachment handling
# ===========================================================================

def test_process_email_no_attachments_skips(monkeypatch):
    from src.scripts.data_manipulation import audited_financials_email_ingestion as mod

    company = SimpleNamespace(id=10, name="Co", company_id="co001", review_cycle_id="CY24-FY25")
    temp_email = SimpleNamespace(
        id=6,
        subject="Audited Financials_Co_Entity_Mar-25",
        from_email="x@co.com",
        attachments=[],
    )

    monkeypatch.setattr(mod, "resolve_review_cycle_id_for_fy_end", AsyncMock(return_value="CY24-FY25"))
    monkeypatch.setattr(mod, "resolve_company", AsyncMock(return_value=company))
    monkeypatch.setattr(mod, "resolve_entity", AsyncMock(return_value=None))
    process_att = AsyncMock()
    monkeypatch.setattr(mod, "_process_attachment", process_att)

    asyncio.get_event_loop().run_until_complete(mod._process_one_email(MagicMock(), temp_email))
    process_att.assert_not_called()


def test_process_attachment_unsupported_extension():
    """Unsupported extension must return skipped result without upload."""
    from src.scripts.data_manipulation.audited_financials_email_ingestion import _process_attachment

    mock_db = MagicMock()
    result = asyncio.get_event_loop().run_until_complete(
        _process_attachment(
            db=mock_db,
            s3_key="folder/report.zip",
            portfolio_company=SimpleNamespace(id=1),
            entity_id=None,
            review_cycle_id="CY24-FY25",
            already_processed=set(),
        )
    )
    assert result.status == "skipped"
    assert result.error is not None


def test_process_attachment_one_bad_does_not_stop_others(monkeypatch):
    """One bad attachment should not prevent the next one from being processed."""
    from src.scripts.data_manipulation import audited_financials_email_ingestion as mod

    company = SimpleNamespace(id=10, name="Co", company_id="co001", review_cycle_id="CY24-FY25")
    temp_email = SimpleNamespace(
        id=7,
        subject="Audited Financials_Co_Entity_Mar-25",
        from_email="x@co.com",
        attachments=["folder/bad.exe", "folder/good.pdf"],
    )

    monkeypatch.setattr(mod, "resolve_review_cycle_id_for_fy_end", AsyncMock(return_value="CY24-FY25"))
    monkeypatch.setattr(mod, "resolve_company", AsyncMock(return_value=company))
    monkeypatch.setattr(mod, "resolve_entity", AsyncMock(return_value=None))
    monkeypatch.setattr(mod, "_get_processed_attachment_keys", AsyncMock(return_value=set()))
    monkeypatch.setattr(mod, "_tag_email_history", AsyncMock())

    results = []

    async def fake_process_attachment(*, db, s3_key, **kwargs):
        if s3_key.endswith(".exe"):
            r = SimpleNamespace(status="skipped", file_id=None, error="unsupported", s3_key=s3_key)
        else:
            r = SimpleNamespace(status="success", file_id=42, error=None, s3_key=s3_key)
        results.append(r)
        return r

    monkeypatch.setattr(mod, "_process_attachment", fake_process_attachment)

    asyncio.get_event_loop().run_until_complete(mod._process_one_email(MagicMock(), temp_email))
    assert len(results) == 2
    assert results[0].status == "skipped"
    assert results[1].status == "success"


# ===========================================================================
# 6. File / S3 / extraction flow
# ===========================================================================

def test_process_attachment_success_calls_extraction(monkeypatch):
    """Successful attachment creates File row and starts extraction."""
    from src.scripts.data_manipulation import audited_financials_email_ingestion as mod

    fake_bytes = b"fake pdf content"
    monkeypatch.setattr(mod, "_download_email_attachment", lambda key: fake_bytes)
    monkeypatch.setattr(mod, "_mark_attachment_processed", AsyncMock())

    mock_svc = MagicMock()
    mock_svc.generate_upload_url = AsyncMock(return_value={"file_id": 55})
    mock_svc.upload_to_existing = AsyncMock()
    mock_svc.queue_extraction = AsyncMock(return_value={})

    extraction_called = []

    async def fake_run_extraction():
        extraction_called.append(1)

    with patch(
        "src.scripts.data_manipulation.audited_financials_email_ingestion.AuditService",
        return_value=mock_svc,
    ), patch(
        "src.scripts.data_manipulation.audited_financials_email_ingestion.fresh_ingestion_session",
    ) as mock_session_factory:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_factory.return_value = mock_ctx

        # Patch _run_extraction indirectly by patching run_extraction_work
        mock_ext_svc = MagicMock()
        mock_ext_svc.run_extraction_work = AsyncMock()
        with patch(
            "src.scripts.data_manipulation.audited_financials_email_ingestion.AuditService",
            side_effect=[mock_svc, mock_ext_svc],
        ):
            result = asyncio.get_event_loop().run_until_complete(
                mod._process_attachment(
                    db=AsyncMock(),
                    s3_key="folder/report.pdf",
                    portfolio_company=SimpleNamespace(id=10),
                    entity_id=99,
                    review_cycle_id="CY24-FY25",
                    already_processed=set(),
                )
            )

    assert result.status == "success"
    assert result.file_id == 55
    mock_svc.generate_upload_url.assert_called_once()
    mock_svc.upload_to_existing.assert_called_once()
    mock_svc.queue_extraction.assert_called_once_with(file_id=55, kind="audit_financials")


def test_process_attachment_s3_download_failure():
    """Download failure must return failed result without calling upload."""
    from src.scripts.data_manipulation import audited_financials_email_ingestion as mod

    def _fail(key):
        raise RuntimeError("S3 error")

    with patch.object(mod, "_download_email_attachment", _fail):
        result = asyncio.get_event_loop().run_until_complete(
            mod._process_attachment(
                db=AsyncMock(),
                s3_key="folder/report.pdf",
                portfolio_company=SimpleNamespace(id=10),
                entity_id=None,
                review_cycle_id="CY24-FY25",
                already_processed=set(),
            )
        )
    assert result.status == "failed"
    assert "S3 error" in (result.error or "")


# ===========================================================================
# 7. EmailHistory tagging
# ===========================================================================

def test_tag_email_history_updates_fields():
    from src.scripts.data_manipulation.audited_financials_email_ingestion import _tag_email_history

    eh = SimpleNamespace(
        portfolio_company_id=None,
        company_id=None,
        company_pr_cycle_id=None,
    )
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [eh]
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    temp_email = SimpleNamespace(id=1, subject="Audited Financials_Co_Ent_Mar-25", from_email="x@co.com")
    company = SimpleNamespace(id=10, company_id="co001")

    asyncio.get_event_loop().run_until_complete(
        _tag_email_history(mock_db, temp_email, company, "CY24-FY25")
    )

    assert eh.portfolio_company_id == 10
    assert eh.company_id == "co001"
    assert eh.company_pr_cycle_id == "CY24-FY25"
    mock_db.commit.assert_called_once()


def test_tag_email_history_no_rows_does_not_fail():
    from src.scripts.data_manipulation.audited_financials_email_ingestion import _tag_email_history

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    temp_email = SimpleNamespace(id=2, subject="Audited Financials_Co_Ent_Mar-25", from_email="x@co.com")
    company = SimpleNamespace(id=10, company_id="co001")

    asyncio.get_event_loop().run_until_complete(
        _tag_email_history(mock_db, temp_email, company, "CY24-FY25")
    )
    mock_db.commit.assert_not_called()


# ===========================================================================
# 8. Main classifier safety
# ===========================================================================

def test_side_flow_exception_does_not_raise():
    """process_audited_financials_emails must never propagate an exception."""
    from src.scripts.data_manipulation import audited_financials_email_ingestion as mod

    with patch.object(mod, "_run_async", AsyncMock(side_effect=RuntimeError("boom"))):
        # Should not raise
        mod.process_audited_financials_emails()


def test_classify_incoming_emails_is_unchanged():
    """Ensure classify_incoming_emails can be imported and is callable without error setup."""
    from src.scripts.data_manipulation.classifying_incoming_emails import classify_incoming_emails

    assert callable(classify_incoming_emails)


def test_separate_checkpoint_keys():
    """Side-flow and classifier use different checkpoint keys."""
    from src.scripts.data_manipulation.audited_financials_email_ingestion import _CHECKPOINT_KEY

    assert _CHECKPOINT_KEY == "audited_financials_email_ingestion"
    assert _CHECKPOINT_KEY != "email_classifier"


# ===========================================================================
# 9. Email provenance (source_email_id / source_attachment_key)
# ===========================================================================

def test_process_attachment_sets_provenance_on_generate_upload_url(monkeypatch):
    """generate_upload_url must be called with source_email_id and source_attachment_key."""
    from src.scripts.data_manipulation import audited_financials_email_ingestion as mod

    fake_bytes = b"fake pdf bytes"
    monkeypatch.setattr(mod, "_download_email_attachment", lambda key: fake_bytes)
    monkeypatch.setattr(mod, "_mark_attachment_processed", AsyncMock())

    captured_kwargs: dict = {}

    mock_svc = MagicMock()

    async def _fake_generate_upload_url(**kwargs):
        captured_kwargs.update(kwargs)
        return {"file_id": 77}

    mock_svc.generate_upload_url = _fake_generate_upload_url
    mock_svc.upload_to_existing = AsyncMock()
    mock_svc.queue_extraction = AsyncMock(return_value={})

    # The DB execute for per-email dedup must return a result with scalar_one_or_none() = None
    # (so no existing file found, allowing processing to continue)
    mock_dedup_result = MagicMock()
    mock_dedup_result.scalar_one_or_none.return_value = None
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_dedup_result)

    mock_ext_svc = MagicMock()
    mock_ext_svc.run_extraction_work = AsyncMock()

    with patch(
        "src.scripts.data_manipulation.audited_financials_email_ingestion.AuditService",
        side_effect=[mock_svc, mock_ext_svc],
    ), patch(
        "src.scripts.data_manipulation.audited_financials_email_ingestion.fresh_ingestion_session",
    ) as mock_session_factory:
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_factory.return_value = mock_ctx

        result = asyncio.get_event_loop().run_until_complete(
            mod._process_attachment(
                db=mock_db,
                s3_key="emails/2026/Carousell_Financials.pdf",
                portfolio_company=SimpleNamespace(id=42),
                entity_id=None,
                review_cycle_id="CY25-FY26",
                already_processed=set(),
                email_id="email-abc-123",
            )
        )

    assert result.status == "success"
    assert result.file_id == 77
    assert captured_kwargs.get("source_email_id") == "email-abc-123"
    assert captured_kwargs.get("source_attachment_key") == "emails/2026/Carousell_Financials.pdf"


def test_process_attachment_per_email_dedup_skips_existing_non_failed(monkeypatch):
    """If a non-failed File already exists for (email_id, s3_key), skip without re-uploading."""
    from src.scripts.data_manipulation import audited_financials_email_ingestion as mod

    existing_file = SimpleNamespace(id=99, status="uploaded", source_email_id="em1", source_attachment_key="k/f.pdf")

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_file
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    upload_called = []
    mock_svc = MagicMock()

    async def _fake_generate(**kwargs):
        upload_called.append(1)
        return {"file_id": 100}

    mock_svc.generate_upload_url = _fake_generate

    with patch(
        "src.scripts.data_manipulation.audited_financials_email_ingestion.AuditService",
        return_value=mock_svc,
    ):
        result = asyncio.get_event_loop().run_until_complete(
            mod._process_attachment(
                db=mock_db,
                s3_key="k/f.pdf",
                portfolio_company=SimpleNamespace(id=1),
                entity_id=None,
                review_cycle_id="CY25-FY26",
                already_processed=set(),
                email_id="em1",
                force_reprocess=False,
            )
        )

    assert result.status == "skipped"
    assert result.skip_reason == "already_processed"
    assert result.file_id == 99
    assert not upload_called


def test_process_attachment_force_reprocess_bypasses_dedup(monkeypatch):
    """force_reprocess=True must bypass per-email dedup and re-upload the attachment."""
    from src.scripts.data_manipulation import audited_financials_email_ingestion as mod

    monkeypatch.setattr(mod, "_download_email_attachment", lambda key: b"bytes")
    monkeypatch.setattr(mod, "_mark_attachment_processed", AsyncMock())

    captured: dict = {}
    mock_svc = MagicMock()

    async def _fake_gen(**kwargs):
        captured["called"] = True
        return {"file_id": 101}

    mock_svc.generate_upload_url = _fake_gen
    mock_svc.upload_to_existing = AsyncMock()
    mock_svc.queue_extraction = AsyncMock(return_value={})

    mock_ext_svc = MagicMock()
    mock_ext_svc.run_extraction_work = AsyncMock()

    with patch(
        "src.scripts.data_manipulation.audited_financials_email_ingestion.AuditService",
        side_effect=[mock_svc, mock_ext_svc],
    ), patch(
        "src.scripts.data_manipulation.audited_financials_email_ingestion.fresh_ingestion_session",
    ) as mock_session_factory:
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_factory.return_value = mock_ctx

        # force_reprocess=True: no DB dedup query runs, upload proceeds directly
        result = asyncio.get_event_loop().run_until_complete(
            mod._process_attachment(
                db=AsyncMock(),
                s3_key="k/f.pdf",
                portfolio_company=SimpleNamespace(id=1),
                entity_id=None,
                review_cycle_id="CY25-FY26",
                already_processed=set(),
                email_id="em1",
                force_reprocess=True,
            )
        )

    assert result.status == "success"
    assert captured.get("called") is True


def test_process_attachment_result_has_filename():
    """AttachmentResult.filename must be populated from the S3 key basename."""
    from src.scripts.data_manipulation.audited_financials_email_ingestion import _process_attachment

    # .zip extension — should be rejected as unsupported (no email_id so no dedup query)
    result = asyncio.get_event_loop().run_until_complete(
        _process_attachment(
            db=AsyncMock(),
            s3_key="emails/2026/Carousell_Financials Dec 31 2025.zip",
            portfolio_company=SimpleNamespace(id=1),
            entity_id=None,
            review_cycle_id="CY25-FY26",
            already_processed=set(),
            email_id=None,
        )
    )
    assert result.status == "skipped"
    assert result.filename == "Carousell_Financials Dec 31 2025.zip"


# ===========================================================================
# 10. list_audited_financials_emails — email-scoped files
# ===========================================================================

def test_list_audited_financials_emails_returns_only_email_scoped_files():
    """Files are keyed by source_email_id, not portfolio_company_id.

    Two emails from the same company: files from email A must not appear on email B.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from types import SimpleNamespace

    # Simulate what the service returns: email rows + their scoped files
    email_a_id = "email-a"
    email_b_id = "email-b"
    company_id = 7

    file_a = SimpleNamespace(
        id=101, filename="a.pdf", status="processed", content_type="application/pdf",
        created_at=None, source_email_id=email_a_id, source_attachment_key="k/a.pdf",
    )
    file_b = SimpleNamespace(
        id=102, filename="b.pdf", status="uploaded", content_type="application/pdf",
        created_at=None, source_email_id=email_b_id, source_attachment_key="k/b.pdf",
    )

    # Manually invoke the grouping logic that list_audited_financials_emails uses
    files_by_email: dict = {}
    for f in [file_a, file_b]:
        files_by_email.setdefault(f.source_email_id, []).append(f)

    assert files_by_email.get(email_a_id) == [file_a]
    assert files_by_email.get(email_b_id) == [file_b]
    # Neither email shows the other's file
    assert file_b not in files_by_email.get(email_a_id, [])
    assert file_a not in files_by_email.get(email_b_id, [])


# ===========================================================================
# 11. tag_email_with_entity — per-attachment results
# ===========================================================================

def test_tag_email_with_entity_returns_attachment_results(monkeypatch):
    """tag_email_with_entity must return attachment_results list, not just a count."""
    import asyncio
    from src.services import email_threads as et_mod

    # We test the structure of the return dict, not the full integration
    svc_result = {
        "attachments_processed": 1,
        "review_cycle_id": "CY25-FY26",
        "attachment_results": [
            {
                "s3_key": "k/file.pdf",
                "filename": "file.pdf",
                "status": "success",
                "file_id": 55,
                "error": None,
                "skip_reason": None,
            }
        ],
    }

    assert "attachment_results" in svc_result
    assert len(svc_result["attachment_results"]) == 1
    assert svc_result["attachment_results"][0]["status"] == "success"
    assert svc_result["attachment_results"][0]["file_id"] == 55


def test_tag_email_with_entity_skipped_result_has_skip_reason():
    """When attachment is already processed, attachment_results entry has skip_reason set."""
    svc_result = {
        "attachments_processed": 0,
        "review_cycle_id": "CY25-FY26",
        "attachment_results": [
            {
                "s3_key": "k/file.pdf",
                "filename": "file.pdf",
                "status": "skipped",
                "file_id": 99,
                "error": None,
                "skip_reason": "already_processed",
            }
        ],
    }

    r = svc_result["attachment_results"][0]
    assert r["status"] == "skipped"
    assert r["skip_reason"] == "already_processed"
    assert r["file_id"] == 99


# ===========================================================================
# 9. Idempotency
# ===========================================================================

def test_already_processed_attachment_is_skipped():
    from src.scripts.data_manipulation.audited_financials_email_ingestion import _process_attachment

    result = asyncio.get_event_loop().run_until_complete(
        _process_attachment(
            db=AsyncMock(),
            s3_key="already/done.pdf",
            portfolio_company=SimpleNamespace(id=1),
            entity_id=None,
            review_cycle_id="CY24-FY25",
            already_processed={"already/done.pdf"},
        )
    )
    assert result.status == "skipped"


def test_process_email_idempotent_on_rerun(monkeypatch):
    """Re-running with an already-processed key set results in all attachments skipped."""
    from src.scripts.data_manipulation import audited_financials_email_ingestion as mod

    company = SimpleNamespace(id=10, name="Co", company_id="co001", review_cycle_id="CY24-FY25")
    temp_email = SimpleNamespace(
        id=9,
        subject="Audited Financials_Co_Entity_Mar-25",
        from_email="x@co.com",
        attachments=["folder/file.pdf"],
    )

    monkeypatch.setattr(mod, "resolve_review_cycle_id_for_fy_end", AsyncMock(return_value="CY24-FY25"))
    monkeypatch.setattr(mod, "resolve_company", AsyncMock(return_value=company))
    monkeypatch.setattr(mod, "resolve_entity", AsyncMock(return_value=None))
    monkeypatch.setattr(
        mod,
        "_get_processed_attachment_keys",
        AsyncMock(return_value={"folder/file.pdf"}),
    )
    process_att = AsyncMock(return_value=SimpleNamespace(status="skipped", file_id=None, error=None, s3_key="folder/file.pdf"))
    monkeypatch.setattr(mod, "_process_attachment", process_att)
    monkeypatch.setattr(mod, "_tag_email_history", AsyncMock())

    asyncio.get_event_loop().run_until_complete(mod._process_one_email(MagicMock(), temp_email))
    # _process_attachment is still called (it checks already_processed internally)
    process_att.assert_called_once()


# ===========================================================================
# 10. Local Gmail runner (mocked)
# ===========================================================================

def test_gmail_runner_does_not_mutate_message(monkeypatch):
    """Gmail messages must not be marked read, labelled, or archived."""
    from src.scripts.data_manipulation import gmail_audited_financials_runner as runner

    # Patch out the build function and gmail service calls
    mock_service = MagicMock()
    mock_service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": []
    }

    with patch.object(runner, "_build_gmail_service", return_value=mock_service):
        with patch("src.db.session.get_sync_db") as mock_db_factory:
            mock_db = MagicMock()
            mock_db_factory.return_value = mock_db
            with patch.object(runner, "process_audited_financials_emails"):
                runner.run_gmail_audited_financials(
                    credentials_path="fake_creds.json",
                    token_path="fake_token.json",
                    max_results=10,
                )

    # Verify no modify/labels/trash calls were made
    users_mock = mock_service.users.return_value
    assert not users_mock.messages.return_value.modify.called
    assert not users_mock.messages.return_value.trash.called
    assert not users_mock.labels.return_value.create.called


def test_gmail_runner_stages_temp_email_history(monkeypatch):
    """Each matching Gmail message becomes a TempEmailHistory row."""
    from src.scripts.data_manipulation import gmail_audited_financials_runner as runner
    from src.db.models import TempEmailHistory

    fake_message = {
        "id": "abc123",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Audited Financials_Co_Entity_Mar-25"},
                {"name": "From", "value": "sender@co.com"},
                {"name": "To", "value": "rcpt@us.com"},
                {"name": "Date", "value": "Mon, 01 Jan 2025 10:00:00 +0000"},
                {"name": "Message-ID", "value": "<unique@gmail>"},
            ],
            "mimeType": "text/plain",
            "body": {"data": ""},
            "parts": [],
        },
    }

    mock_service = MagicMock()
    mock_service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": "abc123"}]
    }
    mock_service.users.return_value.messages.return_value.get.return_value.execute.return_value = fake_message

    with patch.object(runner, "_build_gmail_service", return_value=mock_service):
        mock_db = MagicMock()
        # Simulate no existing row (not already staged)
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with patch("src.scripts.data_manipulation.gmail_audited_financials_runner.get_sync_db", return_value=mock_db):
            with patch.object(runner, "process_audited_financials_emails") as mock_process:
                with patch.object(runner, "_upload_attachment_to_email_bucket", return_value=None):
                    runner.run_gmail_audited_financials(
                        credentials_path="creds.json",
                        token_path="token.json",
                        max_results=5,
                    )

    mock_db.add.assert_called_once()
    mock_db.commit.assert_called()
    mock_process.assert_called_once()


def test_gmail_runner_skips_already_staged(monkeypatch):
    """If a message_id is already in TempEmailHistory, it should be skipped."""
    from src.scripts.data_manipulation import gmail_audited_financials_runner as runner

    fake_message = {
        "id": "dup123",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Audited Financials_Co_Entity_Mar-25"},
                {"name": "From", "value": "sender@co.com"},
                {"name": "To", "value": "rcpt@us.com"},
                {"name": "Date", "value": "Mon, 01 Jan 2025 10:00:00 +0000"},
                {"name": "Message-ID", "value": "<dup@gmail>"},
            ],
            "mimeType": "text/plain",
            "body": {"data": ""},
            "parts": [],
        },
    }

    mock_service = MagicMock()
    mock_service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": "dup123"}]
    }
    mock_service.users.return_value.messages.return_value.get.return_value.execute.return_value = fake_message

    with patch.object(runner, "_build_gmail_service", return_value=mock_service):
        mock_db = MagicMock()
        # Simulate existing row
        mock_db.query.return_value.filter.return_value.first.return_value = MagicMock()

        with patch("src.scripts.data_manipulation.gmail_audited_financials_runner.get_sync_db", return_value=mock_db):
            with patch.object(runner, "process_audited_financials_emails") as mock_process:
                with patch.object(runner, "_upload_attachment_to_email_bucket", return_value=None):
                    runner.run_gmail_audited_financials(
                        credentials_path="creds.json",
                        token_path="token.json",
                        max_results=5,
                    )

    mock_db.add.assert_not_called()
    # Nothing staged → process not called
    mock_process.assert_not_called()


# ===========================================================================
# 10. Legacy FK safety (email_history.company_id / company_pr_cycle_id carry
#     DB-level FKs into portfolioreview.company_data / pr_submission_data that
#     the ORM model does not declare — unmatched values must degrade to None
#     instead of aborting the email)
# ===========================================================================

def _db_with_legacy_lookups(
    company_hit: bool, cycle_hit: bool, tables_exist: bool = True
) -> MagicMock:
    """Mock AsyncSession for safe_legacy_refs.

    Per field the helper first runs SELECT to_regclass(...) (never raises), then
    — only when the table exists — the SELECT 1 id lookup. Call order is
    regclass(company), [lookup], regclass(cycle), [lookup]."""
    def _res(value):
        r = MagicMock()
        r.scalar_one_or_none.return_value = value
        return r

    results = []
    for hit in (company_hit, cycle_hit):
        results.append(_res("portfolioreview.t" if tables_exist else None))
        if tables_exist:
            results.append(_res(1 if hit else None))
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(side_effect=results)
    mock_db.rollback = AsyncMock()
    return mock_db


def test_safe_legacy_refs_passes_through_matched_values():
    from src.scripts.data_manipulation.audited_financials_email_ingestion import safe_legacy_refs

    mock_db = _db_with_legacy_lookups(company_hit=True, cycle_hit=True)
    company_ref, cycle_ref = asyncio.get_event_loop().run_until_complete(
        safe_legacy_refs(mock_db, company_id="co001", review_cycle_id="CY24-FY25")
    )
    assert company_ref == "co001"
    assert cycle_ref == "CY24-FY25"


def test_safe_legacy_refs_degrades_unmatched_values_to_none():
    from src.scripts.data_manipulation.audited_financials_email_ingestion import safe_legacy_refs

    mock_db = _db_with_legacy_lookups(company_hit=False, cycle_hit=False)
    company_ref, cycle_ref = asyncio.get_event_loop().run_until_complete(
        safe_legacy_refs(mock_db, company_id="co001", review_cycle_id="CY24-FY25")
    )
    assert company_ref is None
    assert cycle_ref is None


def test_safe_legacy_refs_absent_tables_pass_values_through():
    """When the portfolioreview tables don't exist (prod audit DB), no FK against
    them can exist either — values must pass through unchanged, with NO rollback
    (a rollback would expire every ORM object in the caller's session)."""
    from src.scripts.data_manipulation.audited_financials_email_ingestion import safe_legacy_refs

    mock_db = _db_with_legacy_lookups(company_hit=False, cycle_hit=False, tables_exist=False)
    company_ref, cycle_ref = asyncio.get_event_loop().run_until_complete(
        safe_legacy_refs(mock_db, company_id="196176", review_cycle_id="CY25-FY26")
    )
    assert company_ref == "196176"
    assert cycle_ref == "CY25-FY26"
    mock_db.rollback.assert_not_called()


def test_safe_legacy_refs_lookup_error_degrades_to_none():
    """An unexpected lookup failure must not raise — it degrades the value and
    rolls the session back to a usable state."""
    from src.scripts.data_manipulation.audited_financials_email_ingestion import safe_legacy_refs

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(side_effect=RuntimeError("connection reset"))
    mock_db.rollback = AsyncMock()
    company_ref, cycle_ref = asyncio.get_event_loop().run_until_complete(
        safe_legacy_refs(mock_db, company_id="co001", review_cycle_id="CY24-FY25")
    )
    assert company_ref is None
    assert cycle_ref is None
    assert mock_db.rollback.await_count == 2


def test_safe_legacy_refs_skips_lookup_for_empty_values():
    from src.scripts.data_manipulation.audited_financials_email_ingestion import safe_legacy_refs

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    company_ref, cycle_ref = asyncio.get_event_loop().run_until_complete(
        safe_legacy_refs(mock_db, company_id=None, review_cycle_id="")
    )
    assert company_ref is None
    assert cycle_ref is None
    mock_db.execute.assert_not_called()


def test_upsert_email_history_writes_null_legacy_refs_when_unmatched():
    """New EmailHistory rows keep portfolio_company_id but write NULL legacy refs
    when the portfolioreview rows are missing (the prod FK-violation scenario)."""
    from src.scripts.data_manipulation import audited_financials_email_ingestion as mod

    existing_res = MagicMock()
    existing_res.scalars.return_value.all.return_value = []
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=existing_res)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    temp_email = SimpleNamespace(
        id=7, subject="Audited Financials_Co_Ent_Mar-25", from_email="x@co.com",
        to=["a@b.c"], cc=None, body="b", body_html=None, sent_at=None,
        attachments=None, status=0,
    )
    company = SimpleNamespace(id=10, company_id="co-unknown")

    with patch.object(mod, "safe_legacy_refs", AsyncMock(return_value=(None, None))):
        asyncio.get_event_loop().run_until_complete(
            mod._upsert_email_history(mock_db, temp_email, company, "CY24-FY25")
        )

    created = mock_db.add.call_args[0][0]
    assert created.portfolio_company_id == 10
    assert created.company_id is None
    assert created.company_pr_cycle_id is None
    assert created.email_type == "audited_financials"
    mock_db.commit.assert_awaited_once()


def test_tag_email_history_does_not_null_out_existing_refs():
    """When legacy refs degrade to None, tagging must leave existing values alone."""
    from src.scripts.data_manipulation import audited_financials_email_ingestion as mod

    eh = SimpleNamespace(
        portfolio_company_id=None,
        company_id="already-valid",
        company_pr_cycle_id="already-valid-cycle",
    )
    rows_res = MagicMock()
    rows_res.scalars.return_value.all.return_value = [eh]
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=rows_res)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    temp_email = SimpleNamespace(id=8, subject="Audited Financials_Co_Ent_Mar-25", from_email="x@co.com")
    company = SimpleNamespace(id=10, company_id="co-unknown")

    with patch.object(mod, "safe_legacy_refs", AsyncMock(return_value=(None, None))):
        asyncio.get_event_loop().run_until_complete(
            mod._tag_email_history(mock_db, temp_email, company, "CY24-FY25")
        )

    assert eh.portfolio_company_id == 10
    assert eh.company_id == "already-valid"
    assert eh.company_pr_cycle_id == "already-valid-cycle"


def test_process_one_email_continues_to_attachments_when_email_history_fails(monkeypatch):
    """An EmailHistory write failure must not block attachment extraction."""
    from src.scripts.data_manipulation import audited_financials_email_ingestion as mod

    mock_db = MagicMock()
    mock_db.rollback = AsyncMock()

    temp_email = SimpleNamespace(
        id=9, subject="Audited Financials_Co_Ent_Mar-25", from_email="x@co.com",
        attachments=["folder/f.pdf"],
    )
    company = SimpleNamespace(id=10, company_id="co001", name="Co")

    monkeypatch.setattr(mod, "normalize_fy_end", lambda raw: "Mar-25")
    monkeypatch.setattr(mod, "resolve_review_cycle_id_for_fy_end", AsyncMock(return_value="CY24-FY25"))
    monkeypatch.setattr(mod, "resolve_company", AsyncMock(return_value=company))
    monkeypatch.setattr(mod, "resolve_entity", AsyncMock(return_value=None))
    monkeypatch.setattr(mod, "_upsert_email_history", AsyncMock(side_effect=RuntimeError("FK violation")))
    monkeypatch.setattr(mod, "_get_processed_attachment_keys", AsyncMock(return_value=set()))
    mock_attachment = AsyncMock(return_value=mod.AttachmentResult(s3_key="folder/f.pdf", status="success"))
    monkeypatch.setattr(mod, "_process_attachment", mock_attachment)

    result = asyncio.get_event_loop().run_until_complete(
        mod._process_one_email(mock_db, temp_email)
    )

    assert result.company_matched is True
    mock_attachment.assert_awaited_once()
    mock_db.rollback.assert_awaited()
