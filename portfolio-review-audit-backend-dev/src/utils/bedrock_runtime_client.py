"""
Bedrock Runtime (``converse``) boto3 client.

- **Deployments (``LOCAL_DEV`` false):** by default STS ``assume_role`` targets
  ``arn:aws:iam::{account}:role/bedrock-caller-devapp-role`` so Converse runs as that role
  (see ``BEDROCK_RUNTIME_ASSUME_ROLE_ARN`` / ``BEDROCK_RUNTIME_CALLER_ACCOUNT_ID`` / ``BEDROCK_RUNTIME_CALLER_ROLE_NAME``).

- **LOCAL_DEV:** defaults to ambient credentials (``aws sso`` / static keys via
  ``local_dev_static_credentials_kwargs``); set ``BEDROCK_RUNTIME_ASSUME_ROLE_ARN`` or
  ``BEDROCK_CROSS_ACCOUNT_ROLE_ARN`` to test assume-role locally.

Legacy: ``BEDROCK_CROSS_ACCOUNT_ROLE_ARN`` applies to **runtime Converse** as well when set.
"""
from __future__ import annotations

from typing import Any, Optional

import boto3
from botocore.client import BaseClient
from botocore.config import Config

from src.configs.env import settings


_RUNTIME_ASSUME_ROLE_SESSION = "bedrock-runtime-converse"

# Cache caller account from STS inside a process when env does not specify it (deployment only).
_caller_identity_account_cache: Optional[str] = None


def local_dev_static_credentials_kwargs() -> dict[str, Any]:
    """When ``LOCAL_DEV`` and access key + secret are in settings, return boto3 kwargs; else ``{}``."""
    if not settings.LOCAL_DEV:
        return {}
    key = (settings.AWS_ACCESS_KEY_ID or "").strip()
    secret = (settings.AWS_SECRET_ACCESS_KEY or "").strip()
    if not key or not secret:
        return {}
    out: dict[str, Any] = {
        "aws_access_key_id": key,
        "aws_secret_access_key": secret,
    }
    session = (settings.AWS_SESSION_TOKEN or "").strip()
    if session:
        out["aws_session_token"] = session
    return out


def _caller_account_id(region: str) -> str:
    """Resolve AWS account for building default ``bedrock-caller-*`` role ARN."""
    global _caller_identity_account_cache
    configured = (settings.BEDROCK_RUNTIME_CALLER_ACCOUNT_ID or "").strip()
    if configured:
        return configured
    if _caller_identity_account_cache:
        return _caller_identity_account_cache
    sts = boto3.client("sts", region_name=region)
    _caller_identity_account_cache = sts.get_caller_identity()["Account"]
    return _caller_identity_account_cache


def _resolved_runtime_assume_role_arn(region: str) -> Optional[str]:
    """Return role ARN for ``bedrock-runtime`` Converse calls, or None to use ambient creds."""
    explicit = (
        (settings.BEDROCK_RUNTIME_ASSUME_ROLE_ARN or "").strip()
        or (settings.BEDROCK_CROSS_ACCOUNT_ROLE_ARN or "").strip()
    )
    if explicit:
        return explicit

    if settings.LOCAL_DEV:
        # Local developers use direct keys / SSO unless they set an explicit assume ARN above.
        return None

    # Deployed API: assume the Bedrock caller role in this account unless overridden via env vars above.
    role_name = (settings.BEDROCK_RUNTIME_CALLER_ROLE_NAME or "").strip() or "bedrock-caller-devapp-role"
    account = _caller_account_id(region)
    return f"arn:aws:iam::{account}:role/{role_name}"


def bedrock_runtime_client(
    region: str,
    *,
    read_timeout_seconds: Optional[float] = None,
    disable_retries: bool = False,
) -> BaseClient:
    """Build a ``bedrock-runtime`` boto3 client, assuming the configured caller role when needed.

    ``disable_retries=True`` turns off boto3's automatic retry-on-ReadTimeout so the caller
    owns retry logic (avoids silently multiplying a 360 s timeout into 1080 s).
    """
    retry_cfg: dict = (
        {"max_attempts": 1, "mode": "legacy"} if disable_retries
        else {"max_attempts": 3, "mode": "standard"}
    )
    botocore_config = Config(
        read_timeout=int(read_timeout_seconds) if read_timeout_seconds is not None else 60,
        connect_timeout=20,
        retries=retry_cfg,
    )
    kwargs: dict[str, Any]

    kwargs: dict[str, Any]
    role_arn = _resolved_runtime_assume_role_arn(region)
    if role_arn:
        sts = boto3.client("sts", region_name=region)
        assumed = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName=_RUNTIME_ASSUME_ROLE_SESSION,
        )
        creds = assumed["Credentials"]
        kwargs = {
            "aws_access_key_id": creds["AccessKeyId"],
            "aws_secret_access_key": creds["SecretAccessKey"],
            "aws_session_token": creds["SessionToken"],
        }
    else:
        kwargs = local_dev_static_credentials_kwargs()
    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=botocore_config,
        **kwargs,
    )
