"""
Unified async chat completion: OpenAI or AWS Bedrock Runtime ``converse`` (boto3 + IAM / IRSA).

PDFs and Excel workbooks (.xlsx/.xls) go as native Bedrock document blocks when supported;
OpenAI uses native PDF file parts. Other types fall back to extracted text.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
from typing import Any, List, Optional

import anthropic as _anthropic_sdk
from openai import AsyncOpenAI, BadRequestError

from src.configs.env import settings
from src.llm.config import effective_bedrock_runtime_model_id, resolve_llm_provider, validate_llm_config
from src.utils.aws_credential_logging import log_bedrock_credential_audit
from src.utils.bedrock_runtime_client import bedrock_runtime_client

logger = logging.getLogger(__name__)

# OpenAI uploaded PDF helper cap (Files API guard).
_MAX_OPENAI_PDF_BYTES = 4_500_000
# Bedrock Converse inline ``bytes`` max per doc (see API restrictions). Claude 4+ PDF exempt — :func:`_bedrock_pdf_exempt_inline_size`.
_BEDROCK_INLINE_PDF_BYTES_LIMIT = 4_500_000


def _bedrock_pdf_exempt_inline_size(model_id: str) -> bool:
    """Bedrock waives the 4.5 MB inline document cap for PDF on Claude 4+."""
    mid = (model_id or "").lower()
    if ".anthropic.claude-" not in mid and not mid.startswith("anthropic.claude-"):
        return False

    # e.g. anthropic.claude-opus-4-7, global.anthropic.claude-opus-4-7.
    return bool(re.search(r"anthropic\.claude-(opus|sonnet|haiku)-4", mid))

def _bedrock_native_document_format(filename: str) -> Optional[str]:
    """Bedrock Converse document formats we send inline (see DocumentBlock API)."""
    fn = (filename or "").strip().lower()
    if fn.endswith(".pdf"):
        return "pdf"
    if fn.endswith(".xlsx"):
        return "xlsx"
    if fn.endswith(".xls"):
        return "xls"
    return None


def _bedrock_safe_document_name(pdf_filename: str) -> str:
    """
    Bedrock Converse ``document.name`` may only use: alnum, single spaces, hyphens, (), [].
    Underscores, dots, slashes, etc. are rejected (see DocumentBlock API).
    """
    name = (pdf_filename or "").strip() or "document.pdf"
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = name.replace("_", "-")
    # Map any other disallowed character runs to a single hyphen.
    name = re.sub(r"[^A-Za-z0-9 \-\(\)\[\]]+", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    name = re.sub(r"\s+", " ", name).strip()
    if not name or not re.search(r"[A-Za-z0-9]", name):
        name = "document-pdf"
    # API max length 200.
    if len(name) > 200:
        name = name[:200]
    return name


def _openai_messages_to_bedrock(
    messages: List[dict[str, Any]],
) -> tuple[Optional[List[dict[str, str]]], List[dict[str, Any]]]:
    system_blocks: List[dict[str, str]] = []
    bedrock_messages: List[dict[str, Any]] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content")
        if role == "system":
            if isinstance(content, str):
                system_blocks.append({"text": content})
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        system_blocks.append({"text": str(part.get("text", ""))})
            continue
        if role not in ("user", "assistant"):
            role = "user"
        parts: List[dict[str, Any]] = []
        if isinstance(content, str):
            parts.append({"text": content})
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    parts.append({"text": str(part.get("text", ""))})
        if not parts:
            parts.append({"text": ""})
        bedrock_messages.append({"role": role, "content": parts})
    sys_out: Optional[List[dict[str, str]]] = system_blocks if system_blocks else None
    return sys_out, bedrock_messages


def _bedrock_output_tool_input(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Extract the forced tool-call ``input`` (structured output) from a Converse response."""
    msg = (data.get("output") or {}).get("message") or {}
    for block in msg.get("content") or []:
        if isinstance(block, dict) and isinstance(block.get("toolUse"), dict):
            inp = block["toolUse"].get("input")
            if isinstance(inp, dict):
                return inp
    return None


def _bedrock_output_text(data: dict[str, Any]) -> str:
    stop_reason = data.get("stopReason") or ""

    if stop_reason == "max_tokens":
        raise ValueError(
            "Bedrock response truncated (stopReason=max_tokens): "
            "the model hit the token limit before finishing its output. "
            "Increase max_tokens or reduce the input document size."
        )
    if stop_reason == "content_filtered":
        raise ValueError(
            "Bedrock response blocked by content filter (stopReason=content_filtered)"
        )
    msg = (data.get("output") or {}).get("message") or {}
    out_parts: List[str] = []
    for block in msg.get("content") or []:
        if isinstance(block, dict) and "text" in block:
            out_parts.append(str(block["text"]))
    text = "".join(out_parts)
    if not text:
        raise ValueError(
            f"Bedrock returned no text content (stopReason={stop_reason!r}); "
            "output may contain only non-text blocks or be empty"
        )
    return text


def _bedrock_converse_with_document_sync(
    *,
    system_text: str,
    user_instruction: str,
    document_bytes: bytes,
    document_filename: str,
    document_format: str,
    model_id: str,
    region: str,
    max_tokens: int = 4096,
    timeout: Optional[float] = None,
) -> str:
    """
    Bedrock Runtime ``converse`` with an inline document block (PDF, xlsx, xls, …).

    Timeout behaviour:
    - Uses ``BEDROCK_PDF_LLM_TIMEOUT`` (default 360 s) when no explicit ``timeout`` is given.
    - boto3 retries are **disabled** (``disable_retries=True``) so the ceiling is exactly one
      attempt per call.  A single app-level retry is attempted on ``ReadTimeoutError`` so
      transient slowness doesn't fail the whole extraction.

    See: https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_DocumentBlock.html
    """
    log_bedrock_credential_audit(
        logger,
        context=(
            f"bedrock_converse_with_document model_id={model_id!r} region={region!r} "
            f"format={document_format!r} file={document_filename!r}"
        ),
    )
    if (
        len(document_bytes) > _BEDROCK_INLINE_PDF_BYTES_LIMIT
        and not _bedrock_pdf_exempt_inline_size(model_id)
    ):
        raise ValueError(
            f"{document_format.upper()} exceeds inline Bedrock document size guard for this model "
            f"(limit {_BEDROCK_INLINE_PDF_BYTES_LIMIT} bytes). Use a Claude 4+ model for large files."
        )
    safe_name = _bedrock_safe_document_name(document_filename)
    # Use the dedicated PDF timeout — large financial documents with max_tokens=8192 can take minutes.
    read_timeout = timeout if timeout is not None else float(settings.BEDROCK_PDF_LLM_TIMEOUT)
    msg: dict[str, Any] = {
        "role": "user",
        "content": [
            {
                "document": {
                    "format": document_format,
                    "name": safe_name,
                    "source": {"bytes": document_bytes},
                }
            },
            {"text": user_instruction},
        ],
    }
    kw: dict[str, Any] = {
        "modelId": model_id,
        "messages": [msg],
        "inferenceConfig": {"maxTokens": max_tokens},
    }
    if system_text:
        kw["system"] = [{"text": system_text}]

    # One app-level retry on ReadTimeout — boto3 retries are disabled to avoid silent tripling.
    last_exc: Optional[Exception] = None
    for attempt in range(1, 3):
        try:
            # Fresh client per attempt (credentials may have been refreshed by STS).
            client = bedrock_runtime_client(region, read_timeout_seconds=read_timeout, disable_retries=True)
            data = client.converse(**kw)
            return _bedrock_output_text(data)
        except Exception as exc:
            exc_name = type(exc).__name__
            is_timeout = "Timeout" in exc_name or "timeout" in str(exc).lower()
            last_exc = exc
            if is_timeout and attempt == 1:
                logger.warning(
                    "Bedrock Converse document attempt %d timed out after %.0fs — retrying once. "
                    "model=%s format=%s file=%s bytes=%d",
                    attempt,
                    read_timeout,
                    model_id,
                    document_format,
                    document_filename,
                    len(document_bytes),
                )
                continue
            logger.exception(
                "Bedrock Converse (%s document) failed attempt=%d",
                document_format,
                attempt,
            )
            raise
    # Should not be reached, but keeps type-checker happy.
    raise RuntimeError(f"Bedrock Converse {document_format} document failed after retries") from last_exc


async def _bedrock_converse_with_document(
    *,
    system_text: str,
    user_instruction: str,
    document_bytes: bytes,
    document_filename: str,
    document_format: str,
    model_id: str,
    region: str,
    max_tokens: int = 4096,
    timeout: Optional[float] = None,
) -> str:
    def _run() -> str:
        return _bedrock_converse_with_document_sync(
            system_text=system_text,
            user_instruction=user_instruction,
            document_bytes=document_bytes,
            document_filename=document_filename,
            document_format=document_format,
            model_id=model_id,
            region=region,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    return await asyncio.to_thread(_run)


# --- Image blocks (rendered statement pages) — image+text financial-extraction pipeline ---

def _bedrock_converse_with_images_sync(
    *,
    system_text: str,
    user_instruction: str,
    images: List[bytes],
    model_id: str,
    region: str,
    max_tokens: int = 4096,
    timeout: Optional[float] = None,
) -> str:
    """Bedrock ``converse`` with one or more PNG image blocks + a text instruction."""
    log_bedrock_credential_audit(
        logger,
        context=f"bedrock_converse_with_images model_id={model_id!r} region={region!r} n_images={len(images)}",
    )
    read_timeout = timeout if timeout is not None else float(settings.BEDROCK_PDF_LLM_TIMEOUT)
    content: List[dict[str, Any]] = [
        {"image": {"format": "png", "source": {"bytes": img}}} for img in images
    ]
    content.append({"text": user_instruction})
    kw: dict[str, Any] = {
        "modelId": model_id,
        "messages": [{"role": "user", "content": content}],
        "inferenceConfig": {"maxTokens": max_tokens},
    }
    if system_text:
        kw["system"] = [{"text": system_text}]

    last_exc: Optional[Exception] = None
    for attempt in range(1, 3):
        try:
            client = bedrock_runtime_client(region, read_timeout_seconds=read_timeout, disable_retries=True)
            data = client.converse(**kw)
            return _bedrock_output_text(data)
        except Exception as exc:
            is_timeout = "Timeout" in type(exc).__name__ or "timeout" in str(exc).lower()
            last_exc = exc
            if is_timeout and attempt == 1:
                logger.warning(
                    "Bedrock Converse images attempt %d timed out after %.0fs — retrying once (n_images=%d)",
                    attempt,
                    read_timeout,
                    len(images),
                )
                continue
            logger.exception("Bedrock Converse (images) failed attempt=%d", attempt)
            raise
    raise RuntimeError("Bedrock Converse images failed after retries") from last_exc


async def _bedrock_converse_with_images(
    *,
    system_text: str,
    user_instruction: str,
    images: List[bytes],
    model_id: str,
    region: str,
    max_tokens: int = 4096,
    timeout: Optional[float] = None,
) -> str:
    def _run() -> str:
        return _bedrock_converse_with_images_sync(
            system_text=system_text,
            user_instruction=user_instruction,
            images=images,
            model_id=model_id,
            region=region,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    return await asyncio.to_thread(_run)


# gpt-5 / o-series are *reasoning* models: they (a) reject ``max_tokens`` (require
# ``max_completion_tokens``) and (b) spend part of that budget on hidden reasoning tokens, so a
# small cap can be fully consumed by reasoning and return an empty completion / no tool call.
_OPENAI_REASONING_MODEL_RE = re.compile(r"^(gpt-5|o1|o3|o4)(\b|[-_])", re.IGNORECASE)
# Floor for the combined (reasoning + visible output) budget on reasoning models, so a forced tool
# call still has room to be emitted after the model finishes thinking.
_OPENAI_REASONING_MIN_COMPLETION_TOKENS = 25000


def _is_openai_reasoning_model(model: str) -> bool:
    return bool(_OPENAI_REASONING_MODEL_RE.match((model or "").strip()))


async def _openai_create_chat(
    client: AsyncOpenAI, kwargs: dict[str, Any], *, max_tokens: Optional[int]
) -> Any:
    """``chat.completions.create`` that copes with the output-token parameter name + reasoning budget.

    - **Reasoning models** (gpt-5 / o-series): send ``max_completion_tokens`` directly (no wasteful
      ``max_tokens`` 400 probe), and raise it to a floor so hidden reasoning tokens don't eat the
      whole budget and leave an empty completion / missing tool call.
    - **Classic models**: send ``max_tokens``; if the API still rejects it (model family changed),
      transparently retry with ``max_completion_tokens``.
    """
    model = str(kwargs.get("model") or "")
    if max_tokens is None:
        return await client.chat.completions.create(**kwargs)
    if _is_openai_reasoning_model(model):
        budget = max(max_tokens, _OPENAI_REASONING_MIN_COMPLETION_TOKENS)
        return await client.chat.completions.create(**kwargs, max_completion_tokens=budget)
    try:
        return await client.chat.completions.create(**kwargs, max_tokens=max_tokens)
    except BadRequestError as exc:
        if "max_completion_tokens" in str(exc):
            budget = max(max_tokens, _OPENAI_REASONING_MIN_COMPLETION_TOKENS)
            return await client.chat.completions.create(**kwargs, max_completion_tokens=budget)
        raise


async def _openai_chat_with_images(
    *,
    system_text: str,
    user_instruction: str,
    images: List[bytes],
    model: str,
    max_tokens: Optional[int] = None,
) -> str:
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    user_content: List[dict[str, Any]] = [{"type": "text", "text": user_instruction}]
    for img in images:
        b64 = base64.b64encode(img).decode()
        user_content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}}
        )
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_content},
        ],
    }
    resp = await _openai_create_chat(client, kwargs, max_tokens=max_tokens)
    return (resp.choices[0].message.content or "").strip()


async def completion_with_images(
    *,
    system_instruction: str,
    user_instruction: str,
    images: List[bytes],
    max_tokens: int = 8192,
    http_timeout: Optional[float] = None,
) -> str:
    """
    Send rendered page **images** (+ the exact-digit text inside ``user_instruction``) to the
    active provider. Used by the image+text financial-statement pipeline so the LLM reads the
    actual statement pages (columns visible) rather than a linearized native PDF.

    Bedrock → Converse image blocks; OpenAI → ``image_url`` parts (``detail=high``).
    """
    if not images:
        raise ValueError("completion_with_images requires at least one image")
    validate_llm_config(settings)
    provider = resolve_llm_provider(settings)
    if provider == "anthropic":
        resolved = settings.ANTHROPIC_MODEL
        if not resolved:
            raise ValueError("ANTHROPIC_MODEL is not set for image input.")
        logger.info("LLM input: Anthropic direct API %d image block(s) via %s", len(images), resolved)
        return await _anthropic_chat_with_images(
            system_text=system_instruction,
            user_instruction=user_instruction,
            images=images,
            model=resolved,
            max_tokens=max_tokens,
        )
    if provider == "bedrock":
        model_id = effective_bedrock_runtime_model_id(settings)
        logger.info("LLM input: Bedrock Converse %d image block(s)", len(images))
        return await _bedrock_converse_with_images(
            system_text=system_instruction,
            user_instruction=user_instruction,
            images=images,
            model_id=model_id,
            region=settings.BEDROCK_REGION or "",
            max_tokens=max_tokens,
            timeout=http_timeout,
        )
    resolved = settings.OPENAI_VISION_MODEL or settings.OPENAI_MODEL
    if not resolved:
        raise ValueError("OPENAI_VISION_MODEL / OPENAI_MODEL is not set for image input.")
    logger.info("LLM input: OpenAI %d image block(s) via %s", len(images), resolved)
    return await _openai_chat_with_images(
        system_text=system_instruction,
        user_instruction=user_instruction,
        images=images,
        model=resolved,
        max_tokens=max_tokens,
    )


async def _bedrock_converse_with_pdf(
    *,
    system_text: str,
    user_instruction: str,
    pdf_bytes: bytes,
    pdf_filename: str,
    model_id: str,
    region: str,
    max_tokens: int = 4096,
    timeout: Optional[float] = None,
) -> str:
    return await _bedrock_converse_with_document(
        system_text=system_text,
        user_instruction=user_instruction,
        document_bytes=pdf_bytes,
        document_filename=pdf_filename,
        document_format="pdf",
        model_id=model_id,
        region=region,
        max_tokens=max_tokens,
        timeout=timeout,
    )


def _bedrock_converse_with_pdf_from_s3_sync(
    *,
    system_text: str,
    user_instruction: str,
    pdf_s3_uri: str,
    pdf_filename: str,
    model_id: str,
    region: str,
    max_tokens: int = 4096,
    timeout: Optional[float] = None,
    bucket_owner: Optional[str] = None,
) -> str:
    """Bedrock Runtime ``converse`` with PDF referenced by S3 (no 4.5 MiB inline limit on the request)."""
    log_bedrock_credential_audit(
        logger,
        context=(
            f"bedrock_converse_with_pdf_from_s3 model_id={model_id!r} region={region!r} "
            f"s3_preview={pdf_s3_uri[:120]!r}"
        ),
    )
    safe_name = _bedrock_safe_document_name(pdf_filename)
    s3loc: dict[str, Any] = {"uri": pdf_s3_uri}
    bo = (bucket_owner or "").strip()
    if bo:
        s3loc["bucketOwner"] = bo
    read_timeout = timeout if timeout is not None else float(settings.EXTERNAL_API_TIMEOUT)
    client = bedrock_runtime_client(region, read_timeout_seconds=read_timeout)
    msg: dict[str, Any] = {
        "role": "user",
        "content": [
            {"document": {"format": "pdf", "name": safe_name, "source": {"s3Location": s3loc}}},
            {"text": user_instruction},
        ],
    }
    kw: dict[str, Any] = {
        "modelId": model_id,
        "messages": [msg],
        "inferenceConfig": {"maxTokens": max_tokens},
    }
    if system_text:
        kw["system"] = [{"text": system_text}]
    try:
        data = client.converse(**kw)
    except Exception:
        logger.exception("Bedrock Converse (PDF document from S3) failed")
        raise
    return _bedrock_output_text(data)


async def _bedrock_converse_with_pdf_from_s3(
    *,
    system_text: str,
    user_instruction: str,
    pdf_s3_uri: str,
    pdf_filename: str,
    model_id: str,
    region: str,
    max_tokens: int = 4096,
    timeout: Optional[float] = None,
    bucket_owner: Optional[str] = None,
) -> str:
    def _run() -> str:
        return _bedrock_converse_with_pdf_from_s3_sync(
            system_text=system_text,
            user_instruction=user_instruction,
            pdf_s3_uri=pdf_s3_uri,
            pdf_filename=pdf_filename,
            model_id=model_id,
            region=region,
            max_tokens=max_tokens,
            timeout=timeout,
            bucket_owner=bucket_owner,
        )

    return await asyncio.to_thread(_run)


async def _openai_chat_with_pdf(
    *,
    system_text: str,
    user_instruction: str,
    pdf_bytes: bytes,
    pdf_filename: str,
    model: str,
    max_tokens: Optional[int] = None,
) -> str:
    """Upload PDF to OpenAI Files, then attach by file_id in chat (native PDF to the model)."""
    if len(pdf_bytes) > _MAX_OPENAI_PDF_BYTES:
        raise ValueError(f"PDF exceeds max size ({_MAX_OPENAI_PDF_BYTES} bytes) for OpenAI input.")
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    fn = (pdf_filename or "document.pdf").replace("/", "_")[-200:] or "document.pdf"
    uploaded = await client.files.create(file=(fn, io.BytesIO(pdf_bytes)), purpose="user_data")
    user_content: List[dict[str, Any]] = [
        {"type": "file", "file": {"file_id": uploaded.id}},
        {"type": "text", "text": user_instruction},
    ]
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_content},
        ],
    }
    resp = await _openai_create_chat(client, kwargs, max_tokens=max_tokens)
    choice = resp.choices[0].message
    return (choice.content or "").strip()


def _bedrock_converse_sync(
    *,
    messages: List[dict[str, Any]],
    model_id: str,
    region: str,
    max_tokens: int = 4096,
    timeout: Optional[float] = None,
    tool: Optional[dict[str, Any]] = None,
) -> str:
    log_bedrock_credential_audit(
        logger,
        context=f"bedrock_converse model_id={model_id!r} region={region!r}",
    )
    system_blocks, bedrock_messages = _openai_messages_to_bedrock(messages)
    kw: dict[str, Any] = {
        "modelId": model_id,
        "messages": bedrock_messages,
        "inferenceConfig": {"maxTokens": max_tokens},
    }
    if system_blocks:
        kw["system"] = system_blocks
    if tool is not None:
        # Forced tool use → Claude must return `toolUse.input` matching `tool["schema"]`.
        kw["toolConfig"] = {
            "tools": [{"toolSpec": {"name": tool["name"], "inputSchema": {"json": tool["schema"]}}}],
            "toolChoice": {"tool": {"name": tool["name"]}},
        }
    read_timeout = timeout if timeout is not None else float(settings.EXTERNAL_API_TIMEOUT)
    client = bedrock_runtime_client(region, read_timeout_seconds=read_timeout)
    try:
        data = client.converse(**kw)
    except Exception:
        logger.exception("Bedrock Converse failed")
        raise
    if tool is not None:
        tool_input = _bedrock_output_tool_input(data)
        if tool_input is not None:
            return json.dumps(tool_input)
    return _bedrock_output_text(data)


async def _bedrock_converse(
    *,
    messages: List[dict[str, Any]],
    model_id: str,
    region: str,
    max_tokens: int = 4096,
    timeout: Optional[float] = None,
    tool: Optional[dict[str, Any]] = None,
) -> str:
    def _run() -> str:
        return _bedrock_converse_sync(
            messages=messages,
            model_id=model_id,
            region=region,
            max_tokens=max_tokens,
            timeout=timeout,
            tool=tool,
        )

    return await asyncio.to_thread(_run)


async def _openai_chat(
    *,
    messages: List[dict[str, Any]],
    model: str,
    max_tokens: Optional[int] = None,
    tool: Optional[dict[str, Any]] = None,
) -> str:
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    if tool is not None:
        # Forced function call → the model must return arguments matching `tool["schema"]`.
        kwargs["tools"] = [{"type": "function", "function": {"name": tool["name"], "parameters": tool["schema"]}}]
        kwargs["tool_choice"] = {"type": "function", "function": {"name": tool["name"]}}
    resp = await _openai_create_chat(client, kwargs, max_tokens=max_tokens)
    choice = resp.choices[0].message
    if tool is not None:
        calls = getattr(choice, "tool_calls", None) or []
        if calls:
            return (calls[0].function.arguments or "").strip()
        # Fell back to a normal message (model declined the tool) — let the caller parse it.
    return (choice.content or "").strip()


def _anthropic_messages_from_openai(
    messages: List[dict[str, Any]],
) -> tuple[str, List[dict[str, Any]]]:
    """Split OpenAI-style messages into (system_text, anthropic_messages)."""
    system_parts: List[str] = []
    anthropic_messages: List[dict[str, Any]] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            if isinstance(content, str):
                system_parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        system_parts.append(str(part.get("text", "")))
            continue
        if role not in ("user", "assistant"):
            role = "user"
        if isinstance(content, list):
            text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            content = "\n".join(text_parts)
        anthropic_messages.append({"role": role, "content": str(content)})
    return "\n".join(system_parts), anthropic_messages


async def _anthropic_chat(
    *,
    messages: List[dict[str, Any]],
    model: str,
    max_tokens: int = 4096,
    tool: Optional[dict[str, Any]] = None,
) -> str:
    client = _anthropic_sdk.AsyncAnthropic(api_key=settings.ANTHROPIC_KEY)
    system_text, anthropic_messages = _anthropic_messages_from_openai(messages)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": anthropic_messages,
    }
    if system_text:
        kwargs["system"] = system_text
    if tool is not None:
        kwargs["tools"] = [
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": tool["schema"],
            }
        ]
        kwargs["tool_choice"] = {"type": "tool", "name": tool["name"]}
    resp = await client.messages.create(**kwargs)
    if tool is not None:
        for block in resp.content:
            if block.type == "tool_use":
                return json.dumps(block.input)
    for block in resp.content:
        if hasattr(block, "text"):
            return block.text.strip()
    raise ValueError("Anthropic returned no text content")


async def _anthropic_chat_with_images(
    *,
    system_text: str,
    user_instruction: str,
    images: List[bytes],
    model: str,
    max_tokens: int = 8192,
) -> str:
    client = _anthropic_sdk.AsyncAnthropic(api_key=settings.ANTHROPIC_KEY)
    content: List[dict[str, Any]] = []
    for img in images:
        b64 = base64.b64encode(img).decode()
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}})
    content.append({"type": "text", "text": user_instruction})
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
    }
    if system_text:
        kwargs["system"] = system_text
    resp = await client.messages.create(**kwargs)
    for block in resp.content:
        if hasattr(block, "text"):
            return block.text.strip()
    raise ValueError("Anthropic returned no text content")


async def _anthropic_chat_with_pdf(
    *,
    system_text: str,
    user_instruction: str,
    pdf_bytes: bytes,
    model: str,
    max_tokens: int = 8192,
) -> str:
    client = _anthropic_sdk.AsyncAnthropic(api_key=settings.ANTHROPIC_KEY)
    b64 = base64.b64encode(pdf_bytes).decode()
    content: List[dict[str, Any]] = [
        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}},
        {"type": "text", "text": user_instruction},
    ]
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
    }
    if system_text:
        kwargs["system"] = system_text
    resp = await client.messages.create(**kwargs)
    for block in resp.content:
        if hasattr(block, "text"):
            return block.text.strip()
    raise ValueError("Anthropic returned no text content")


async def chat_completion(
    messages: List[dict[str, Any]],
    *,
    model: Optional[str] = None,
    vision: bool = False,
    max_tokens: Optional[int] = None,
    http_timeout: Optional[float] = None,
    tool: Optional[dict[str, Any]] = None,
) -> str:
    """
    Run a chat completion on the configured provider.

    :param messages: OpenAI-style messages (role + content).
    :param model: Override model; defaults to OPENAI_MODEL / OPENAI_VISION_MODEL or BEDROCK_MODEL_ID.
    :param vision: If True and using OpenAI, prefer OPENAI_VISION_MODEL when model is unset.
    """
    validate_llm_config(settings)
    provider = resolve_llm_provider(settings)
    if provider == "anthropic":
        resolved = model or settings.ANTHROPIC_MODEL
        if not resolved:
            raise ValueError("No Anthropic model configured (ANTHROPIC_MODEL).")
        logger.info("LLM: Anthropic direct API via %s", resolved)
        return await _anthropic_chat(messages=messages, model=resolved, max_tokens=max_tokens or 4096, tool=tool)
    if provider == "openai":
        resolved = model
        if resolved is None:
            if vision and settings.OPENAI_VISION_MODEL:
                resolved = settings.OPENAI_VISION_MODEL
            else:
                resolved = settings.OPENAI_MODEL
        if not resolved:
            raise ValueError("No OpenAI model configured (OPENAI_MODEL / OPENAI_VISION_MODEL).")
        return await _openai_chat(messages=messages, model=resolved, max_tokens=max_tokens, tool=tool)
    resolved_model = effective_bedrock_runtime_model_id(settings, override=model)
    return await _bedrock_converse(
        messages=messages,
        model_id=resolved_model,
        region=settings.BEDROCK_REGION or "",
        max_tokens=max_tokens or 4096,
        timeout=http_timeout,
        tool=tool,
    )


async def _completion_with_extracted_text(
    *,
    system_instruction: str,
    user_instruction: str,
    file_bytes: bytes,
    filename: str,
    max_tokens: int,
    http_timeout: Optional[float],
    is_xlsx: bool,
) -> str:
    from src.services.document_text import MAX_DOCUMENT_CHARS, extract_text_from_file_bytes

    text = extract_text_from_file_bytes(filename, file_bytes)
    if not text.strip():
        logger.warning(
            "completion_with_document: empty extracted text for %s",
            filename,
        )
    cap = MAX_DOCUMENT_CHARS
    if len(text) > cap:
        text = text[:cap] + "\n\n[truncated]"
    if is_xlsx:
        body = (
            f"{user_instruction}\n\n"
            "The document is an Excel workbook. Its content has been parsed into "
            "sheet/row text below. Use this text to extract the financial data.\n\n"
            f"Spreadsheet text:\n\n{text}"
        )
    else:
        body = f"{user_instruction}\n\nDocument text:\n\n{text}"
    return await chat_completion(
        [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": body},
        ],
        max_tokens=max_tokens,
        http_timeout=http_timeout,
    )


async def completion_with_document(
    *,
    system_instruction: str,
    user_instruction: str,
    file_bytes: bytes,
    filename: str,
    max_tokens: int = 8192,
    http_timeout: Optional[float] = None,
    storage_uri: Optional[str] = None,
) -> str:
    """
    Prefer native document input to the active provider (Bedrock document block or OpenAI file part).

    Bedrock: PDF, ``.xlsx``, and ``.xls`` are sent as Converse ``document.source.bytes``.
    On failure (or unsupported provider), falls back to ``extract_text_from_file_bytes`` + text chat.

    **Bedrock:** files are still stored in S3 by the app; ``storage_uri`` is optional metadata only.
    Claude 4+ PDFs are exempt from the 4.5 MB inline cap per Bedrock API restrictions; other models enforce the guard.
    """
    from src.services.document_text import is_xlsx_filename

    validate_llm_config(settings)
    provider = resolve_llm_provider(settings)
    fn = (filename or "").strip().lower()
    native_format = _bedrock_native_document_format(filename)
    is_xlsx = is_xlsx_filename(filename)

    if native_format and provider == "bedrock":
        resolved_model = effective_bedrock_runtime_model_id(settings)
        su = (storage_uri or "").strip()
        if su.startswith("s3://"):
            logger.info(
                "LLM input: Bedrock Converse %s inline bytes (%s bytes; object also at %s)",
                native_format,
                len(file_bytes),
                su[:80],
            )
        else:
            logger.info(
                "LLM input: Bedrock Converse %s inline bytes (%s bytes)",
                native_format,
                len(file_bytes),
            )
        try:
            return await _bedrock_converse_with_document(
                system_text=system_instruction,
                user_instruction=user_instruction,
                document_bytes=file_bytes,
                document_filename=filename or f"document.{native_format}",
                document_format=native_format,
                model_id=resolved_model,
                region=settings.BEDROCK_REGION or "",
                max_tokens=max_tokens,
                timeout=http_timeout,
            )
        except Exception as exc:
            logger.warning(
                "Bedrock native %s document failed (%s); falling back to extracted text",
                native_format,
                str(exc)[:400],
            )
            return await _completion_with_extracted_text(
                system_instruction=system_instruction,
                user_instruction=user_instruction,
                file_bytes=file_bytes,
                filename=filename,
                max_tokens=max_tokens,
                http_timeout=http_timeout,
                is_xlsx=is_xlsx,
            )

    is_pdf = fn.endswith(".pdf")
    if is_pdf and provider == "anthropic":
        resolved = settings.ANTHROPIC_MODEL
        if not resolved:
            raise ValueError("ANTHROPIC_MODEL is not set.")
        logger.info("LLM input: native PDF via Anthropic direct API (%s bytes) via %s", len(file_bytes), resolved)
        try:
            return await _anthropic_chat_with_pdf(
                system_text=system_instruction,
                user_instruction=user_instruction,
                pdf_bytes=file_bytes,
                model=resolved,
                max_tokens=max_tokens,
            )
        except Exception:
            logger.info("Falling back to extracted text for Anthropic after PDF failure")
            return await _completion_with_extracted_text(
                system_instruction=system_instruction,
                user_instruction=user_instruction,
                file_bytes=file_bytes,
                filename=filename,
                max_tokens=max_tokens,
                http_timeout=http_timeout,
                is_xlsx=False,
            )

    if is_pdf and provider == "openai":
        resolved = settings.OPENAI_MODEL
        if not resolved:
            raise ValueError("OPENAI_MODEL is not set.")
        logger.info("LLM input: native PDF via OpenAI chat file part (%s bytes)", len(file_bytes))
        try:
            return await _openai_chat_with_pdf(
                system_text=system_instruction,
                user_instruction=user_instruction,
                pdf_bytes=file_bytes,
                pdf_filename=filename or "document.pdf",
                model=resolved,
                max_tokens=max_tokens,
            )
        except Exception:
            logger.info("Falling back to extracted text for OpenAI after PDF chat failure")
            return await _completion_with_extracted_text(
                system_instruction=system_instruction,
                user_instruction=user_instruction,
                file_bytes=file_bytes,
                filename=filename,
                max_tokens=max_tokens,
                http_timeout=http_timeout,
                is_xlsx=False,
            )

    return await _completion_with_extracted_text(
        system_instruction=system_instruction,
        user_instruction=user_instruction,
        file_bytes=file_bytes,
        filename=filename,
        max_tokens=max_tokens,
        http_timeout=http_timeout,
        is_xlsx=is_xlsx,
    )
