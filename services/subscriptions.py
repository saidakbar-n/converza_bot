"""Converza monthly subscription — billed via @ConverzaApp_bot + Click."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

from db.supabase_client import sb
from services.config import is_production
from services.payments import is_configured_provider_token
from services.telegram_bots import app_api_base

logger = logging.getLogger(__name__)

DEFAULT_PRICE_UZS = int(os.getenv("CONVERZA_SUBSCRIPTION_PRICE_UZS", "500000"))
SUBSCRIPTION_DAYS = int(os.getenv("CONVERZA_SUBSCRIPTION_PERIOD_DAYS", "30"))


def subscription_required() -> bool:
    if os.getenv("SUBSCRIPTION_REQUIRED", "").strip().lower() in ("0", "false", "no"):
        return False
    if not is_production():
        return os.getenv("SUBSCRIPTION_REQUIRED", "").strip().lower() in ("1", "true", "yes")
    return True


def get_subscription_provider_token() -> str:
    for key in (
        "CONVERZA_SUBSCRIPTION_PROVIDER_TOKEN",
        "CONVERZA_CLICK_PROVIDER_TOKEN",
        "CLICK_TEST_PROVIDER_TOKEN",
    ):
        candidate = (os.getenv(key) or "").strip()
        if is_configured_provider_token(candidate):
            return candidate
    return ""


def subscription_payments_configured() -> bool:
    return bool(get_subscription_provider_token())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_subscription(org_id: str) -> dict | None:
    result = (
        sb.table("org_subscriptions")
        .select("*")
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if result is None:
        return None
    return result.data


def is_subscription_active(org_id: str) -> bool:
    if not subscription_required():
        return True
    row = fetch_subscription(org_id)
    if not row or row.get("status") != "active":
        return False
    period_end = _parse_ts(row.get("current_period_end"))
    if period_end and period_end < _now():
        return False
    return True


def activate_subscription(
    org_id: str,
    *,
    amount_uzs: int | None = None,
    charge_id: str | None = None,
) -> dict:
    now = _now()
    period_end = now + timedelta(days=SUBSCRIPTION_DAYS)
    existing = fetch_subscription(org_id)
    if existing and existing.get("status") == "active":
        current_end = _parse_ts(existing.get("current_period_end"))
        if current_end and current_end > now:
            period_end = current_end + timedelta(days=SUBSCRIPTION_DAYS)

    payload = {
        "org_id": org_id,
        "status": "active",
        "amount_uzs": amount_uzs or existing.get("amount_uzs") if existing else amount_uzs or DEFAULT_PRICE_UZS,
        "current_period_start": now.isoformat(),
        "current_period_end": period_end.isoformat(),
        "last_payment_at": now.isoformat(),
        "telegram_payment_charge_id": charge_id,
        "updated_at": now.isoformat(),
    }
    result = sb.table("org_subscriptions").upsert(payload, on_conflict="org_id").execute()
    return (result.data or [payload])[0]


def subscription_status_text(org_id: str) -> str:
    if not subscription_required():
        return "Obuna talab qilinmaydi (dev rejim)."
    row = fetch_subscription(org_id)
    if not row or row.get("status") != "active":
        return "Obuna faol emas — /subscribe buyrug'i bilan to'lov qiling."
    end = _parse_ts(row.get("current_period_end"))
    if end:
        return f"Obuna faol — {end.strftime('%d.%m.%Y')} gacha."
    return "Obuna faol."


async def send_subscription_invoice(chat_id: int, org_id: str) -> tuple[bool, str]:
    token = get_subscription_provider_token()
    if not is_configured_provider_token(token):
        web = (os.getenv("WEB_APP_URL") or "https://getconverza.com").rstrip("/")
        return False, (
            "Obuna to'lovi hozir sozlanmagan. Administratorga xabar bering yoki "
            f"keyinroq qayta urinib ko'ring.\n\nVeb: {web}"
        )

    amount = DEFAULT_PRICE_UZS
    body = {
        "chat_id": chat_id,
        "title": "Converza oylik obuna"[:32],
        "description": (
            "DM Closer + Co-Pilot — Telegram sotuv avtomatlashtirish (30 kun)"
        )[:255],
        "payload": f"subscription_{org_id}",
        "provider_token": token,
        "currency": "UZS",
        "prices": [{"label": "Oylik obuna"[:32], "amount": amount}],
    }
    api = app_api_base()
    if not api:
        return False, "App bot token sozlanmagan."

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{api}/sendInvoice", json=body)
    if resp.is_success:
        return True, "Obuna hisob-fakturasi yuborildi. To'lovni Telegram orqali yakunlang."
    return False, f"Hisob-faktura yuborilmadi: {resp.text[:240]}"
