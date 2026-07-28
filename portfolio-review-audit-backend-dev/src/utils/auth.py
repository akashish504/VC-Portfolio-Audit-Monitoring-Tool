"""
Okta JWT Authentication Utility
"""
import logging
from typing import Any, Dict, Optional

import jwt
from fastapi import HTTPException
from jwt import PyJWKClient
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError, PyJWTError

from src.configs.env import settings

logger = logging.getLogger(__name__)

_jwks_client: Optional[PyJWKClient] = None


def _jwks_url() -> str:
    return f"{settings.OKTA_ISSUER.rstrip('/')}/v1/keys"


def get_jwks_client() -> PyJWKClient:
    """Return a cached JWKS client for the configured Okta issuer."""
    global _jwks_client
    if not settings.OKTA_ISSUER:
        raise HTTPException(
            status_code=503,
            detail={"message": "Okta is not configured", "hint": "Set OKTA_ISSUER and OKTA_AUDIENCE in .env"},
        )
    if _jwks_client is None:
        _jwks_client = PyJWKClient(_jwks_url(), cache_keys=True)
    return _jwks_client


def invalidate_jwks_cache():
    """Invalidate the JWKS cache (call this on key rotation)."""
    global _jwks_client
    _jwks_client = None


async def verify_token(token: str) -> Dict[str, Any]:
    """
    Verify an Okta-issued JWT and return its decoded claims.
    Raises HTTPException 401 if the token is missing, expired, or invalid.
    """
    if not token:
        raise HTTPException(status_code=401, detail={"message": "Token missing"})
    if not settings.OKTA_ISSUER or not settings.OKTA_AUDIENCE:
        raise HTTPException(
            status_code=503,
            detail={"message": "Okta is not configured", "hint": "Set OKTA_ISSUER and OKTA_AUDIENCE in .env"},
        )
    try:
        client = get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.OKTA_AUDIENCE,
            issuer=settings.OKTA_ISSUER,
        )
        logger.debug("Token verified for subject: %s", claims.get("sub"))
        return claims
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail={"message": "Token has expired"})
    except (InvalidTokenError, PyJWTError):
        logger.warning("JWT validation failed (invalid signature or claims)", exc_info=True)
        raise HTTPException(status_code=401, detail={"message": "Invalid token"})
    except HTTPException:
        raise
    except Exception:
        logger.exception("Token verification failed")
        raise HTTPException(
            status_code=401,
            detail={"message": "Token verification failed"},
        )
