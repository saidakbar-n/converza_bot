"""Shared Telegram outbound message helper."""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{os.environ.get('TELEGRAM_BOT_TOKEN', '')}"


async def send_message(
    chat_id: int,
    text: str,
    business_connection_id: str | None = None,
) -> bool:
    payload: dict = {"chat_id": chat_id, "text": text}
    if business_connection_id:
        payload["business_connection_id"] = business_connection_id
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{TELEGRAM_API}/sendMessage",
                json=payload,
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Failed to send Telegram message to chat %s: %s", chat_id, exc)
        return False
    return True
