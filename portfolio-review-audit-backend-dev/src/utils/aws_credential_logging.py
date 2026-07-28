"""Optional hook for AWS/Bedrock credential diagnostics (disabled)."""

from __future__ import annotations

import logging


def log_bedrock_credential_audit(_logger: logging.Logger, *, context: str) -> None:
    """No-op. Verbose credential logging was removed for security and log noise."""
