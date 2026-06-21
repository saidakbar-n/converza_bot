"""Telegram Business connection events — received on @ConverzaSales_bot."""

import logging

from db.supabase_client import sb
from models.schemas import TelegramUpdate
from services.brand_passport import sync_organization
from services.telegram_bots import APP_BOT_USERNAME, SALES_BOT_USERNAME
from services.telegram_send import send_app_message

logger = logging.getLogger(__name__)


async def handle_business_connection(update: TelegramUpdate) -> None:
    conn = update.business_connection
    if not conn:
        return

    org_id = str(conn["user"]["id"])
    connection_id = conn["id"]
    is_enabled = conn.get("is_enabled", False)
    owner_chat_id = conn.get("user", {}).get("id")

    sync_organization(org_id)
    try:
        sb.table("organizations").upsert({
            "id": org_id,
            "business_connection_id": connection_id if is_enabled else None,
        }).execute()
    except Exception as exc:
        logger.warning("business_connection upsert skipped for %s: %s", org_id, exc)

    if not owner_chat_id:
        return

    if is_enabled:
        text = (
            "✅ Telegram Business ulanishi faollashtirildi!\n\n"
            f"@{SALES_BOT_USERNAME} endi mijozlar xabarlariga avtomatik javob beradi.\n"
            f"Boshqaruv va sozlamalar: @{APP_BOT_USERNAME} yoki veb-sahifa."
        )
    else:
        text = (
            "⚠️ Telegram Business ulanishi o'chirildi.\n\n"
            f"Qayta ulash: Sozlamalar → Business → Chatbots → @{SALES_BOT_USERNAME}"
        )
    await send_app_message(int(owner_chat_id), text)
