"""LLM routing: OpenAI (local) vs AWS Bedrock (deployment)."""
from src.llm.client import chat_completion, completion_with_document, completion_with_images
from src.llm.config import resolve_llm_provider, validate_llm_config

__all__ = [
    "chat_completion",
    "completion_with_document",
    "completion_with_images",
    "resolve_llm_provider",
    "validate_llm_config",
]
