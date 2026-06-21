"""Two-bot setup: App (onboarding/admin/subscription) vs Sales (DM closer)."""

import os

# @ConverzaSales_bot — business DMs + end-customer Click invoices
SALES_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or os.environ.get(
    "SALES_BOT_TOKEN", ""
).strip()
SALES_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "ConverzaSales_bot").strip().lstrip("@")

# @ConverzaApp_bot — website login, onboarding, Converza subscription
APP_BOT_TOKEN = (
    os.environ.get("TELEGRAM_APP_BOT_TOKEN", "").strip()
    or os.environ.get("MANAGER_BOT_TOKEN", "").strip()
    or SALES_BOT_TOKEN
)
APP_BOT_USERNAME = os.getenv("TELEGRAM_APP_BOT_USERNAME", "ConverzaApp_bot").strip().lstrip("@")


def sales_api_base() -> str:
    if not SALES_BOT_TOKEN:
        return ""
    return f"https://api.telegram.org/bot{SALES_BOT_TOKEN}"


def app_api_base() -> str:
    if not APP_BOT_TOKEN:
        return ""
    return f"https://api.telegram.org/bot{APP_BOT_TOKEN}"
