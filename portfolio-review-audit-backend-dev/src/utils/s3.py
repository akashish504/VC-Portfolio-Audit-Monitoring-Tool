"""
AWS S3 utility — upload, download, delete, and generate presigned URLs
"""
import logging
import boto3
from botocore.exceptions import ClientError
from typing import Optional
from src.configs.env import settings
from src.utils.bedrock_runtime_client import local_dev_static_credentials_kwargs

logger = logging.getLogger(__name__)


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Return (bucket, key) from ``s3://bucket/key``."""
    if not uri or not uri.startswith("s3://"):
        raise ValueError("storage_uri must be s3://bucket/key")
    rest = uri[5:]
    bucket, sep, key = rest.partition("/")
    if not sep or not bucket or not key:
        raise ValueError("Invalid s3 storage_uri")
    return bucket, key


def get_s3_client():
    """S3 client: IRSA/default chain; in LOCAL_DEV, optional keys from settings (see .env)."""
    return boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
        **local_dev_static_credentials_kwargs(),
    )


def _require_bucket(bucket: str, *, env_var_name: str) -> None:
    if not bucket:
        raise RuntimeError(
            f"{env_var_name} is not configured. Set {env_var_name} (and AWS_REGION/AWS credentials) in the backend environment."
        )


def upload_file(
    file_bytes: bytes,
    s3_key: str,
    content_type: str = "application/pdf",
    *,
    bucket: Optional[str] = None,
    bucket_env_var_name: str = "S3_BUCKET",
) -> str:
    """
    Upload a file to S3.
    Returns the S3 key on success.
    """
    resolved_bucket = bucket if bucket is not None else settings.S3_BUCKET
    _require_bucket(resolved_bucket, env_var_name=bucket_env_var_name)
    client = get_s3_client()
    try:
        client.put_object(
            Bucket=resolved_bucket,
            Key=s3_key,
            Body=file_bytes,
            ContentType=content_type,
        )
        logger.info(f"Uploaded to S3: {s3_key}")
        return s3_key
    except ClientError as e:
        logger.error(f"S3 upload failed for {s3_key}: {e}")
        raise


def download_storage_uri(storage_uri: str) -> bytes:
    """Download object bytes using the bucket name embedded in ``s3://bucket/key``."""
    bucket, key = parse_s3_uri(storage_uri)
    return download_file(key, bucket=bucket, bucket_env_var_name="S3_BUCKET")


def download_file(
    s3_key: str,
    *,
    bucket: Optional[str] = None,
    bucket_env_var_name: str = "S3_BUCKET",
) -> bytes:
    """Download a file from S3 and return its bytes"""
    resolved_bucket = bucket if bucket is not None else settings.S3_BUCKET
    _require_bucket(resolved_bucket, env_var_name=bucket_env_var_name)
    client = get_s3_client()
    try:
        response = client.get_object(Bucket=resolved_bucket, Key=s3_key)
        content = response["Body"].read()
        logger.info(f"Downloaded from S3: {s3_key}")
        return content
    except ClientError as e:
        logger.error(f"S3 download failed for {s3_key}: {e}")
        raise


def delete_file(
    s3_key: str,
    *,
    bucket: Optional[str] = None,
    bucket_env_var_name: str = "S3_BUCKET",
) -> None:
    """Delete a file from S3"""
    resolved_bucket = bucket if bucket is not None else settings.S3_BUCKET
    _require_bucket(resolved_bucket, env_var_name=bucket_env_var_name)
    client = get_s3_client()
    try:
        client.delete_object(Bucket=resolved_bucket, Key=s3_key)
        logger.info(f"Deleted from S3: {s3_key}")
    except ClientError as e:
        logger.error(f"S3 delete failed for {s3_key}: {e}")
        raise


def delete_file_best_effort(
    s3_key: str,
    *,
    bucket: Optional[str] = None,
    bucket_env_var_name: str = "S3_BUCKET",
) -> None:
    """Delete object if possible; log and ignore failures (e.g. IAM lacks ``s3:DeleteObject``).

    Use after replacing an object with a new key so upload/conversion still succeeds.
    """
    resolved_bucket = bucket if bucket is not None else settings.S3_BUCKET
    _require_bucket(resolved_bucket, env_var_name=bucket_env_var_name)
    client = get_s3_client()
    try:
        client.delete_object(Bucket=resolved_bucket, Key=s3_key)
        logger.info("Deleted from S3: %s", s3_key)
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code") or ""
        logger.warning(
            "S3 delete skipped for %s (%s): %s — object may remain; grant s3:DeleteObject or use lifecycle rules",
            s3_key,
            code,
            e,
        )


def generate_presigned_url(
    s3_key: str,
    expires_in: int = 3600,
    *,
    bucket: Optional[str] = None,
    bucket_env_var_name: str = "S3_BUCKET",
) -> str:
    """Generate a pre-signed URL for temporary file access"""
    resolved_bucket = bucket if bucket is not None else settings.S3_BUCKET
    _require_bucket(resolved_bucket, env_var_name=bucket_env_var_name)
    client = get_s3_client()
    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": resolved_bucket, "Key": s3_key},
            ExpiresIn=expires_in,
        )
        return url
    except ClientError as e:
        logger.error(f"Failed to generate presigned URL for {s3_key}: {e}")
        raise
