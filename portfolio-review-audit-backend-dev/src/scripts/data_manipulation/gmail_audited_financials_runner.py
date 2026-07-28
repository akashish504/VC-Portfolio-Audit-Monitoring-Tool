"""Local/manual Gmail runner for audited-financials email ingestion.

Fetches recent Gmail messages that match the audited-financials subject pattern,
inserts them as TempEmailHistory rows (read-only on Gmail — no labels, no archive,
no mark-read), then calls process_audited_financials_emails().

Prerequisites:
    pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib

Usage (credentials from .env, no credentials.json needed):
    python -m src.scripts.data_manipulation.gmail_audited_financials_runner \
        [--token /path/to/token.json] \
        [--max-results 20]

OAuth scopes required: https://www.googleapis.com/auth/gmail.readonly

The runner intentionally does NOT:
  - mark messages as read
  - apply labels
  - archive or trash messages
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import email as stdlib_email
import json
import logging
import os
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Optional

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_SUBJECT_PREFIX = "Audited Financials"


# ---------------------------------------------------------------------------
# Gmail API helpers
# ---------------------------------------------------------------------------

def _client_config_from_env() -> dict:
    """Build the OAuth2 client config dict from environment variables."""
    from src.configs.env import settings as _settings
    import os

    client_id = os.environ.get("GMAIL_CLIENT_ID", "")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "")
    redirect_uri = os.environ.get("GMAIL_REDIRECT_URI", "http://localhost")
    project_id = os.environ.get("GMAIL_PROJECT_ID", "")
    token_uri = os.environ.get("GMAIL_TOKEN_URI", "https://oauth2.googleapis.com/token")
    auth_uri = os.environ.get("GMAIL_AUTH_URI", "https://accounts.google.com/o/oauth2/auth")
    cert_url = os.environ.get(
        "GMAIL_AUTH_PROVIDER_X509_CERT_URL",
        "https://www.googleapis.com/oauth2/v1/certs",
    )

    if not client_id or not client_secret:
        raise RuntimeError(
            "GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET must be set in the environment / .env"
        )

    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": [redirect_uri, "urn:ietf:wg:oauth:2.0:oob"],
            "auth_uri": auth_uri,
            "token_uri": token_uri,
            "project_id": project_id,
            "auth_provider_x509_cert_url": cert_url,
        }
    }


def _build_gmail_service(token_path: Optional[str] = None, credentials_path: Optional[str] = None):
    """Authenticate and return a Gmail API service object.

    Uses GMAIL_REFRESH_TOKEN from env directly — no browser flow needed.
    Falls back to token file or credentials file if env vars are incomplete.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN", "")
    client_id = os.environ.get("GMAIL_CLIENT_ID", "")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "")
    token_uri = os.environ.get("GMAIL_TOKEN_URI", "https://oauth2.googleapis.com/token")

    if refresh_token and client_id and client_secret:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=token_uri,
            client_id=client_id,
            client_secret=client_secret,
            scopes=_SCOPES,
        )
        creds.refresh(Request())
        return build("gmail", "v1", credentials=creds)

    # Fallback: token file
    if token_path and os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, _SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        return build("gmail", "v1", credentials=creds)

    raise RuntimeError(
        "GMAIL_REFRESH_TOKEN, GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET must be set in .env"
    )


def _list_matching_messages(service, max_results: int = 50) -> list[str]:
    """Return message IDs whose subject starts with 'Audited Financials'.

    Fetches recent messages from INBOX and filters by subject prefix locally,
    because Gmail's subject: query operator is case-insensitive but can miss
    emails depending on label state.
    """
    result = (
        service.users()
        .messages()
        .list(
            userId="me",
            labelIds=["INBOX"],
            maxResults=max_results,
        )
        .execute()
    )
    matched: list[str] = []
    for m in result.get("messages", []):
        meta = (
            service.users()
            .messages()
            .get(userId="me", id=m["id"], format="metadata", metadataHeaders=["Subject"])
            .execute()
        )
        subject = _header(meta.get("payload", {}), "Subject") or ""
        if subject.strip().startswith(_SUBJECT_PREFIX):
            matched.append(m["id"])
    return matched


def _fetch_message(service, message_id: str) -> dict:
    return (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )


def _header(payload: dict, name: str) -> Optional[str]:
    for h in payload.get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value")
    return None


def _decode_body(part: dict) -> str:
    data = part.get("body", {}).get("data", "")
    if not data:
        return ""
    return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")


def _extract_body(payload: dict) -> tuple[str, str]:
    """Return (plain_text, html) from a Gmail message payload."""
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        return _decode_body(payload), ""
    if mime == "text/html":
        return "", _decode_body(payload)

    plain, html = "", ""
    for part in payload.get("parts", []):
        p, h = _extract_body(part)
        plain = plain or p
        html = html or h
    return plain, html


def _upload_attachment_to_email_bucket(
    service, message_id: str, attachment_id: str, filename: str
) -> Optional[str]:
    """Download attachment from Gmail and upload to the email S3 bucket.

    Returns the S3 key on success, None on failure.
    """
    from src.configs.env import settings
    from src.utils.s3 import get_s3_client, upload_file as s3_upload_file
    import mimetypes
    import uuid

    bucket = settings.AWS_S3_EMAIL_BUCKET
    if not bucket:
        logger.error("gmail_runner: AWS_S3_EMAIL_BUCKET not configured — cannot upload attachment")
        return None

    try:
        att_data = (
            service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute()
        )
        raw = att_data.get("data", "")
        if not raw:
            logger.warning("gmail_runner: empty attachment data for %s/%s", message_id, attachment_id)
            return None
        file_bytes = base64.urlsafe_b64decode(raw + "==")
    except Exception as exc:
        logger.error("gmail_runner: failed to fetch attachment %s: %s", attachment_id, exc)
        return None

    folder_id = str(uuid.uuid4())
    safe_name = filename.replace(" ", "_") or "attachment"
    path_prefix = settings.AWS_S3_EMAIL_ATTACH_PATH or "email-attachments"
    key = f"{path_prefix}/{folder_id}/{safe_name}"
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    try:
        s3_upload_file(
            file_bytes,
            key,
            content_type=mime_type,
            bucket=bucket,
            bucket_env_var_name="AWS_S3_EMAIL_BUCKET",
        )
    except Exception as exc:
        logger.error("gmail_runner: S3 upload failed for attachment %s: %s", filename, exc)
        return None

    return key


def _parse_sent_at(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).replace(tzinfo=None)
    except Exception:
        return None


def _gmail_message_to_temp_row(service, msg: dict) -> Optional[dict]:
    """Convert a Gmail message dict into kwargs for TempEmailHistory creation."""
    payload = msg.get("payload", {})
    subject = _header(payload, "Subject") or ""
    if not subject.startswith(_SUBJECT_PREFIX):
        return None

    from_email = _header(payload, "From") or ""
    to_raw = _header(payload, "To") or ""
    cc_raw = _header(payload, "Cc") or ""
    to_list = [addr.strip() for addr in to_raw.split(",") if addr.strip()]
    cc_list = [addr.strip() for addr in cc_raw.split(",") if addr.strip()]
    sent_at = _parse_sent_at(_header(payload, "Date"))
    plain, html = _extract_body(payload)

    # Collect only top-level part attachments (no thread-reply inspection)
    attachment_keys: list[str] = []
    message_id_str = msg["id"]
    for part in payload.get("parts", []):
        att_id = part.get("body", {}).get("attachmentId")
        fname = part.get("filename") or ""
        if att_id and fname:
            key = _upload_attachment_to_email_bucket(service, message_id_str, att_id, fname)
            if key:
                attachment_keys.append(key)

    gmail_message_id = _header(payload, "Message-ID") or message_id_str

    return {
        "message_id": gmail_message_id,
        "from_email": from_email,
        "to": to_list or None,
        "cc": cc_list or None,
        "subject": subject,
        "body": plain,
        "body_html": html,
        "attachments": attachment_keys or None,
        "sent_at": sent_at,
        "email_type": "incoming",
        "created_on": datetime.utcnow(),
        "updated_on": datetime.utcnow(),
        "status": 0,
    }


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_gmail_audited_financials(
    token_path: str,
    credentials_path: Optional[str] = None,
    max_results: int = 50,
) -> None:
    """Fetch Gmail messages, stage as TempEmailHistory, then run the side-flow."""
    from src.db.models import TempEmailHistory
    from src.db.session import get_sync_db
    from src.scripts.data_manipulation.audited_financials_email_ingestion import (
        process_audited_financials_emails,
    )

    service = _build_gmail_service(token_path=token_path, credentials_path=credentials_path)
    message_ids = _list_matching_messages(service, max_results=max_results)
    logger.info("gmail_runner: found %d matching Gmail message(s)", len(message_ids))

    db = get_sync_db()
    staged = 0
    try:
        for mid in message_ids:
            try:
                msg = _fetch_message(service, mid)
                row_kwargs = _gmail_message_to_temp_row(service, msg)
                if row_kwargs is None:
                    continue

                # Idempotency: skip if already staged
                existing = (
                    db.query(TempEmailHistory)
                    .filter(TempEmailHistory.message_id == row_kwargs["message_id"])
                    .first()
                )
                if existing:
                    logger.info(
                        "gmail_runner: message_id=%s already staged — skipping",
                        row_kwargs["message_id"],
                    )
                    continue

                db.add(TempEmailHistory(**row_kwargs))
                db.commit()
                staged += 1
                logger.info(
                    "gmail_runner: staged message_id=%s subject=%r",
                    row_kwargs["message_id"],
                    row_kwargs["subject"],
                )
            except Exception as exc:
                logger.exception("gmail_runner: failed to stage message %s: %s", mid, exc)
                db.rollback()
    finally:
        db.close()

    logger.info("gmail_runner: staged %d new TempEmailHistory row(s)", staged)

    if staged > 0:
        logger.info("gmail_runner: triggering audited-financials processing")
        asyncio.run(process_audited_financials_emails())
    else:
        logger.info("gmail_runner: nothing new to process")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Gmail → TempEmailHistory → audited-financials side-flow")
    parser.add_argument(
        "--credentials",
        default=None,
        help="Path to Google OAuth2 credentials.json (optional; defaults to env vars)",
    )
    parser.add_argument(
        "--token",
        default="gmail_token.json",
        help="Path to cached token file (created on first run, default: gmail_token.json)",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=50,
        help="Max Gmail messages to fetch (default: 50)",
    )
    args = parser.parse_args()
    run_gmail_audited_financials(
        token_path=args.token,
        credentials_path=args.credentials,
        max_results=args.max_results,
    )


if __name__ == "__main__":
    main()
