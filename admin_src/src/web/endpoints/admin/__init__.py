from fastapi import APIRouter

from src.core.constants import API_V1

from .ad_links import router as ad_links_router
from .appearance import router as appearance_router
from .apps import router as apps_router
from .audit import router as audit_router
from .broadcasts import router as broadcasts_router
from .email_template import router as email_template_router
from .gateways import router as gateways_router
from .menu import router as menu_router
from .plans import router as plans_router
from .promocodes import router as promocodes_router
from .remnawave import router as remnawave_router
from .settings import router as settings_router
from .statistics import router as statistics_router
from .subscriptions import router as subscriptions_router
from .support import router as support_router
from .transactions import router as transactions_router
from .users import router as users_router

router = APIRouter(prefix=API_V1 + "/admin")
router.include_router(statistics_router)
router.include_router(users_router)
router.include_router(subscriptions_router)
router.include_router(transactions_router)
router.include_router(promocodes_router)
router.include_router(plans_router)
router.include_router(broadcasts_router)
router.include_router(settings_router)
router.include_router(gateways_router)
router.include_router(ad_links_router)
router.include_router(remnawave_router)
router.include_router(support_router)
router.include_router(appearance_router)
router.include_router(apps_router)
router.include_router(menu_router)
router.include_router(email_template_router)
router.include_router(audit_router)

__all__ = ["router"]
