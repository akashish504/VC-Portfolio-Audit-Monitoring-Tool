from .dummy import router as dummy_router
from .draft_email import router as draft_email_router
from .audit_views import router as audit_views_router
from .email_attachments import router as email_attachments_router
from .email_template import router as email_template_router
from .email_threads import router as email_threads_router
from .portfolio import router as portfolio_router
from .settings import router as settings_router

__all__ = [
    "dummy_router",
    "draft_email_router",
    "audit_views_router",
    "email_attachments_router",
    "email_template_router",
    "email_threads_router",
    "portfolio_router",
    "settings_router",
]
