"""Shared SQLAlchemy filters for `File` rows stored for different purposes."""

from __future__ import annotations

from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONB

from src.db.models import File, FileOCRMetadata

# Entity / org-chart uploads use AuditService.upload_file_direct + extraction kind "org_chart";
# batch org-chart uploads (ZIP extraction) use "org_chart_batch".
# Neither should appear in audit document / file-tagging lists.
ORG_CHART_FILE_TAG = "org_chart"
ORG_CHART_BATCH_FILE_TAG = "org_chart_batch"
ORG_CHART_KIND = "org_chart"


def exclude_org_chart_uploads_clause():
    """
    Match File rows that are audit/financial documents, excluding entity-structure uploads.

    Excluded when tagged `org_chart` or `org_chart_batch`, or when
    ocr_json.kind == "org_chart".
    """
    tagged = select(File.id).where(
        cast(File.tags, JSONB).contains([ORG_CHART_FILE_TAG])
        | cast(File.tags, JSONB).contains([ORG_CHART_BATCH_FILE_TAG])
    )
    kinded = select(FileOCRMetadata.file_id).where(
        FileOCRMetadata.ocr_json["kind"].as_string() == ORG_CHART_KIND
    )
    return File.id.not_in(tagged.union(kinded))
