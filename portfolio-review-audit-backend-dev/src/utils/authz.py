"""
Authorization helpers.
"""
from __future__ import annotations

import logging
from typing import Any, FrozenSet

from fastapi import HTTPException, Request

from src.configs.env import settings

logger = logging.getLogger(__name__)


def _groups_from_claims(claims: dict[str, Any]) -> FrozenSet[str]:
    raw = claims.get("groups")
    if isinstance(raw, list):
        return frozenset(str(x).strip() for x in raw if str(x).strip())
    if isinstance(raw, str) and raw.strip():
        return frozenset({raw.strip()})
    return frozenset()


def _required_groups() -> FrozenSet[str]:
    raw = (settings.AUTHZ_REQUIRED_GROUPS or "").strip()
    if not raw:
        return frozenset()
    return frozenset(g.strip() for g in raw.split(",") if g.strip())


def resolve_tenant_id(claims: dict[str, Any]) -> str:
    """Deprecated: tenant scoping removed. Kept for backward compatibility."""
    required_groups = _required_groups()
    if required_groups:
        if not _groups_from_claims(claims).intersection(required_groups):
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions",
            )
    return (settings.AUTHZ_DEFAULT_TENANT or "default").strip()[:64]


def get_effective_tenant_id(request: Request) -> str:
    claims = getattr(request.state, "jwt_claims", None)
    if not isinstance(claims, dict):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return resolve_tenant_id(claims)


def require_infra_probe(request: Request) -> None:
    """
    Block unauthenticated infrastructure probes unless DEBUG or a shared secret matches.
    """
    secret = (settings.INFRA_PROBE_SECRET or "").strip()
    hdr = (request.headers.get("X-Infra-Probe-Secret") or "").strip()
    if secret and hdr == secret:
        return
    if settings.DEBUG:
        return
    raise HTTPException(status_code=404, detail="Not found")
