"""Gate DM Closer until org setup is complete."""

from services.brand_passport import fetch_passport_by_org, get_org_context
from services.subscriptions import is_subscription_active

REASON_LABELS = {
    "no_business_connection": "Telegram Business ulanishi yo'q",
    "no_active_subscription": "Converza obunasi faol emas",
    "no_brand_passport": "Brend pasporti saqlanmagan",
    "missing_brand_name": "Brend nomi to'ldirilmagan",
    "missing_core_offer": "Asosiy taklif to'ldirilmagan",
    "missing_pricing": "Kamida bitta narx darajasi kerak",
}


def assess_closer_readiness(org_id: str) -> tuple[bool, str]:
    """
    Return (ready, reason_code).

    DM Closer requires subscription, business connection, and brand passport
    before replying to customer DMs autonomously.
    """
    if not is_subscription_active(org_id):
        return False, "no_active_subscription"

    org = get_org_context(org_id)
    if not org.get("business_connection_id"):
        return False, "no_business_connection"

    passport = fetch_passport_by_org(org_id)
    if not passport:
        return False, "no_brand_passport"

    if not str(passport.get("brand_name") or "").strip():
        return False, "missing_brand_name"
    if not str(passport.get("core_offer") or "").strip():
        return False, "missing_core_offer"
    if not (passport.get("pricing") or []):
        return False, "missing_pricing"

    return True, ""


def readiness_label(reason_code: str) -> str:
    return REASON_LABELS.get(reason_code, reason_code)
