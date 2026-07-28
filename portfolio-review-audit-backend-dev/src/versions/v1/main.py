"""
API v1 — portfolio (dev) + audit / users / comparison (merged from portfolio-review-audit-backend-main).
"""
from fastapi import APIRouter
from src.versions.v1.routes import (
    actionable_deals,
    audit,
    audit_views,
    comparison,
    dashboard,
    dashboard_data,
    data,
    draft_email,
    dummy,
    email_attachments,
    email_template,
    email_threads,
    export,
    fx,
    master_scoping,
    org_chart_batches,
    org_chart_reconciliation,
    portfolio,
    settings,
    sync_alerts,
    user,
    users_profile,
)

api_router = APIRouter()


@api_router.get("/v1/health", tags=["Health"])
async def health_check():
    return {"status": "ok"}


api_router.include_router(dummy.router, prefix="/v1", tags=["Utility"])
api_router.include_router(data.router, prefix="/v1/data", tags=["Data"])
api_router.include_router(portfolio.router, prefix="/v1", tags=["Portfolio"])
api_router.include_router(fx.router, prefix="/v1", tags=["FX"])
api_router.include_router(settings.router, prefix="/v1", tags=["Settings"])
api_router.include_router(user.router, prefix="/v1", tags=["User"])
api_router.include_router(users_profile.router, prefix="/v1/users", tags=["User profiles"])
api_router.include_router(audit.router, prefix="/v1/audit-files", tags=["Audit Files"])
api_router.include_router(comparison.router, prefix="/v1/comparisons", tags=["Analysis runs"])
api_router.include_router(audit_views.router, prefix="/v1", tags=["Audit views"])

api_router.include_router(email_template.router, prefix="/v1", tags=["EmailTemplates"])
api_router.include_router(draft_email.router, prefix="/v1", tags=["DraftEmail"])
api_router.include_router(email_threads.router, prefix="/v1", tags=["EmailThreads"])
api_router.include_router(email_attachments.router, prefix="/v1", tags=["EmailAttachments"])
api_router.include_router(export.router, prefix="/v1", tags=["Export"])
api_router.include_router(dashboard.router, prefix="/v1/dashboard", tags=["Dashboard"])
api_router.include_router(dashboard_data.router, prefix="/v1", tags=["Dashboard Data"])
api_router.include_router(master_scoping.router, prefix="/v1", tags=["Master Scoping"])
api_router.include_router(actionable_deals.router, prefix="/v1", tags=["Actionable Deals"])
api_router.include_router(org_chart_batches.router, prefix="/v1", tags=["OrgChartBatches"])
api_router.include_router(org_chart_reconciliation.router, prefix="/v1", tags=["OrgChartReconciliation"])
api_router.include_router(sync_alerts.router, prefix="/v1/sync-alerts", tags=["SyncAlerts"])
