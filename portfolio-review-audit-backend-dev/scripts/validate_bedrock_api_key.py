#!/usr/bin/env python3
"""
Smoke-test Bedrock Runtime Converse with the same IAM path as src/llm/client.py
(boto3 default chain, or LOCAL_DEV + AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from .env).

Usage (from portfolio-review-audit-backend with pipenv so .env loads into Settings):
  pipenv run python scripts/validate_bedrock_api_key.py
"""
from __future__ import annotations

import json
import os
import sys

# Project root: portfolio-review-audit-backend/
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> int:
    try:
        from src.configs.env import settings
        from src.utils.bedrock_runtime_client import bedrock_runtime_client
    except ImportError as e:
        print(f"Import error (run from repo root / use pipenv): {e}", file=sys.stderr)
        return 2

    from src.llm.config import effective_bedrock_runtime_model_id

    region = (settings.BEDROCK_REGION or "").strip()
    model_raw = (settings.BEDROCK_MODEL_ID or "").strip()
    model_id = effective_bedrock_runtime_model_id(settings)

    if not region:
        print("Missing BEDROCK_REGION (in .env or environment)", file=sys.stderr)
        return 1
    if not model_raw:
        print("Missing BEDROCK_MODEL_ID (in .env or environment)", file=sys.stderr)
        return 1

    print(
        f"region={region!r} model_id_raw={model_raw!r} model_id_resolved={model_id!r} "
        f"LOCAL_DEV={settings.LOCAL_DEV}"
    )

    client = bedrock_runtime_client(region, read_timeout_seconds=60.0)
    try:
        resp = client.converse(
            modelId=model_id,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": 'Reply with exactly the word "ok" and nothing else.'}],
                }
            ],
            inferenceConfig={"maxTokens": 32},
        )
    except Exception as e:
        print(f"converse() failed: {e}", file=sys.stderr)
        return 1

    out = resp.get("output") or {}
    msg = out.get("message") or {}
    parts = msg.get("content") or []
    texts = [p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p]
    text = "".join(texts).strip()
    print(json.dumps(resp, indent=2, default=str)[:4000])
    print(f"\nAssistant text (trimmed): {text[:200]!r}")

    if text:
        print("\nOK: Bedrock Converse succeeded with current credentials.")
        return 0
    print("\nFAILED: empty assistant output.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
