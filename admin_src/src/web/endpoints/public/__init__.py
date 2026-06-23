from fastapi import APIRouter

from src.core.constants import API_V1

from src.web.endpoints.public.auth import router as auth_router
from src.web.endpoints.public.plans import router as plans_router
from src.web.endpoints.public.referral import router as referral_router
from src.web.endpoints.public.subscription import router as subscription_router
from .balance import router as balance_router
from .me_role import router as me_role_router
from .set_password import router as set_password_router
from .support import router as support_router
from .server_stats import router as server_stats_router

router = APIRouter(prefix=API_V1 + "/public")
router.include_router(plans_router)
router.include_router(auth_router)
router.include_router(me_role_router)
router.include_router(set_password_router)
router.include_router(support_router)
router.include_router(server_stats_router)
router.include_router(subscription_router)
router.include_router(referral_router)
router.include_router(balance_router)

__all__ = ["router"]
