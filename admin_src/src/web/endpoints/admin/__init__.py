from fastapi import APIRouter

from src.core.constants import API_V1

from .abuse import router as abuse_router
from .admin_ip import router as admin_ip_router
from .two_factor import router as two_factor_router
from .ad_links import router as ad_links_router
from .appearance import router as appearance_router
from .apps import router as apps_router
from .audit import router as audit_router
from .auth_settings import router as auth_settings_router
from .broadcasts import router as broadcasts_router
from .cashback import router as cashback_router
from .email_settings import router as email_settings_router
from .email_template import router as email_template_router
from .gateways import router as gateways_router
from .grants import router as grants_router
from .import_users import router as import_users_router
from .info import router as info_router
from .menu import router as menu_router
from .morning_summary import router as morning_summary_router
from .new_device import router as new_device_router
from .notifications import router as notifications_router
from .plans import router as plans_router
from .promocodes import router as promocodes_router
from .remnawave import router as remnawave_router
from .server_status import router as server_status_router
from .settings import router as settings_router
from .settings_io import router as settings_io_router
from .statistics import router as statistics_router
from .subscription_app import router as subscription_app_router
from .subscriptions import router as subscriptions_router
from .support import router as support_router
from .topup import router as topup_router
from .traffic_alert import router as traffic_alert_router
from .login_alert import router as login_alert_router
from .transactions import router as transactions_router
from .digest import router as digest_router
from .email_gate import router as email_gate_router
from .freeze import router as freeze_router
from .promo_banner import router as promo_banner_router
from .reserve import router as reserve_router
from .trial_discount import router as trial_discount_router
from .winback import router as winback_router
from .updates import router as updates_router
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
router.include_router(settings_io_router)
router.include_router(server_status_router)
router.include_router(subscription_app_router)
router.include_router(cashback_router)
router.include_router(topup_router)
router.include_router(trial_discount_router)
router.include_router(promo_banner_router)
router.include_router(reserve_router)
router.include_router(winback_router)
router.include_router(digest_router)
router.include_router(traffic_alert_router)
router.include_router(login_alert_router)
router.include_router(new_device_router)
router.include_router(email_gate_router)
router.include_router(freeze_router)
router.include_router(morning_summary_router)
router.include_router(notifications_router)
router.include_router(gateways_router)
router.include_router(ad_links_router)
router.include_router(remnawave_router)
router.include_router(support_router)
router.include_router(appearance_router)
router.include_router(apps_router)
router.include_router(info_router)
router.include_router(menu_router)
router.include_router(email_template_router)
router.include_router(email_settings_router)
router.include_router(auth_settings_router)
router.include_router(audit_router)
router.include_router(grants_router)
router.include_router(updates_router)
router.include_router(abuse_router)
router.include_router(admin_ip_router)
router.include_router(two_factor_router)
router.include_router(import_users_router)

__all__ = ["router"]
