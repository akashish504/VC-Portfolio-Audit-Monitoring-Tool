"""
AWS Bedrock batch inference (S3 JSONL in/out; Converse-shaped modelInput / modelOutput in JSONL).

Used for **org-chart** and **audit** document flows when LLM_PROVIDER=bedrock.

- **PDF:** `document.source.s3Location` points at a copy under the same batch input prefix (AWS requirement).
- **Non-PDF (audit only):** text-only Converse `modelInput` (no document block).

**S3:** All staging uses **`settings.S3_BUCKET`** (same env as portfolio file uploads / `S3_BUCKET` in `.env`).
Paths look like: `s3://{S3_BUCKET}/{BEDROCK_BATCH_S3_PREFIX}/org-chart|audit/.../`.

See: https://docs.aws.amazon.com/bedrock/latest/userguide/batch-inference.html
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

from src.configs.env import Settings, settings
from src.llm.client import _bedrock_output_text, _bedrock_safe_document_name
from src.llm.config import effective_bedrock_runtime_model_id
from src.utils.aws_credential_logging import log_bedrock_credential_audit
from src.utils.bedrock_runtime_client import local_dev_static_credentials_kwargs
from src.utils.s3 import download_file, get_s3_client, parse_s3_uri, upload_file

logger = logging.getLogger(__name__)

_TERMINAL_OK = frozenset({"Completed", "PartiallyCompleted"})
_TERMINAL_BAD = frozenset({"Failed", "Expired", "Stopped"})
_TERMINAL = _TERMINAL_OK | _TERMINAL_BAD


def validate_bedrock_batch_config(s: Settings | None = None) -> None:
    """Required env for Bedrock batch (IAM via batch role; no HTTP bearer for these flows)."""
    cfg = s or settings
    if not cfg.BEDROCK_MODEL_ID:
        raise ValueError("BEDROCK_MODEL_ID is required for Bedrock batch extraction.")
    if not cfg.BEDROCK_REGION:
        raise ValueError("BEDROCK_REGION is required for Bedrock batch extraction.")
    if not cfg.BEDROCK_BATCH_ROLE_ARN:
        raise ValueError(
            "BEDROCK_BATCH_ROLE_ARN is required for Bedrock batch (IAM role assumed by the batch job)."
        )
    if not (cfg.S3_BUCKET or "").strip():
        raise ValueError("S3_BUCKET is required to stage batch input/output JSONL and PDF copy.")


def _bedrock_control_plane_client(cfg: Settings):
    if cfg.BEDROCK_CROSS_ACCOUNT_ROLE_ARN:
        sts = boto3.client("sts", region_name=cfg.BEDROCK_REGION)
        assumed = sts.assume_role(
            RoleArn=cfg.BEDROCK_CROSS_ACCOUNT_ROLE_ARN,
            RoleSessionName="bedrock-batch-session"
        )
        creds = assumed["Credentials"]
        return boto3.client(
            "bedrock",
            region_name=cfg.BEDROCK_REGION,
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )
    return boto3.client(
        "bedrock",
        region_name=cfg.BEDROCK_REGION,
        **local_dev_static_credentials_kwargs(),
    )


def _copy_object_same_bucket(bucket: str, src_key: str, dst_key: str) -> None:
    c = get_s3_client()
    c.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": src_key}, Key=dst_key)


def _build_converse_batch_model_input_pdf(
    *,
    system_text: str,
    user_instruction: str,
    pdf_s3_uri: str,
    pdf_filename: str,
    max_tokens: int,
) -> dict[str, Any]:
    safe_name = _bedrock_safe_document_name(pdf_filename)
    return {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "document": {
                            "format": "pdf",
                            "name": safe_name,
                            "source": {
                                "s3Location": {
                                    "uri": pdf_s3_uri,
                                }
                            },
                        }
                    },
                    {"text": user_instruction},
                ],
            }
        ],
        "system": [{"text": system_text}],
        "inferenceConfig": {"maxTokens": max_tokens},
    }


def _build_converse_batch_model_input_text_only(
    *,
    system_text: str,
    user_text: str,
    max_tokens: int,
) -> dict[str, Any]:
    return {
        "system": [{"text": system_text}],
        "messages": [{"role": "user", "content": [{"text": user_text}]}],
        "inferenceConfig": {"maxTokens": max_tokens},
    }


def _parse_output_jsonl(body: str) -> str:
    """First successful assistant text from batch output JSONL (Converse-shaped modelOutput)."""
    last_err: Optional[str] = None
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            last_err = str(e)
            continue
        if row.get("error"):
            last_err = str(row.get("error"))
            continue
        mo = row.get("modelOutput")
        if not isinstance(mo, dict):
            continue
        text = _bedrock_output_text(mo)
        if text.strip():
            return text
    raise ValueError(
        f"No usable model output in batch result JSONL. Last issue: {last_err or 'empty lines'}"
    )


def _list_jsonl_keys(bucket: str, prefix: str) -> list[str]:
    c = get_s3_client()
    keys: list[str] = []
    token = None
    while True:
        kw: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        resp = c.list_objects_v2(**kw)
        for obj in resp.get("Contents") or []:
            k = obj.get("Key") or ""
            if k.endswith(".jsonl"):
                keys.append(k)
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return sorted(keys)


def _sanitize_job_prefix(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9-]+", "-", name).strip("-").lower()
    return (s[:32] if s else "job") or "job"


def run_bedrock_batch_sync(
    *,
    file_id: int,
    path_segment: str,
    job_name_prefix: str,
    record_id: str,
    system_text: str,
    max_tokens: int,
    pdf_mode: bool,
    storage_uri: Optional[str] = None,
    filename: str = "document.pdf",
    user_instruction: str = "",
    text_user_body: Optional[str] = None,
) -> tuple[str, str]:
    """
    Submit batch Converse job; return (assistant_text, job_arn).

    - **pdf_mode True:** copy object at ``storage_uri`` to ``document.pdf`` under the batch prefix; JSONL references it.
    - **pdf_mode False:** ``text_user_body`` is the full user message (e.g. instruction + extracted text). Only ``input.jsonl`` in the folder.
    """
    cfg = settings
    log_bedrock_credential_audit(
        logger,
        context=(
            f"run_bedrock_batch_sync start file_id={file_id} path_segment={path_segment!r} "
            f"pdf_mode={pdf_mode} job_prefix={job_name_prefix!r}"
        ),
    )
    validate_bedrock_batch_config(cfg)

    run_id = str(uuid.uuid4())
    _pfx = (cfg.BEDROCK_BATCH_S3_PREFIX or "bedrock-batch").strip().strip("/")
    if not _pfx:
        _pfx = "bedrock-batch"
    seg = path_segment.strip().strip("/")
    base = f"{_pfx}/{seg}/{file_id}/{run_id}"
    bucket = (cfg.S3_BUCKET or "").strip()

    if pdf_mode:
        if not storage_uri:
            raise ValueError("storage_uri is required for PDF batch mode.")
        src_bucket, src_key = parse_s3_uri(storage_uri)
        if src_bucket != bucket:
            raise ValueError(
                f"File bucket ({src_bucket}) must match S3_BUCKET ({bucket}) for Bedrock batch staging."
            )
        pdf_key = f"{base}/document.pdf"
        jsonl_key = f"{base}/input.jsonl"
        input_folder_uri = f"s3://{bucket}/{base}/"
        pdf_s3_uri = f"s3://{bucket}/{pdf_key}"
        _copy_object_same_bucket(bucket, src_key, pdf_key)
        model_input = _build_converse_batch_model_input_pdf(
            system_text=system_text,
            user_instruction=user_instruction,
            pdf_s3_uri=pdf_s3_uri,
            pdf_filename=filename,
            max_tokens=max_tokens,
        )
    else:
        if not text_user_body or not str(text_user_body).strip():
            raise ValueError("text_user_body is required for text-only batch mode.")
        jsonl_key = f"{base}/input.jsonl"
        input_folder_uri = f"s3://{bucket}/{base}/"
        model_input = _build_converse_batch_model_input_text_only(
            system_text=system_text,
            user_text=text_user_body,
            max_tokens=max_tokens,
        )

    record = {"recordId": record_id, "modelInput": model_input}
    jsonl_body = json.dumps(record, ensure_ascii=False) + "\n"
    upload_file(
        jsonl_body.encode("utf-8"),
        jsonl_key,
        content_type="application/x-ndjson",
        bucket=bucket,
    )

    jp = _sanitize_job_prefix(job_name_prefix)
    job_name = f"{jp}-{file_id}-{run_id[:8]}"
    if len(job_name) > 63:
        job_name = job_name[:63]

    out_prefix = f"{base}/output/"
    output_uri = f"s3://{bucket}/{out_prefix}"

    model_id = effective_bedrock_runtime_model_id(cfg)
    client = _bedrock_control_plane_client(cfg)
    log_bedrock_credential_audit(
        logger,
        context=(
            f"run_bedrock_batch_sync before CreateModelInvocationJob file_id={file_id} "
            f"job_name={job_name!r} region={cfg.BEDROCK_REGION!r} "
            f"role_arn={cfg.BEDROCK_BATCH_ROLE_ARN!r} model_id={model_id!r} "
            f"input_folder_uri={input_folder_uri!r} output_uri={output_uri!r}"
        ),
    )
    owner = (cfg.S3_BUCKET_OWNER_ACCOUNT_ID or "").strip()
    s3_in: dict[str, Any] = {
        "s3InputFormat": "JSONL",
        "s3Uri": input_folder_uri,
    }
    s3_out: dict[str, Any] = {"s3Uri": output_uri}
    if owner:
        s3_in["s3BucketOwner"] = owner
        s3_out["s3BucketOwner"] = owner

    try:
        created = client.create_model_invocation_job(
            jobName=job_name,
            roleArn=cfg.BEDROCK_BATCH_ROLE_ARN,
            modelId=model_id,
            inputDataConfig={"s3InputDataConfig": s3_in},
            outputDataConfig={"s3OutputDataConfig": s3_out},
            timeoutDurationInHours=cfg.BEDROCK_BATCH_TIMEOUT_HOURS,
        )
    except ClientError as e:
        logger.exception("create_model_invocation_job failed")
        raise ValueError(f"Bedrock batch job could not be created: {e}") from e

    job_arn = created["jobArn"]
    logger.info("Bedrock batch job started job_arn=%s input=%s", job_arn, input_folder_uri)

    poll = max(5, int(cfg.BEDROCK_BATCH_POLL_INTERVAL_SECONDS))
    while True:
        detail = client.get_model_invocation_job(jobIdentifier=job_arn)
        status = detail.get("status") or ""
        logger.info(
            "Bedrock batch job_arn=%s status=%s processed=%s/%s",
            job_arn,
            status,
            detail.get("processedRecordCount"),
            detail.get("totalRecordCount"),
        )
        if status in _TERMINAL:
            if status in _TERMINAL_BAD:
                msg = detail.get("message") or status
                raise ValueError(f"Bedrock batch job failed: {msg}")
            break
        time.sleep(poll)

    jsonl_keys = _list_jsonl_keys(bucket, out_prefix)
    if not jsonl_keys:
        raise ValueError(f"No output .jsonl under s3://{bucket}/{out_prefix}")

    combined = []
    for jk in jsonl_keys:
        raw = download_file(jk, bucket=bucket)
        combined.append(raw.decode("utf-8", errors="replace"))

    text = _parse_output_jsonl("\n".join(combined))
    return text, job_arn


async def run_bedrock_batch_async(**kwargs: Any) -> tuple[str, str]:
    """Async wrapper (blocking boto3 + polling in a thread)."""
    return await asyncio.to_thread(run_bedrock_batch_sync, **kwargs)


# --- Call-site helpers (org-chart / audit) ---


async def run_org_chart_bedrock_batch(
    *,
    file_id: int,
    storage_uri: str,
    filename: str,
    system_text: str,
    user_instruction: str,
    max_tokens: int = 8192,
) -> tuple[str, str]:
    return await run_bedrock_batch_async(
        file_id=file_id,
        path_segment="org-chart",
        job_name_prefix="org-chart",
        record_id=f"org-chart-{file_id}",
        system_text=system_text,
        max_tokens=max_tokens,
        pdf_mode=True,
        storage_uri=storage_uri,
        filename=filename,
        user_instruction=user_instruction,
        text_user_body=None,
    )


async def run_audit_bedrock_batch(
    *,
    file_id: int,
    kind_slug: str,
    storage_uri: str,
    filename: str,
    system_text: str,
    user_instruction: str,
    text_user_body: Optional[str],
    max_tokens: int = 8192,
) -> tuple[str, str]:
    """Audit extraction: PDF uses S3 document; non-PDF uses ``text_user_body`` (instruction + extracted text)."""
    pdf_mode = (filename or "").lower().strip().endswith(".pdf")
    return await run_bedrock_batch_async(
        file_id=file_id,
        path_segment=f"audit/{kind_slug}",
        job_name_prefix=f"audit-{kind_slug}",
        record_id=f"audit-{kind_slug}-{file_id}",
        system_text=system_text,
        max_tokens=max_tokens,
        pdf_mode=pdf_mode,
        storage_uri=storage_uri if pdf_mode else None,
        filename=filename,
        user_instruction=user_instruction,
        text_user_body=text_user_body if not pdf_mode else None,
    )
