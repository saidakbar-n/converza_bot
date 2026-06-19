"""Per-org Click payments via Telegram BotFather provider tokens."""

import os

from services.config import is_production

INVALID_PROVIDER_TOKENS = frozenset(
    {"", "123456", "test", "changeme", "your_token_here", "your_click_provider_token_here"}
)


def is_configured_provider_token(provider_token: str | None) -> bool:
    token = (provider_token or "").strip()
    if token.lower() in INVALID_PROVIDER_TOKENS:
        return False
    return len(token) >= 20 and ":" in token


def get_payment_provider_token(org: dict) -> str:
    """Resolve Click provider token for this org.

    Production: only the org's own click_token (voluntary per tenant).
    Development: org token first, then optional env test tokens.
    """
    org_token = (org.get("click_token") or "").strip()
    if is_production():
        return org_token if is_configured_provider_token(org_token) else ""

    if is_configured_provider_token(org_token):
        return org_token

    for env_key in (
        "CLICK_TEST_PROVIDER_TOKEN",
        "CLICK_PROVIDER_TOKEN",
        "TELEGRAM_PAYMENT_PROVIDER_TOKEN",
    ):
        candidate = (os.getenv(env_key) or "").strip()
        if is_configured_provider_token(candidate):
            return candidate
    return ""


def payments_enabled(org: dict) -> bool:
    return bool(get_payment_provider_token(org))


def payment_setup_message() -> str:
    """Instructions for the business owner (onboarding / owner chat only)."""
    web_url = (os.getenv("WEB_APP_URL") or "https://getconverza.com").rstrip("/")
    if is_production():
        return (
            "Click to'lovi yoqilmagan.\n\n"
            f"{web_url} → Brend pasporti → Click token (ixtiyoriy) maydoniga "
            "o'z provider tokeningizni qo'shing.\n"
            "@BotFather → bot → Payments → Click.\n\n"
            "Token bermasangiz, DM Closer matnli javob beradi — to'lov havolasi yuborilmaydi."
        )
    return (
        "Click test token topilmadi.\n"
        "@BotFather → Payments → Click dan provider token oling va "
        "brend pasportiga yozing yoki CLICK_TEST_PROVIDER_TOKEN ni .env ga qo'shing."
    )


def payment_unavailable_prospect_message() -> str:
    """Short message for prospects when an invoice was requested but Click is not configured."""
    return (
        "To'lov havolasini hozir yubora olmaymiz. "
        "Iltimos, biz bilan bevosita bog'laning — yordam beramiz."
    )
