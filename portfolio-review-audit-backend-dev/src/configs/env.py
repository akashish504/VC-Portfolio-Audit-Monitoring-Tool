"""
Environment configuration
"""
import os
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

APP_CONTEXT_PATH = "/api"


class Settings(BaseSettings):
    """
    Application settings — loaded from environment variables.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Server
    APP_NAME: str = os.environ.get("APP_NAME", "vc-audit-tool")
    ENV: str = os.environ.get("ENV", "development")
    DEBUG: bool = os.environ.get("DEBUG", "false").lower() in ("1", "true", "yes")
    LOCAL_DEV: bool = os.environ.get("LOCAL_DEV", "false").lower() in ("1", "true", "yes")
    DISABLE_AUTH: bool = os.environ.get("DISABLE_AUTH", "false").lower() in ("1", "true", "yes")
    DISABLE_CSRF: bool = os.environ.get("DISABLE_CSRF", "false").lower() in ("1", "true", "yes")

    # PostgreSQL
    POSTGRES_USER: str = os.environ.get("POSTGRES_USER", "postgres")
    POSTGRES_PASSWORD: str = os.environ.get("POSTGRES_PASSWORD", "tailoredAI")
    POSTGRES_HOST: str = os.environ.get("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: str = os.environ.get("POSTGRES_PORT", "5432")
    POSTGRES_DB: str = os.environ.get("POSTGRES_DB", "peakxv_pr_tool")
    POSTGRES_SSLMODE: str = os.environ.get("POSTGRES_SSLMODE", "require")
    POSTGRES_SSLROOTCERT: Optional[str] = os.environ.get("POSTGRES_SSLROOTCERT")

    # Source DB (portfolio-review server) — only used in prod for classify_incoming_emails
    PR_POSTGRES_USER: Optional[str] = os.environ.get("PR_POSTGRES_USER")
    PR_POSTGRES_PASSWORD: Optional[str] = os.environ.get("PR_POSTGRES_PASSWORD")
    PR_POSTGRES_HOST: Optional[str] = os.environ.get("PR_POSTGRES_HOST")
    PR_POSTGRES_PORT: str = os.environ.get("PR_POSTGRES_PORT", "5432")
    PR_POSTGRES_DB: Optional[str] = os.environ.get("PR_POSTGRES_DB")

    # AWS S3 / boto3 — deployments: IRSA or instance profile (default chain).
    # LOCAL_DEV: optional static keys from .env for local Bedrock/S3 (see bedrock_runtime_client).
    AWS_REGION: str = os.environ.get("AWS_REGION", "")
    AWS_ACCESS_KEY_ID: Optional[str] = os.environ.get("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY: Optional[str] = os.environ.get("AWS_SECRET_ACCESS_KEY")
    AWS_SESSION_TOKEN: Optional[str] = os.environ.get("AWS_SESSION_TOKEN")

    # Primary bucket for org charts + audit uploads.
    # Backward-compatible with legacy AWS_S3_BUCKET.
    S3_BUCKET: str = os.environ.get("S3_BUCKET") or os.environ.get("AWS_S3_BUCKET", "")
    # 12-digit account ID for Bedrock batch S3 in/out (cross-account / bucket-owner visibility).
    S3_BUCKET_OWNER_ACCOUNT_ID: Optional[str] = os.environ.get("S3_BUCKET_OWNER_ACCOUNT_ID")

    # Bucket for draft email attachments.
    AWS_S3_EMAIL_BUCKET: str = os.environ.get("AWS_S3_EMAIL_BUCKET", "")
    AWS_S3_EMAIL_ATTACH_PATH: str = os.environ.get("AWS_S3_EMAIL_ATTACH_PATH", "email-attachments")
    MAX_ATTACHMENT_SIZE_MB: int = int(os.environ.get("MAX_ATTACHMENT_SIZE_MB", "1"))
    MAX_ATTACHMENT_FILES: int = int(os.environ.get("MAX_ATTACHMENT_FILES", "10"))

    # Snowflake
    SNOWFLAKE_USER: str = os.environ.get("SNOWFLAKE_USER", "")
    SNOWFLAKE_PASSWORD: str = os.environ.get("SNOWFLAKE_PASSWORD", "")
    SNOWFLAKE_ACCOUNT: str = os.environ.get("SNOWFLAKE_ACCOUNT", "")
    SNOWFLAKE_WAREHOUSE: str = os.environ.get("SNOWFLAKE_WAREHOUSE", "")
    SNOWFLAKE_DATABASE: str = os.environ.get("SNOWFLAKE_DATABASE", "")
    SNOWFLAKE_SCHEMA: str = os.environ.get("SNOWFLAKE_SCHEMA", "")

    # Security
    CSRF_SECRET_KEY: str = os.environ.get("CSRF_SECRET_KEY", "")
    CORS_ORIGIN: str = os.environ.get("CORS_ORIGIN", "http://localhost:3000")
    AUTHZ_TENANT_CLAIM: str = os.environ.get("AUTHZ_TENANT_CLAIM", "tenant_id")
    AUTHZ_REQUIRED_GROUPS: str = os.environ.get("AUTHZ_REQUIRED_GROUPS", "")
    AUTHZ_ALLOW_DEFAULT_TENANT: bool = os.environ.get("AUTHZ_ALLOW_DEFAULT_TENANT", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    AUTHZ_DEFAULT_TENANT: str = os.environ.get("AUTHZ_DEFAULT_TENANT", "default")
    INFRA_PROBE_SECRET: str = os.environ.get("INFRA_PROBE_SECRET", "")

    # Okta (JWT)
    OKTA_ISSUER: Optional[str] = os.environ.get("OKTA_ISSUER")
    OKTA_AUDIENCE: Optional[str] = os.environ.get("OKTA_AUDIENCE")

    # Timeouts
    EXTERNAL_API_TIMEOUT: int = int(os.environ.get("EXTERNAL_API_TIMEOUT", "30"))

    # FX: FX_USE_MOCK=true forces mock quotes. Else live XE — if XE_ACCOUNT_ID/XE_API_KEY missing, service falls back to mock.
    FX_USE_MOCK: bool = os.environ.get("FX_USE_MOCK", "false").lower() in ("1", "true", "yes")
    XE_CONVERT_URL: str = os.environ.get(
        "XE_CONVERT_URL",
        "https://xecdapi.xe.com/v1/convert_from.json/",
    ).strip()
    XE_HISTORIC_URL: str = os.environ.get(
        "XE_HISTORIC_URL",
        "https://xecdapi.xe.com/v1/historic_rate.json/",
    ).strip()
    XE_ACCOUNT_ID: Optional[str] = os.environ.get("XE_ACCOUNT_ID")
    XE_API_KEY: Optional[str] = os.environ.get("XE_API_KEY")
    # Comma-separated ISO codes for XE ``to=`` batch (exclude ``from`` at call time).
    FX_QUOTE_CURRENCIES: Optional[str] = os.environ.get("FX_QUOTE_CURRENCIES")
    # Legacy — ignored when FX_USE_MOCK=false (live FX is XE-only).
    FX_API_URL: Optional[str] = os.environ.get("FX_API_URL")
    FX_API_HEADERS: Optional[str] = os.environ.get("FX_API_HEADERS")

    # Email sending
    EMAIL_BASE_URL: str = os.environ.get("EMAIL_BASE_URL", "")
    BASE_FROM_EMAIL: str = os.environ.get("BASE_FROM_EMAIL", "audit@vantagecap.com")

    # LLM — local (LOCAL_DEV): prefers Anthropic when ANTHROPIC_KEY is set; else OpenAI when OPENAI_API_KEY; else Bedrock.
    # Bedrock Runtime uses IAM (IRSA or LOCAL_DEV access keys); AWS_BEARER_TOKEN_BEDROCK is unused.
    LLM_PROVIDER: Optional[str] = os.environ.get("LLM_PROVIDER")
    OPENAI_API_KEY: Optional[str] = os.environ.get("OPENAI_API_KEY")
    OPENAI_MODEL: Optional[str] = os.environ.get("OPENAI_MODEL", "gpt-4o")
    OPENAI_VISION_MODEL: Optional[str] = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o")
    ANTHROPIC_KEY: Optional[str] = os.environ.get("ANTHROPIC_KEY")
    ANTHROPIC_MODEL: Optional[str] = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    # Unused: Bedrock Runtime Converse uses IAM (IRSA or local keys). Kept for backward-compatible .env.
    AWS_BEARER_TOKEN_BEDROCK: Optional[str] = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")

    BEDROCK_MODEL_ID: Optional[str] = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-sonnet-4-6")
    BEDROCK_REGION: Optional[str] = os.environ.get("BEDROCK_REGION")

    # Bedrock batch inference: S3 JSONL; IAM role for the job. (Do not set modelInvocationType=Converse on
    # CreateModelInvocationJob — it causes ValidationException in some org Bedrock accounts.)
    BEDROCK_BATCH_ROLE_ARN: Optional[str] = os.environ.get("BEDROCK_BATCH_ROLE_ARN")
    BEDROCK_BATCH_S3_PREFIX: str = os.environ.get("BEDROCK_BATCH_S3_PREFIX", "bedrock-batch")
    BEDROCK_BATCH_POLL_INTERVAL_SECONDS: int = int(os.environ.get("BEDROCK_BATCH_POLL_INTERVAL_SECONDS", "15"))
    BEDROCK_BATCH_TIMEOUT_HOURS: int = int(os.environ.get("BEDROCK_BATCH_TIMEOUT_HOURS", "24"))
    BEDROCK_CROSS_ACCOUNT_ROLE_ARN: Optional[str] = os.environ.get("BEDROCK_CROSS_ACCOUNT_ROLE_ARN")
    # Bedrock Runtime ``converse`` (non-LOCAL_DEV): prefer assuming this IAM role instead of IRSA caller.
    # If unset, ARN is derived from ``BEDROCK_RUNTIME_CALLER_ACCOUNT_ID`` (or STS account) +
    # ``BEDROCK_RUNTIME_CALLER_ROLE_NAME`` (default ``bedrock-caller-devapp-role``).
    BEDROCK_RUNTIME_ASSUME_ROLE_ARN: Optional[str] = os.environ.get("BEDROCK_RUNTIME_ASSUME_ROLE_ARN")
    # 12-digit account ID owning ``bedrock-caller-*`` role; avoids extra STS lookup when set in deployment.
    BEDROCK_RUNTIME_CALLER_ACCOUNT_ID: Optional[str] = os.environ.get("BEDROCK_RUNTIME_CALLER_ACCOUNT_ID")
    BEDROCK_RUNTIME_CALLER_ROLE_NAME: str = os.environ.get(
        "BEDROCK_RUNTIME_CALLER_ROLE_NAME",
        "bedrock-caller-devapp-role",
    )

    # Longer timeout for Bedrock org-chart / text-only LLM calls.
    ORG_CHART_LLM_TIMEOUT: int = int(os.environ.get("ORG_CHART_LLM_TIMEOUT", "120"))

    # Per-request read timeout (seconds) for Bedrock Converse calls that send a PDF inline.
    # Large multi-page financial PDFs + max_tokens=8192 can take 3-4 min on Claude Sonnet 4.x.
    # Keep well above the longest expected model latency; boto3 retries are disabled on the PDF
    # path (see bedrock_runtime_client) so this is a hard per-attempt ceiling.
    BEDROCK_PDF_LLM_TIMEOUT: int = int(os.environ.get("BEDROCK_PDF_LLM_TIMEOUT", "360"))

    # --- Image+text financial-statement extraction pipeline (statement pages → image+digits) ---
    # ON by default: audit_financials PDFs are extracted via the statement-page image+text pipeline
    # (renders only the located statement pages) instead of the legacy whole-document native pass.
    # It falls back to the legacy pass automatically on any miss/error. Set the flag to a falsey
    # value (EXTRACTION_USE_IMAGE_PIPELINE=false) to force the legacy pass everywhere.
    EXTRACTION_USE_IMAGE_PIPELINE: bool = os.environ.get("EXTRACTION_USE_IMAGE_PIPELINE", "true").lower() in ("1", "true", "yes")
    # Target long-edge px for rendered statement pages (page-size agnostic; ~2000 is the sweet spot).
    EXTRACTION_RENDER_TARGET_PX: int = int(os.environ.get("EXTRACTION_RENDER_TARGET_PX", "2000"))
    # Use AWS Textract to recover exact digits from SCANNED statement pages (no text layer).
    EXTRACTION_TEXTRACT_ENABLED: bool = os.environ.get("EXTRACTION_TEXTRACT_ENABLED", "true").lower() in ("1", "true", "yes")
    # Textract is not available in every region (absent in eu-north-1); call it here.
    TEXTRACT_REGION: str = os.environ.get("TEXTRACT_REGION", "us-east-1")

    # --- Pass-2 mapping: band-aware deterministic mapper (raw tree → canonical) ---
    # OFF by default: when enabled, audit-financials pass-2 mapping uses the deterministic
    # band-locked mapper (src.services.financial_deterministic_mapping) instead of the LLM
    # "loose semantic matching" pass. Placement is gated by source band (statement / balance-sheet
    # side+currentness / cash-flow activity) + arithmetic role (total vs component), so cross-band
    # mis-tagging (e.g. financing lines in operating, non-current vs current) is structurally
    # impossible. Lines that don't clear the confidence floor are surfaced as unmatched (HITL).
    # Falls back to the LLM mapper automatically on any error.
    EXTRACTION_USE_DETERMINISTIC_MAPPER: bool = os.environ.get(
        "EXTRACTION_USE_DETERMINISTIC_MAPPER", "true"
    ).lower() in ("1", "true", "yes")

    # DOCX → PDF (LibreOffice headless); rare uploads can be tens of MiB.
    LIBREOFFICE_PATH: Optional[str] = os.environ.get("LIBREOFFICE_PATH")
    DOCX_TO_PDF_TIMEOUT_SEC: int = int(os.environ.get("DOCX_TO_PDF_TIMEOUT_SEC", "600"))
    MAX_DOCX_UPLOAD_BYTES: int = int(os.environ.get("MAX_DOCX_UPLOAD_BYTES", str(35 * 1024 * 1024)))

    @field_validator("LLM_PROVIDER", mode="before")
    @classmethod
    def _normalize_llm_provider(cls, v: Optional[str]) -> Optional[str]:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        s = v.strip().lower()
        if s not in ("openai", "bedrock", "anthropic"):
            raise ValueError("LLM_PROVIDER must be 'openai', 'bedrock', or 'anthropic'")
        return s


settings = Settings()

# Local dev: pick a default LLM when `LLM_PROVIDER` is not set in the environment.
# - Prefer OpenAI when OPENAI_API_KEY is set (typical local ChatGPT / API testing).
# - Else fall back to Bedrock when model + region are set (IAM or local access keys).
if settings.LOCAL_DEV and os.environ.get("LLM_PROVIDER") is None:
    if settings.ANTHROPIC_KEY and str(settings.ANTHROPIC_KEY).strip():
        settings.LLM_PROVIDER = "anthropic"
    elif settings.OPENAI_API_KEY and str(settings.OPENAI_API_KEY).strip():
        settings.LLM_PROVIDER = "openai"
    elif settings.BEDROCK_MODEL_ID and settings.BEDROCK_REGION:
        settings.LLM_PROVIDER = "bedrock"