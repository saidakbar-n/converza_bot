"""Shared Telegram outbound helpers — App bot vs Sales bot."""

import logging
from typing import Literal

import httpx

from services.telegram_bots import app_api_base, sales_api_base

logger = logging.getLogger(__name__)

BotKind = Literal["app", "sales"]


def _api_base(bot: BotKind) -> str:
    if bot == "app":
        return app_api_base()
    return sales_api_base()


async def send_message(
    chat_id: int,
    text: str,
    business_connection_id: str | None = None,
    *,
    bot: BotKind = "sales",
) -> bool:
    api = _api_base(bot)
    if not api:
        logger.warning("No Telegram token for bot=%s", bot)
        return False
    payload: dict = {"chat_id": chat_id, "text": text}
    if business_connection_id:
        payload["business_connection_id"] = business_connection_id
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(f"{api}/sendMessage", json=payload)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "Failed to send Telegram message bot=%s chat=%s: %s",
            bot,
            chat_id,
            exc,
        )
        return False
    return True


async def send_app_message(chat_id: int, text: str) -> bool:
    return await send_message(chat_id, text, bot="app")


async def send_sales_message(
    chat_id: int,
    text: str,
    business_connection_id: str | None = None,
) -> bool:
    return await send_message(
        chat_id,
        text,
        business_connection_id=business_connection_id,
        bot="sales",
    )
