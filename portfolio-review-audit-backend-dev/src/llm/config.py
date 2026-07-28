"""
Resolve which LLM backend is active (OpenAI vs AWS Bedrock vs Anthropic direct) from settings.
"""
from typing import Literal, Optional

from src.configs.env import Settings

# Bedrock Converse / on-demand inference for some foundation model IDs returns ValidationException and
# requires an application inference profile ID (e.g. ``global.anthropic...``). Map known bare IDs here.
_BEDROCK_FOUNDATION_TO_DEFAULT_INFERENCE_PROFILE: dict[str, str] = {
    "anthropic.claude-opus-4-7": "global.anthropic.claude-opus-4-7",
    "anthropic.claude-sonnet-4-6": "global.anthropic.claude-sonnet-4-6",
}


def effective_bedrock_runtime_model_id(settings: Settings, override: Optional[str] = None) -> str:
    """
    Resolve the ``modelId`` for Bedrock Runtime (Converse / batch) calls.

    If ``BEDROCK_MODEL_ID`` or ``override`` is a bare Anthropic foundation ID that AWS only serves via an
    inference profile, remap to our default profile. Otherwise return the configured ID unchanged — set
    ``eu.``, ``us.``, or ``global.`` prefixes explicitly when your policy requires a specific geography.
    """
    raw = (override if override is not None else settings.BEDROCK_MODEL_ID) or ""
    raw = raw.strip()
    if not raw:
        raise ValueError("BEDROCK_MODEL_ID is not set.")
    return _BEDROCK_FOUNDATION_TO_DEFAULT_INFERENCE_PROFILE.get(raw, raw)


def effective_document_extraction_provider(settings: Settings) -> Literal["openai", "bedrock", "anthropic"]:
    """
    Provider used for **file** extraction (audit + org-chart).

    - **LOCAL_DEV:** follows ``resolve_llm_provider`` (Anthropic > OpenAI > Bedrock).
    - **Otherwise:** follows ``resolve_llm_provider`` (typically Bedrock with PDFs read from uploaded ``s3://`` via Converse).
    """
    return resolve_llm_provider(settings)


def resolve_llm_provider(settings: Settings) -> Literal["openai", "bedrock", "anthropic"]:
    if settings.LLM_PROVIDER == "anthropic":
        return "anthropic"
    if settings.LLM_PROVIDER == "openai":
        return "openai"
    if settings.LLM_PROVIDER == "bedrock":
        return "bedrock"
    # Local: prefer Anthropic > OpenAI > Bedrock when LLM_PROVIDER is not forced.
    if settings.LOCAL_DEV and (settings.ANTHROPIC_KEY or "").strip():
        return "anthropic"
    if settings.LOCAL_DEV and (settings.OPENAI_API_KEY or "").strip():
        return "openai"
    if settings.BEDROCK_MODEL_ID and settings.BEDROCK_REGION:
        return "bedrock"
    return "openai"


def validate_llm_config(
    settings: Settings,
    *,
    provider: Optional[Literal["openai", "bedrock", "anthropic"]] = None,
) -> None:
    """Raise ValueError if the provider is missing required configuration."""
    p = provider if provider is not None else resolve_llm_provider(settings)
    if p == "anthropic":
        if not settings.ANTHROPIC_KEY:
            raise ValueError("ANTHROPIC_KEY is required when using Anthropic.")
        if not settings.ANTHROPIC_MODEL:
            raise ValueError("ANTHROPIC_MODEL is required when using Anthropic.")
        return
    if p == "openai":
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is required when using OpenAI.")
        if not settings.OPENAI_MODEL:
            raise ValueError("OPENAI_MODEL is required when using OpenAI.")
        return
    if not settings.BEDROCK_MODEL_ID:
        raise ValueError("BEDROCK_MODEL_ID is required when using Bedrock.")
    if not settings.BEDROCK_REGION:
        raise ValueError("BEDROCK_REGION is required when using Bedrock.")
